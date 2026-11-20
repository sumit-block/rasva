import io
import wave

import numpy as np
import pytest

from rasva.audio.reference import analyze, transcript_check
from tests.conftest import make_client, wait_for

CLONER = "tests.fake_narrator:CloningNarrator"
NARRATOR = "tests.fake_narrator:FakeNarrator"
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
RATE = 24_000
TRANSCRIPT = "Mera naam Sumit hai aur yeh meri awaaz ka namoona hai, dhyaan se suniye."
CONSENT = {"basis": "self", "speaker_name": "", "accepted": True}


def speechlike(seconds=8.0, amp=0.3, noise=0.0005, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    burst = (np.floor(t / 0.55) % 1 == 0) & ((t % 0.55) < 0.4)  # 0.4 s talk, 0.15 s pause
    tone = amp * np.sin(2 * np.pi * (180 + 40 * np.sin(2 * np.pi * 3 * t)) * t)
    return (np.where(burst, tone, 0) + noise * rng.standard_normal(t.size)).astype(np.float32)


def wav(samples):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


# ------------------------------------------------------------------ analysis


def codes(report):
    return {i["code"] for i in report["issues"]}


def test_good_recording():
    r = analyze(speechlike(), RATE)
    assert r["level"] == "good", r
    assert 7 < r["duration_s"] < 9 and r["snr_db"] > 30


@pytest.mark.parametrize(
    ("samples", "code", "level"),
    [
        (speechlike(2.0), "too_short", "error"),
        (speechlike(4.0), "short", "warning"),
        (np.sign(speechlike()) * 1.0, "clipping", "error"),
        (speechlike(amp=0.003), "too_quiet", "error"),
        (speechlike(noise=0.12), "noisy", "error"),  # ~5 dB signal-to-noise
        (speechlike(noise=0.05), "noise", "warning"),  # ~13 dB
    ],
)
def test_problems_get_plain_advice(samples, code, level):
    r = analyze(samples.astype(np.float32), RATE)
    assert code in codes(r) and r["level"] == level
    assert all(i["message"] for i in r["issues"])


def test_transcript_plausibility():
    assert transcript_check("", 8)[0]["severity"] == "error"
    assert transcript_check("Hi.", 8)[0]["code"] == "transcript_short"
    assert transcript_check(TRANSCRIPT, 6) == []


# ------------------------------------------------------------------ API


def upload(c, samples, name="ref.wav"):
    r = c.post("/api/v1/voices/reference", files={"file": (name, wav(samples), "audio/wav")})
    assert r.status_code == 201, r.text
    return r.json()


def create(c, asset_id, **over):
    body = {
        "name": "My voice", "language": "hi", "reference_asset_id": asset_id,
        "transcript": TRANSCRIPT, "transcript_confirmed": True, "consent": CONSENT, **over,
    }  # fmt: skip
    return c.post("/api/v1/voices", json=body)


def test_consent_and_quality_gates(settings):
    with make_client(settings, extra_engines=(CLONER,)) as c:
        bad = upload(c, speechlike(2.0))
        assert bad["quality"]["level"] == "error"
        r = create(c, bad["asset"]["id"])
        assert r.status_code == 422 and "5 seconds" in r.json()["error"]["message"]

        good = upload(c, speechlike())["asset"]["id"]
        r = create(c, good, consent={"basis": "self", "accepted": False})
        assert r.status_code == 422 and "consent" in r.json()["error"]["message"]
        r = create(c, good, consent={"basis": "permission", "speaker_name": "", "accepted": True})
        assert r.status_code == 422 and "permission" in r.json()["error"]["message"]
        assert create(c, good, transcript_confirmed=False).status_code == 422
        assert create(c, good, transcript="").status_code == 422

        r = create(
            c, good, consent={"basis": "permission", "speaker_name": "Dadi", "accepted": True}
        )
        assert r.status_code == 201, r.text
        v = r.json()
        assert v["consent"]["speaker_name"] == "Dadi" and "deceive" in v["consent"]["statement"]
        assert v["consent"]["recorded_from"] == "this Mac"
        assert create(c, good).status_code == 422  # one profile per recording
        log = c.get("/api/v1/voices/consent-log").json()
        assert log[0]["event"] == "granted" and log[0]["speaker_name"] == "Dadi"
        # Reference recordings live in a hidden project and are never cleanup candidates.
        assert c.get("/api/v1/projects").json() == []
        groups = c.get("/api/v1/storage/cleanup").json()["groups"]
        assert all(g["count"] == 0 for g in groups if g["id"] in ("untracked", "temp"))


def test_profiles_reach_cloning_engines_only(settings):
    with make_client(settings, extra_engines=(CLONER, NARRATOR)) as c:
        voice = create(c, upload(c, speechlike())["asset"]["id"]).json()
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        inputs = {"text": "कृष्ण ने कहा।", "voice_profile_id": voice["id"], "preview": True}
        body = {"project_id": p["id"], "task": "audio.narrate", "inputs": inputs}
        r = c.post("/api/v1/jobs", json={**body, "engine_id": "test-narrator"})
        assert r.status_code == 422 and "own voices" in r.json()["error"]["message"]
        job = c.post("/api/v1/jobs", json={**body, "engine_id": "test-cloner"}).json()
        assert job["inputs"]["voice_profile_id"] == voice["id"]
        assert job["inputs"]["reference"]["transcript"] == TRANSCRIPT
        assert wait_for(c, job["id"], TERMINAL)["state"] == "completed"

        ch = c.post(f"/api/v1/projects/{p['id']}/chapters", json={
            "title": "c", "script": "Pehla vaakya. Doosra vaakya.",
            "settings": {"engine_id": "test-cloner", "voice_profile_id": voice["id"]},
        })  # fmt: skip
        assert ch.status_code == 201, ch.text
        assert ch.json()["settings"]["voice_profile_id"] == voice["id"]
        ch = c.post(f"/api/v1/chapters/{ch.json()['id']}/generate", json={}).json()
        for s in ch["segments"]:
            wait_for(c, s["takes"][0]["job_id"], TERMINAL)
        ch = c.get(f"/api/v1/chapters/{ch['id']}").json()
        assert {s["status"] for s in ch["segments"]} == {"ready"}
        # Switching from your voice to a preset voice marks audio outdated.
        preset_voice = {"settings": {"voice_profile_id": "", "voice_id": "hf_alpha"}}
        ch = c.patch(f"/api/v1/chapters/{ch['id']}", json=preset_voice).json()
        assert {s["status"] for s in ch["segments"]} == {"outdated"}


def test_delete_removes_recording_and_keeps_consent_history(settings):
    with make_client(settings, extra_engines=(CLONER,)) as c:
        ref = upload(c, speechlike())
        voice = create(c, ref["asset"]["id"]).json()
        path = settings.paths.projects / ref["asset"]["rel_path"]
        assert path.exists()
        assert c.delete(f"/api/v1/voices/{voice['id']}").status_code == 204
        assert not path.exists()
        assert c.get(f"/api/v1/voices/{voice['id']}").status_code == 404
        events = [e["event"] for e in c.get("/api/v1/voices/consent-log").json()]
        assert events == ["deleted", "granted"]
        p = c.post("/api/v1/projects", json={"name": "p", "module": "audio"}).json()
        inputs = {"text": "Hi.", "voice_profile_id": voice["id"]}
        r = c.post("/api/v1/jobs", json={"project_id": p["id"], "task": "audio.narrate",
                                         "engine_id": "test-cloner", "inputs": inputs})  # fmt: skip
        assert r.status_code == 422 and "deleted" in r.json()["error"]["message"]


def test_record_again_discards_unused_reference(settings):
    with make_client(settings, extra_engines=(CLONER,)) as c:
        ref = upload(c, speechlike(2.0))
        assert c.delete(f"/api/v1/voices/reference/{ref['asset']['id']}").status_code == 204
        assert not (settings.paths.projects / ref["asset"]["rel_path"]).exists()
        used = upload(c, speechlike())["asset"]["id"]
        create(c, used)
        assert c.delete(f"/api/v1/voices/reference/{used}").status_code == 409
