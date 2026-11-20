"""FLUX.2 Klein adapter (I1) with fake mlx, mflux and PIL modules: contract,
prepare (quantise once, atomically), local loading, references, progress, cancel."""

from __future__ import annotations

import json
import re
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

from rasva.engines import flux2klein as K
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


class FakeRuntime:
    def __init__(self, pixels=None):
        rt = self
        self.built: list[tuple[str, str, int | None]] = []
        self.saved_to: list[str] = []
        self.calls: list[dict] = []
        self.opened: list[str] = []
        self.pixels = np.full((8, 8, 3), 128, dtype=np.uint8) if pixels is None else pixels

        class Img:
            def __init__(self, arr, size=(8, 8)):
                self.arr, self.width, self.height = arr, size[0], size[1]

            def convert(self, mode):
                return self

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
            def __init__(self, model_path=None, quantize=None):
                rt.built.append(("edit", model_path, quantize))
                self.callbacks = Callbacks()

            def generate_image(self, **kw):
                rt.calls.append(kw)
                for t in range(kw["num_inference_steps"]):
                    for cb in self.callbacks.items:
                        cb.call_in_loop(t, kw["seed"], kw["prompt"], None, None, None)
                return types.SimpleNamespace(image=Img(rt.pixels))

        class Klein:
            def __init__(self, model_path=None, quantize=None):
                rt.built.append(("klein", model_path, quantize))

            def save_model(self, base_path):
                rt.saved_to.append(base_path)
                Path(base_path).mkdir(parents=True)
                (Path(base_path) / "transformer").mkdir()
                (Path(base_path) / "transformer" / "0.safetensors").write_bytes(b"q4")

        variants = types.ModuleType("mflux.models.flux2.variants")
        variants.Flux2Klein, variants.Flux2KleinEdit = Klein, Edit
        mx = types.ModuleType("mlx.core")
        mx.reset_peak_memory = lambda: setattr(rt, "reset", True)
        mx.get_peak_memory = lambda: 5 * 1024**3
        mlx = types.ModuleType("mlx")
        mlx.core = mx
        pil = types.ModuleType("PIL")

        def open_(path):
            rt.opened.append(str(path))
            return Img(rt.pixels)

        pil.Image = types.SimpleNamespace(open=open_)
        pil.ImageOps = types.SimpleNamespace(exif_transpose=lambda img: img)
        self.modules = {
            "mflux": types.ModuleType("mflux"), "mflux.models": types.ModuleType("mflux.models"),
            "mflux.models.flux2": types.ModuleType("mflux.models.flux2"),
            "mflux.models.flux2.variants": variants, "mlx": mlx, "mlx.core": mx, "PIL": pil,
        }  # fmt: skip


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
    (d / K.Q_DIR).mkdir(parents=True)
    (d / K.Q_DIR / K.MARKER).write_text(json.dumps({"bits": K.BITS}))
    return d


def photo(tmp_path, name="me.jpg"):
    p = tmp_path / name
    p.write_bytes(b"jpeg")
    return str(p)


def run(engine, tmp_path, task="image.generate", cancel=None, **inputs):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    report = lambda f, d: progress.append((f, d))  # noqa: E731
    ctx = RunContext("job_x", out, report, cancel or threading.Event())
    clean = engine.validate_input(task, {"prompt": "a red kite over hills", **inputs})
    return engine.run(task, clean, ctx), out, progress


def loaded(tmp_path):
    engine = K.Flux2KleinEngine()
    engine.load(RuntimeContext(prepared_dir(tmp_path), tmp_path / "cache"))
    return engine


# ---- descriptor, gating, manifest, lockfile ----


def test_registered_for_generate_and_edit():
    d = EngineRegistry().get(K.ENGINE_ID).descriptor
    assert d.modality == "image" and d.tasks == ["image.generate", "image.edit"]
    assert d.license_class == "permissive" and "Apache-2.0" in d.license
    assert any("without a content filter" in n for n in d.license_notes)
    assert [p.id for p in d.presets] == ["draft", "standard", "large"]
    assert prepare_owner(K.Flux2KleinEngine) is K.Flux2KleinEngine


def test_gating():
    assert K.Flux2KleinEngine().check_compatibility(MAC).compatible
    assert not K.Flux2KleinEngine().check_compatibility(LINUX).compatible


def test_manifest_is_the_official_bf16_files_without_duplicates():
    manifest = json.loads(K.MANIFEST_PATH.read_text())
    (source,) = manifest["sources"]
    assert source["repo"] == "black-forest-labs/FLUX.2-klein-4B"
    assert re.fullmatch(r"[0-9a-f]{40}", source["revision"])
    paths = [f["path"] for f in source["files"]]
    assert "flux-2-klein-4b.safetensors" not in paths  # 7.75 GB single-file duplicate
    assert not any(p.endswith((".jpg", ".ckpt", ".bin", ".pt")) for p in paths)
    for part in ("transformer", "text_encoder", "vae", "tokenizer"):
        assert any(p.startswith(f"{part}/") for p in paths), part


def test_lockfile_is_hashed_and_pins_mflux():
    lock = resolve_lockfile(EngineRegistry().get(K.ENGINE_ID)).read_text()
    reqs = [ln for ln in lock.splitlines() if ln and not ln.startswith((" ", "#"))]
    for req in reqs:
        block = lock[lock.index(req) :].split("\n")
        assert any("--hash=sha256:" in ln for ln in block[:3]), req
    assert re.search(r"^mflux==0\.24\.0 ", lock, re.M) and re.search(r"^mlx==", lock, re.M)


# ---- prepare and verify ----


def test_prepare_quantises_once_into_q4(fake, tmp_path):
    rt = fake()
    model = tmp_path / "model"
    model.mkdir()
    engine = K.Flux2KleinEngine()
    engine.prepare(InstallContext(model, tmp_path))
    assert rt.built == [("klein", str(model), 4)]
    assert rt.saved_to == [str(model / f".{K.Q_DIR}.partial")]  # written aside, then renamed
    assert not (model / f".{K.Q_DIR}.partial").exists()
    info = json.loads((model / K.Q_DIR / K.MARKER).read_text())
    assert info["bits"] == 4 and info["source_repo"] == K.REPO_ID
    engine.prepare(InstallContext(model, tmp_path))  # idempotent
    assert len(rt.built) == 1


def test_verify_needs_downloads_and_the_prepared_copy(tmp_path):
    engine = K.Flux2KleinEngine()
    model = tmp_path / "model"
    for a in engine.descriptor.artifacts:
        (model / a.path).parent.mkdir(parents=True, exist_ok=True)
        (model / a.path).write_bytes(b"x")
    assert not engine.verify(InstallContext(model, tmp_path))
    (model / K.Q_DIR).mkdir()
    (model / K.Q_DIR / K.MARKER).write_text(json.dumps({"bits": 8}))
    assert not engine.verify(InstallContext(model, tmp_path))  # wrong bits
    (model / K.Q_DIR / K.MARKER).write_text(json.dumps({"bits": 4}))
    assert engine.verify(InstallContext(model, tmp_path))


# ---- validation ----


def test_validation(tmp_path):
    e = K.Flux2KleinEngine()
    with pytest.raises(EngineInputError, match="Describe the picture"):
        e.validate_input("image.generate", {"prompt": " "})
    with pytest.raises(EngineInputError, match="Describe the change"):
        e.validate_input("image.edit", {"prompt": ""})
    with pytest.raises(EngineInputError, match="multiples of 16"):
        e.validate_input("image.generate", {"prompt": "x", "width": 500})
    with pytest.raises(EngineInputError, match="multiples of 16"):
        e.validate_input("image.generate", {"prompt": "x", "height": 2048})
    with pytest.raises(EngineInputError, match="Steps must be between"):
        e.validate_input("image.generate", {"prompt": "x", "steps": 20})
    with pytest.raises(EngineInputError, match="edit instead"):
        e.validate_input("image.generate", {"prompt": "x", "references": [{"path": "p"}]})
    with pytest.raises(EngineInputError, match="at least one photo"):
        e.validate_input("image.edit", {"prompt": "x"})
    with pytest.raises(EngineInputError, match="missing"):
        e.validate_input("image.edit", {"prompt": "x", "references": [{"path": "/nope"}]})
    ok = e.validate_input("image.generate", {"prompt": "  a  kite ", "seed": "3"})
    assert ok == {"prompt": "a kite", "steps": 4, "guidance": 1.0,
                  "width": 768, "height": 768, "seed": 3}  # fmt: skip


def test_references_are_ordered_by_role(tmp_path):
    refs = [
        {"asset_id": "g", "role": "garment", "path": photo(tmp_path, "g.jpg")},
        {"asset_id": "b", "role": "background", "path": photo(tmp_path, "b.jpg")},
        {"asset_id": "p", "role": "person", "path": photo(tmp_path, "p.jpg")},
    ]
    ok = K.Flux2KleinEngine().validate_input("image.edit", {"prompt": "x", "references": refs})
    assert [r["asset_id"] for r in ok["references"]] == ["p", "g", "b"]


# ---- runtime ----


def test_load_requires_the_prepared_copy(fake, tmp_path):
    fake()
    with pytest.raises(EngineError) as exc:
        K.Flux2KleinEngine().load(RuntimeContext(tmp_path / "empty", tmp_path))
    assert exc.value.code == "not_prepared"


def test_generates_with_progress_seed_and_clean_png(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    assert rt.built == [("edit", str(tmp_path / "model" / K.Q_DIR), None)]
    result, out, progress = run(engine, tmp_path, seed=11, width=512, height=512)
    (call,) = rt.calls
    assert call["seed"] == 11 and call["image_paths"] is None
    assert call["num_inference_steps"] == 4 and (call["width"], call["height"]) == (512, 512)
    assert (out / "image.png").is_file() and rt.reset
    assert [d for _, d in progress][1:5] == [f"Step {k} of 4" for k in range(1, 5)]
    assert progress[-1] == (1.0, "Saving image")
    meta = result.outputs[0].metadata
    assert meta["synthetic"] and meta["content_filter"] is False and meta["quantization"] == 4
    assert "prompt" not in meta
    assert result.metrics["gpu_peak_bytes"] == 5 * 1024**3


def test_edit_passes_prepared_references_in_role_order(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    refs = [
        {"asset_id": "g", "role": "garment", "path": photo(tmp_path, "g.jpg")},
        {"asset_id": "p", "role": "person", "path": photo(tmp_path, "p.jpg")},
    ]
    result, out, _ = run(engine, tmp_path, task="image.edit", references=refs)
    assert rt.opened == [refs[1]["path"], refs[0]["path"]]  # person first
    assert rt.thumb == (K.REFERENCE_MAX_SIDE, K.REFERENCE_MAX_SIDE)
    assert len(rt.calls[0]["image_paths"]) == 2
    assert not (out / ".references").exists()  # temporary copies removed
    assert result.outputs[0].metadata["source_asset_ids"] == ["p", "g"]


def test_blank_image_is_an_error(fake, tmp_path):
    fake(pixels=np.zeros((8, 8, 3), dtype=np.uint8))
    with pytest.raises(EngineError) as exc:
        run(loaded(tmp_path), tmp_path)
    assert exc.value.code == "blank_image"


def test_cancel_stops_between_steps(fake, tmp_path):
    fake()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Canceled):
        run(loaded(tmp_path), tmp_path, cancel=cancel)
