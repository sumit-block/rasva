import hashlib
import os
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from rasva.config import GB
from rasva.db import _split_sql, connect, current_version, migrate, utcnow
from rasva.engines.contract import ModelArtifact
from tests.conftest import make_client, wait_for
from tests.install_engines import make_engine

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
BIG = os.urandom(3 * 1024 * 1024 + 123)
SMALL = os.urandom(2048)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class FileServer:
    """Local HTTP server with Range support and fault injection."""

    def __init__(self) -> None:
        self.files = {"/big.bin": BIG, "/small.bin": SMALL}
        self.requests: list[tuple[str, str | None]] = []
        self.truncate_next = 0  # number of upcoming responses to cut short
        self.ignore_range = False
        self.corrupt = False
        self.delay_per_chunk = 0.0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                rng = self.headers.get("Range")
                server.requests.append((self.path, rng))
                if self.path == "/redirect-insecure":
                    self.send_response(302)
                    self.send_header("Location", "http://evil.example/big.bin")
                    self.end_headers()
                    return
                body = server.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                if server.corrupt:
                    body = bytes(len(body))
                start = 0
                if rng and not server.ignore_range:
                    start = int(rng.removeprefix("bytes=").split("-")[0])
                    if start >= len(body):
                        self.send_response(416)
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                payload = body[start:]
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if server.truncate_next:
                    server.truncate_next -= 1
                    self.wfile.write(payload[: len(payload) // 3])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                step = 256 * 1024
                for i in range(0, len(payload), step):
                    if server.delay_per_chunk:
                        time.sleep(server.delay_per_chunk)
                    try:
                        self.wfile.write(payload[i : i + step])
                    except (BrokenPipeError, ConnectionResetError):
                        return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def bytes_served(self, path: str) -> int:
        return len([r for r in self.requests if r[0] == path])


@pytest.fixture
def server():
    s = FileServer()
    yield s
    s.httpd.shutdown()


@pytest.fixture
def isettings(settings):
    from dataclasses import replace

    return replace(settings, allow_loopback_http_downloads=True, download_backoff_s=0.01)


def artifacts(server, big_path="/big.bin"):
    return [
        ModelArtifact(
            path="weights/model.bin",
            url=server.url + big_path,
            sha256=sha(BIG),
            size_bytes=len(BIG),
        ),
        ModelArtifact(
            path="voices/a.bin",
            url=server.url + "/small.bin",
            sha256=sha(SMALL),
            size_bytes=len(SMALL),
        ),
    ]


def client_with(settings, *engine_paths):
    c = make_client(settings, extra_engines=engine_paths)
    c.__enter__()
    return c


def install(c, engine_id, **body):
    return c.post(f"/api/v1/models/{engine_id}/install", json=body or None)


def model(c, engine_id):
    return c.get(f"/api/v1/models/{engine_id}").json()


def model_dir(settings, engine_id):
    return settings.paths.models / "audio" / engine_id


# ----------------------------------------------------------------- happy path


def test_install_then_remove(isettings, server):
    path = make_engine("Happy", artifacts(server))
    c = client_with(isettings, path)
    try:
        assert model(c, "test-happy")["install_state"] == "not_installed"
        assert c.post(
            "/api/v1/jobs", json={"project_id": "x", "task": "test.installed"}
        ).status_code in (404, 422)

        r = install(c, "test-happy")
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["task"] == "model.install" and job["project_id"] is None
        # Duplicate request returns the same job.
        assert install(c, "test-happy").json()["id"] == job["id"]

        done = wait_for(c, job["id"], TERMINAL)
        assert done["state"] == "completed", done
        assert [e["state"] for e in done["events"]] == [
            "queued",
            "downloading",
            "preparing",
            "processing",
            "finalizing",
            "completed",
        ]
        assert done["metrics"]["downloaded_bytes"] == len(BIG) + len(SMALL)
        d = model_dir(isettings, "test-happy")
        assert (d / "weights" / "model.bin").read_bytes() == BIG
        assert (d / "voices" / "a.bin").read_bytes() == SMALL
        assert not list(isettings.paths.downloads.iterdir())  # no leftover .part files

        info = model(c, "test-happy")
        assert info["install_state"] == "installed"
        assert info["installed_bytes"] >= len(BIG)
        assert info["required_bytes"] == 0
        assert install(c, "test-happy").status_code == 409  # already installed

        removal = c.delete("/api/v1/models/test-happy")
        assert removal.status_code == 202
        gone = wait_for(c, removal.json()["id"], TERMINAL)
        assert gone["state"] == "completed" and gone["metrics"]["freed_bytes"] >= len(BIG)
        assert not d.exists()
        assert model(c, "test-happy")["install_state"] == "not_installed"
        assert c.delete("/api/v1/models/test-happy").status_code == 409
    finally:
        c.__exit__(None, None, None)


def test_reinstall_skips_verified_files(isettings, server):
    path = make_engine("Skip", artifacts(server))
    c = client_with(isettings, path)
    try:
        d = model_dir(isettings, "test-skip")
        (d / "weights").mkdir(parents=True)
        (d / "weights" / "model.bin").write_bytes(BIG)  # already present and valid
        assert model(c, "test-skip")["required_bytes"] == len(SMALL)
        done = wait_for(c, install(c, "test-skip").json()["id"], TERMINAL)
        assert done["state"] == "completed"
        assert server.bytes_served("/big.bin") == 0
    finally:
        c.__exit__(None, None, None)


# ----------------------------------------------------------- resilience


def test_resumes_after_dropped_connection(isettings, server):
    server.truncate_next = 1
    path = make_engine("Resume", artifacts(server))
    c = client_with(isettings, path)
    try:
        done = wait_for(c, install(c, "test-resume").json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        big_requests = [r for r in server.requests if r[0] == "/big.bin"]
        assert big_requests[0][1] is None
        assert big_requests[1][1] is not None and big_requests[1][1] != "bytes=0-"
        assert (model_dir(isettings, "test-resume") / "weights" / "model.bin").read_bytes() == BIG
    finally:
        c.__exit__(None, None, None)


def test_restarts_when_server_ignores_range(isettings, server):
    server.truncate_next = 1
    server.ignore_range = True
    path = make_engine("NoRange", artifacts(server))
    c = client_with(isettings, path)
    try:
        done = wait_for(c, install(c, "test-norange").json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        assert (model_dir(isettings, "test-norange") / "weights" / "model.bin").read_bytes() == BIG
    finally:
        c.__exit__(None, None, None)


def test_checksum_mismatch_installs_nothing(isettings, server):
    server.corrupt = True
    path = make_engine("Corrupt", artifacts(server))
    c = client_with(isettings, path)
    try:
        done = wait_for(c, install(c, "test-corrupt").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "checksum_mismatch"
        assert not (model_dir(isettings, "test-corrupt") / "weights" / "model.bin").exists()
        assert not list(isettings.paths.downloads.iterdir())
        info = model(c, "test-corrupt")
        assert info["install_state"] == "failed" and info["install_error"] == "checksum_mismatch"
    finally:
        c.__exit__(None, None, None)


def test_http_error_is_not_retried(isettings, server):
    arts = artifacts(server, big_path="/missing.bin")
    c = client_with(isettings, make_engine("Missing", arts))
    try:
        done = wait_for(c, install(c, "test-missing").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "download_failed"
        assert server.bytes_served("/missing.bin") == 1
    finally:
        c.__exit__(None, None, None)


def test_insecure_redirect_blocked(isettings, server):
    arts = artifacts(server, big_path="/redirect-insecure")
    c = client_with(isettings, make_engine("Redirect", arts))
    try:
        done = wait_for(c, install(c, "test-redirect").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "insecure_source"
    finally:
        c.__exit__(None, None, None)


def test_http_source_refused_outside_tests(settings, server):
    c = client_with(settings, make_engine("Plain", artifacts(server)))
    try:
        r = install(c, "test-plain")
        assert r.status_code == 409 and r.json()["error"]["code"] == "insecure_source"
    finally:
        c.__exit__(None, None, None)


# ----------------------------------------------------------- cancel / gating


def test_cancel_keeps_partial_and_resume_continues(isettings, server):
    server.delay_per_chunk = 0.05
    c = client_with(isettings, make_engine("Cancel", artifacts(server)))
    try:
        job = install(c, "test-cancel").json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            j = c.get(f"/api/v1/jobs/{job['id']}").json()
            if j["state"] == "downloading" and (j["progress"] or 0) > 0.1:
                break
            time.sleep(0.02)
        c.post(f"/api/v1/jobs/{job['id']}/cancel")
        assert wait_for(c, job["id"], TERMINAL)["state"] == "canceled"
        parts = list(isettings.paths.downloads.iterdir())
        assert len(parts) == 1 and 0 < parts[0].stat().st_size < len(BIG)
        assert model(c, "test-cancel")["install_state"] == "not_installed"

        server.delay_per_chunk = 0
        retry = c.post(f"/api/v1/jobs/{job['id']}/retry").json()
        assert wait_for(c, retry["id"], TERMINAL)["state"] == "completed"
        assert server.requests[-2][1] is not None  # resumed with a Range header
    finally:
        c.__exit__(None, None, None)


def test_pause_shows_downloaded_bytes_and_can_be_discarded(isettings, server):
    """Pause = cancel during download: parts are kept and reported, so the Models
    page can offer Resume; Discard deletes them, and only when asked."""
    server.delay_per_chunk = 0.05
    c = client_with(isettings, make_engine("Pause", artifacts(server)))
    try:
        assert model(c, "test-pause")["downloaded_bytes"] == 0
        job = install(c, "test-pause").json()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            j = c.get(f"/api/v1/jobs/{job['id']}").json()
            if j["state"] == "downloading" and (j["progress"] or 0) > 0.1:
                break
            time.sleep(0.02)
        refused = c.delete("/api/v1/models/test-pause/download")
        assert refused.status_code == 409  # not while it is downloading
        c.post(f"/api/v1/jobs/{job['id']}/cancel")
        assert wait_for(c, job["id"], TERMINAL)["state"] == "canceled"
        info = model(c, "test-pause")
        assert info["install_state"] == "not_installed"
        assert 0 < info["downloaded_bytes"] < info["download_bytes"]

        r = c.delete("/api/v1/models/test-pause/download")
        assert r.status_code == 200 and r.json()["freed_bytes"] >= info["downloaded_bytes"]
        assert model(c, "test-pause")["downloaded_bytes"] == 0
        assert not list(isettings.paths.downloads.iterdir())
    finally:
        c.__exit__(None, None, None)


def test_discard_refused_for_installed_engine(isettings, server):
    c = client_with(isettings, make_engine("Kept", artifacts(server)))
    try:
        job = install(c, "test-kept").json()
        assert wait_for(c, job["id"], TERMINAL)["state"] == "completed"
        assert model(c, "test-kept")["downloaded_bytes"] == 0  # only for paused downloads
        r = c.delete("/api/v1/models/test-kept/download")
        assert r.status_code == 409
        assert model_dir(isettings, "test-kept").exists()
    finally:
        c.__exit__(None, None, None)


def test_restricted_licence_needs_confirmation(isettings, server):
    c = client_with(
        isettings, make_engine("Restricted", artifacts(server), license_class="restricted")
    )
    try:
        assert model(c, "test-restricted")["needs_license_confirmation"] is True
        r = install(c, "test-restricted")
        assert r.status_code == 409 and r.json()["error"]["code"] == "license_confirmation_required"
        r = install(c, "test-restricted", accept_license=True)
        assert r.status_code == 202
        wait_for(c, r.json()["id"], TERMINAL)
    finally:
        c.__exit__(None, None, None)


def test_storage_preflight_blocks_before_queueing(isettings, server):
    c = client_with(isettings, make_engine("Full", artifacts(server)))
    try:
        c.patch("/api/v1/settings", json={"storage.os_reserve_bytes": 500 * GB})
        r = install(c, "test-full")
        assert r.status_code == 507 and r.json()["error"]["code"] == "storage_full"
        assert "macOS" in r.json()["error"]["message"]
        assert server.requests == []
        assert c.get("/api/v1/jobs").json() == []
    finally:
        c.__exit__(None, None, None)


def test_failed_self_check(isettings, server):
    c = client_with(isettings, make_engine("BadVerify", artifacts(server), verify_ok=False))
    try:
        done = wait_for(c, install(c, "test-badverify").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "verify_failed"
    finally:
        c.__exit__(None, None, None)


def test_remove_refused_while_engine_has_jobs(isettings, server):
    c = client_with(isettings, make_engine("Busy", artifacts(server)))
    try:
        wait_for(c, install(c, "test-busy").json()["id"], TERMINAL)
        project = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        blocker = c.post(
            "/api/v1/jobs",
            json={
                "project_id": project["id"],
                "task": "test.run",
                "engine_id": "test-fault",
                "inputs": {"behaviour": "slow", "seconds": 3},
            },
        ).json()
        queued = c.post(
            "/api/v1/jobs", json={"project_id": project["id"], "task": "test.installed"}
        )
        assert queued.status_code == 201, queued.text
        r = c.delete("/api/v1/models/test-busy")
        assert r.status_code == 409 and r.json()["error"]["code"] == "engine_in_use"
        for j in (queued.json(), blocker):
            c.post(f"/api/v1/jobs/{j['id']}/cancel")
            wait_for(c, j["id"], TERMINAL)
    finally:
        c.__exit__(None, None, None)


def test_model_tasks_not_accepted_through_jobs_api(client, project):
    r = client.post(
        "/api/v1/jobs",
        json={
            "project_id": project["id"],
            "task": "model.install",
            "engine_id": "diagnostic-tone",
        },
    )
    assert r.status_code == 422


# ----------------------------------------------------------- environments


@pytest.fixture
def fake_uv(tmp_path):
    script = tmp_path / "fake-uv"
    script.write_text(f"""#!{sys.executable}
import os, sys, pathlib
args = sys.argv[1:]
if args[0] == "venv":
    target = pathlib.Path(args[-1])
    (target / "bin").mkdir(parents=True)
    # A real interpreter shim, so a worker can be started from this env.
    shim = "#!/bin/sh" + chr(10) + 'exec {sys.executable} "$@"' + chr(10)
    (target / "bin" / "python").write_text(shim)
    (target / "bin" / "python").chmod(0o755)
elif args[:2] == ["pip", "install"]:
    assert "--require-hashes" in args
    assert args[args.index("--only-binary") + 1] == ":all:"
    if os.environ.get("FAKE_UV_SLOW"):
        import time
        time.sleep(float(os.environ["FAKE_UV_SLOW"]))
    sys.exit(1 if os.environ.get("FAKE_UV_FAIL") else 0)
""")
    script.chmod(0o755)
    return str(script)


def test_builds_isolated_environment(isettings, server, fake_uv):
    from dataclasses import replace

    s = replace(isettings, uv_executable=fake_uv)
    c = client_with(
        s, make_engine("WithEnv", artifacts(server), requirements_lock="data/engine-lock.txt")
    )
    try:
        done = wait_for(c, install(c, "test-withenv").json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        env = s.paths.env_dir("test-withenv")
        assert (env / "bin" / "python").exists() and (env / ".rasva-lock-sha256").exists()
        assert not list(s.paths.envs.glob(".*.partial"))
        removal = c.delete("/api/v1/models/test-withenv").json()
        wait_for(c, removal["id"], TERMINAL)
        assert not env.exists()
    finally:
        c.__exit__(None, None, None)


def test_failed_environment_leaves_nothing_half_built(isettings, server, fake_uv, monkeypatch):
    from dataclasses import replace

    monkeypatch.setenv("FAKE_UV_FAIL", "1")
    s = replace(isettings, uv_executable=fake_uv)
    c = client_with(
        s, make_engine("EnvFail", artifacts(server), requirements_lock="data/engine-lock.txt")
    )
    try:
        done = wait_for(c, install(c, "test-envfail").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "environment_failed"
        assert not s.paths.env_dir("test-envfail").exists()
        assert not list(s.paths.envs.glob(".*.partial"))
        assert (s.paths.logs / "engines" / "test-envfail-install.log").exists()
    finally:
        c.__exit__(None, None, None)


# ----------------------------------------------------------- software updates


def _env_engine(isettings, server, fake_uv, name):
    from dataclasses import replace

    s = replace(isettings, uv_executable=fake_uv)
    c = client_with(
        s, make_engine(name, artifacts(server), requirements_lock="data/engine-lock.txt")
    )
    engine_id = f"test-{name.lower()}"
    done = wait_for(c, install(c, engine_id).json()["id"], TERMINAL)
    assert done["state"] == "completed", done
    return s, c, engine_id


def _make_outdated(s, engine_id):
    """Pretend the env was built from an older lockfile, and mark it so we can tell."""
    env = s.paths.env_dir(engine_id)
    (env / ".rasva-lock-sha256").write_text("0" * 64)
    (env / "old-env-sentinel").write_text("old")
    return env


def _no_leftovers(s):
    return not list(s.paths.envs.glob(".*.partial")) and not list(s.paths.envs.glob(".*.previous"))


def update(c, engine_id):
    return c.post(f"/api/v1/models/{engine_id}/update")


def test_update_rebuilds_environment_without_downloading(isettings, server, fake_uv):
    s, c, eid = _env_engine(isettings, server, fake_uv, "Upd")
    try:
        assert model(c, eid)["software_update_available"] is False
        assert update(c, eid).json()["error"]["code"] == "up_to_date"
        env = _make_outdated(s, eid)
        assert model(c, eid)["software_update_available"] is True

        weights = model_dir(s, eid) / "weights" / "model.bin"
        before = weights.stat()
        requests_before = list(server.requests)

        r = update(c, eid)
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["task"] == "model.update" and job["project_id"] is None
        assert update(c, eid).json()["id"] == job["id"]  # idempotent while open
        # While updating, install and remove are refused rather than racing it.
        assert install(c, eid).status_code == 409
        assert c.delete(f"/api/v1/models/{eid}").status_code == 409

        done = wait_for(c, job["id"], TERMINAL)
        assert done["state"] == "completed", done
        assert [e["state"] for e in done["events"]] == [
            "queued", "preparing", "processing", "finalizing", "completed",
        ]  # fmt: skip

        # No model file was fetched, rewritten or deleted.
        assert server.requests == requests_before
        after = weights.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert weights.read_bytes() == BIG

        # The new env is in place; the old one is gone; nothing is left behind.
        lock = sha((Path(__file__).parent / "data" / "engine-lock.txt").read_bytes())
        assert (env / ".rasva-lock-sha256").read_text() == lock
        assert not (env / "old-env-sentinel").exists()
        assert _no_leftovers(s)
        info = model(c, eid)
        assert info["install_state"] == "installed"
        assert info["software_update_available"] is False
        assert info["installed_bytes"] >= len(BIG)
    finally:
        c.__exit__(None, None, None)


def test_failed_update_keeps_old_environment(isettings, server, fake_uv, monkeypatch):
    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdFail")
    try:
        env = _make_outdated(s, eid)
        monkeypatch.setenv("FAKE_UV_FAIL", "1")
        done = wait_for(c, update(c, eid).json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "environment_failed"
        assert "previous version was kept" in done["error_message"]
        assert (env / "old-env-sentinel").read_text() == "old"
        assert (env / "bin" / "python").exists()
        assert _no_leftovers(s)
        info = model(c, eid)
        assert info["install_state"] == "installed" and info["software_update_available"] is True

        monkeypatch.delenv("FAKE_UV_FAIL")
        retry = c.post(f"/api/v1/jobs/{done['id']}/retry")
        assert retry.status_code == 201 and retry.json()["task"] == "model.update"
        assert wait_for(c, retry.json()["id"], TERMINAL)["state"] == "completed"
        assert model(c, eid)["software_update_available"] is False
    finally:
        c.__exit__(None, None, None)


def test_update_failing_self_check_rolls_back(isettings, server, fake_uv):
    import tests.install_engines as engines

    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdVerify")
    try:
        env = _make_outdated(s, eid)
        engines.UpdVerify.verify = lambda self, ctx: False
        done = wait_for(c, update(c, eid).json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "verify_failed"
        assert (env / "old-env-sentinel").read_text() == "old"
        assert (env / ".rasva-lock-sha256").read_text() == "0" * 64
        assert _no_leftovers(s)
        assert model(c, eid)["install_state"] == "installed"
    finally:
        c.__exit__(None, None, None)


def test_canceled_update_keeps_old_environment(isettings, server, fake_uv, monkeypatch):
    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdCancel")
    try:
        env = _make_outdated(s, eid)
        monkeypatch.setenv("FAKE_UV_SLOW", "5")
        job = update(c, eid).json()
        wait_for(c, job["id"], {"preparing"})
        c.post(f"/api/v1/jobs/{job['id']}/cancel")
        assert wait_for(c, job["id"], TERMINAL)["state"] == "canceled"
        assert (env / "old-env-sentinel").read_text() == "old"
        assert _no_leftovers(s)
        info = model(c, eid)
        assert info["install_state"] == "installed" and info["software_update_available"] is True
    finally:
        c.__exit__(None, None, None)


def test_update_refused_while_engine_busy_or_not_installed(isettings, server, fake_uv):
    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdBusy")
    try:
        _make_outdated(s, eid)
        project = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        blocker = c.post(
            "/api/v1/jobs",
            json={
                "project_id": project["id"],
                "task": "test.run",
                "engine_id": "test-fault",
                "inputs": {"behaviour": "slow", "seconds": 3},
            },
        ).json()
        queued = c.post(
            "/api/v1/jobs",
            json={"project_id": project["id"], "task": "test.installed", "engine_id": eid},
        )
        assert queued.status_code == 201, queued.text
        r = update(c, eid)
        assert r.status_code == 409 and r.json()["error"]["code"] == "engine_in_use"
        for j in (queued.json(), blocker):
            c.post(f"/api/v1/jobs/{j['id']}/cancel")
            wait_for(c, j["id"], TERMINAL)

        assert update(c, "diagnostic-tone").json()["error"]["code"] == "built_in"
        assert update(c, "nope").status_code == 404
    finally:
        c.__exit__(None, None, None)

    c2 = client_with(
        s, make_engine("UpdNone", artifacts(server), requirements_lock="data/engine-lock.txt")
    )
    try:
        assert model(c2, "test-updnone")["software_update_available"] is False
        r = update(c2, "test-updnone")
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_installed"
    finally:
        c2.__exit__(None, None, None)


def test_jobs_on_outdated_engine_still_run_and_failures_point_to_update(isettings, server, fake_uv):
    # Policy (ADR-0003): an outdated engine is not blocked. These test engines are
    # defined at runtime, so a worker cannot import them and dies before "ready",
    # the same way an engine fails when its outdated env cannot import it.
    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdRun")
    try:
        project = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()

        def run_job() -> dict:
            r = c.post(
                "/api/v1/jobs",
                json={"project_id": project["id"], "task": "test.installed", "engine_id": eid},
            )
            assert r.status_code == 201, r.text
            return wait_for(c, r.json()["id"], TERMINAL, timeout=60)

        current = run_job()
        assert current["state"] == "failed" and current["error_code"] == "engine_crashed"
        assert "Open Models" not in current["error_message"]

        _make_outdated(s, eid)
        outdated = run_job()
        assert outdated["state"] == "failed" and outdated["error_code"] == "engine_crashed"
        assert "software update is available" in outdated["error_message"]
        assert "Open Models and choose Update" in outdated["error_message"]
    finally:
        c.__exit__(None, None, None)


def test_restart_restores_environment_after_interrupted_swap(isettings, server, fake_uv):
    s, c, eid = _env_engine(isettings, server, fake_uv, "UpdCrash")
    c.__exit__(None, None, None)
    env = s.paths.env_dir(eid)
    backup = s.paths.envs / f".{eid}.previous"
    os.replace(env, backup)  # crash after moving the old env aside, before the new one

    with make_client(s, extra_engines=("tests.install_engines:UpdCrash",)) as c2:
        assert (env / "bin" / "python").exists()
        assert not backup.exists()
        assert c2.get(f"/api/v1/models/{eid}").json()["install_state"] == "installed"


# ----------------------------------------------------------- recovery / schema


def test_restart_recovers_install_state_and_cleans_orphans(isettings, server):
    path = make_engine("Recover", artifacts(server))
    c = client_with(isettings, path)
    c.__exit__(None, None, None)
    conn = sqlite3.connect(isettings.paths.db_file)
    now = utcnow()
    conn.execute(
        "INSERT INTO models(id, engine_id, source, revision, license, license_class,"
        " install_state, created_at, updated_at) VALUES ('test-recover', 'test-recover',"
        " 's', '1', 'l', 'permissive', 'installing', ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()
    wanted = isettings.paths.downloads / f"{sha(BIG)}.part"
    wanted.write_bytes(BIG[:1000])
    orphan = isettings.paths.downloads / f"{'0' * 64}.part"
    orphan.write_bytes(b"stale")
    stale_env = isettings.paths.envs / ".old.partial"
    stale_env.mkdir()

    with make_client(isettings, extra_engines=(path,)) as c2:
        info = c2.get("/api/v1/models/test-recover").json()
        assert info["install_state"] == "failed" and info["install_error"] == "interrupted"
    assert wanted.exists()
    assert not orphan.exists()
    assert not stale_env.exists()


def test_artifact_paths_cannot_escape():
    for bad in ("../x", "a/../../x", "/abs", "a//b", "."):
        with pytest.raises(ValidationError):
            ModelArtifact(path=bad, url="https://x/y", sha256="0" * 64, size_bytes=1)


def test_migration_from_v1_preserves_jobs_and_events(tmp_path):
    from importlib import resources

    conn = connect(tmp_path / "db.sqlite3")
    current_version(conn)
    sql = (resources.files("rasva.migrations") / "0001_initial.sql").read_text()
    for stmt in _split_sql(sql):
        conn.execute(stmt)
    now = utcnow()
    conn.execute("INSERT INTO schema_migrations VALUES (1, '0001_initial.sql', ?)", (now,))
    conn.execute(
        "INSERT INTO projects(id, name, module, created_at, updated_at)"
        " VALUES ('p', 'P', 'audio', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO jobs(id, project_id, task, engine_id, state, created_at, updated_at)"
        " VALUES ('j', 'p', 'diagnostic.tone', 'diagnostic-tone', 'completed', ?, ?)",
        (now, now),
    )
    conn.execute("INSERT INTO job_events(job_id, state, at) VALUES ('j', 'completed', ?)", (now,))

    assert migrate(conn, tmp_path / "backups") >= 2
    assert conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
    assert conn.execute("SELECT project_id FROM jobs").fetchone()[0] == "p"
    assert any((tmp_path / "backups").iterdir())  # backed up before migrating
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    # Only system tasks may omit a project.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs(id, task, engine_id, state, created_at, updated_at)"
            " VALUES ('k', 'diagnostic.tone', 'e', 'queued', ?, ?)",
            (now, now),
        )
    conn.execute(
        "INSERT INTO jobs(id, task, engine_id, state, created_at, updated_at)"
        " VALUES ('m', 'model.install', 'e', 'queued', ?, ?)",
        (now, now),
    )


# ----------------------------------------------------------------- prepare step


def test_prepare_runs_once_in_its_own_process(isettings, server):
    from tests.install_engines import Preparing

    c = client_with(isettings, make_engine("Prep", artifacts(server), base=Preparing))
    try:
        done = wait_for(c, install(c, "test-prep").json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        assert "prepare_s" in done["metrics"]
        d = model_dir(isettings, "test-prep")
        assert (d / "prepared.txt").read_text() == BIG[:4].hex()
        assert (d / "prepare-runs.txt").read_text() == "1"
        assert not (d / ".prepared.tmp").exists()
        log = (isettings.paths.logs / "engines" / "test-prep-install.log").read_text()
        assert "rasva.engines.prepare" in log and "tests.install_engines:Preparing" in log
    finally:
        c.__exit__(None, None, None)


def test_failed_prepare_keeps_downloads_and_reinstall_skips_them(isettings, server):
    from tests.install_engines import FailingPrepare

    c = client_with(isettings, make_engine("PrepFail", artifacts(server), base=FailingPrepare))
    try:
        done = wait_for(c, install(c, "test-prepfail").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "prepare_failed"
        assert "downloaded files are kept" in done["error_message"]
        d = model_dir(isettings, "test-prepfail")
        assert (d / "weights" / "model.bin").read_bytes() == BIG  # not deleted
        again = wait_for(c, install(c, "test-prepfail").json()["id"], TERMINAL)
        assert again["error_code"] == "prepare_failed"
        assert (
            again["metrics"].get("downloaded_bytes", 0) == 0
            or "downloaded_bytes" not in again["metrics"]
        )
    finally:
        c.__exit__(None, None, None)


def test_prepare_cannot_use_the_network(isettings, server):
    from tests.install_engines import NetworkPrepare

    c = client_with(isettings, make_engine("PrepNet", artifacts(server), base=NetworkPrepare))
    try:
        done = wait_for(c, install(c, "test-prepnet").json()["id"], TERMINAL)
        assert done["state"] == "failed" and done["error_code"] == "prepare_failed"
        log = (isettings.paths.logs / "engines" / "test-prepnet-install.log").read_text()
        assert "blocked a network connection while preparing" in log
    finally:
        c.__exit__(None, None, None)


# ---- download speed and time left ----


def test_download_speed_over_a_window():
    from rasva.services.installer import DownloadSpeed

    s = DownloadSpeed()
    assert s.add(0.0, 0) is None  # too short to tell
    assert s.add(1.0, 5 * 1024**2) is None
    assert s.add(2.0, 10 * 1024**2) == 5 * 1024**2  # 5 MB/s
    for t in range(3, 30):
        rate = s.add(float(t), 10 * 1024**2 + (t - 2) * 1024**2)  # slows to 1 MB/s
    assert rate == pytest.approx(1024**2, rel=0.01)  # old samples have left the window


def test_resumed_bytes_do_not_count_as_speed():
    from rasva.services.installer import DownloadSpeed

    s = DownloadSpeed()
    s.add(0.0, 0)
    s.restart()  # next file, resumed at 1.5 GB
    s.add(10.0, 1536 * 1024**2)
    rate = s.add(13.0, 1536 * 1024**2 + 12 * 1024**2)
    assert rate == pytest.approx(4 * 1024**2)


def test_download_detail_text():
    from rasva.services.installer import download_detail

    gb = 1024**3
    assert download_detail(gb, 30 * gb, None) == "Downloaded 1.00 GB of 30.00 GB"
    text = download_detail(10 * gb, 30 * gb, 4 * 1024**2)
    assert text == "Downloaded 10.00 GB of 30.00 GB · 4.0 MB/s · about 1 h 25 min left"
    assert download_detail(29 * gb, 30 * gb, 4 * 1024**2).endswith("· about 4 min left")
    assert download_detail(30 * gb - 1024**2, 30 * gb, 100 * 1024**2).endswith(
        "under a minute left"
    )
    assert download_detail(30 * gb, 30 * gb, 4 * 1024**2) == "Downloaded 30.00 GB of 30.00 GB"
    assert download_detail(gb, 30 * gb, 0.0).endswith("waiting for data")
