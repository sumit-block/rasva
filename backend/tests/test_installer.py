import hashlib
import os
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    (target / "bin" / "python").write_text("fake")
elif args[:2] == ["pip", "install"]:
    assert "--require-hashes" in args
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
