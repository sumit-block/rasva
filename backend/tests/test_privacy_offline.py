"""Privacy and offline guarantees (PRD §21.5, §27, §28)."""

import ipaddress
import re
import sys
from pathlib import Path

import pytest

from rasva.config import Paths
from rasva.logging_setup import rotate_if_large
from tests.conftest import make_client, wait_for, wav_bytes
from tests.test_chapters import NARRATOR, SETTINGS, generate, settle

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
SECRET_EN = "Zephyrine quietly sold the violet harbour"
SECRET_HI = "गुप्तनामवाला सौदागर"


def run_everything(c):
    """Exercise every place user text flows through."""
    p = c.post("/api/v1/projects", json={"name": "Book", "module": "audio"}).json()
    script = f"{SECRET_EN}.\n[[fail]] {SECRET_EN} again."
    ch = c.post(f"/api/v1/projects/{p['id']}/chapters",
                json={"title": "Ch", "script": script, "settings": SETTINGS}).json()  # fmt: skip
    generate(c, ch["id"])
    settle(c, ch["id"])
    c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": f"{SECRET_EN}."})
    generate(c, ch["id"])
    settle(c, ch["id"])
    job = c.post(f"/api/v1/chapters/{ch['id']}/export", json={}).json()
    wait_for(c, job["id"], TERMINAL)

    entry = c.post("/api/v1/pronunciations", json={
        "written_forms": [SECRET_HI], "spoken": SECRET_HI + "जी", "language": "hi",
        "examples": [f"{SECRET_HI} आए।"],
    }).json()  # fmt: skip
    body = {"engine_id": "test-narrator", "voice_id": "hf_alpha", "example": 0}
    prev = c.post(f"/api/v1/pronunciations/{entry['id']}/preview", json=body).json()
    wait_for(c, prev["id"], TERMINAL)
    c.post("/api/v1/pronunciations/check", json={"text": f"{SECRET_HI} आए", "language": "hi"})

    exp = c.post("/api/v1/experiments", json={
        "name": "e", "texts": [SECRET_EN],
        "candidates": [{"engine_id": "test-narrator", "settings": {"voice_id": "af_heart"}}],
    }).json()  # fmt: skip
    wait_for(c, exp["runs"][0]["job_id"], TERMINAL)
    c.post(f"/api/v1/projects/{p['id']}/assets",
           files={"file": (f"{SECRET_EN}.wav", wav_bytes(), "audio/wav")})  # fmt: skip
    # Invalid input paths also must not echo private text into logs.
    bad = {"text": SECRET_HI, "voice_id": "af_heart"}
    c.post("/api/v1/jobs", json={"project_id": p["id"], "task": "audio.narrate",
                                 "engine_id": "test-narrator", "inputs": bad})  # fmt: skip
    c.get("/api/v1/audio/recommendation", params={"language": "hi"})
    c.get("/api/v1/storage/cleanup")


def test_private_text_never_reaches_logs(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        run_everything(c)
    logs = [f for f in settings.paths.logs.rglob("*") if f.is_file()]
    assert logs, "expected log files to exist"
    for f in logs:
        text = f.read_text(encoding="utf-8", errors="replace")
        for secret in (SECRET_EN, "Zephyrine", SECRET_HI, "गुप्तनामवाला"):
            assert secret not in text, f"{secret!r} found in {f.name}"


_connections: list[object] = []
_recording = False


def _audit(event, args):
    if _recording and event == "socket.connect":
        _connections.append(args[1])


sys.addaudithook(_audit)


def _is_local(address) -> bool:
    if isinstance(address, str | bytes):  # AF_UNIX
        return True
    host = address[0]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def test_no_outbound_connections_during_normal_use(settings):
    global _recording
    _connections.clear()
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        _recording = True
        try:
            run_everything(c)
            for path in ("/system/check", "/system/storage", "/models", "/experiments"):
                assert c.get(f"/api/v1{path}").status_code == 200
        finally:
            _recording = False
    outbound = [a for a in _connections if not _is_local(a)]
    assert outbound == [], f"unexpected network connections: {outbound}"


def test_engine_workers_run_offline(tmp_path):
    env = Paths(tmp_path).worker_env()
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1" and env["DO_NOT_TRACK"] == "1"
    for key in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "TMPDIR", "UV_CACHE_DIR"):
        assert env[key].startswith(str(tmp_path))  # caches stay in the managed root


def test_engine_logs_rotate(tmp_path):
    log = tmp_path / "engine.log"
    log.write_bytes(b"x" * 2000)
    rotate_if_large(log, max_bytes=1000)
    assert not log.exists() and (tmp_path / "engine.log.1").stat().st_size == 2000
    rotate_if_large(tmp_path / "missing.log", max_bytes=1)  # no error


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@pytest.mark.skipif(not (DIST / "index.html").exists(), reason="frontend not built")
def test_frontend_bundle_loads_nothing_from_the_internet():
    html = (DIST / "index.html").read_text()
    for attr in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not re.match(r"^(https?:)?//", attr), f"external asset in index.html: {attr}"
    for css in DIST.rglob("*.css"):
        text = css.read_text()
        assert not re.search(r"@import\s+url\(\s*['\"]?(https?:)?//", text), css.name
        assert not re.search(r"url\(\s*['\"]?(https?:)?//", text), f"remote url() in {css.name}"
    for js in DIST.rglob("*.js"):
        text = js.read_text()
        # Fetching or loading remote resources (documentation links in error strings are fine).
        assert not re.search(r"(fetch|import|EventSource|WebSocket)\(\s*[\"'`]https?://", text), (
            js.name
        )
