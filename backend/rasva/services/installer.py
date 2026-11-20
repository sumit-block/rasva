"""Model installer (P0-013, PRD §8, §23, §28; ADR-0002, ADR-0003).

Install runs as a queued job so it never competes with inference for memory or
bandwidth, and so it shows the same progress, cancel and retry UI as any job.

Guarantees:
- Nothing is downloaded without a storage preflight (budget and macOS reserve).
- Only https sources; every redirect hop is checked too.
- Every artifact is verified against its pinned SHA-256 and exact size before it
  is moved (atomically) into the model directory. A bad file is never installed.
- Interrupted or canceled downloads keep their ``.part`` file and resume with an
  HTTP Range request next time. Parts are keyed by SHA-256, so a changed
  artifact can never resume onto stale bytes.
- Engine environments are built in a hidden ``.partial`` directory from a
  hash-pinned lockfile and only swapped into place when complete.
- Removing an engine deletes its weights, environment and partial downloads only;
  project files are never touched.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import logging
import os
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from rasva import __version__
from rasva.config import Settings
from rasva.db import dumps, loads, utcnow
from rasva.engines.contract import EngineDescriptor, InstallContext, ModelArtifact
from rasva.engines.registry import EngineRegistry, RegisteredEngine, load_class
from rasva.jobs.states import JobState
from rasva.security import LOOPBACK_NAMES
from rasva.services.settings_store import SettingsStore
from rasva.services.storage import StorageService, disk_usage_bytes

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
PROGRESS_INTERVAL_S = 0.25
ENV_MARKER = ".rasva-lock-sha256"


class InstallError(Exception):
    """Expected failure with a message safe to show users."""

    def __init__(self, code: str, user_message: str, reasons: list[str] | None = None) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.reasons = reasons or []


class InstallCanceled(Exception):
    pass


class _Retryable(Exception):
    """A transient download problem; the attempt may be resumed."""


@dataclass
class Hooks:
    transition: Callable[[JobState, str], None]
    progress: Callable[[float | None, str | None], None]
    cancel: asyncio.Event


def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB" if n >= 1024**3 else f"{n / 1024**2:.0f} MB"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- models table


class ModelStore:
    """Install state per engine in the ``models`` table (id == engine id)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, engine_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM models WHERE id = ?", (engine_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["metadata"] = loads(d["metadata"], {})
        return d

    def all(self) -> dict[str, dict[str, Any]]:
        out = {}
        for row in self._conn.execute("SELECT * FROM models"):
            d = dict(row)
            d["metadata"] = loads(d["metadata"], {})
            out[d["id"]] = d
        return out

    def is_installed(self, engine_id: str) -> bool:
        m = self.get(engine_id)
        return bool(m and m["install_state"] == "installed")

    def set_state(
        self,
        d: EngineDescriptor,
        state: str,
        *,
        installed_bytes: int | None = None,
        metadata: dict[str, Any] | None = None,
        validated: bool = False,
    ) -> None:
        now = utcnow()
        first_host = urlsplit(d.artifacts[0].url).netloc if d.artifacts else "built-in"
        source = d.source_url or first_host
        meta = metadata if metadata is not None else {}
        self._conn.execute(
            "INSERT INTO models(id, engine_id, source, revision, license, license_class,"
            " install_state, installed_bytes, validated_at, metadata, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET source = excluded.source,"
            " revision = excluded.revision, license = excluded.license,"
            " license_class = excluded.license_class, install_state = excluded.install_state,"
            " installed_bytes = COALESCE(?, models.installed_bytes),"
            " validated_at = COALESCE(excluded.validated_at, models.validated_at),"
            " metadata = excluded.metadata, updated_at = excluded.updated_at",
            (
                d.id, d.id, source, d.version, d.license, d.license_class, state,
                installed_bytes or 0, now if validated else None, dumps(meta), now, now,
                installed_bytes,
            ),
        )  # fmt: skip

    def delete(self, engine_id: str) -> None:
        self._conn.execute("DELETE FROM models WHERE id = ?", (engine_id,))


# ------------------------------------------------------------------ downloads


def check_source_url(url: str, allow_loopback_http: bool) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return
    if allow_loopback_http and parts.scheme == "http" and host in LOOPBACK_NAMES:
        return
    raise InstallError(
        "insecure_source",
        "This engine's download address is not secure (https), so Rasva will not install it.",
    )


class Downloader:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        backoff_s: float = 1.0,
    ) -> None:
        self._settings = settings
        self._paths = settings.paths
        self._client_factory = client_factory or self._default_client
        self._backoff = backoff_s

    def _default_client(self) -> httpx.AsyncClient:
        allow_http = self._settings.allow_loopback_http_downloads

        async def check_hop(request: httpx.Request) -> None:
            # Runs for the first request and for every redirect hop.
            check_source_url(str(request.url), allow_http)

        return httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=10,
            timeout=httpx.Timeout(30.0, read=60.0),
            headers={"User-Agent": f"RasvaStudio/{__version__}"},
            event_hooks={"request": [check_hop]},
        )

    def part_path(self, artifact: ModelArtifact) -> Path:
        return self._paths.downloads / f"{artifact.sha256}.part"

    async def fetch(
        self,
        artifact: ModelArtifact,
        dest: Path,
        report: Callable[[int], None],
        cancel: asyncio.Event,
    ) -> int:
        """Download, verify and install one artifact. Returns bytes transferred."""
        check_source_url(artifact.url, self._settings.allow_loopback_http_downloads)
        if dest.is_file() and dest.stat().st_size == artifact.size_bytes:
            if await asyncio.to_thread(sha256_file, dest) == artifact.sha256:
                report(artifact.size_bytes)
                return 0
            dest.unlink()  # damaged or replaced; fetch again

        part = self.part_path(artifact)
        part.parent.mkdir(parents=True, exist_ok=True)
        transferred = 0
        attempt = 0
        async with self._client_factory() as client:
            while True:
                try:
                    transferred += await self._attempt(client, artifact, part, report, cancel)
                    break
                except _Retryable as exc:
                    attempt += 1
                    log.warning(
                        "download attempt failed",
                        extra={"artifact": artifact.path, "attempt": attempt, "reason": str(exc)},
                    )
                    if attempt > self._settings.download_retries:
                        raise InstallError(
                            "download_failed",
                            "The download kept failing. Check your internet connection and "
                            "retry; what was already downloaded is kept.",
                        ) from exc
                    await asyncio.sleep(min(self._backoff * 2 ** (attempt - 1), 15))
                    if cancel.is_set():
                        raise InstallCanceled from None

        digest = await asyncio.to_thread(sha256_file, part)
        if digest != artifact.sha256:
            part.unlink(missing_ok=True)
            raise InstallError(
                "checksum_mismatch",
                "A downloaded file did not match its expected fingerprint, so it was "
                "discarded. Nothing was installed from it. Retry, or report the engine.",
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, dest)
        return transferred

    async def _attempt(
        self,
        client: httpx.AsyncClient,
        artifact: ModelArtifact,
        part: Path,
        report: Callable[[int], None],
        cancel: asyncio.Event,
    ) -> int:
        size = artifact.size_bytes
        offset = part.stat().st_size if part.exists() else 0
        if offset > size:
            part.unlink()
            offset = 0
        report(offset)
        if offset == size:
            return 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            async with client.stream("GET", artifact.url, headers=headers) as resp:
                status = resp.status_code
                if status == 416:
                    part.unlink(missing_ok=True)
                    raise _Retryable("range not satisfiable; restarting")
                if status in (408, 429) or status >= 500:
                    raise _Retryable(f"server returned {status}")
                if status not in (200, 206):
                    raise InstallError(
                        "download_failed",
                        f"The download server refused the request (error {status}).",
                    )
                if offset and status == 200:
                    offset = 0  # server ignored Range; start over
                elif status == 206:
                    content_range = resp.headers.get("content-range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        part.unlink(missing_ok=True)
                        raise _Retryable("unexpected Content-Range; restarting")

                written = 0
                with part.open("ab" if offset else "wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if cancel.is_set():
                            raise InstallCanceled
                        written += len(chunk)
                        if offset + written > size:
                            fh.close()
                            part.unlink(missing_ok=True)
                            raise InstallError(
                                "size_mismatch",
                                "A downloaded file was larger than expected, so it was "
                                "discarded. Nothing was installed from it.",
                            )
                        fh.write(chunk)
                        report(offset + written)
        except httpx.TransportError as exc:
            # Includes timeouts and dropped connections; the .part is kept.
            raise _Retryable(type(exc).__name__) from exc
        if offset + written < size:
            raise _Retryable("connection closed early")
        return written


# --------------------------------------------------------------- environments


def resolve_lockfile(engine: RegisteredEngine) -> Path:
    rel = engine.descriptor.requirements_lock
    assert rel is not None
    module = importlib.import_module(engine.import_path.partition(":")[0])
    package = module.__package__ or module.__name__
    path = Path(str(resources.files(package) / rel))
    if not path.is_file():
        raise InstallError("environment_failed", "This engine's software list is missing.")
    return path


class EnvironmentBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._paths = settings.paths

    def uv(self) -> str:
        exe = self._settings.uv_executable or shutil.which("uv")
        if not exe:
            raise InstallError(
                "missing_tool",
                "Rasva's setup tool (uv) was not found, so this engine's software cannot be "
                "installed. Reinstall Rasva Studio and retry.",
            )
        return exe

    def is_current(self, engine_id: str, lock: Path) -> bool:
        target = self._paths.env_dir(engine_id)
        marker = target / ENV_MARKER
        return (
            (target / "bin" / "python").exists()
            and marker.is_file()
            and marker.read_text().strip() == sha256_file(lock)
        )

    async def build(self, engine_id: str, lock: Path, cancel: asyncio.Event) -> None:
        if self.is_current(engine_id, lock):
            return
        uv = self.uv()
        target = self._paths.env_dir(engine_id)
        work = self._paths.envs / f".{engine_id}.partial"
        shutil.rmtree(work, ignore_errors=True)
        env = {**os.environ, **self._paths.worker_env()}
        for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            env.pop(key, None)
        log_path = self._paths.logs / "engines" / f"{engine_id}-install.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(
                [uv, "venv", "--relocatable", "--python", sys.executable, str(work)],
                env, log_path, cancel,
            )  # fmt: skip
            await self._run(
                [
                    uv, "pip", "install", "--python", str(work / "bin" / "python"),
                    "--require-hashes", "--no-config", "-r", str(lock),
                ],
                env, log_path, cancel,
            )  # fmt: skip
            (work / ENV_MARKER).write_text(sha256_file(lock))
            shutil.rmtree(target, ignore_errors=True)
            os.replace(work, target)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _run(
        self, cmd: list[str], env: dict[str, str], log_path: Path, cancel: asyncio.Event
    ) -> None:
        with log_path.open("ab") as out:
            out.write(f"\n$ {' '.join(cmd)}\n".encode())
            out.flush()
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=out, stderr=asyncio.subprocess.STDOUT, env=env,
                cwd=str(self._paths.tmp),
            )  # fmt: skip
            wait = asyncio.ensure_future(proc.wait())
            cancel_wait = asyncio.ensure_future(cancel.wait())
            done, _ = await asyncio.wait({wait, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
            cancel_wait.cancel()
            if wait not in done:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await wait
                raise InstallCanceled
        if proc.returncode != 0:
            raise InstallError(
                "environment_failed",
                "Setting up this engine's software failed. Details are in the engine install log.",
            )


# ------------------------------------------------------------------ installer


class Installer:
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        registry: EngineRegistry,
        storage: StorageService,
        downloader: Downloader | None = None,
    ) -> None:
        self._settings = settings
        self._paths = settings.paths
        self._conn = conn
        self._registry = registry
        self._storage = storage
        self._store = ModelStore(conn)
        self._downloader = downloader or Downloader(settings)
        self._envs = EnvironmentBuilder(settings)

    # ---- sizing ----

    def required_bytes(self, engine: RegisteredEngine) -> int:
        """Peak extra space an install needs, net of finished and partial files."""
        d = engine.descriptor
        model_dir = self._paths.model_dir(d.modality, d.id)
        need = 0
        for a in d.artifacts:
            dest = model_dir / a.path
            if dest.is_file() and dest.stat().st_size == a.size_bytes:
                continue
            part = self._downloader.part_path(a)
            have = part.stat().st_size if part.exists() else 0
            need += max(a.size_bytes - min(have, a.size_bytes), 0)
        if d.requirements_lock and not (self._paths.env_dir(d.id) / "bin" / "python").exists():
            need += max(d.approx_installed_bytes - sum(a.size_bytes for a in d.artifacts), 0)
        return need

    def preflight(self, engine: RegisteredEngine) -> None:
        store = SettingsStore(self._conn)
        check = self._storage.preflight(
            self.required_bytes(engine),
            "download",
            budget_bytes=store.get("storage.budget_bytes"),
            os_reserve_bytes=store.get("storage.os_reserve_bytes"),
        )
        if not check.allowed:
            raise InstallError("storage_full", " ".join(check.reasons), check.reasons)

    # ---- lifecycle ----

    def recover_after_restart(self) -> None:
        for engine_id, m in self._store.all().items():
            if m["install_state"] in ("installing", "removing"):
                meta = {**m["metadata"], "error": "interrupted"}
                engine = self._registry.get(engine_id)
                if engine:
                    self._store.set_state(engine.descriptor, "failed", metadata=meta)
                else:
                    self._store.delete(engine_id)
        self.cleanup_orphans()

    def cleanup_orphans(self) -> int:
        """Delete partial downloads no registered engine needs, and stale env builds."""
        wanted = {f"{a.sha256}.part" for e in self._registry.all() for a in e.descriptor.artifacts}
        freed = 0
        if self._paths.downloads.exists():
            for p in self._paths.downloads.iterdir():
                if p.name not in wanted:
                    freed += p.stat().st_size if p.is_file() else 0
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        p.unlink(missing_ok=True)
        if self._paths.envs.exists():
            for p in self._paths.envs.glob(".*.partial"):
                shutil.rmtree(p, ignore_errors=True)
        if freed:
            log.info("removed orphaned partial downloads", extra={"bytes": freed})
        return freed

    def _artifact_dest(self, model_dir: Path, artifact: ModelArtifact) -> Path:
        dest = (model_dir / artifact.path).resolve()
        if not dest.is_relative_to(model_dir.resolve()):
            raise InstallError("invalid_engine", "This engine's file list is invalid.")
        return dest

    async def install(self, engine: RegisteredEngine, hooks: Hooks) -> dict[str, Any]:
        d = engine.descriptor
        if not d.requires_install:
            raise InstallError("not_installable", "This engine is built in.")
        if not d.artifacts and not d.requirements_lock:
            raise InstallError("invalid_engine", "This engine has nothing to install.")
        for a in d.artifacts:
            check_source_url(a.url, self._settings.allow_loopback_http_downloads)
        self.preflight(engine)

        model_dir = self._paths.model_dir(d.modality, d.id)
        meta = {"artifacts": [{"path": a.path, "sha256": a.sha256} for a in d.artifacts]}
        self._store.set_state(d, "installing", metadata=meta)
        started = time.perf_counter()
        try:
            transferred = await self._download_all(d, model_dir, hooks)
            download_s = time.perf_counter() - started

            hooks.transition(JobState.PREPARING, "Setting up engine software")
            env_started = time.perf_counter()
            if d.requirements_lock:
                await self._envs.build(d.id, resolve_lockfile(engine), hooks.cancel)
            env_s = time.perf_counter() - env_started

            hooks.transition(JobState.PROCESSING, "Checking the installation")
            ctx = InstallContext(model_dir=model_dir, cache_dir=self._paths.cache)
            engine_obj = load_class(engine.import_path)()
            if not await asyncio.to_thread(engine_obj.verify, ctx):
                raise InstallError(
                    "verify_failed", "The engine was downloaded but failed its self-check."
                )

            hooks.transition(JobState.FINALIZING, "Finishing")
            installed = disk_usage_bytes(model_dir) + disk_usage_bytes(self._paths.env_dir(d.id))
            self._store.set_state(
                d, "installed", installed_bytes=installed, metadata=meta, validated=True
            )
            return {
                "downloaded_bytes": transferred,
                "installed_bytes": installed,
                "download_s": round(download_s, 3),
                "environment_s": round(env_s, 3),
            }
        except InstallCanceled:
            # Not installed; partial downloads stay for resume.
            self._store.delete(d.id)
            raise
        except BaseException as exc:
            code = exc.code if isinstance(exc, InstallError) else "internal_error"
            self._store.set_state(d, "failed", metadata={**meta, "error": code})
            raise
        finally:
            self._storage.invalidate()

    async def _download_all(self, d: EngineDescriptor, model_dir: Path, hooks: Hooks) -> int:
        total = sum(a.size_bytes for a in d.artifacts)
        if not total:
            return 0
        hooks.transition(JobState.DOWNLOADING, f"Downloading {_gb(total)}")
        done_before = 0
        last = 0.0

        def make_report(base: int) -> Callable[[int], None]:
            def report(n: int) -> None:
                nonlocal last
                now = time.monotonic()
                current = base + n
                if now - last >= PROGRESS_INTERVAL_S or current >= total:
                    last = now
                    hooks.progress(current / total, f"Downloaded {_gb(current)} of {_gb(total)}")

            return report

        transferred = 0
        for a in d.artifacts:
            if hooks.cancel.is_set():
                raise InstallCanceled
            dest = self._artifact_dest(model_dir, a)
            transferred += await self._downloader.fetch(
                a, dest, make_report(done_before), hooks.cancel
            )
            done_before += a.size_bytes
        return transferred

    async def remove(self, engine: RegisteredEngine, hooks: Hooks) -> dict[str, Any]:
        d = engine.descriptor
        hooks.transition(JobState.PROCESSING, "Removing engine files")
        if self._store.get(d.id):
            self._store.set_state(d, "removing")
        model_dir = self._paths.model_dir(d.modality, d.id)
        freed = disk_usage_bytes(model_dir) + disk_usage_bytes(self._paths.env_dir(d.id))
        try:
            ctx = InstallContext(model_dir=model_dir, cache_dir=self._paths.cache)
            await asyncio.to_thread(load_class(engine.import_path)().remove, ctx)
            await asyncio.to_thread(shutil.rmtree, model_dir, True)
            await asyncio.to_thread(shutil.rmtree, self._paths.env_dir(d.id), True)
            for a in d.artifacts:
                part = self._downloader.part_path(a)
                if part.exists():
                    freed += part.stat().st_size
                    part.unlink(missing_ok=True)
            hooks.transition(JobState.FINALIZING, "Finishing")
            self._store.delete(d.id)
        except BaseException:
            self._store.set_state(d, "failed", metadata={"error": "remove_failed"})
            raise
        finally:
            self._storage.invalidate()
        return {"freed_bytes": freed}
