from rasva.engines.kokoro import KokoroEngine
from tests.conftest import make_client, set_host, set_pinned, wait_for

TERMINAL = {"completed", "failed", "canceled", "interrupted"}
NARRATOR = "tests.fake_narrator:FakeNarrator"


def test_narration_through_queue_and_worker(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        p = c.post("/api/v1/projects", json={"name": "Gita", "module": "audio"}).json()
        job = c.post(
            "/api/v1/jobs",
            json={
                "project_id": p["id"],
                "task": "audio.narrate",
                "engine_id": "test-narrator",
                "inputs": {
                    "text": "अर्जुन ने पूछा। कृष्ण ने कहा।",
                    "voice_id": "hf_alpha",
                    "preview": True,
                },
            },
        )
        assert job.status_code == 201, job.text
        assert job.json()["inputs"]["language"] == "hi"  # normalised by validate_input
        done = wait_for(c, job.json()["id"], TERMINAL)
        assert done["state"] == "completed", done
        assert done["metrics"]["segments"] == 1  # preview = first sentence
        asset = c.get(f"/api/v1/assets/{done['outputs'][0]}").json()
        assert asset["kind"] == "output" and asset["metadata"]["synthetic"] is True


def test_narration_input_errors_are_reported_before_queueing(settings):
    with make_client(settings, extra_engines=(NARRATOR,)) as c:
        p = c.post("/api/v1/projects", json={"name": "x", "module": "audio"}).json()
        r = c.post(
            "/api/v1/jobs",
            json={
                "project_id": p["id"],
                "task": "audio.narrate",
                "engine_id": "test-narrator",
                "inputs": {"text": "Arjuna asked about धर्म.", "voice_id": "af_heart"},
            },
        )
        assert r.status_code == 422 and "Hindi voice" in r.json()["error"]["message"]


def test_kokoro_listed_but_not_installable_until_pinned(settings, monkeypatch):
    set_host(monkeypatch, apple_silicon=True)
    set_pinned(monkeypatch, KokoroEngine, pinned=False)
    with make_client(settings) as client:
        kokoro = client.get("/api/v1/models/kokoro-82m").json()
        assert kokoro["install_state"] == "not_installed"
        assert kokoro["compatibility"]["compatible"] is False
        assert "pinned" in kokoro["compatibility"]["reasons"][0]
        assert kokoro["license_notes"] and "GPL" in kokoro["license_notes"][0]
        r = client.post("/api/v1/models/kokoro-82m/install")
        assert r.status_code == 409 and r.json()["error"]["code"] == "incompatible"
        p = client.post("/api/v1/projects", json={"name": "x", "module": "audio"}).json()
        r = client.post(
            "/api/v1/jobs",
            json={
                "project_id": p["id"],
                "task": "audio.narrate",
                "inputs": {"text": "hi"},
            },
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "no_engine"


def test_kokoro_pinned_is_refused_off_apple_silicon(settings, monkeypatch):
    set_host(monkeypatch, apple_silicon=False)
    set_pinned(monkeypatch, KokoroEngine, pinned=True)
    with make_client(settings) as client:
        compat = client.get("/api/v1/models/kokoro-82m").json()["compatibility"]
        assert compat["compatible"] is False
        assert compat["reasons"] == ["Kokoro's software package is built for Apple Silicon Macs."]
        r = client.post("/api/v1/models/kokoro-82m/install")
        assert r.status_code == 409 and r.json()["error"]["code"] == "incompatible"


def test_kokoro_pinned_on_apple_silicon_is_installable(settings, monkeypatch):
    set_host(monkeypatch, apple_silicon=True)
    set_pinned(monkeypatch, KokoroEngine, pinned=True)
    with make_client(settings) as client:
        kokoro = client.get("/api/v1/models/kokoro-82m").json()
        assert kokoro["compatibility"] == {"compatible": True, "reasons": []}
        assert kokoro["download_bytes"] == 1
