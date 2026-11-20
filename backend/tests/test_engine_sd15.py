"""Stable Diffusion 1.5 adapter (I0) with fake torch, diffusers and PIL modules:
contract, local-only loading, seeds, progress, cancel and blank-image handling."""

from __future__ import annotations

import json
import re
import sys
import threading
import types

import numpy as np
import pytest

from rasva.engines import sd15 as S
from rasva.engines.contract import (
    Canceled,
    EngineError,
    EngineInputError,
    InstallContext,
    RunContext,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.registry import EngineRegistry
from rasva.services.installer import resolve_lockfile
from rasva.services.presets import apply_preset
from tests.conftest import make_client, set_host, set_pinned

MAC = SystemFacts("darwin", "arm64", 16 * 1024**3, True, False, True)
LINUX = SystemFacts("linux", "x86_64", 16 * 1024**3, False, False, False)


class FakeRuntime:
    """Records what the adapter asks of torch/diffusers; returns a grey image."""

    def __init__(self, mps=True, fail_device=None, image=None):
        rt = self
        self.loads: list[tuple[str, dict]] = []
        self.calls: list[dict] = []
        self.seeds: list[int] = []
        self.moved_to: list[str] = []
        self.saved: list[tuple[tuple, str]] = []
        self.image = np.full((512, 512, 3), 0.5, dtype=np.float32) if image is None else image

        torch = types.ModuleType("torch")
        torch.float16, torch.float32 = "torch.float16", "torch.float32"
        torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
        torch.mps = types.SimpleNamespace(driver_allocated_memory=lambda: 3 * 1024**3)

        class Generator:
            def __init__(self, device):
                assert device == "cpu"

            def manual_seed(self, seed):
                rt.seeds.append(seed)
                return self

        torch.Generator = Generator

        class Tokenizer:
            def __call__(self, text):
                return types.SimpleNamespace(input_ids=[0] * (len(text.split()) + 2))

        class Pipe:
            tokenizer = Tokenizer()
            scheduler = types.SimpleNamespace(config={"name": "pndm"})

            def set_progress_bar_config(self, disable):
                assert disable

            def to(self, device):
                if device == fail_device:
                    raise RuntimeError("unsupported op on this device")
                rt.moved_to.append(device)
                return self

            def enable_attention_slicing(self):
                rt.sliced = True

            def __call__(self, prompt, **kw):
                rt.calls.append({"prompt": prompt, **kw})
                for i in range(kw["num_inference_steps"]):
                    kw["callback_on_step_end"](self, i, i, {})
                return types.SimpleNamespace(images=[rt.image])

        class Pipeline:
            @classmethod
            def from_pretrained(cls, path, **kw):
                rt.loads.append((path, kw))
                return Pipe()

        class Scheduler:
            @classmethod
            def from_config(cls, config):
                return "dpm++2m"

        diffusers = types.ModuleType("diffusers")
        diffusers.StableDiffusionPipeline = Pipeline
        diffusers.DPMSolverMultistepScheduler = Scheduler

        class Img:
            def __init__(self, arr):
                self.arr = arr

            def save(self, path):
                rt.saved.append((self.arr.shape, str(self.arr.dtype)))
                path.write_bytes(b"\x89PNG fake")

        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(fromarray=Img)
        self.modules = {"torch": torch, "diffusers": diffusers, "PIL": pil}


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        rt = FakeRuntime(**kw)
        for name, mod in rt.modules.items():
            monkeypatch.setitem(sys.modules, name, mod)
        return rt

    monkeypatch.delenv("RASVA_TORCH_DEVICE", raising=False)
    return install


def loaded(tmp_path):
    engine = S.StableDiffusion15Engine()
    engine.load(RuntimeContext(tmp_path / "model", tmp_path / "cache"))
    return engine


def run(engine, tmp_path, cancel=None, **inputs):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    report = lambda f, d: progress.append((f, d))  # noqa: E731
    ctx = RunContext("job_x", out, report, cancel or threading.Event())
    clean = engine.validate_input(S.TASK, {"prompt": "a lighthouse at dusk", **inputs})
    return engine.run(S.TASK, clean, ctx), out, progress


# ---- descriptor, gating, manifest, lockfile ----


def test_registered_as_restricted_image_engine():
    d = EngineRegistry().get(S.ENGINE_ID).descriptor
    assert d.modality == "image" and d.tasks == [S.TASK]
    assert d.license_class == "restricted"  # the installer asks the owner to confirm
    assert any("safety checker" in n for n in d.license_notes)
    assert [p.id for p in d.presets] == ["quick", "balanced", "detailed"]


def test_gating(monkeypatch):
    monkeypatch.setattr(
        S.StableDiffusion15Engine,
        "descriptor",
        S.StableDiffusion15Engine.descriptor.model_copy(update={"artifacts": []}),
    )
    unpinned = S.StableDiffusion15Engine().check_compatibility(MAC)
    assert not unpinned.compatible and "pin_engine.py sd15" in unpinned.reasons[0]
    assert not S.StableDiffusion15Engine().check_compatibility(LINUX).compatible


def test_manifest_is_fp16_safetensors_without_safety_checker():
    manifest = json.loads(S.MANIFEST_PATH.read_text())
    (source,) = manifest["sources"]
    assert source["repo"] == S.REPO_ID and re.fullmatch(r"[0-9a-f]{40}", source["revision"])
    paths = [f["path"] for f in source["files"]]
    weights = [p for p in paths if p.endswith((".safetensors", ".ckpt", ".bin", ".pt"))]
    assert weights and all(p.endswith(".fp16.safetensors") for p in weights)
    assert not any(p.startswith(("safety_checker/", "feature_extractor/")) for p in paths)
    for folder in ("unet", "vae", "text_encoder"):
        assert f"{folder}/config.json" in paths


def test_verify(tmp_path):
    engine = S.StableDiffusion15Engine()
    model = tmp_path / "model"
    for a in engine.descriptor.artifacts[:-1]:
        (model / a.path).parent.mkdir(parents=True, exist_ok=True)
        (model / a.path).write_bytes(b"x")
    assert not engine.verify(InstallContext(model, tmp_path))
    last = engine.descriptor.artifacts[-1]
    (model / last.path).parent.mkdir(parents=True, exist_ok=True)
    (model / last.path).write_bytes(b"x")
    assert engine.verify(InstallContext(model, tmp_path))


def test_lockfile_is_hashed_and_pins_the_stack():
    lock = resolve_lockfile(EngineRegistry().get(S.ENGINE_ID)).read_text()
    reqs = [ln for ln in lock.splitlines() if ln and not ln.startswith((" ", "#"))]
    for req in reqs:
        block = lock[lock.index(req) :].split("\n")
        assert any("--hash=sha256:" in ln for ln in block[:3]), req
    for pin in (r"diffusers==0\.43\.0", r"torch==2\.11\.0", r"transformers==5\.", r"pydantic==2\."):
        assert re.search(rf"^{pin}", lock, re.M), pin


def test_listed_in_models_api(settings, monkeypatch):
    set_host(monkeypatch, apple_silicon=True)
    set_pinned(monkeypatch, S.StableDiffusion15Engine, pinned=True)
    with make_client(settings) as client:
        sd = client.get(f"/api/v1/models/{S.ENGINE_ID}").json()
        assert sd["modality"] == "image" and sd["needs_license_confirmation"] is True
        assert sd["compatibility"]["compatible"] is True
        r = client.post(f"/api/v1/models/{S.ENGINE_ID}/install", json={})
        assert r.status_code == 409 and r.json()["error"]["code"] == "license_confirmation_required"


# ---- input validation (runs in the API process: no torch) ----


def test_validation():
    e = S.StableDiffusion15Engine()
    with pytest.raises(EngineInputError, match="Describe"):
        e.validate_input(S.TASK, {"prompt": "   "})
    with pytest.raises(EngineInputError, match="under"):
        e.validate_input(S.TASK, {"prompt": "x" * 1001})
    with pytest.raises(EngineInputError, match="Steps must be between"):
        e.validate_input(S.TASK, {"prompt": "x", "steps": 5})
    with pytest.raises(EngineInputError, match="Guidance must be between"):
        e.validate_input(S.TASK, {"prompt": "x", "guidance": 30})
    with pytest.raises(EngineInputError, match="seed"):
        e.validate_input(S.TASK, {"prompt": "x", "seed": -1})
    with pytest.raises(EngineInputError, match="cannot do"):
        e.validate_input("audio.narrate", {"prompt": "x"})
    ok = e.validate_input(S.TASK, {"prompt": "  a  red\n kite ", "seed": "42"})
    assert ok == {"prompt": "a red kite", "negative_prompt": "", "steps": 25,
                  "guidance": 7.5, "seed": 42}  # fmt: skip


def test_seed_is_chosen_once_and_stored():
    e = S.StableDiffusion15Engine()
    a = e.validate_input(S.TASK, {"prompt": "x"})["seed"]
    b = e.validate_input(S.TASK, {"prompt": "x"})["seed"]
    assert 0 <= a <= S.MAX_SEED and a != b  # random, but fixed in the job's inputs


def test_presets_set_steps():
    d = S.StableDiffusion15Engine.descriptor
    assert apply_preset(d, {"prompt": "x", "preset": "quick", "steps": 40})["steps"] == 15


# ---- runtime ----


def test_loads_local_fp16_files_on_mps_without_safety_checker(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    ((path, kw),) = rt.loads
    assert path == str(tmp_path / "model")
    assert kw["local_files_only"] is True and kw["use_safetensors"] is True
    assert kw["variant"] == "fp16" and kw["torch_dtype"] == "torch.float16"
    assert kw["safety_checker"] is None and kw["requires_safety_checker"] is False
    assert rt.moved_to == ["mps"] and rt.sliced
    assert engine.device == "mps" and engine.dtype == "float16"
    assert engine._pipe.scheduler == "dpm++2m"


def test_cpu_uses_float32(fake, tmp_path, monkeypatch):
    rt = fake()
    monkeypatch.setenv("RASVA_TORCH_DEVICE", "cpu")
    engine = loaded(tmp_path)
    assert rt.loads[0][1]["torch_dtype"] == "torch.float32" and engine.device == "cpu"


def test_mps_failure_falls_back_to_cpu_and_logs_why(fake, tmp_path, capsys):
    rt = fake(fail_device="mps")
    engine = loaded(tmp_path)
    assert engine.device == "cpu" and rt.moved_to == ["cpu"]
    assert "unsupported op on this device" in capsys.readouterr().err


def test_generates_png_with_seed_progress_and_metadata(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    result, out, progress = run(engine, tmp_path, seed=7, steps=12, negative_prompt="blurry")
    (call,) = rt.calls
    assert call["prompt"] == "a lighthouse at dusk" and call["negative_prompt"] == "blurry"
    assert call["num_inference_steps"] == 12 and call["height"] == call["width"] == 512
    assert rt.seeds == [7]
    assert (out / "image.png").is_file() and rt.saved == [((512, 512, 3), "uint8")]
    assert progress[0] == (0.0, "Starting") and progress[-1] == (1.0, "Saving image")
    assert ("Step 12 of 12") in [d for _, d in progress]
    (output,) = result.outputs
    assert output.media_type == "image/png"
    meta = output.metadata
    assert meta["synthetic"] and meta["seed"] == 7 and meta["safety_checker"] is False
    assert meta["model_revision"] and meta["width"] == 512
    assert "prompt" not in meta  # the prompt stays in the job inputs, not copied around
    m = result.metrics
    assert m["steps"] == 12 and m["device"] == "mps" and m["gpu_peak_bytes"] == 3 * 1024**3
    assert m["truncated_prompt"] is False


def test_same_seed_same_noise(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    run(engine, tmp_path, seed=99)
    run(engine, tmp_path, seed=99)
    assert rt.seeds == [99, 99]


def test_long_prompt_is_flagged_as_truncated(fake, tmp_path):
    fake()
    result, *_ = run(loaded(tmp_path), tmp_path, prompt="word " * 100)
    assert result.metrics["truncated_prompt"] is True


@pytest.mark.parametrize("bad", [np.zeros((512, 512, 3)), np.full((512, 512, 3), np.nan)])
def test_blank_or_nan_image_is_an_error_not_a_file(fake, tmp_path, bad):
    rt = fake(image=bad.astype(np.float32))
    with pytest.raises(EngineError) as exc:
        run(loaded(tmp_path), tmp_path)
    assert exc.value.code == "blank_image" and not rt.saved


def test_cancel_stops_between_steps(fake, tmp_path):
    rt = fake()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Canceled):
        run(loaded(tmp_path), tmp_path, cancel=cancel)
    assert not rt.saved
