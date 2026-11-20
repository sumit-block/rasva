"""Chatterbox and IndicF5 draft adapters, driven by fake runtimes.

Neither model can run here (no Apple Silicon, no access to the weights), so
these tests pin down how each adapter uses its library: local files only,
per-sentence synthesis, voice conditioning reuse, and honest gating while the
download list (and, for IndicF5, the file layout and lockfile) are unpinned.
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

from rasva.engines import chatterbox as C
from rasva.engines import indicf5 as F
from rasva.engines import manifest as M
from rasva.engines.contract import (
    EngineDescriptor,
    EngineInputError,
    InstallContext,
    RunContext,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.registry import EngineRegistry
from rasva.services.installer import resolve_lockfile

SHA = "b" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
MAC = SystemFacts("darwin", "arm64", 16 * 1024**3, True, False, True)
LINUX = SystemFacts("linux", "x86_64", 16 * 1024**3, False, False, False)


def ctx_for(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext("job_x", out, lambda f, d: progress.append((f, d)), threading.Event())
    return ctx, out, progress


def reference(tmp_path, transcript="यह मेरी आवाज़ है।"):
    path = tmp_path / "ref.wav"
    path.write_bytes(b"RIFF")
    return {"path": str(path), "transcript": transcript, "language": "hi"}


# ================================================================ manifest


def write_manifest(path, engine, sources, **extra):
    path.write_text(json.dumps({"engine": engine, "sources": sources, **extra}))
    return path


def test_manifest_pins_every_url_to_a_commit(tmp_path):
    sources = [
        {"repo": "a/x", "revision": COMMIT, "subdir": "",
         "files": [{"path": "w.pt", "sha256": SHA, "size_bytes": 5}]},
        {"repo": "b/y", "revision": COMMIT, "subdir": "vocos",
         "files": [{"path": "config.yaml", "sha256": SHA, "size_bytes": 1}]},
    ]  # fmt: skip
    m = M.load(write_manifest(tmp_path / "m.json", "e", sources), "e", {"a/x", "b/y"})
    arts = M.artifacts(m)
    assert [a.path for a in arts] == ["w.pt", "vocos/config.yaml"]
    assert arts[1].url == f"https://huggingface.co/b/y/resolve/{COMMIT}/config.yaml"
    assert M.revisions(m) == {"a/x": COMMIT, "b/y": COMMIT}
    assert M.load(tmp_path / "missing.json", "e", {"a/x"}) is None


@pytest.mark.parametrize(
    ("engine", "repos", "revision", "message"),
    [
        ("other", {"a/x"}, COMMIT, "different engine"),
        ("e", {"a/x", "c/z"}, COMMIT, "must pin exactly"),
        ("e", {"a/x"}, "main", "full commit"),
    ],
)
def test_manifest_rejects_unpinned_or_foreign(tmp_path, engine, repos, revision, message):
    src = [{"repo": "a/x", "revision": revision, "subdir": "", "files": []}]
    path = write_manifest(tmp_path / "m.json", engine, src)
    with pytest.raises(M.ManifestError, match=message):
        M.load(path, "e", repos)


def test_manifest_paths_cannot_escape_the_model_dir(tmp_path):
    src = [{"repo": "a/x", "revision": COMMIT, "subdir": "",
            "files": [{"path": "../evil.pt", "sha256": SHA, "size_bytes": 5}]}]  # fmt: skip
    m = M.load(write_manifest(tmp_path / "m.json", "e", src), "e", {"a/x"})
    with pytest.raises(ValueError, match="segments"):
        M.artifacts(m)


# ================================================================ chatterbox

CB_MANIFEST = {
    "engine": C.ENGINE_ID,
    "sources": [{"repo": C.REPO_ID, "revision": COMMIT, "subdir": "",
                 "files": [{"path": f, "sha256": SHA, "size_bytes": 10} for f in C.MODEL_FILES]}],
}  # fmt: skip


class PinnedChatterbox(C.ChatterboxEngine):
    descriptor = EngineDescriptor(
        **{**C.ChatterboxEngine.descriptor.model_dump(), "artifacts": M.artifacts(CB_MANIFEST)}
    )


class FakeChatterbox:
    def __init__(self, fail_device=None):
        self.loads, self.generated, self.conditioned = [], [], []
        rt = self

        class Tensor:
            def __init__(self, arr):
                self.arr = arr

            def squeeze(self, dim):
                return Tensor(self.arr[dim])

            def cpu(self):
                return self

            def numpy(self):
                return self.arr

        class TTS:
            sr = 24_000

            def __init__(self):
                self.conds = "builtin"

            @classmethod
            def from_local(cls, ckpt_dir, device):
                rt.loads.append((Path(ckpt_dir), device))
                if device == fail_device:
                    raise RuntimeError("unsupported op on this device")
                return cls()

            def prepare_conditionals(self, wav_fpath, exaggeration=0.5):
                rt.conditioned.append((wav_fpath, exaggeration))
                self.conds = f"ref:{wav_fpath}"

            def generate(self, text, language_id, **kw):
                rt.generated.append({"text": text, "language_id": language_id,
                                     "conds": self.conds, **kw})  # fmt: skip
                return Tensor(np.full((1, 2400), 0.3, dtype=np.float32))

        self.TTS = TTS
        mtl = types.ModuleType("chatterbox.mtl_tts")
        mtl.ChatterboxMultilingualTTS = TTS
        self.modules = {"chatterbox": types.ModuleType("chatterbox"), "chatterbox.mtl_tts": mtl}


def fake_torch(mps=True):
    torch = types.ModuleType("torch")
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    return torch


@pytest.fixture
def cb(monkeypatch):
    rt = FakeChatterbox()
    for name, mod in rt.modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "torch", fake_torch())
    monkeypatch.delenv("RASVA_TORCH_DEVICE", raising=False)
    return rt


def cb_loaded(tmp_path):
    engine = PinnedChatterbox()
    engine.load(RuntimeContext(tmp_path / "model", tmp_path / "cache"))
    return engine


def cb_run(engine, tmp_path, **inputs):
    ctx, out, progress = ctx_for(tmp_path)
    clean = engine.validate_input("audio.narrate", inputs)
    return engine.run("audio.narrate", clean, ctx), out, progress


def test_chatterbox_gating():
    unpinned = C.ChatterboxEngine().check_compatibility(MAC)
    if not C.ChatterboxEngine.descriptor.artifacts:
        assert not unpinned.compatible and "pin_engine.py chatterbox" in unpinned.reasons[0]
    assert PinnedChatterbox().check_compatibility(MAC).compatible
    assert not PinnedChatterbox().check_compatibility(LINUX).compatible
    d = C.ChatterboxEngine.descriptor
    assert "voice_cloning" in d.features and d.python_version == "3.11"
    assert d.default_voices == {"en": "default_en", "hi": "default_hi"}


def test_chatterbox_verify(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    for f in C.MODEL_FILES[:-1]:
        (model / f).write_bytes(b"x")
    assert not PinnedChatterbox().verify(InstallContext(model, tmp_path))
    (model / C.MODEL_FILES[-1]).write_bytes(b"x")
    assert PinnedChatterbox().verify(InstallContext(model, tmp_path))


def test_chatterbox_validation(tmp_path):
    e = PinnedChatterbox()
    with pytest.raises(EngineInputError, match="Choose a Chatterbox voice"):
        e.validate_input("audio.narrate", {"text": "hi", "voice_id": "af_heart"})
    with pytest.raises(EngineInputError, match="between"):
        e.validate_input("audio.narrate", {"text": "hi", "voice_id": "default_en", "cfg_weight": 2})
    with pytest.raises(EngineInputError, match="missing"):
        e.validate_input("audio.narrate", {"text": "hi", "reference": {"path": "/nope"}})
    ok = e.validate_input("audio.narrate", {"text": "hi", "voice_id": "default_hi"})
    assert ok["exaggeration"] == 0.5 and ok["speed"] == 1.0 and "reference" not in ok
    own = e.validate_input("audio.narrate", {"text": "hi", "reference": reference(tmp_path)})
    assert own["voice_id"] == "own" and own["reference"]["language"] == "hi"


def test_chatterbox_loads_locally_and_falls_back_to_cpu(cb, tmp_path, monkeypatch):
    engine = cb_loaded(tmp_path)
    assert cb.loads == [(tmp_path / "model", "mps")] and engine.device == "mps"

    rt = FakeChatterbox(fail_device="mps")
    for name, mod in rt.modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    engine = cb_loaded(tmp_path)
    assert [d for _, d in rt.loads] == ["mps", "cpu"] and engine.device == "cpu"


def test_chatterbox_language_per_sentence_and_wav(cb, tmp_path):
    engine = cb_loaded(tmp_path)
    text = "अर्जुन ने पूछा। Then Krishna smiled.\n\nयुद्ध शुरू हुआ।"
    result, out, progress = cb_run(engine, tmp_path, text=text, voice_id="default_hi")
    assert [(g["text"], g["language_id"]) for g in cb.generated] == [
        ("अर्जुन ने पूछा।", "hi"), ("Then Krishna smiled.", "en"), ("युद्ध शुरू हुआ।", "hi"),
    ]  # fmt: skip
    assert all(g["cfg_weight"] == 0.5 and g["conds"] == "builtin" for g in cb.generated)
    with wave.open(str(out / "narration.wav")) as w:
        assert w.getframerate() == 24_000 and w.getnchannels() == 1
    meta = result.outputs[0].metadata
    assert meta["synthetic"] and meta["watermark"] == "perth" and not meta["cloned"]
    assert len(meta["segments"]) == 3 and progress[-1][0] == 1.0
    assert result.metrics["device"] == "mps"


def test_chatterbox_reuses_voice_conditioning(cb, tmp_path):
    engine = cb_loaded(tmp_path)
    ref = reference(tmp_path)
    cb_run(engine, tmp_path, text="पहला वाक्य। दूसरा वाक्य।", reference=ref)
    cb_run(engine, tmp_path, text="तीसरा वाक्य।", reference=ref)
    assert cb.conditioned == [(ref["path"], 0.5)]  # once, not per sentence or per job
    cb_run(engine, tmp_path, text="चौथा।", reference=ref, exaggeration=0.7)
    assert len(cb.conditioned) == 2  # exaggeration is baked into the conditioning
    result, *_ = cb_run(engine, tmp_path, text="Back to normal.", voice_id="default_en")
    assert cb.generated[-1]["conds"] == "builtin"  # built-in voice restored
    assert not result.outputs[0].metadata["cloned"]


def test_chatterbox_preview_is_first_sentence(cb, tmp_path):
    engine = cb_loaded(tmp_path)
    cb_run(engine, tmp_path, text="One. Two. Three.", voice_id="default_en", preview=True)
    assert [g["text"] for g in cb.generated] == ["One."]


def test_chatterbox_lockfile_is_hashed_and_matches_its_package():
    lock = resolve_lockfile(EngineRegistry().get(C.ENGINE_ID))
    text = lock.read_text()
    reqs = [ln for ln in text.splitlines() if ln and not ln.startswith((" ", "#"))]
    assert reqs
    for req in reqs:
        block = text[text.index(req) :].split("\n")
        assert any("--hash=sha256:" in ln for ln in block[:3]), req
    assert re.search(r"^chatterbox-tts==0\.1\.7 ", text, re.M)
    assert re.search(r"^torch==2\.6\.0 ", text, re.M)
    assert re.search(r"^pydantic==2\.", text, re.M)
    assert "git+" not in text  # wheels from PyPI only


# ================================================================ IndicF5

LAYOUT = {
    "checkpoint": "model.safetensors",
    "vocab": "checkpoints/vocab.txt",
    "model_cfg": {"dim": 1024, "depth": 22, "heads": 16, "ff_mult": 2,
                  "text_dim": 512, "conv_layers": 4},
    "use_ema": True,
}  # fmt: skip


class FakeF5:
    def __init__(self):
        self.calls = {"load_model": [], "load_checkpoint": [], "load_vocoder": [],
                      "prep": [], "infer": []}  # fmt: skip
        c = self.calls

        def load_model(model_cls, model_cfg, vocab_file=None, device=None, **kw):
            c["load_model"].append({"cls": model_cls, "cfg": model_cfg, "vocab": vocab_file,
                                    "device": device, **kw})  # fmt: skip
            return "model"

        def load_checkpoint(model, ckpt_path, device, use_ema=True, **kw):
            c["load_checkpoint"].append((model, ckpt_path, device, use_ema))
            return "model+weights"

        def load_vocoder(name="vocos", is_local=False, local_path="", device=None, **kw):
            c["load_vocoder"].append((name, is_local, local_path, device))
            return "vocoder"

        def preprocess_ref_audio_text(path, text, show_info=print, **kw):
            show_info("Converting audio...")  # must be swallowed
            c["prep"].append((path, text))
            return f"prepared:{path}", text + " "

        def infer_process(ref_audio, ref_text, gen_text, model, vocoder, **kw):
            c["infer"].append({"ref_audio": ref_audio, "gen_text": gen_text, "model": model,
                               "vocoder": vocoder, **kw})  # fmt: skip
            return np.full(4800, 0.2, dtype=np.float32), 24_000, None

        ui = types.ModuleType("f5_tts.infer.utils_infer")
        ui.load_model, ui.load_checkpoint, ui.load_vocoder = (
            load_model,
            load_checkpoint,
            load_vocoder,
        )
        ui.preprocess_ref_audio_text, ui.infer_process = preprocess_ref_audio_text, infer_process
        model_mod = types.ModuleType("f5_tts.model")
        model_mod.DiT = "DiT"
        infer = types.ModuleType("f5_tts.infer")
        infer.utils_infer = ui
        self.modules = {"f5_tts": types.ModuleType("f5_tts"), "f5_tts.infer": infer,
                        "f5_tts.infer.utils_infer": ui, "f5_tts.model": model_mod}  # fmt: skip


@pytest.fixture
def f5(monkeypatch):
    rt = FakeF5()
    for name, mod in rt.modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "torch", fake_torch(mps=False))
    monkeypatch.setattr(F, "_LAYOUT", LAYOUT)
    return rt


def f5_loaded(tmp_path):
    engine = F.IndicF5Engine()
    engine.load(RuntimeContext(tmp_path / "model", tmp_path / "cache"))
    return engine


def f5_run(engine, tmp_path, **inputs):
    ctx, out, progress = ctx_for(tmp_path)
    clean = engine.validate_input("audio.narrate", inputs)
    return engine.run("audio.narrate", clean, ctx), out, progress


def test_indicf5_gating(monkeypatch, tmp_path):
    e = F.IndicF5Engine()
    monkeypatch.setattr(F, "_MANIFEST", None)
    monkeypatch.setattr(F, "_READY", False)
    monkeypatch.setattr(F, "LOCKFILE", tmp_path / "requirements.lock")
    reasons = e.check_compatibility(MAC).reasons
    assert any("pin_engine.py indicf5" in r for r in reasons)
    assert any("lock_engine.sh" in r for r in reasons)

    monkeypatch.setattr(F, "_MANIFEST", {"engine": "indicf5", "sources": []})
    assert any("layout" in r for r in e.check_compatibility(MAC).reasons)

    monkeypatch.setattr(F, "_READY", True)
    (tmp_path / "requirements.lock").write_text("x==1 --hash=sha256:" + SHA)
    assert e.check_compatibility(MAC).compatible
    assert not e.check_compatibility(LINUX).compatible
    d = F.IndicF5Engine.descriptor
    assert d.license_class == "unknown" and d.voices == [] and "voice_cloning" in d.features


def test_indicf5_unready_manifest_offers_nothing_to_download():
    if not F._READY:
        assert F.IndicF5Engine.descriptor.artifacts == []


def test_indicf5_validation(tmp_path):
    e = F.IndicF5Engine()
    ref = reference(tmp_path)
    with pytest.raises(EngineInputError, match="Devanagari"):
        e.validate_input("audio.narrate", {"text": "Krishna ne kaha", "reference": ref})
    with pytest.raises(EngineInputError, match="Devanagari"):
        e.validate_input(
            "audio.narrate", {"text": "Then Krishna said to Arjuna: धर्म", "reference": ref}
        )
    with pytest.raises(EngineInputError, match="one of your voices"):
        e.validate_input("audio.narrate", {"text": "कृष्ण ने कहा", "voice_id": "hf_alpha"})
    with pytest.raises(EngineInputError, match="missing"):
        e.validate_input("audio.narrate", {"text": "कृष्ण", "reference": {"path": "/nope"}})
    with pytest.raises(EngineInputError, match="Steps"):
        e.validate_input("audio.narrate", {"text": "कृष्ण", "reference": ref, "nfe_step": 7})
    with pytest.raises(EngineInputError, match="Speed"):
        e.validate_input("audio.narrate", {"text": "कृष्ण", "reference": ref, "speed": 3})
    # A few English words inside Hindi text are allowed.
    ok = e.validate_input("audio.narrate", {"text": "कृष्ण ने Arjun से कहा", "reference": ref})
    assert ok["nfe_step"] == 32 and ok["voice_id"] == "own"


def test_indicf5_loads_local_files_only(f5, tmp_path):
    engine = f5_loaded(tmp_path)
    model_dir = tmp_path / "model"
    lm = f5.calls["load_model"][0]
    assert lm["cls"] == "DiT" and lm["cfg"] == LAYOUT["model_cfg"]
    assert lm["vocab"] == str(model_dir / "checkpoints" / "vocab.txt")
    assert f5.calls["load_checkpoint"] == [
        ("model", str(model_dir / "model.safetensors"), "cpu", True)
    ]
    assert f5.calls["load_vocoder"] == [("vocos", True, str(model_dir / "vocos"), "cpu")]
    assert engine.device == "cpu"


def test_indicf5_narrates_each_sentence_with_cached_reference(f5, tmp_path):
    engine = f5_loaded(tmp_path)
    ref = reference(tmp_path)
    result, out, _ = f5_run(engine, tmp_path, text="अर्जुन ने पूछा। कृष्ण मुस्कुराए।",
                            reference=ref, nfe_step=16, speed=0.9)  # fmt: skip
    f5_run(engine, tmp_path, text="युद्ध शुरू हुआ।", reference=ref)
    assert f5.calls["prep"] == [(ref["path"], ref["transcript"])]  # prepared once
    infer = f5.calls["infer"]
    assert [c["gen_text"] for c in infer] == ["अर्जुन ने पूछा।", "कृष्ण मुस्कुराए।", "युद्ध शुरू हुआ।"]
    assert infer[0]["model"] == "model+weights" and infer[0]["vocoder"] == "vocoder"
    assert infer[0]["nfe_step"] == 16 and infer[0]["speed"] == 0.9 and infer[2]["nfe_step"] == 32
    assert infer[0]["show_info"]("quiet") is None
    with wave.open(str(out / "narration.wav")) as w:
        assert w.getframerate() == 24_000
    meta = result.outputs[0].metadata
    assert meta["cloned"] and meta["synthetic"] and meta["nfe_step"] == 16
    assert len(meta["segments"]) == 2


def test_indicf5_new_reference_is_prepared_again(f5, tmp_path):
    engine = f5_loaded(tmp_path)
    f5_run(engine, tmp_path, text="पहला।", reference=reference(tmp_path))
    f5_run(engine, tmp_path, text="दूसरा।", reference=reference(tmp_path, "दूसरी आवाज़ है।"))
    assert len(f5.calls["prep"]) == 2
    engine.unload()
    assert engine._ref is None and engine._model is None


def test_new_engines_are_registered_and_listed(client):
    engines = {e["id"]: e for e in client.get("/api/v1/models").json()}
    for engine_id in (C.ENGINE_ID, F.ENGINE_ID):
        assert engine_id in engines
    # The test server is not an Apple Silicon Mac with pinned files: neither installs.
    for engine_id in (C.ENGINE_ID, F.ENGINE_ID):
        assert engines[engine_id]["compatibility"]["compatible"] is False
