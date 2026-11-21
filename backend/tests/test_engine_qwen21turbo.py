"""Qwen-Image 2.1 Turbo adapter with fake mlx, mflux and PIL modules: contract,
prepare (Turbo config, 4-bit, atomic), one class for both tasks, sizes, references,
RGBA flattening, progress and cancel."""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

from rasva.engines import qwen21turbo as Q
from rasva.engines.contract import (
    Canceled,
    EngineError,
    EngineInputError,
    InstallContext,
    RunContext,
    RuntimeContext,
    SystemFacts,
    prepare_owner,
)
from rasva.engines.registry import EngineRegistry
from rasva.services.installer import resolve_lockfile

MAC = SystemFacts("darwin", "arm64", 16 * 1024**3, True, True, True)
LINUX = SystemFacts("linux", "x86_64", 16 * 1024**3, False, False, False)
TURBO = object()  # stands in for ModelConfig.qwen_image_21_turbo()


class FakeRuntime:
    def __init__(self, mode="RGBA", pixels=None, photo_size=(600, 900)):
        rt = self
        self.built: list[dict] = []
        self.saved_to: list[str] = []
        self.calls: list[dict] = []
        self.pixels = np.full((8, 8, 3), 128, dtype=np.uint8) if pixels is None else pixels

        class Img:
            def __init__(self, arr, mode="RGB", size=(8, 8)):
                self.arr, self.mode, self.size = arr, mode, size
                self.width, self.height = size

            def convert(self, mode):
                rt.converted = mode
                return Img(self.arr, mode, self.size)

            def getchannel(self, name):
                return "alpha"

            def paste(self, img, mask=None):
                rt.pasted_mask = mask

            def __array__(self, dtype=None, copy=None):
                return self.arr.astype(dtype) if dtype else self.arr

            def thumbnail(self, size):
                rt.thumb = size

            def save(self, path, format=None):
                Path(path).write_bytes(b"\x89PNG fake")
                rt.saved_png = str(path)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Callbacks:
            def __init__(self):
                self.items = []

            def register(self, cb):
                self.items.append(cb)

        class Edit:
            def __init__(self, model_path=None, quantize=None, model_config=None):
                rt.built.append({"path": model_path, "quantize": quantize, "config": model_config})
                self.callbacks = Callbacks()

            def generate_image(self, **kw):
                rt.calls.append(kw)
                for t in range(kw["num_inference_steps"]):
                    for cb in self.callbacks.items:
                        cb.call_in_loop(t, kw["seed"], kw["prompt"], None, None, None)
                return types.SimpleNamespace(image=Img(rt.pixels, mode))

            def save_model(self, base_path):
                rt.saved_to.append(base_path)
                Path(base_path, "transformer").mkdir(parents=True)
                Path(base_path, "transformer", "0.safetensors").write_bytes(b"q4")

        edit_mod = types.ModuleType("mflux.models.qwen21.variants.edit.qwen_image_21_edit")
        edit_mod.QwenImage21Edit = Edit
        config_mod = types.ModuleType("mflux.models.common.config")
        config_mod.ModelConfig = types.SimpleNamespace(qwen_image_21_turbo=lambda: TURBO)
        mx = types.ModuleType("mlx.core")
        mx.reset_peak_memory = lambda: setattr(rt, "reset", True)
        mx.get_peak_memory = lambda: 9 * 1024**3
        mx.set_cache_limit = lambda n: setattr(rt, "cache_limit", n)
        mlx = types.ModuleType("mlx")
        mlx.core = mx
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(
            open=lambda p: Img(rt.pixels, "RGB", photo_size),
            new=lambda mode, size, color: Img(rt.pixels, mode, size),
            LANCZOS=1,
        )
        pil.ImageOps = types.SimpleNamespace(exif_transpose=lambda img: img)
        names = [
            "mflux", "mflux.models", "mflux.models.common", "mflux.models.qwen21",
            "mflux.models.qwen21.variants", "mflux.models.qwen21.variants.edit",
        ]  # fmt: skip
        self.modules = {n: types.ModuleType(n) for n in names}
        self.modules.update({
            "mflux.models.common.config": config_mod,
            "mflux.models.qwen21.variants.edit.qwen_image_21_edit": edit_mod,
            "mlx": mlx, "mlx.core": mx, "PIL": pil,
        })  # fmt: skip


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        rt = FakeRuntime(**kw)
        for name, mod in rt.modules.items():
            monkeypatch.setitem(sys.modules, name, mod)
        return rt

    return install


def prepared_dir(tmp_path):
    d = tmp_path / "model"
    (d / Q.Q_DIR).mkdir(parents=True)
    (d / Q.Q_DIR / Q.MARKER).write_text(json.dumps({"bits": Q.BITS}))
    return d


def photo(tmp_path, name="me.jpg"):
    p = tmp_path / name
    p.write_bytes(b"jpeg")
    return str(p)


def run(engine, tmp_path, task="image.generate", cancel=None, **inputs):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext(
        "job_x", out, lambda f, d: progress.append((f, d)), cancel or threading.Event()
    )
    clean = engine.validate_input(task, {"prompt": "a red kite over hills", **inputs})
    return engine.run(task, clean, ctx), out, progress


def loaded(tmp_path):
    engine = Q.Qwen21TurboEngine()
    engine.load(RuntimeContext(prepared_dir(tmp_path), tmp_path / "cache"))
    return engine


# ---- descriptor, gating, manifest, lockfile ----


def test_registered_as_restricted_image_engine():
    d = EngineRegistry().get(Q.ENGINE_ID).descriptor
    assert d.modality == "image" and set(d.tasks) == {"image.generate", "image.edit"}
    assert d.license_class == "restricted"  # Qwen Research License: the installer asks
    assert "research or evaluation" in d.license
    assert "pose" not in d.features and "outfit" not in d.features  # those stay on FLUX
    assert "negative_prompt" not in d.features  # Turbo runs without CFG
    assert d.input_ranges == {"steps": [1, 8, 8]}
    assert d.approx_peak_memory_bytes + 1024**3 < 16 * 1024**3  # the memory guard can start it


def test_manifest_pins_the_turbo_repo():
    manifest = json.loads(Q.MANIFEST_PATH.read_text())
    (source,) = manifest["sources"]
    assert source["repo"] == "Qwen/Qwen-Image-2.1-Turbo" and len(source["revision"]) == 40
    paths = {f["path"] for f in source["files"]}
    assert "text_encoder/model.safetensors" in paths and "LICENSE" in paths
    assert not any(p.startswith("assets/") for p in paths)
    assert all(len(f["sha256"]) == 64 for f in source["files"])


def test_lockfile_is_hash_pinned_mflux():
    lock = resolve_lockfile(EngineRegistry().get(Q.ENGINE_ID)).read_text()
    assert "mflux==0.24.0" in lock and "--hash=sha256:" in lock


def test_compatibility():
    assert Q.Qwen21TurboEngine().check_compatibility(MAC).compatible
    assert not Q.Qwen21TurboEngine().check_compatibility(LINUX).compatible


# ---- prepare and load ----


def test_prepare_uses_turbo_config_at_4_bit(fake, tmp_path):
    rt = fake()
    model = tmp_path / "model"
    model.mkdir()
    assert prepare_owner(Q.Qwen21TurboEngine) is Q.Qwen21TurboEngine
    Q.Qwen21TurboEngine().prepare(InstallContext(model, tmp_path / "cache"))
    assert rt.built == [{"path": str(model), "quantize": 4, "config": TURBO}]
    assert rt.saved_to == [str(model / f".{Q.Q_DIR}.partial")]  # written aside, then renamed
    info = json.loads((model / Q.Q_DIR / Q.MARKER).read_text())
    assert info["bits"] == 4 and info["source_repo"] == Q.REPO_ID
    Q.Qwen21TurboEngine().prepare(InstallContext(model, tmp_path / "cache"))
    assert len(rt.built) == 1  # idempotent


def test_load_refuses_unprepared(fake, tmp_path):
    fake()
    with pytest.raises(EngineError) as e:
        Q.Qwen21TurboEngine().load(RuntimeContext(tmp_path, tmp_path / "cache"))
    assert e.value.code == "not_prepared"


def test_one_class_for_both_tasks(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    assert rt.built == [
        {"path": str(tmp_path / "model" / Q.Q_DIR), "quantize": None, "config": TURBO}
    ]
    assert rt.cache_limit == 1024**3
    run(engine, tmp_path)
    run(
        engine,
        tmp_path,
        "image.edit",
        references=[{"asset_id": "a", "role": "person", "path": photo(tmp_path)}],
    )
    assert len(rt.built) == 1  # no reload when switching task


# ---- validation ----


def test_validation(fake, tmp_path):
    engine = Q.Qwen21TurboEngine()
    out = engine.validate_input("image.generate", {"prompt": "  a  cup "})
    assert out["prompt"] == "a cup" and out["steps"] == 8
    assert (out["width"], out["height"]) == (1024, 1024) and isinstance(out["seed"], int)
    for bad in (
        {"steps": 9}, {"steps": 0}, {"width": 1000}, {"width": 512}, {"height": 1600},
        {"seed": -1}, {"prompt": ""}, {"prompt": "x" * 2001},
    ):  # fmt: skip
        with pytest.raises(EngineInputError):
            engine.validate_input("image.generate", {"prompt": "a cup", **bad})
    with pytest.raises(EngineInputError):
        engine.validate_input("image.generate", {"prompt": "a cup", "references": [{"path": "x"}]})
    with pytest.raises(EngineInputError):
        engine.validate_input("image.edit", {"prompt": "make it night"})
    edit = engine.validate_input("image.edit", {"prompt": "night", "references": [
        {"asset_id": "b", "role": "background", "path": photo(tmp_path, "b.jpg")},
        {"asset_id": "p", "role": "person", "path": photo(tmp_path)},
    ]})  # fmt: skip
    assert [r["role"] for r in edit["references"]] == ["person", "background"]
    assert "width" not in edit  # sized from the main photo at run time


def test_fit_size_keeps_shape_in_multiples_of_32():
    assert Q.fit_size(1000, 1000) == (1024, 1024)
    w, h = Q.fit_size(600, 900)
    assert w % 32 == 0 and h % 32 == 0 and h > w
    assert Q.fit_size(4000, 100) == (1536, 768)  # clamped into range


# ---- run ----


def test_text_to_image_run(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    result, out, progress = run(engine, tmp_path, seed=7, steps=4, width=1152, height=896)
    (call,) = rt.calls
    assert call["image_paths"] is None and call["guidance"] == 1.0
    assert (call["width"], call["height"], call["num_inference_steps"]) == (1152, 896, 4)
    assert call["enhance_prompt"] is False and call["verify"] is False  # no extra text generation
    assert rt.pasted_mask == "alpha"  # RGBA flattened onto white
    assert (out / "image.png").is_file()
    meta = result.outputs[0].metadata
    assert meta["seed"] == 7 and meta["quantization"] == 4 and meta["synthetic"] is True
    assert "prompt" not in meta
    assert result.metrics["gpu_peak_bytes"] == 9 * 1024**3
    assert [d for _, d in progress][1:5] == [f"Step {i} of 4" for i in range(1, 5)]


def test_edit_run_sizes_from_main_photo_and_cleans_up(fake, tmp_path):
    rt = fake(photo_size=(600, 900))
    engine = loaded(tmp_path)
    refs = [{"asset_id": "a", "role": "person", "path": photo(tmp_path)}]
    result, out, _ = run(engine, tmp_path, "image.edit", references=refs)
    (call,) = rt.calls
    assert len(call["image_paths"]) == 1 and (call["width"], call["height"]) == Q.fit_size(600, 900)
    assert not (out / ".references").exists()
    assert result.outputs[0].metadata["source_asset_ids"] == ["a"]


def test_blank_image_fails(fake, tmp_path):
    fake(mode="RGB", pixels=np.zeros((8, 8, 3), dtype=np.uint8))
    with pytest.raises(EngineError) as e:
        run(loaded(tmp_path), tmp_path)
    assert e.value.code == "blank_image"


def test_cancel_stops_between_steps(fake, tmp_path):
    fake()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Canceled):
        run(loaded(tmp_path), tmp_path, cancel=cancel)
