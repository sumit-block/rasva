"""Pose change (I2) on the FLUX.2 Klein engine: rasva.engines.pose.edit turns a
pose job into Klein's inputs (instruction, reference order and sizes, output
size, seed), and the Klein engine runs it with fake mflux/mlx/PIL modules.
The API test uses the real queue and worker with the same validation."""

from __future__ import annotations

import sys
import threading
import types

import numpy as np
import pytest

from rasva.engines import flux2klein as K
from rasva.engines.contract import Canceled, EngineError, EngineInputError, RunContext
from rasva.engines.pose import edit, skeleton
from tests.conftest import make_client, wait_for
from tests.test_engine_flux2klein import FakeRuntime, loaded
from tests.test_pose_figures import PNG, extract, new_project, upload

POSE = "tests.fake_pose:FakePoseFinder"
EDITOR = "tests.fake_pose_editor:FakePoseEditor"


def figure_png(w=512, h=768):
    return skeleton.encode_png(np.zeros((h, w, 3), np.uint8))


def refs(tmp_path, figure=True, background=False, pose_size=(512, 768)):
    out = []
    roles = ["background"] * background + ["pose", "person"]  # deliberately out of order
    for role in roles:
        path = tmp_path / f"{role}.png"
        path.write_bytes(figure_png(*pose_size) if role == "pose" else PNG)
        out.append({"asset_id": f"ast_{role}", "role": role, "path": str(path),
                    **({"pose_figure": True} if role == "pose" and figure else {})})  # fmt: skip
    return out


# ---- validation and the plan (pure, API process) ----


def test_is_pose_edit():
    assert edit.is_pose_edit({"references": [{"role": "person"}, {"role": "pose"}]})
    assert not edit.is_pose_edit({"references": [{"role": "person"}, {"role": "garment"}]})
    assert not edit.is_pose_edit({})


def test_limits_match_the_klein_engine():
    assert edit.RANGES == K.RANGES and edit.DEFAULTS["steps"] == K.DEFAULTS["steps"]
    assert K.SIZE_RANGE[0] <= edit.MIN_SIDE and K.SIZE_RANGE[1] >= edit.OUT_LONG_SIDE
    assert "pose" in K.Flux2KleinEngine.descriptor.features


def test_validation(tmp_path):
    good = refs(tmp_path)
    person, pose = good[1], good[0]
    with pytest.raises(EngineInputError, match="person"):
        edit.validate({"references": [pose]})
    with pytest.raises(EngineInputError, match="one pose"):
        edit.validate({"references": [person, pose, pose]})
    with pytest.raises(EngineInputError, match="optional background"):
        edit.validate({"references": [*good, {**person, "role": "garment"}]})
    with pytest.raises(EngineInputError, match="missing"):
        edit.validate({"references": [person, {**pose, "path": str(tmp_path / "x.png")}]})
    with pytest.raises(EngineInputError, match="face"):
        edit.validate({"references": good, "identity": "max"})
    with pytest.raises(EngineInputError, match="Steps"):
        edit.validate({"references": good, "steps": 50})
    with pytest.raises(EngineInputError, match="under"):
        edit.validate({"references": good, "prompt": "x" * 501})
    ok = edit.validate({"references": good, "seed": "9", "prompt": "  in a  red sari "})
    assert ok["identity"] == "balanced" and ok["pose_source"] == "figure"
    assert ok["steps"] == 4 and ok["guidance"] == 1.0 and ok["seed"] == 9
    assert ok["user_prompt"] == "in a red sari" and ok["prompt"].endswith("in a red sari")
    assert [r["role"] for r in ok["references"]] == ["person", "pose"]  # image 1, image 2
    assert (ok["width"], ok["height"]) == (512, 768)
    photo = refs(tmp_path, figure=False)
    assert edit.validate({"references": photo})["pose_source"] == "photo"


def test_seed_is_chosen_once_and_retry_keeps_the_users_words(tmp_path):
    first = edit.validate({"references": refs(tmp_path), "prompt": "smiling"})
    other = edit.validate({"references": refs(tmp_path)})
    assert 0 <= first["seed"] <= edit.MAX_SEED and first["seed"] != other["seed"]
    # Retry sends the stored inputs back (built prompt + user_prompt): same result.
    again = edit.validate({**first, "references": refs(tmp_path)})
    assert again["prompt"] == first["prompt"] and again["seed"] == first["seed"]
    assert again["prompt"].count("smiling") == 1


@pytest.mark.parametrize("identity", list(edit.IDENTITY))
def test_identity_settings_change_what_the_model_sees(tmp_path, identity):
    ok = edit.validate({"references": refs(tmp_path), "identity": identity, "seed": 1})
    ident = edit.IDENTITY[identity]
    sides = {r["role"]: r["max_side"] for r in ok["references"]}
    assert sides == {"person": ident.person_side, "pose": ident.pose_side}
    assert ident.emphasis in ok["prompt"]


def test_identity_levers_are_ordered():
    keep, bal, pose = (edit.IDENTITY[k] for k in ("keep_face", "balanced", "follow_pose"))
    assert keep.person_side >= bal.person_side >= pose.person_side
    assert keep.pose_side < bal.pose_side < pose.pose_side


def test_figure_and_photo_instructions(tmp_path):
    fig = edit.validate({"references": refs(tmp_path, background=True), "seed": 1})
    assert [r["role"] for r in fig["references"]] == ["person", "pose", "background"]
    assert "stick-figure" in fig["prompt"] and "Do not draw the stick figure" in fig["prompt"]
    assert "setting from image 3" in fig["prompt"]
    photo = edit.validate({"references": refs(tmp_path, figure=False), "seed": 1})
    assert "Do not copy the face" in photo["prompt"]


def test_output_size_follows_the_pose(tmp_path):
    assert edit.output_size((512, 768)) == (512, 768)
    assert edit.output_size((768, 512)) == (768, 512)
    assert edit.output_size(None) == (512, 768)
    w, h = edit.output_size((1000, 1333))
    assert w % 16 == 0 and h == 768
    assert edit.output_size((100, 4000)) == (256, 768)  # never below Klein's minimum
    land = edit.validate({"references": refs(tmp_path, pose_size=(900, 600)), "seed": 1})
    assert (land["width"], land["height"]) == (768, 512)


def test_image_size_reads_png_and_jpeg_headers(tmp_path):
    png = tmp_path / "f.png"
    png.write_bytes(figure_png(20, 30))
    assert edit.image_size(str(png)) == (20, 30)
    # Minimal JPEG: SOI, an APP0 segment, then SOF0 with height 300 and width 200.
    app0 = b"\xff\xe0\x00\x10" + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0\x00\x11\x08" + (300).to_bytes(2, "big") + (200).to_bytes(2, "big") + b"\x03"
    jpg = tmp_path / "p.jpg"
    jpg.write_bytes(b"\xff\xd8" + app0 + sof0 + b"\x00" * 16)
    assert edit.image_size(str(jpg)) == (200, 300)
    (tmp_path / "x.webp").write_bytes(b"RIFF....WEBP")
    assert edit.image_size(str(tmp_path / "x.webp")) is None


def test_metadata_fields():
    assert edit.output_metadata({"prompt": "x"}) == {}
    meta = edit.output_metadata({"identity": "keep_face", "pose_source": "photo"})
    assert meta == {"edit": "pose", "identity": "keep_face", "pose_source": "photo"}


# ---- on the Klein engine (fake mflux, mlx and PIL) ----


class SizedImg:
    def __init__(self, size, log):
        self.size, self.width, self.height, self.log = size, size[0], size[1], log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def convert(self, mode):
        return self

    def resize(self, size, method):
        self.log.append(size)
        return SizedImg(size, self.log)

    def thumbnail(self, size):
        self.log.append(("thumb", size))

    def save(self, path, format=None):
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG fake")


@pytest.fixture
def klein(monkeypatch):
    def install(sizes):
        rt = FakeRuntime()
        for name, mod in rt.modules.items():
            monkeypatch.setitem(sys.modules, name, mod)
        rt.resized = []
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace(
            open=lambda p: SizedImg(sizes[str(p).rsplit("/", 1)[-1]], rt.resized),
            LANCZOS="lanczos",
        )
        pil.ImageOps = types.SimpleNamespace(exif_transpose=lambda img: img)
        monkeypatch.setitem(sys.modules, "PIL", pil)
        return rt

    return install


def klein_run(engine, tmp_path, inputs, cancel=None):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext(
        "job_p", out, lambda f, d: progress.append((f, d)), cancel or threading.Event()
    )
    clean = engine.validate_input("image.edit", inputs)
    return engine.run("image.edit", clean, ctx), out, progress


def test_klein_runs_a_pose_job_with_sizes_order_and_metadata(klein, tmp_path):
    rt = klein({"person.png": (3000, 4000), "pose.png": (512, 768)})
    engine = loaded(tmp_path)
    inputs = {"references": refs(tmp_path), "identity": "follow_pose", "seed": 42,
              "prompt": "smiling"}  # fmt: skip
    result, out, progress = klein_run(engine, tmp_path, inputs)
    (call,) = rt.calls
    assert call["seed"] == 42 and call["num_inference_steps"] == 4 and call["guidance"] == 1.0
    assert (call["width"], call["height"]) == (512, 768)
    assert call["prompt"].startswith("Show the person from image 1")
    assert len(call["image_paths"]) == 2
    # person scaled down to 768, the 768 px figure scaled UP to 1024 (follow_pose)
    assert rt.resized == [(576, 768), (683, 1024)]
    assert "Step 4 of 4" in [d for _, d in progress]
    meta = result.outputs[0].metadata
    assert meta["edit"] == "pose" and meta["identity"] == "follow_pose"
    assert meta["pose_source"] == "figure" and meta["seed"] == 42 and meta["synthetic"]
    assert meta["source_asset_ids"] == ["ast_person", "ast_pose"]
    assert meta["roles"] == ["person", "pose"]
    assert "prompt" not in meta and "smiling" not in str(meta)
    assert "smiling" not in str(result.metrics)


def test_klein_ordinary_edits_are_unchanged(klein, tmp_path):
    rt = klein({"p.jpg": (3000, 4000)})
    engine = loaded(tmp_path)
    person = tmp_path / "p.jpg"
    person.write_bytes(b"jpeg")
    ref = {"asset_id": "p", "role": "person", "path": str(person)}
    result, *_ = klein_run(engine, tmp_path, {"prompt": "red coat", "references": [ref]})
    assert rt.resized == [("thumb", (K.REFERENCE_MAX_SIDE, K.REFERENCE_MAX_SIDE))]
    assert "edit" not in result.outputs[0].metadata


def test_klein_pose_job_blank_and_cancel(klein, tmp_path):
    rt = klein({"person.png": (600, 800), "pose.png": (512, 768)})
    engine = loaded(tmp_path)
    rt.pixels = np.zeros((8, 8, 3), np.uint8)
    with pytest.raises(EngineError) as exc:
        klein_run(engine, tmp_path, {"references": refs(tmp_path), "seed": 1})
    assert exc.value.code == "blank_image" and not (tmp_path / "out" / "image.png").exists()
    rt.pixels = np.full((8, 8, 3), 128, np.uint8)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Canceled):
        klein_run(engine, tmp_path, {"references": refs(tmp_path), "seed": 1}, cancel)


# ---- API -> worker -> asset ----


def test_pose_change_end_to_end_with_a_generated_figure(settings):
    with make_client(settings, extra_engines=(POSE, EDITOR)) as client:
        pid = new_project(client)
        person = upload(client, pid, "person").json()
        _, figure_id = extract(client, pid)
        body = {
            "project_id": pid, "task": "image.edit", "engine_id": "test-pose-editor",
            "inputs": {"references": [{"asset_id": person["id"], "role": "person"},
                                      {"asset_id": figure_id, "role": "pose"}],
                       "identity": "keep_face", "seed": 5, "prompt": "outdoors"},
        }  # fmt: skip
        r = client.post("/api/v1/jobs", json=body)
        assert r.status_code == 201, r.text
        job = r.json()
        assert job["inputs"]["pose_source"] == "figure"  # known from the figure's metadata
        assert job["inputs"]["user_prompt"] == "outdoors"
        assert (job["inputs"]["width"], job["inputs"]["height"]) == (512, 768)  # 64x96 figure
        done = wait_for(client, job["id"], {"completed", "failed"})
        assert done["state"] == "completed", done.get("error_message")
        meta = client.get(f"/api/v1/assets/{done['outputs'][0]}").json()["metadata"]
        assert meta["source_asset_ids"] == [person["id"], figure_id]
        assert meta["identity"] == "keep_face" and meta["seed"] == 5 and meta["synthetic"]
        assert meta["edit"] == "pose"
        bad = dict(body, inputs={"references": [{"asset_id": person["id"], "role": "person"}]})
        assert client.post("/api/v1/jobs", json=bad).status_code == 422


def test_retry_of_a_pose_job_reuses_the_stored_instruction(settings):
    """Retry sends stored inputs (built prompt + user_prompt) back through validation."""
    with make_client(settings, extra_engines=(POSE, EDITOR)) as client:
        pid = new_project(client)
        person = upload(client, pid, "person").json()
        _, figure_id = extract(client, pid)
        r = client.post("/api/v1/jobs", json={
            "project_id": pid, "task": "image.edit", "engine_id": "test-pose-editor",
            "inputs": {"references": [{"asset_id": person["id"], "role": "person"},
                                      {"asset_id": figure_id, "role": "pose"}],
                       "seed": 8, "prompt": "outdoors", "steps": 1},
        }).json()  # fmt: skip
        client.post(f"/api/v1/jobs/{r['id']}/cancel")
        stopped = wait_for(client, r["id"], {"canceled", "completed"})
        if stopped["state"] != "canceled":
            pytest.skip("the job finished before the cancel arrived")
        retry = client.post(f"/api/v1/jobs/{r['id']}/retry")
        assert retry.status_code == 201, retry.text
        again = retry.json()["inputs"]
        assert again["prompt"] == r["inputs"]["prompt"] and again["seed"] == 8
        assert again["prompt"].count("outdoors") == 1
