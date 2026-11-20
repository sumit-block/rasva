import math
import shutil
import sqlite3
import struct
import subprocess
import time
import wave

import numpy as np
import pytest

from rasva.audio.text import group, segment
from tests.conftest import make_client, wait_for

NARRATOR = "tests.fake_narrator:FakeNarrator"
SETTINGS = {"engine_id": "test-narrator", "voice_id": "af_heart"}
TERMINAL = {"completed", "failed", "canceled", "interrupted"}
SCRIPT = "One small step. Then another.\n\nA new paragraph begins. It ends here."


def client(settings):
    return make_client(settings, extra_engines=(NARRATOR,))


def new_chapter(c, script=SCRIPT, **settings):
    p = c.post("/api/v1/projects", json={"name": "Book", "module": "audio"}).json()
    body = {"title": "Ch 1", "script": script, "settings": {**SETTINGS, **settings}}
    r = c.post(f"/api/v1/projects/{p['id']}/chapters", json=body)
    assert r.status_code == 201, r.text
    return p, r.json()


def settle(c, chapter_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ch = c.get(f"/api/v1/chapters/{chapter_id}").json()
        if not ch["summary"]["counts"].get("generating"):
            return ch
        time.sleep(0.1)
    raise AssertionError(f"chapter still generating: {ch['summary']}")


def statuses(ch):
    return [s["status"] for s in ch["segments"]]


def generate(c, chapter_id, scope="needed"):
    r = c.post(f"/api/v1/chapters/{chapter_id}/generate", json={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


def job_count(c):
    return len([j for j in c.get("/api/v1/jobs?limit=500").json() if j["task"] == "audio.narrate"])


# ------------------------------------------------------------------ segmentation


def test_grouping_stays_within_paragraphs_and_target():
    text = " ".join(f"Sentence number {i}." for i in range(40)) + "\n\nShort. Paragraph."
    groups = group(segment(text), target_chars=120)
    assert all(len(g.text) <= 120 for g in groups)
    assert groups[-1].text == "Short. Paragraph." and groups[-1].paragraph == 1
    assert " ".join(g.text for g in groups if g.paragraph == 0) == text.split("\n\n")[0]
    assert [g.index for g in groups] == list(range(len(groups)))


def test_sixty_minute_script_segments_quickly(settings):
    # ~9,000 words, about an hour of narration at 150 words per minute.
    para = "Arjuna looked across the field and saw his teachers and cousins standing ready. " * 6
    script = "\n\n".join([para] * 105)
    with client(settings) as c:
        started = time.perf_counter()
        _, ch = new_chapter(c, script=script)
        assert time.perf_counter() - started < 5
        n = len(ch["segments"])
        assert 200 <= n <= 700
        assert all(len(s["text"]) <= 220 for s in ch["segments"])
        # Editing one paragraph keeps every other segment.
        edited = script.replace(para, para.replace("teachers", "elders"), 1)
        ch2 = c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": edited}).json()
        kept = {s["id"] for s in ch["segments"]} & {s["id"] for s in ch2["segments"]}
        assert len(kept) >= n - 4


# ------------------------------------------------------------------ generation and review


def test_generate_edit_and_regenerate_only_what_changed(settings):
    with client(settings) as c:
        _, ch = new_chapter(c)
        assert set(statuses(ch)) == {"pending"} and ch["summary"]["exportable"] is False
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        assert set(statuses(ch)) == {"ready"} and ch["summary"]["exportable"]
        assert ch["summary"]["duration_s"] > 0
        before = job_count(c)

        edited = SCRIPT.replace("Then another.", "Then one more.")
        ch = c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": edited}).json()
        changed = [s for s in ch["segments"] if s["status"] != "ready"]
        assert len(changed) == 1 and "one more" in changed[0]["text"]
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        assert set(statuses(ch)) == {"ready"}
        assert job_count(c) == before + 1  # no chapter-wide rerender

        # Changing the voice marks everything outdated; "needed" regenerates all.
        ch = c.patch(f"/api/v1/chapters/{ch['id']}",
                     json={"settings": {"voice_id": "am_adam"}}).json()  # fmt: skip
        assert set(statuses(ch)) == {"outdated"}
        assert ch["summary"]["exportable"]  # old takes are still usable meanwhile


def test_new_take_waits_for_review(settings):
    with client(settings) as c:
        _, ch = new_chapter(c)
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        seg = ch["segments"][0]
        old_take = seg["current_take_id"]
        ch = c.post(f"/api/v1/segments/{seg['id']}/regenerate").json()
        assert ch["segments"][0]["status"] == "generating"
        ch = settle(c, ch["id"])
        seg = ch["segments"][0]
        assert seg["status"] == "needs_review" and seg["current_take_id"] == old_take
        new_take = seg["takes"][-1]["id"]

        # Keep old: the review is settled and the old take stays.
        ch = c.post(f"/api/v1/segments/{seg['id']}/takes/{old_take}/use").json()
        assert ch["segments"][0]["status"] == "ready"
        assert ch["segments"][0]["current_take_id"] == old_take
        # Changing one's mind later: use the new take.
        ch = c.post(f"/api/v1/segments/{seg['id']}/takes/{new_take}/use").json()
        assert ch["segments"][0]["current_take_id"] == new_take
        assert len(ch["segments"][0]["takes"]) == 2  # nothing overwritten


def test_failures_are_isolated_and_resumable(settings):
    script = "Fine one. [[fail]] broken. Fine two.\n\n[[crash]] boom. Fine three."
    with client(settings) as c:
        _, ch = new_chapter(c, script=script)
        # Group into separate segments so each fault hits one segment.
        ch = c.patch(f"/api/v1/chapters/{ch['id']}",
                     json={"script": script.replace(". ", ".\n")}).json()  # fmt: skip
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        by_text = {s["text"]: s for s in ch["segments"]}
        assert by_text["[[fail]] broken."]["status"] == "failed"
        assert by_text["[[crash]] boom."]["status"] == "failed"
        assert all(s["status"] == "ready" for t, s in by_text.items() if "[[" not in t)
        r = c.post(f"/api/v1/chapters/{ch['id']}/export", json={})
        assert r.status_code == 409 and "2 sentences" in r.json()["error"]["message"]

        # Retrying failed ones does not touch the good ones.
        before = job_count(c)
        generate(c, ch["id"], scope="failed")
        settle(c, ch["id"])
        assert job_count(c) == before + 2

        fixed = script.replace("[[fail]] ", "").replace("[[crash]] ", "").replace(". ", ".\n")
        c.patch(f"/api/v1/chapters/{ch['id']}", json={"script": fixed})
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        assert set(statuses(ch)) == {"ready"}
        assert c.post(f"/api/v1/chapters/{ch['id']}/export", json={}).status_code == 201


def test_interrupted_generation_resumes_after_restart(settings):
    script = ".\n".join(f"Line {i}" for i in range(12)) + "."
    with client(settings) as c:
        _, ch = new_chapter(c, script=script)
        generate(c, ch["id"])
        # Leave immediately: the running segment is interrupted, the rest stay queued.
    with client(settings) as c:
        ch = settle(c, ch["id"])
        assert all(s in ("ready", "failed") for s in statuses(ch))
        generate(c, ch["id"], scope="failed")
        ch = settle(c, ch["id"])
        assert set(statuses(ch)) == {"ready"}


def test_cancel_stops_remaining_segments(settings):
    with client(settings) as c:
        p, ch = new_chapter(c, script=".\n".join(f"Line {i}" for i in range(30)) + ".")
        blocker = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "test.run", "engine_id": "test-fault",
            "inputs": {"behaviour": "slow", "seconds": 2},
        }).json()  # fmt: skip
        generate(c, ch["id"])
        ch = c.post(f"/api/v1/chapters/{ch['id']}/cancel").json()
        wait_for(c, blocker["id"], TERMINAL)
        ch = settle(c, ch["id"])
        assert set(statuses(ch)) == {"pending"}


# ------------------------------------------------------------------ mixing and export


def write_wav(path, seconds, rate=44_100, channels=2, amp=0.9, freq=220.0):
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", v) * channels
        w.writeframes(bytes(frames))


def read(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767
        return data.reshape(-1, w.getnchannels()), w.getframerate()


def export(c, chapter_id, **body):
    r = c.post(f"/api/v1/chapters/{chapter_id}/export", json=body)
    assert r.status_code == 201, r.text
    job = wait_for(c, r.json()["id"], TERMINAL)
    assert job["state"] == "completed", job
    return job, {c.get(f"/api/v1/assets/{a}").json()["original_name"]: a for a in job["outputs"]}


def asset_path(settings, c, asset_id):
    return settings.paths.projects / c.get(f"/api/v1/assets/{asset_id}").json()["rel_path"]


def upload_music(c, project_id, path, name="music.wav", kind="import"):
    with open(path, "rb") as fh:
        r = c.post(f"/api/v1/projects/{project_id}/assets",
                   files={"file": (name, fh, "audio/wav")}, data={"kind": kind})  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def test_narration_only_export(settings):
    with client(settings) as c:
        _, ch = new_chapter(c)
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        job, files = export(c, ch["id"])
        data, rate = read(asset_path(settings, c, files["chapter.wav"]))
        assert data.shape[1] == 1 and rate == 8000  # narration's own rate when nothing is mixed
        assert np.max(np.abs(data)) <= 0.8913 + 1e-3  # -1 dBFS ceiling
        expected = sum(s["current"]["duration_s"] for s in ch["segments"])
        expected += sum(s["pause_after_s"] for s in ch["segments"][:-1]) + 0.5
        assert abs(len(data) / rate - expected) < 0.1
        assert job["metrics"]["segments"] == len(ch["segments"])
        # The export appears on the chapter and in the project library.
        assert c.get(f"/api/v1/chapters/{ch['id']}").json()["exports"][0]["id"] == job["id"]


def test_music_is_ducked_under_speech_and_never_clips(settings, tmp_path):
    with client(settings) as c:
        p, ch = new_chapter(c, script="A fairly long first sentence to talk over the music bed.")
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        write_wav(tmp_path / "bed.wav", 1.0)  # short and loud: must loop and be limited
        music = upload_music(c, p["id"], tmp_path / "bed.wav")
        ch = c.post(f"/api/v1/chapters/{ch['id']}/tracks",
                    json={"kind": "music", "asset_id": music["id"], "preset": "present",
                          "fade_in_s": 0, "fade_out_s": 0}).json()  # fmt: skip
        c.patch(f"/api/v1/chapters/{ch['id']}", json={"mix": {"duck_db": -20}})
        job, files = export(c, ch["id"], stems=True)
        assert {"chapter.wav", "narration.wav", "music.wav"} <= set(files)
        mix, rate = read(asset_path(settings, c, files["chapter.wav"]))
        voice, _ = read(asset_path(settings, c, files["narration.wav"]))
        bed, _ = read(asset_path(settings, c, files["music.wav"]))
        assert rate == 48_000 and mix.shape[1] == 2
        assert np.max(np.abs(mix)) <= 0.8913 + 1e-3
        # Stems share the mix gain, so they add back up to the mix.
        assert np.max(np.abs(voice + bed - mix)) < 2e-3 * 3
        speech = np.abs(voice[:, 0]) > 0.01
        tail = slice(len(bed) - int(0.1 * rate), len(bed))  # music recovering after speech
        speaking = bed[speech]
        assert speaking.size and np.sqrt(np.mean(speaking**2)) < 0.5 * np.sqrt(
            np.mean(bed[tail] ** 2)
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_mp3_export_and_compressed_music(settings, tmp_path):
    with client(settings) as c:
        p, ch = new_chapter(c)
        generate(c, ch["id"])
        settle(c, ch["id"])
        write_wav(tmp_path / "bed.wav", 2.0, amp=0.3)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_path / "bed.wav"),
                        str(tmp_path / "bed.mp3")], check=True)  # fmt: skip
        with open(tmp_path / "bed.mp3", "rb") as fh:
            music = c.post(f"/api/v1/projects/{p['id']}/assets",
                           files={"file": ("bed.mp3", fh, "audio/mpeg")},
                           data={"kind": "import"}).json()  # fmt: skip
        c.post(f"/api/v1/chapters/{ch['id']}/tracks",
               json={"kind": "music", "asset_id": music["id"], "preset": "quiet"})  # fmt: skip
        job, files = export(c, ch["id"], formats=["wav", "mp3"])
        mp3 = c.get(f"/api/v1/assets/{files['chapter.mp3']}").json()
        assert mp3["media_type"] == "audio/mpeg" and mp3["size_bytes"] > 1000


def test_effect_at_a_sentence(settings, tmp_path):
    with client(settings) as c:
        p, ch = new_chapter(c)
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        write_wav(tmp_path / "bell.wav", 0.2, amp=0.5, freq=880)
        bell = upload_music(c, p["id"], tmp_path / "bell.wav", name="bell.wav")
        url = f"/api/v1/chapters/{ch['id']}/tracks"
        r = c.post(url, json={"kind": "effect", "asset_id": bell["id"], "anchor_segment": 99})
        assert r.status_code == 422
        ch = c.post(
            url, json={"kind": "effect", "asset_id": bell["id"], "anchor_segment": 1}
        ).json()
        assert ch["tracks"][0]["duck"] is False and ch["tracks"][0]["loop"] is False
        _, files = export(c, ch["id"], stems=True)
        fx, rate = read(asset_path(settings, c, files["effects.wav"]))
        first = np.argmax(np.abs(fx[:, 0]) > 0.01) / rate
        exp = c.get(f"/api/v1/chapters/{ch['id']}").json()
        seg0 = exp["segments"][0]
        assert abs(first - (seg0["current"]["duration_s"] + seg0["pause_after_s"])) < 0.05


def test_assemble_task_is_internal_only(settings):
    with client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "x", "module": "audio"}).json()
        r = c.post("/api/v1/jobs", json={
            "project_id": p["id"], "task": "audio.assemble", "engine_id": "audio-mixer",
            "inputs": {"segments": [{"path": "/etc/hosts"}]},
        })  # fmt: skip
        assert r.status_code == 422
        assert "audio-mixer" not in [m["id"] for m in c.get("/api/v1/models").json()]


def test_tracks_must_be_this_projects_audio(settings, tmp_path):
    with client(settings) as c:
        _, ch = new_chapter(c)
        other = c.post("/api/v1/projects", json={"name": "other", "module": "audio"}).json()
        write_wav(tmp_path / "m.wav", 0.2)
        foreign = upload_music(c, other["id"], tmp_path / "m.wav")
        r = c.post(f"/api/v1/chapters/{ch['id']}/tracks",
                   json={"kind": "music", "asset_id": foreign["id"]})  # fmt: skip
        assert r.status_code == 422


def test_block_mixer_memory_on_a_long_chapter(tmp_path):
    """15 minutes at 48 kHz stereo with a looped bed stays block-sized in memory."""
    import resource
    import threading

    from rasva.engines.builtin.mixer import MixerEngine
    from rasva.engines.contract import RunContext

    seg = tmp_path / "seg.wav"
    write_wav(seg, 15.0, rate=24_000, channels=1, amp=0.3)
    bed = tmp_path / "bed.wav"
    write_wav(bed, 5.0)
    engine = MixerEngine()
    inputs = engine.validate_input("audio.assemble", {
        "segments": [{"path": str(seg), "pause_after_s": 0.3}] * 60,
        "tracks": [{"kind": "music", "path": str(bed), "loop": True, "gain_db": -18}],
        "formats": ["wav"],
    })  # fmt: skip
    out = tmp_path / "out"
    out.mkdir()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = engine.run("audio.assemble", inputs,
                        RunContext("j", out, lambda f, d: None, threading.Event()))  # fmt: skip
    grew_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
    assert result.metrics["audio_s"] > 15 * 60
    full_mix_kb = 15 * 60 * 48_000 * 2 * 4 / 1024  # a whole float32 stereo mix in RAM
    assert grew_kb < full_mix_kb / 3
    with wave.open(str(out / "chapter.wav")) as w:
        assert w.getnframes() / w.getframerate() > 15 * 60


def test_chapter_deletion_keeps_generated_files(settings):
    with client(settings) as c:
        p, ch = new_chapter(c)
        generate(c, ch["id"])
        ch = settle(c, ch["id"])
        assert c.delete(f"/api/v1/chapters/{ch['id']}").status_code == 204
        outputs = c.get(f"/api/v1/projects/{p['id']}/assets?kind=output").json()
        assert len(outputs) == len(ch["segments"])
        conn = sqlite3.connect(settings.paths.db_file)
        assert conn.execute("SELECT COUNT(*) FROM takes").fetchone()[0] == 0
