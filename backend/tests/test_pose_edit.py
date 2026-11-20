"""Pose change on the I1 editing engine (rasva.engines.pose.edit) with a fake
Flux2KleinPipeline: validation, reference order and sizes per identity setting,
prompt wording, LoRA switching, seeds, progress, cancel, blank guard, and the
full API path with a test-only editor."""

from __future__ import annotations

import threading
import types

import numpy as np
import pytest

from rasva.engines.contract import Canceled, EngineError, EngineInputError, RunContext
from rasva.engines.pose import edit, skeleton
from tests.conftest import make_client, wait_for
from tests.test_image_refs import PNG, new_project, upload

EDITOR = "tests.fake_editor:FakeEditor"
POSE = "tests.fake_pose:FakePoseFinder"


def refs(tmp_path, figure=True, background=False):
    out = []
    roles = ["person", "pose"] + (["background"] if background else [])
    for role in roles:
        path = tmp_path / f"{role}.png"
        path.write_bytes(PNG)
        out.append({"asset_id": f"ast_{role}", "role": role, "path": str(path),
                    **({"pose_figure": True} if role == "pose" and figure else {})})  # fmt: skip
    return out


class FakeTorch:
    def __init__(self):
        self.seeds = []
        rt = self

        class Generator:
            def __init__(self, device):
                assert device == "cpu"

            def manual_seed(self, seed):
                rt.seeds.append(seed)
                return self

        self.Generator = Generator
        self.mps = types.SimpleNamespace(driver_allocated_memory=lambda: 5 * 1024**3)


class FakePipe:
    def __init__(self, image=None):
        self.calls, self.adapters = [], []
        self.image = image

    def set_adapters(self, names, adapter_weights):
        self.adapters.append((names, adapter_weights))

    def __call__(self, **kw):
        self.calls.append(kw)
        for i in range(kw["num_inference_steps"]):
            kw["callback_on_step_end"](self, i, i, {})
        img = self.image
        if img is None:
            img = np.full((kw["height"], kw["width"], 3), 0.5, np.float32)
        return types.SimpleNamespace(images=[img])


def go(tmp_path, inputs, pipe=None, cancel=None, lora=False, device="mps"):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext(
        "job_e", out, lambda f, d: progress.append((f, d)), cancel or threading.Event()
    )
    pipe = pipe or FakePipe()
    torch = FakeTorch()
    loads = []
    p = edit.plan(inputs, lora_available=lora, pose_size=(512, 768))
    result = edit.run(
        pipe, p, ctx, torch=torch, device=device, model_revision="abc",
        load_image=lambda path, side: loads.append((path, side)) or path, lora_loaded=lora,
    )  # fmt: skip
    return result, pipe, torch, loads, progress, out


# ---- validation ----


def test_is_pose_edit():
    assert edit.is_pose_edit({"references": [{"role": "person"}, {"role": "pose"}]})
    assert not edit.is_pose_edit({"references": [{"role": "person"}, {"role": "garment"}]})
    assert not edit.is_pose_edit({})


def test_validation(tmp_path):
    good = refs(tmp_path)
    with pytest.raises(EngineInputError, match="person"):
        edit.validate({"references": good[1:]})
    with pytest.raises(EngineInputError, match="one pose"):
        edit.validate({"references": good[:1] + [good[1], good[1]]})
    with pytest.raises(EngineInputError, match="optional background"):
        edit.validate({"references": [*good, {**good[0], "role": "garment"}]})
    with pytest.raises(EngineInputError, match="missing"):
        edit.validate({"references": [good[0], {**good[1], "path": str(tmp_path / "x.png")}]})
    with pytest.raises(EngineInputError, match="face"):
        edit.validate({"references": good, "identity": "max"})
    with pytest.raises(EngineInputError, match="Steps"):
        edit.validate({"references": good, "steps": 50})
    with pytest.raises(EngineInputError, match="under"):
        edit.validate({"references": good, "prompt": "x" * 501})
    ok = edit.validate({"references": good, "seed": "9", "prompt": "  in a  red sari "})
    assert ok["identity"] == "balanced" and ok["pose_source"] == "figure"
    assert ok["steps"] == 4 and ok["guidance"] == 1.0 and ok["seed"] == 9
    assert ok["prompt"] == "in a red sari"
    # A pose photo (not a figure) defaults to "photo"; the user may override.
    photo = refs(tmp_path, figure=False)
    assert edit.validate({"references": photo})["pose_source"] == "photo"


def test_seed_is_chosen_once_and_stored(tmp_path):
    a = edit.validate({"references": refs(tmp_path)})["seed"]
    b = edit.validate({"references": refs(tmp_path)})["seed"]
    assert 0 <= a <= edit.MAX_SEED and a != b


# ---- plan ----


@pytest.mark.parametrize("identity", list(edit.IDENTITY))
def test_identity_settings_change_what_the_model_sees(tmp_path, identity):
    inputs = edit.validate({"references": refs(tmp_path), "identity": identity, "seed": 1})
    p = edit.plan(inputs)
    ident = edit.IDENTITY[identity]
    sizes = {i.role: i.max_side for i in p.images}
    assert sizes == {"person": ident.person_side, "pose": ident.pose_side}
    assert ident.emphasis in p.prompt


def test_identity_levers_are_ordered():
    keep, bal, pose = (edit.IDENTITY[k] for k in ("keep_face", "balanced", "follow_pose"))
    assert keep.person_side >= bal.person_side >= pose.person_side
    assert keep.pose_side <= bal.pose_side <= pose.pose_side
    assert keep.lora_scale < bal.lora_scale < pose.lora_scale


def test_figure_prompt_without_lora(tmp_path):
    p = edit.plan(edit.validate({"references": refs(tmp_path, background=True), "seed": 1}))
    assert [i.role for i in p.images] == ["person", "pose", "background"]
    assert "stick-figure" in p.prompt and "Do not draw the stick figure" in p.prompt
    assert "setting from image 3" in p.prompt and p.lora_scale is None
    assert p.source_asset_ids == ["ast_person", "ast_pose", "ast_background"]


def test_photo_pose_prompt_does_not_copy_the_other_person(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path, figure=False), "seed": 1})
    p = edit.plan(inputs, lora_available=True)
    assert "Do not copy the face" in p.prompt and p.lora_scale is None  # LoRA is for skeletons


def test_lora_uses_its_own_order_and_trigger(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path), "identity": "follow_pose", "seed": 1})
    p = edit.plan(inputs, lora_available=True)
    assert [i.role for i in p.images] == ["pose", "person"]
    assert p.prompt.startswith("refcontrol apply pose from image 1 with reference from image 2")
    assert p.lora_scale == 1.0


def test_output_size_follows_the_pose_aspect():
    assert edit.output_size((512, 768)) == (512, 768)
    assert edit.output_size((768, 512)) == (768, 512)
    assert edit.output_size(None) == (512, 768)
    w, h = edit.output_size((1000, 1333))
    assert w % 16 == 0 and h % 16 == 0 and h == 768


def test_png_size_without_pillow(tmp_path):
    path = tmp_path / "f.png"
    path.write_bytes(skeleton.encode_png(np.zeros((30, 20, 3), np.uint8)))
    assert edit.image_size(str(path)) == (20, 30)
    (tmp_path / "j.jpg").write_bytes(b"\xff\xd8\xff")
    assert edit.image_size(str(tmp_path / "j.jpg")) is None


# ---- run ----


def test_run_calls_the_pipeline_with_references_seed_and_progress(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path), "seed": 42, "steps": 5,
                            "prompt": "smiling"})  # fmt: skip
    result, pipe, torch, loads, progress, out = go(tmp_path, inputs)
    (call,) = pipe.calls
    assert call["image"] == [r["path"] for r in inputs["references"]]
    assert [side for _, side in loads] == [1024, 768]  # balanced
    assert (call["width"], call["height"]) == (512, 768)
    assert call["num_inference_steps"] == 5 and call["guidance_scale"] == 1.0
    assert call["output_type"] == "np" and call["prompt"].endswith("smiling")
    assert torch.seeds == [42]
    assert progress[0] == (0.0, "Preparing the photos") and progress[-1] == (1.0, "Saving image")
    assert "Step 5 of 5" in [d for _, d in progress]
    assert (out / "image.png").read_bytes().startswith(b"\x89PNG")
    meta = result.outputs[0].metadata
    assert meta["synthetic"] and meta["seed"] == 42 and meta["model_revision"] == "abc"
    assert meta["source_asset_ids"] == ["ast_person", "ast_pose"] and meta["edit"] == "pose"
    assert meta["reference_roles"] == ["person", "pose"] and (meta["width"], meta["height"]) == (
        512,
        768,
    )
    assert (
        "prompt" not in meta and "smiling" not in str(meta) and "smiling" not in str(result.metrics)
    )
    assert result.metrics["gpu_peak_bytes"] == 5 * 1024**3 and not pipe.adapters


def test_same_seed_same_noise(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path), "seed": 7})
    _, _, t1, *_ = go(tmp_path, inputs)
    _, _, t2, *_ = go(tmp_path, inputs)
    assert t1.seeds == t2.seeds == [7]


def test_lora_weight_is_set_and_switched_off_for_photo_poses(tmp_path):
    figure = edit.validate({"references": refs(tmp_path), "identity": "keep_face", "seed": 1})
    _, pipe, *_ = go(tmp_path, figure, lora=True)
    assert pipe.adapters == [([edit.POSE_LORA_ADAPTER], [0.7])]
    photo = edit.validate({"references": refs(tmp_path, figure=False), "seed": 1})
    _, pipe, *_ = go(tmp_path, photo, lora=True)
    assert pipe.adapters == [([edit.POSE_LORA_ADAPTER], [0.0])]


@pytest.mark.parametrize("bad", [np.zeros((768, 512, 3)), np.full((768, 512, 3), np.nan)])
def test_blank_or_nan_image_is_an_error_not_a_file(tmp_path, bad):
    inputs = edit.validate({"references": refs(tmp_path), "seed": 1})
    with pytest.raises(EngineError) as exc:
        go(tmp_path, inputs, pipe=FakePipe(image=bad.astype(np.float32)))
    assert exc.value.code == "blank_image" and not (tmp_path / "out" / "image.png").exists()


def test_cancel_stops_before_generating(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path), "seed": 1})
    cancel = threading.Event()
    cancel.set()
    pipe = FakePipe()
    with pytest.raises(Canceled):
        go(tmp_path, inputs, pipe=pipe, cancel=cancel)
    assert not pipe.calls and not (tmp_path / "out" / "image.png").exists()


def test_cancel_between_steps(tmp_path):
    inputs = edit.validate({"references": refs(tmp_path), "seed": 1, "steps": 6})
    cancel = threading.Event()

    class Stopping(FakePipe):
        def __call__(self, **kw):
            cancel.set()  # the user presses Cancel while the model is working
            return super().__call__(**kw)

    with pytest.raises(Canceled):
        go(tmp_path, inputs, pipe=Stopping(), cancel=cancel)
    assert not (tmp_path / "out" / "image.png").exists()


# ---- API -> worker -> asset ----


def test_pose_change_end_to_end_with_a_generated_figure(settings):
    with make_client(settings, extra_engines=(POSE, EDITOR)) as client:
        pid = new_project(client)
        person = upload(client, pid, "person").json()
        pose_photo = upload(client, pid, "pose").json()
        r = client.post("/api/v1/jobs", json={
            "project_id": pid, "task": "image.pose", "engine_id": "test-pose",
            "inputs": {"references": [{"asset_id": pose_photo["id"], "role": "pose"}]},
        })  # fmt: skip
        figure_id = wait_for(client, r.json()["id"], {"completed"})["outputs"][0]
        r = client.post("/api/v1/jobs", json={
            "project_id": pid, "task": "image.edit", "engine_id": "test-editor",
            "inputs": {"references": [{"asset_id": person["id"], "role": "person"},
                                      {"asset_id": figure_id, "role": "pose"}],
                       "identity": "keep_face", "seed": 5, "prompt": "outdoors"},
        })  # fmt: skip
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["inputs"]["pose_source"] == "figure"  # known from the figure's metadata
        done = wait_for(client, job["id"], {"completed", "failed"})
        assert done["state"] == "completed", done.get("error_message")
        meta = client.get(f"/api/v1/assets/{done['outputs'][0]}").json()["metadata"]
        assert meta["source_asset_ids"] == [person["id"], figure_id]
        assert meta["identity"] == "keep_face" and meta["seed"] == 5 and meta["synthetic"]
        assert (meta["width"], meta["height"]) == (512, 768)  # the figure's 64x96 aspect
        retry = client.post(f"/api/v1/jobs/{done['id']}/retry")
        assert retry.status_code == 409  # completed jobs are not retried; reruns use seed
        bad = client.post("/api/v1/jobs", json={
            "project_id": pid, "task": "image.edit", "engine_id": "test-editor",
            "inputs": {"references": [{"asset_id": person["id"], "role": "person"}]},
        })  # fmt: skip
        assert bad.status_code == 422


def test_load_image_scales_figures_up_and_photos_down(monkeypatch):
    """The identity settings send the pose at 512/768/1024 px; figures are 768 px,
    so they must be scaled up as well as down (fake Pillow)."""
    import sys

    resized = []

    class Img:
        def __init__(self, w, h):
            self.size, self.width, self.height = (w, h), w, h

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def convert(self, mode):
            assert mode == "RGB"
            return self

        def resize(self, size, method):
            resized.append(size)
            return Img(*size)

    sizes = {"figure.png": (512, 768), "photo.jpg": (3000, 4000)}
    pil = types.ModuleType("PIL")
    pil.Image = types.SimpleNamespace(open=lambda p: Img(*sizes[p]), LANCZOS="lanczos")
    pil.ImageOps = types.SimpleNamespace(exif_transpose=lambda im: im)
    monkeypatch.setitem(sys.modules, "PIL", pil)
    assert edit.load_image("figure.png", 1024).size == (683, 1024)
    assert edit.load_image("photo.jpg", 1024).size == (768, 1024)
    assert edit.load_image("figure.png", 768).size == (512, 768) and len(resized) == 2
