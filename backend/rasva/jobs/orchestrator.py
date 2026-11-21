"""Single-heavy-job orchestrator (PRD §21.1, §24).

- Exactly one job executes at a time.
- Each engine runs in its own worker subprocess. A worker stays resident between
  jobs for the same engine (warm start) and is shut down before a different
  engine loads, or after an idle timeout, so only one set of heavy weights is
  ever in unified memory.
- Cancellation is cooperative first (engine checks at safe points), then the
  worker is killed after a grace period.
- A crashed worker fails only the current job; completed outputs are untouched.
- Memory guard (PRD §24): before an engine loads cold, available memory must
  cover the engine's measured peak plus a safety margin. If it does not, an idle
  worker of another engine is unloaded and memory re-read; if there is still no
  room the job stays queued with a "Waiting for memory" detail and is re-checked
  every ``memory_recheck_s``. Jobs that load nothing (warm engine, model tasks,
  light built-ins) never wait, and may run past a job that is waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from rasva.config import Settings
from rasva.engines.registry import EngineRegistry, RegisteredEngine
from rasva.jobs import repository as repo
from rasva.jobs.events import EventBus
from rasva.jobs.states import TERMINAL, InvalidTransition, JobState
from rasva.logging_setup import rotate_if_large
from rasva.services import assets as asset_svc
from rasva.services import memory as memory_svc
from rasva.services import projects as project_svc
from rasva.services.installer import (
    Downloader,
    Hooks,
    InstallCanceled,
    Installer,
    InstallError,
    ModelStore,
)
from rasva.services.settings_store import SettingsStore
from rasva.services.storage import StorageService

log = logging.getLogger(__name__)

READY_TIMEOUT_S = 60.0
MODEL_TASKS = frozenset({"model.install", "model.update", "model.remove"})
PROGRESS_WRITE_INTERVAL_S = 0.25

MSG_CRASH = (
    "The engine stopped unexpectedly. Your other work is safe. If your Mac was low on "
    "memory, close other apps or choose a lighter preset, then retry."
)
MSG_SOFTWARE_UPDATE = (
    "A software update is available for this engine. Open Models and choose Update, then retry."
)
MSG_MEMORY_KILL = (
    "Your Mac ran low on memory and macOS stopped the engine. Your other work is safe. "
    "Close other apps or choose a lighter preset, then retry."
)
MemoryVerdict = Literal["go", "wait", "failed"]


class WorkerGone(Exception):
    def __init__(self, returncode: int | None) -> None:
        super().__init__(f"worker exited with {returncode}")
        self.returncode = returncode


class Worker:
    """Parent-side handle for one engine worker subprocess."""

    def __init__(self, engine: RegisteredEngine, proc: asyncio.subprocess.Process) -> None:
        self.engine = engine
        self.proc = proc
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.loaded = False
        self.last_used = time.monotonic()
        self._reader = asyncio.create_task(
            self._read(), name=f"worker-reader-{engine.descriptor.id}"
        )

    @property
    def engine_id(self) -> str:
        return self.engine.descriptor.id

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            while line := await self.proc.stdout.readline():
                try:
                    await self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("worker %s sent malformed line", self.engine_id)
        finally:
            await self.events.put(None)

    async def send(self, msg: dict[str, Any]) -> None:
        if not self.alive or self.proc.stdin is None:
            raise WorkerGone(self.proc.returncode)
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise WorkerGone(self.proc.returncode) from exc

    async def next_event(self) -> dict[str, Any]:
        event = await self.events.get()
        if event is None:
            rc = await self.proc.wait()
            raise WorkerGone(rc)
        return event

    async def kill(self) -> None:
        if self.alive:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
        await self.proc.wait()
        self._reader.cancel()

    async def shutdown(self, timeout: float = 10.0) -> None:
        if self.alive:
            with contextlib.suppress(WorkerGone):
                await self.send({"op": "shutdown"})
            try:
                await asyncio.wait_for(self.proc.wait(), timeout)
            except TimeoutError:
                log.warning("worker %s did not exit; killing", self.engine_id)
        await self.kill()


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        connect: Callable[[], sqlite3.Connection],
        registry: EngineRegistry,
        storage: StorageService,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._paths = settings.paths
        self._conn = connect()
        self._registry = registry
        self._storage = storage
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._worker: Worker | None = None
        self._current_job: str | None = None
        self._cancel = asyncio.Event()
        # Set while the next heavy job is held back for memory; shown in status.
        self._memory_wait: dict[str, Any] | None = None
        self._installer = Installer(
            settings,
            self._conn,
            registry,
            storage,
            Downloader(settings, backoff_s=settings.download_backoff_s),
        )

    # ---- lifecycle ----

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for job_id in repo.recover_after_restart(self._conn):
            log.info("job %s marked interrupted after restart", job_id)
        self._installer.recover_after_restart()
        shutil.rmtree(self._paths.staging, ignore_errors=True)
        self._paths.staging.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._run(), name="rasva-orchestrator")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        self._cancel.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._worker:
            await self._worker.kill()
            self._worker = None
        self._conn.close()

    # ---- thread-safe entry points for API handlers ----

    def notify(self) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def cancel(self, job_id: str) -> None:
        def _do() -> None:
            if self._current_job == job_id:
                self._cancel.set()
            # A job waiting for memory is still queued: re-select now.
            self._wake.set()

        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(_do)

    def status(self) -> dict[str, Any]:
        w = self._worker
        return {
            "current_job": self._current_job,
            "resident_engine": w.engine_id if w and w.alive else None,
            "resident_engine_loaded": bool(w and w.alive and w.loaded),
            "memory_wait": dict(self._memory_wait) if self._memory_wait else None,
        }

    # ---- main loop ----

    async def _run(self) -> None:
        while not self._stopping:
            try:
                job = await self._select()
                if job is None:
                    await self._idle_wait(
                        self._settings.memory_recheck_s if self._memory_wait else None
                    )
                    continue
                await self._execute(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let one bad job kill the scheduler.
                log.exception("orchestrator loop error")
                await asyncio.sleep(1)

    async def _idle_wait(self, recheck_s: float | None = None) -> None:
        self._wake.clear()
        timeout = recheck_s
        w = self._worker
        if w and w.alive:
            remaining = self._settings.engine_idle_unload_s - (time.monotonic() - w.last_used)
            if remaining <= 0:
                log.info("unloading idle engine %s", w.engine_id)
                await self._drop_worker()
                return
            timeout = remaining if timeout is None else min(timeout, remaining)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout)

    # ---- memory guard (PRD §24) ----

    async def _select(self) -> dict[str, Any] | None:
        """Pick the next job to run, holding back a cold start that has no room.

        Queue order is kept for every job that loads an engine. Only jobs that
        need no memory check may run past a job that is waiting for memory.
        """
        while True:
            queued = repo.queued(self._conn)
            if not queued:
                self._memory_wait = None
                return None
            head = queued[0]
            verdict = await self._memory_verdict(head)
            if verdict == "failed":
                continue
            if verdict == "go":
                self._memory_wait = None
                return head
            return next((j for j in queued[1:] if not self._needs_memory_check(j)), None)

    def _needs_memory_check(self, job: dict[str, Any]) -> bool:
        if job["task"] in MODEL_TASKS:
            return False
        engine = self._registry.get(job["engine_id"])
        if engine is None or engine.descriptor.approx_peak_memory_bytes <= 0:
            return False
        d = engine.descriptor
        if d.requires_install and not ModelStore(self._conn).is_installed(d.id):
            return False  # _execute fails it straight away
        w = self._worker
        # Warm: the engine is already loaded, so starting adds nothing.
        return not (w and w.alive and w.loaded and w.engine_id == d.id)

    async def _memory_verdict(self, job: dict[str, Any]) -> MemoryVerdict:
        if not self._needs_memory_check(job):
            return "go"
        engine = self._registry.get(job["engine_id"])
        assert engine is not None
        d = engine.descriptor
        margin = int(SettingsStore(self._conn).get("memory.safety_margin_bytes"))
        needed = d.approx_peak_memory_bytes + margin
        reading = memory_svc.read()
        if needed > reading.total_bytes:
            self._fail(
                job["id"],
                "not_enough_memory",
                f"{d.display_name} needs about {memory_svc.format_gb(needed)} of memory, "
                f"more than this Mac has ({memory_svc.format_gb(reading.total_bytes)}). "
                "Choose a lighter engine, or lower the memory safety margin in Settings.",
            )
            return "failed"
        if reading.available_bytes >= needed:
            return "go"
        w = self._worker
        if w and w.alive:
            # Another engine (or a worker that never finished loading) is idle:
            # it would be unloaded before this load anyway, so free it now.
            log.info(
                "unloading idle engine to make room",
                extra={"engine": w.engine_id, "for_engine": d.id},
            )
            await self._drop_worker()
            reading = memory_svc.read()
            if reading.available_bytes >= needed:
                return "go"
        self._hold_for_memory(job, d.display_name, needed, reading)
        return "wait"

    def _hold_for_memory(
        self,
        job: dict[str, Any],
        engine_name: str,
        needed: int,
        reading: memory_svc.MemoryReading,
    ) -> None:
        first = not self._memory_wait or self._memory_wait["job_id"] != job["id"]
        self._memory_wait = {
            "job_id": job["id"],
            "engine_id": job["engine_id"],
            "needed_bytes": needed,
            "available_bytes": reading.available_bytes,
        }
        if first:
            log.info(
                "job waiting for memory",
                extra={
                    "job": job["id"],
                    "engine": job["engine_id"],
                    "needed_bytes": needed,
                    "available_bytes": reading.available_bytes,
                },
            )
        # Only stable text goes on the job, so it is written once, not every re-check.
        detail = (
            f"Waiting for memory: {engine_name} needs about "
            f"{memory_svc.format_gb(needed)} free. Close other apps or wait for them "
            "to finish; this starts on its own."
        )
        if job["stage_detail"] != detail:
            updated = repo.set_queued_detail(self._conn, job["id"], detail)
            if updated:
                self._publish(updated)

    async def _drop_worker_quietly(self) -> None:
        w, self._worker = self._worker, None
        if w:
            with contextlib.suppress(Exception):
                await w.kill()

    async def _drop_worker(self) -> None:
        if self._worker:
            await self._worker.shutdown()
            self._worker = None

    async def _execute(self, job: dict[str, Any]) -> None:
        if job["task"] in MODEL_TASKS:
            await self._execute_model_task(job)
            return
        job_id = job["id"]
        engine = self._registry.get(job["engine_id"])
        if engine is None or (
            engine.descriptor.requires_install
            and not ModelStore(self._conn).is_installed(engine.descriptor.id)
        ):
            self._fail(
                job_id,
                "engine_not_installed",
                "The engine for this job is not installed. Install it from Models, then retry.",
            )
            return
        try:
            self._transition(job_id, JobState.PREPARING, detail="Preparing")
        except InvalidTransition:
            return  # canceled between selection and start
        self._current_job = job_id
        self._cancel.clear()
        staging = self._paths.staging / job_id
        staging.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        metrics: dict[str, Any] = {}
        try:
            if repo.get(self._conn, job_id)["cancel_requested"]:
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
                return
            worker = await self._ensure_worker(engine, job_id, metrics)
            if worker is None:
                return
            if self._cancel.is_set():
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
                return
            self._transition(job_id, JobState.PROCESSING, detail="Generating")
            await worker.send(
                {
                    "op": "run",
                    "job_id": job_id,
                    "task": job["task"],
                    "inputs": job["inputs"],
                    "output_dir": str(staging),
                }
            )
            event = await self._await_terminal_event(worker, job_id)
            if event is None:
                return
            worker.last_used = time.monotonic()
            kind = event["event"]
            if kind == "canceled":
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
            elif kind == "error":
                self._fail(job_id, event.get("code", "engine_failed"), event.get("message"))
            elif kind == "result":
                metrics.update(event.get("metrics") or {})
                metrics["total_s"] = round(time.perf_counter() - started, 4)
                self._finalize(job, staging, event.get("outputs") or [], metrics)
        except WorkerGone as exc:
            self._worker = None
            # An engine that cannot even import in an outdated env dies before "ready".
            outdated = self._installer.software_update_available(engine)
            self._handle_crash(job_id, exc.returncode, outdated=outdated)
        except asyncio.CancelledError:
            self._mark_interrupted(job_id)
            raise
        except Exception:
            log.exception("job %s failed inside the orchestrator", job_id)
            self._fail(
                job_id,
                "internal_error",
                "Something went wrong inside Rasva. Your other work is safe.",
            )
            await self._drop_worker_quietly()
        finally:
            self._current_job = None
            shutil.rmtree(staging, ignore_errors=True)
            self._storage.invalidate()

    async def _execute_model_task(self, job: dict[str, Any]) -> None:
        """Install, update or remove an engine. Runs in-process: network and disk only."""
        job_id = job["id"]
        engine = self._registry.get(job["engine_id"])
        if engine is None:
            self._fail(job_id, "engine_unavailable", "That engine is no longer available.")
            return
        self._current_job = job_id
        self._cancel.clear()
        last_write = 0.0

        def transition(state: JobState, detail: str) -> None:
            self._transition(job_id, state, detail=detail)

        def progress(fraction: float | None, detail: str | None) -> None:
            nonlocal last_write
            now = time.monotonic()
            if now - last_write >= PROGRESS_WRITE_INTERVAL_S or fraction == 1:
                last_write = now
                self._publish(repo.update_progress(self._conn, job_id, fraction, detail))

        hooks = Hooks(transition=transition, progress=progress, cancel=self._cancel)
        try:
            if repo.get(self._conn, job_id)["cancel_requested"]:
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
                return
            if job["task"] == "model.install":
                metrics = await self._installer.install(engine, hooks)
            elif job["task"] == "model.update":
                # A resident worker would keep running the old environment.
                if self._worker and self._worker.engine_id == engine.descriptor.id:
                    await self._drop_worker()
                metrics = await self._installer.update(engine, hooks)
            elif job["task"] == "model.remove":
                self._transition(job_id, JobState.PREPARING, detail="Stopping the engine")
                if self._worker and self._worker.engine_id == engine.descriptor.id:
                    await self._drop_worker()
                metrics = await self._installer.remove(engine, hooks)
            else:
                raise InstallError("unsupported_task", "Rasva does not know this engine task.")
            self._transition(job_id, JobState.COMPLETED, detail="Done", metrics=metrics)
        except InstallCanceled:
            with contextlib.suppress(InvalidTransition):
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
        except InstallError as exc:
            self._fail(job_id, exc.code, exc.user_message)
        except asyncio.CancelledError:
            self._mark_interrupted(job_id)
            raise
        except Exception:
            log.exception("model task %s failed", job_id)
            self._fail(
                job_id,
                "internal_error",
                "Something went wrong inside Rasva. Your other work is safe.",
            )
        finally:
            self._current_job = None
            self._storage.invalidate()

    async def _ensure_worker(
        self, engine: RegisteredEngine, job_id: str, metrics: dict[str, Any]
    ) -> Worker | None:
        w = self._worker
        if w and (not w.alive or w.engine_id != engine.descriptor.id):
            # Unload the previous engine before loading another (PRD §23, §24).
            await self._drop_worker()
            w = None
        if w is None:
            if self._installer.software_update_available(engine):
                # Policy (ADR-0003): run on the old environment; Models offers the update.
                log.warning("engine %s is running on an outdated environment", engine.descriptor.id)
            w = await self._spawn(engine)
            self._worker = w
        if not w.loaded:
            self._transition(job_id, JobState.LOADING_MODEL, detail="Loading engine")
            await w.send({"op": "load"})
            event = await self._await_terminal_event(w, job_id, accept={"loaded", "error"})
            if event is None:
                return None
            if event["event"] == "error":
                message = event.get("message")
                if self._installer.software_update_available(engine):
                    message = f"{message} {MSG_SOFTWARE_UPDATE}" if message else MSG_SOFTWARE_UPDATE
                self._fail(job_id, event.get("code", "load_failed"), message)
                await self._drop_worker()
                return None
            w.loaded = True
            metrics["load_s"] = event.get("load_s")
            metrics["cold_start"] = True
        else:
            metrics["cold_start"] = False
        return w

    async def _spawn(self, engine: RegisteredEngine) -> Worker:
        d = engine.descriptor
        model_dir = self._paths.models / d.modality / d.id
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TORCH_"))}
        env.update(self._paths.worker_env())
        backend_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = backend_root
        env["PYTHONUNBUFFERED"] = "1"
        log_dir = self._paths.logs / "engines"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{d.id}.log"
        rotate_if_large(log_path)
        with log_path.open("ab") as stderr:
            proc = await asyncio.create_subprocess_exec(
                engine.python_executable(self._paths.envs),
                "-m",
                "rasva.engines.worker",
                "--engine",
                engine.import_path,
                "--model-dir",
                str(model_dir),
                "--cache-dir",
                str(self._paths.cache),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                env=env,
                cwd=str(self._paths.tmp),
            )
        worker = Worker(engine, proc)
        try:
            ready = await asyncio.wait_for(worker.next_event(), READY_TIMEOUT_S)
        except TimeoutError:
            await worker.kill()
            raise WorkerGone(None) from None
        if ready.get("event") != "ready":
            await worker.kill()
            raise WorkerGone(proc.returncode)
        log.info("engine worker started", extra={"engine": d.id, "pid": proc.pid})
        return worker

    async def _await_terminal_event(
        self,
        worker: Worker,
        job_id: str,
        accept: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """Pump worker events, applying progress, until a terminal event.

        Returns None if the job was canceled by killing the worker.
        """
        accept = accept or {"result", "error", "canceled"}
        last_write = 0.0
        cancel_sent_at: float | None = None
        while True:
            if self._cancel.is_set() and cancel_sent_at is None:
                cancel_sent_at = time.monotonic()
                with contextlib.suppress(WorkerGone):
                    await worker.send({"op": "cancel"})
            timeout = None
            if cancel_sent_at is not None:
                timeout = max(
                    0.0, self._settings.cancel_grace_s - (time.monotonic() - cancel_sent_at)
                )
            get = asyncio.ensure_future(worker.next_event())
            waiters: set[asyncio.Future[Any]] = {get}
            cancel_wait = None
            if cancel_sent_at is None:
                cancel_wait = asyncio.ensure_future(self._cancel.wait())
                waiters.add(cancel_wait)
            done, _ = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait and not cancel_wait.done():
                cancel_wait.cancel()
            if get not in done:
                get.cancel()
                with contextlib.suppress(asyncio.CancelledError, WorkerGone):
                    await get
                if cancel_sent_at is not None and timeout is not None and not done:
                    # Engine ignored cancel within the grace period: kill it.
                    log.warning("engine %s ignored cancel; killing worker", worker.engine_id)
                    await worker.kill()
                    self._worker = None
                    self._transition(job_id, JobState.CANCELED, detail="Canceled (engine stopped)")
                    return None
                continue
            event = get.result()  # may raise WorkerGone
            kind = event.get("event")
            if kind == "progress":
                now = time.monotonic()
                fraction = event.get("fraction")
                if now - last_write >= PROGRESS_WRITE_INTERVAL_S or fraction == 1:
                    last_write = now
                    job = repo.update_progress(self._conn, job_id, fraction, event.get("detail"))
                    self._publish(job)
                continue
            if kind == "canceled" and "canceled" not in accept:
                self._transition(job_id, JobState.CANCELED, detail="Canceled")
                return None
            if kind in accept:
                return event
            log.debug("ignoring worker event %s", kind)

    def _finalize(
        self,
        job: dict[str, Any],
        staging: Path,
        outputs: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> None:
        job_id = job["id"]
        self._transition(job_id, JobState.FINALIZING, detail="Saving results")
        try:
            if not outputs:
                raise asset_svc.AssetError("The engine finished without producing a file.")
            saved = [
                asset_svc.promote_output(
                    self._conn,
                    self._paths,
                    project_id=job["project_id"],
                    job_id=job_id,
                    staging_dir=staging,
                    filename=o.get("filename", ""),
                    media_type=o.get("media_type", "application/octet-stream"),
                    metadata=o.get("metadata") or {},
                )
                for o in outputs
            ]
        except asset_svc.AssetError as exc:
            log.error("finalize failed for %s: %s", job_id, exc)
            self._fail(job_id, "invalid_output", "The engine produced an unusable result.")
            return
        project_svc.touch(self._conn, job["project_id"])
        self._transition(
            job_id,
            JobState.COMPLETED,
            detail="Done",
            outputs=[a["id"] for a in saved],
            metrics=metrics,
        )

    # ---- helpers ----

    def _publish(self, job: dict[str, Any]) -> None:
        self._bus.publish({"type": "job", "job": job})

    def _transition(
        self, job_id: str, target: JobState, detail: str | None = None, **fields: Any
    ) -> None:
        if "stage_detail" not in fields:
            fields["stage_detail"] = None if target in TERMINAL else detail
        job = repo.transition(self._conn, job_id, target, detail=detail, **fields)
        self._publish(job)

    def _fail(self, job_id: str, code: str, message: str | None) -> None:
        with contextlib.suppress(InvalidTransition, repo.JobNotFound):
            self._transition(
                job_id,
                JobState.FAILED,
                detail=code,
                error_code=code,
                error_message=message or "The job failed.",
            )

    def _mark_interrupted(self, job_id: str) -> None:
        with contextlib.suppress(InvalidTransition, repo.JobNotFound, sqlite3.Error):
            self._transition(
                job_id,
                JobState.INTERRUPTED,
                detail="Rasva stopped",
                error_code="interrupted",
                error_message="Rasva Studio stopped while this was running. You can retry it.",
            )

    def _handle_crash(self, job_id: str, returncode: int | None, *, outdated: bool = False) -> None:
        log.error(
            "engine worker exited unexpectedly", extra={"job": job_id, "returncode": returncode}
        )
        killed = returncode is not None and returncode == -signal.SIGKILL
        message = MSG_MEMORY_KILL if killed else MSG_CRASH
        if outdated and not killed:
            message = f"{message} {MSG_SOFTWARE_UPDATE}"
        self._fail(job_id, "out_of_memory" if killed else "engine_crashed", message)
