"""Kokoro adapter tests with a fake ``kokoro`` runtime.

The real model cannot run in CI (no Apple Silicon, no access to the weights), so
these tests pin down the adapter's contract with the kokoro API: local files
only, one pipeline per language, our own segmentation, and a clean WAV.
"""

import json
import re
import sys
import threading
import types
import wave
from pathlib import Path

import numpy as np
import pytest

from rasva.engines import kokoro as K
from rasva.engines.contract import (
    Canceled,
    EngineDescriptor,
    EngineInputError,
    RunContext,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.registry import EngineRegistry
from rasva.services.installer import resolve_lockfile

SHA = "a" * 64
MANIFEST = {
    "repo": K.REPO_ID,
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "files": [
        {"path": "config.json", "sha256": SHA, "size_bytes": 10},
        {"path": "kokoro-v1_0.pth", "sha256": SHA, "size_bytes": 100},
        {"path": "voices/af_heart.pt", "sha256": SHA, "size_bytes": 5},
        {"path": "voices/hf_alpha.pt", "sha256": SHA, "size_bytes": 5},
    ],
}


class PinnedKokoro(K.KokoroEngine):
    descriptor = EngineDescriptor(
        **{
            **K.KokoroEngine.descriptor.model_dump(),
            "artifacts": K.artifacts_from(MANIFEST),
            "voices": K.voices_from(MANIFEST),
        }
    )


class FakeRuntime:
    """Records how the adapter drives kokoro."""

    def __init__(self, amplitude=0.5, seconds_per_char=0.01, long_phonemes=False):
        self.model_args = None
        self.pipelines = []
        self.calls = []
        runtime = self

        class KModel:
            def __init__(self, repo_id=None, config=None, model=None):
                runtime.model_args = {"repo_id": repo_id, "config": config, "model": model}

            def to(self, device):
                runtime.device = device
                return self

            def eval(self):
                return self

        class Result:
            def __init__(self, text):
                n = max(1, int(len(text) * seconds_per_char * K.SAMPLE_RATE))
                self.audio = np.full(n, amplitude, dtype=np.float32)
                self.phonemes = "x" * (K.PHONEME_LIMIT if long_phonemes else len(text))

        class KPipeline:
            def __init__(self, lang_code, repo_id=None, model=True):
                assert isinstance(model, KModel), "must reuse the loaded model"
                runtime.pipelines.append({"lang_code": lang_code, "repo_id": repo_id})

            def __call__(self, text, voice=None, speed=1, split_pattern=r"\n+"):
                runtime.calls.append(
                    {"text": text, "voice": voice, "speed": speed, "split_pattern": split_pattern}
                )
                yield Result(text)

        self.module = types.SimpleNamespace(KModel=KModel, KPipeline=KPipeline)


@pytest.fixture
def fake(monkeypatch):
    rt = FakeRuntime()
    monkeypatch.setitem(sys.modules, "kokoro", rt.module)
    return rt


def loaded(tmp_path):
    engine = PinnedKokoro()
    engine.load(RuntimeContext(model_dir=tmp_path / "model", cache_dir=tmp_path / "cache"))
    return engine


def run(engine, tmp_path, cancel=None, **inputs):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext(
        job_id="job_x",
        output_dir=out,
        _progress=lambda f, d: progress.append((f, d)),
        _cancel=cancel or threading.Event(),
    )
    clean = engine.validate_input("audio.narrate", inputs)
    return engine.run("audio.narrate", clean, ctx), out, progress


# ---------------------------------------------------------------- validation


def test_validation_rules():
    e = PinnedKokoro()
    with pytest.raises(EngineInputError, match="Write or paste"):
        e.validate_input("audio.narrate", {"text": "  ", "voice_id": "af_heart"})
    with pytest.raises(EngineInputError, match="up to"):
        e.validate_input("audio.narrate", {"text": "a" * (K.MAX_CHARS + 1), "voice_id": "af_heart"})
    with pytest.raises(EngineInputError, match="Choose one"):
        e.validate_input("audio.narrate", {"text": "hi", "voice_id": "am_adam"})  # not pinned
    with pytest.raises(EngineInputError, match="Hindi voice"):
        e.validate_input("audio.narrate", {"text": "कृष्ण ने कहा", "voice_id": "af_heart"})
    # Even one Devanagari word would be silently dropped by an English voice.
    with pytest.raises(EngineInputError, match="Hindi voice"):
        e.validate_input(
            "audio.narrate",
            {"text": "Then Arjuna asked Krishna about धर्म.", "voice_id": "af_heart"},
        )
    with pytest.raises(EngineInputError, match="Speed"):
        e.validate_input("audio.narrate", {"text": "hi", "voice_id": "af_heart", "speed": 3})
    ok = e.validate_input("audio.narrate", {"text": "Krishna ne kaha", "voice_id": "hf_alpha"})
    assert ok["language"] == "hi" and ok["script"] == "latin" and ok["speed"] == 1.0


# ---------------------------------------------------------------- runtime


def test_loads_local_files_only(fake, tmp_path):
    loaded(tmp_path)
    assert fake.model_args == {
        "repo_id": K.REPO_ID,
        "config": str(tmp_path / "model" / "config.json"),
        "model": str(tmp_path / "model" / "kokoro-v1_0.pth"),
    }
    assert fake.device == "cpu"


def test_narration_segments_and_wav(fake, tmp_path):
    text = "अर्जुन ने पूछा। कृष्ण ने उत्तर दिया।\n\nफिर युद्ध शुरू हुआ।"
    engine = loaded(tmp_path)
    result, out, progress = run(engine, tmp_path, text=text, voice_id="hf_alpha", speed=1.1)

    assert fake.pipelines == [{"lang_code": "h", "repo_id": K.REPO_ID}]
    assert [c["text"] for c in fake.calls] == [
        "अर्जुन ने पूछा।",
        "कृष्ण ने उत्तर दिया।",
        "फिर युद्ध शुरू हुआ।",
    ]
    assert all(c["split_pattern"] is None and c["speed"] == 1.1 for c in fake.calls)
    assert all(c["voice"] == str(tmp_path / "model" / "voices" / "hf_alpha.pt") for c in fake.calls)

    with wave.open(str(out / "narration.wav")) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, K.SAMPLE_RATE)
        frames = w.getnframes()
    meta = result.outputs[0].metadata
    assert meta["synthetic"] is True and meta["voice_id"] == "hf_alpha"
    assert meta["model_revision"] is None or isinstance(meta["model_revision"], str)
    assert abs(meta["duration_s"] - frames / K.SAMPLE_RATE) < 1e-3
    starts = [s["start_s"] for s in meta["segments"]]
    assert starts == sorted(starts) and meta["segments"][0]["start_s"] == 0
    # Pauses separate segments; the paragraph pause is longer than the sentence pause.
    gaps = [
        b["start_s"] - a["end_s"]
        for a, b in zip(meta["segments"], meta["segments"][1:], strict=False)
    ]
    assert gaps[0] < gaps[1]
    assert result.metrics["segments"] == 3 and result.metrics["truncated_segments"] == 0
    assert progress[-1][0] == 1.0


def test_one_pipeline_per_language_is_reused(fake, tmp_path):
    engine = loaded(tmp_path)
    run(engine, tmp_path, text="Hello there.", voice_id="af_heart")
    run(engine, tmp_path, text="Hello again.", voice_id="af_heart")
    run(engine, tmp_path, text="नमस्ते।", voice_id="hf_alpha")
    assert [p["lang_code"] for p in fake.pipelines] == ["a", "h"]


def test_preview_uses_first_sentence_only(fake, tmp_path):
    engine = loaded(tmp_path)
    result, _, _ = run(
        engine, tmp_path, text="First. Second. Third.", voice_id="af_heart", preview=True
    )
    assert [c["text"] for c in fake.calls] == ["First."]
    assert result.outputs[0].metadata["kind"] == "preview"


def test_output_is_peak_limited(monkeypatch, tmp_path):
    rt = FakeRuntime(amplitude=1.6)
    monkeypatch.setitem(sys.modules, "kokoro", rt.module)
    _, out, _ = run(loaded(tmp_path), tmp_path, text="Loud.", voice_id="af_heart")
    with wave.open(str(out / "narration.wav")) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert np.max(np.abs(pcm)) <= int(K.PEAK_CEILING * 32767) + 1


def test_reports_possible_truncation(monkeypatch, tmp_path):
    rt = FakeRuntime(long_phonemes=True)
    monkeypatch.setitem(sys.modules, "kokoro", rt.module)
    result, _, _ = run(loaded(tmp_path), tmp_path, text="One. Two.", voice_id="af_heart")
    assert result.metrics["truncated_segments"] == 2


def test_cancel_between_sentences(fake, tmp_path):
    cancel = threading.Event()
    engine = loaded(tmp_path)
    original = fake.module.KPipeline.__call__

    def cancel_after_first(self, *a, **kw):
        yield from original(self, *a, **kw)
        cancel.set()

    fake.module.KPipeline.__call__ = cancel_after_first
    with pytest.raises(Canceled):
        run(engine, tmp_path, cancel=cancel, text="One. Two. Three.", voice_id="af_heart")
    assert len(fake.calls) == 1


# ---------------------------------------------------------------- packaging


def test_manifest_pins_commit_urls():
    arts = K.artifacts_from(MANIFEST)
    assert all(f"/resolve/{MANIFEST['revision']}/" in a.url for a in arts)
    assert not any("/resolve/main/" in a.url for a in arts)
    assert [v.id for v in K.voices_from(MANIFEST)] == ["af_heart", "hf_alpha"]


def test_manifest_for_other_repo_rejected(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({**MANIFEST, "repo": "someone/else"}))
    with pytest.raises(ValueError):
        K.load_manifest(p)


def test_unpinned_engine_is_not_installable():
    unpinned = EngineDescriptor(**{**K.KokoroEngine.descriptor.model_dump(), "artifacts": []})
    engine = type("Unpinned", (K.KokoroEngine,), {"descriptor": unpinned})()
    mac = SystemFacts("macOS", "arm64", 16 * 1024**3, True, False, False)
    report = engine.check_compatibility(mac)
    assert not report.compatible and "pinned" in report.reasons[0]
    assert PinnedKokoro().check_compatibility(mac).compatible


def test_lockfile_is_fully_hashed_and_offline_safe():
    engine = EngineRegistry().get("kokoro-82m")
    lock = resolve_lockfile(engine)
    text = lock.read_text()
    requirements = [ln for ln in text.splitlines() if ln and not ln.startswith((" ", "#"))]
    assert requirements and all("--hash=sha256:" in text for _ in requirements)
    for req in requirements:
        block = text[text.index(req) :].split("\n")
        assert any("--hash=sha256:" in ln for ln in block[:3]), req
    assert re.search(r"^kokoro==0\.9\.4", text, re.M)
    assert re.search(r"^en-core-web-sm @ https://github\.com/explosion/", text, re.M)
    major = int(re.search(r"^transformers==(\d+)", text, re.M).group(1))
    assert major < 5
    assert re.search(r"^pydantic==2\.", text, re.M)  # worker protocol dependency
    assert Path(lock).with_name("requirements.in").exists()
