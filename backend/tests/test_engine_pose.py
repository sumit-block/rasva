"""Pose finder (I2) with a fake onnxruntime: gating while unpinned, local-only
loading, detection and SimCC decoding maths, progress, cancel, the "no person"
guard, the saved figure, and the skeleton drawing shared with the built-in figures."""

from __future__ import annotations

import json
import sys
import threading
import types
import zlib
from pathlib import Path

import numpy as np
import pytest

from rasva.engines import pose as P
from rasva.engines.contract import (
    Canceled,
    EngineError,
    EngineInputError,
    InstallContext,
    RunContext,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.pose import skeleton
from rasva.engines.registry import EngineRegistry
from tests.conftest import make_client, set_host, set_pinned

MAC = SystemFacts("darwin", "arm64", 16 * 1024**3, True, False, True)
LINUX = SystemFacts("linux", "x86_64", 16 * 1024**3, False, False, False)
ROOT = Path(__file__).resolve().parents[2]
ANCHORS = 80 * 80 + 40 * 40 + 20 * 20


def det_output(boxes_scores):
    """YOLOX raw output with given (cx, cy, w, h, score) boxes in 640-space, all 'person'."""
    grid, stride = P._yolox_grid(P.DET_SIZE)
    out = np.zeros((1, ANCHORS, 85), dtype=np.float32)
    for k, (cx, cy, w, h, score) in enumerate(boxes_scores):
        i = 6400 + 1600 + k  # stride-32 anchors
        out[0, i, 0] = cx / stride[i, 0] - grid[i, 0]
        out[0, i, 1] = cy / stride[i, 0] - grid[i, 1]
        out[0, i, 2] = np.log(w / stride[i, 0])
        out[0, i, 3] = np.log(h / stride[i, 0])
        out[0, i, 4] = 1.0
        out[0, i, 5] = score
    return out


def simcc_for(points_px, box, scores=None, k=133):
    """SimCC heatmaps whose arg-max decodes back to points_px (image pixels) for this box."""
    center, scale = P.box_to_center_scale(np.asarray(box, dtype=np.float32))
    wx, wy = int(P.POSE_SIZE[0] * P.SIMCC_SPLIT), int(P.POSE_SIZE[1] * P.SIMCC_SPLIT)
    sx, sy = np.zeros((1, k, wx), np.float32), np.zeros((1, k, wy), np.float32)
    for i, (x, y) in enumerate(points_px):
        lx = (x - center[0] + scale[0] / 2) / scale[0] * P.POSE_SIZE[0] * P.SIMCC_SPLIT
        ly = (y - center[1] + scale[1] / 2) / scale[1] * P.POSE_SIZE[1] * P.SIMCC_SPLIT
        s = 0.9 if scores is None else scores[i]
        sx[0, i, int(round(lx))] = s
        sy[0, i, int(round(ly))] = s
    return sx, sy


class FakeOrt:
    def __init__(self, det, pose, coreml=False):
        rt = self
        self.sessions: list[tuple[str, list[str]]] = []
        self.det_inputs: list[np.ndarray] = []
        self.pose_inputs: list[np.ndarray] = []

        class Session:
            def __init__(self, path, opts, providers):
                rt.sessions.append((path, providers))
                self.kind = "det" if path.endswith(P.DETECTOR_FILE) else "pose"

            def get_inputs(self):
                return [types.SimpleNamespace(name="input")]

            def run(self, names, feeds):
                if self.kind == "det":
                    rt.det_inputs.append(feeds["input"])
                    return [det]
                rt.pose_inputs.append(feeds["input"])
                return list(pose)

        mod = types.ModuleType("onnxruntime")
        mod.InferenceSession = Session
        mod.SessionOptions = lambda: object()
        mod.get_available_providers = lambda: (
            ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            if coreml
            else ["CPUExecutionProvider"]
        )
        self.module = mod


IMG_W, IMG_H = 400, 600
# A standing person, COCO-17 order, in image pixels.
COCO_PX = [(200, 100), (210, 90), (190, 90), (220, 95), (180, 95), (240, 150), (160, 150),
           (250, 230), (150, 230), (255, 300), (145, 300), (225, 320), (175, 320),
           (225, 440), (175, 440), (225, 560), (175, 560)]  # fmt: skip
BOX = (130, 70, 270, 580)  # x1, y1, x2, y2 in image pixels


@pytest.fixture
def fake(monkeypatch, tmp_path):
    def install(det=None, pose=None, coreml=False):
        ratio = min(640 / IMG_H, 640 / IMG_W)
        x1, y1, x2, y2 = (v * ratio for v in BOX)
        det = (
            det_output([((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, 0.95)])
            if det is None
            else det
        )
        pose = simcc_for(COCO_PX, BOX) if pose is None else pose
        rt = FakeOrt(det, pose, coreml)
        monkeypatch.setitem(sys.modules, "onnxruntime", rt.module)
        # Pillow lives in the engine environment only: stand in with numpy.
        monkeypatch.setattr(P, "load_rgb", lambda path: np.full((IMG_H, IMG_W, 3), 128, np.uint8))
        monkeypatch.setattr(P, "resize", lambda img, w, h: np.zeros((h, w, 3), np.uint8))
        monkeypatch.setattr(
            P, "affine_crop", lambda img, c, s, size: np.zeros((size[1], size[0], 3), np.uint8)
        )
        return rt

    monkeypatch.delenv("RASVA_ONNX_COREML", raising=False)
    model = tmp_path / "model"
    model.mkdir()
    for f in (P.DETECTOR_FILE, P.POSE_FILE):
        (model / f).write_bytes(b"onnx")
    return install


def photo(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff fake")
    return p


def loaded(tmp_path):
    engine = P.PoseFinderEngine()
    engine.load(RuntimeContext(tmp_path / "model", tmp_path / "cache"))
    return engine


def run(engine, tmp_path, cancel=None):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    progress = []
    ctx = RunContext(
        "job_p", out, lambda f, d: progress.append((f, d)), cancel or threading.Event()
    )
    clean = engine.validate_input(
        P.TASK,
        {"references": [{"asset_id": "ast_src", "role": "pose", "path": str(photo(tmp_path))}]},
    )
    return engine.run(P.TASK, clean, ctx), out, progress


# ---- descriptor and gating ----


def test_registered_as_unconfirmed_image_tool():
    d = EngineRegistry().get(P.ENGINE_ID).descriptor
    assert d.modality == "image" and d.tasks == [P.TASK]
    assert d.license_class == "unknown"  # installer asks until the owner confirms
    assert d.requirements_lock == "requirements.lock" and d.python_version == "3.12"


def test_draft_is_gated_until_pinned_and_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "LOCKFILE", tmp_path / "requirements.lock")  # not generated
    set_pinned(monkeypatch, P.PoseFinderEngine, pinned=False)
    report = P.PoseFinderEngine().check_compatibility(MAC)
    assert not report.compatible
    assert any("pin_engine.py dwpose" in r for r in report.reasons)
    assert any("requirements.in" in r for r in report.reasons)
    assert not P.PoseFinderEngine().check_compatibility(LINUX).compatible


def test_requirements_have_no_torch_opencv_or_remote_code():
    reqs = (Path(P.__file__).parent / "requirements.in").read_text()
    lines = [ln.split("=")[0].split(">")[0].strip() for ln in reqs.splitlines()
             if ln.strip() and not ln.startswith("#")]  # fmt: skip
    assert lines == ["onnxruntime", "numpy", "pillow", "pydantic"]
    assert "--generate-hashes" in reqs


def test_pin_spec_lists_onnx_only():
    text = (ROOT / "scripts" / "pin_engine.py").read_text()
    spec = text[text.index('"dwpose": {') :].split("},\n}")[0]
    assert "yolox_l.onnx" in spec and "dw-ll_ucoco_384.onnx" in spec and '.pth"' not in spec


def test_listed_but_not_installable_while_unconfirmed(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "LOCKFILE", tmp_path / "requirements.lock")  # not generated
    set_host(monkeypatch, apple_silicon=True)
    set_pinned(monkeypatch, P.PoseFinderEngine, pinned=True)
    with make_client(settings) as client:
        info = client.get(f"/api/v1/models/{P.ENGINE_ID}").json()
        assert info["needs_license_confirmation"] is True
        assert info["compatibility"]["compatible"] is False  # still no lockfile


def test_verify(tmp_path):
    model = tmp_path / "m"
    model.mkdir()
    (model / P.DETECTOR_FILE).write_bytes(b"x")
    assert not P.PoseFinderEngine().verify(InstallContext(model, tmp_path))
    (model / P.POSE_FILE).write_bytes(b"x")
    assert P.PoseFinderEngine().verify(InstallContext(model, tmp_path))


# ---- validation ----


def test_validation(tmp_path):
    e = P.PoseFinderEngine()
    with pytest.raises(EngineInputError, match="one photo"):
        e.validate_input(P.TASK, {})
    with pytest.raises(EngineInputError, match="one photo"):
        e.validate_input(P.TASK, {"references": [{"path": "a"}, {"path": "b"}]})
    with pytest.raises(EngineInputError, match="missing"):
        e.validate_input(P.TASK, {"references": [{"path": str(tmp_path / "gone.png")}]})
    with pytest.raises(EngineInputError, match="cannot do"):
        e.validate_input("image.generate", {})
    ok = e.validate_input(
        P.TASK, {"references": [{"asset_id": "a1", "path": str(photo(tmp_path))}]}
    )
    assert ok["references"][0]["role"] == "pose" and ok["references"][0]["asset_id"] == "a1"


# ---- runtime ----


def test_loads_local_onnx_files_on_cpu(fake, tmp_path):
    rt = fake()
    engine = loaded(tmp_path)
    paths = [Path(p).name for p, _ in rt.sessions]
    assert paths == [P.DETECTOR_FILE, P.POSE_FILE]
    assert all(Path(p).parent == tmp_path / "model" for p, _ in rt.sessions)
    assert engine.providers == ["CPUExecutionProvider"]


def test_coreml_only_when_asked_and_available(fake, tmp_path, monkeypatch):
    fake(coreml=True)
    monkeypatch.setenv("RASVA_ONNX_COREML", "1")
    assert loaded(tmp_path).providers == ["CoreMLExecutionProvider", "CPUExecutionProvider"]


def test_missing_files_fail_with_plain_message(fake, tmp_path):
    fake()
    (tmp_path / "model" / P.POSE_FILE).unlink()
    with pytest.raises(EngineError) as exc:
        loaded(tmp_path)
    assert exc.value.code == "missing_files"


def test_detector_input_is_letterboxed_640(fake, tmp_path):
    rt = fake()
    run(loaded(tmp_path), tmp_path)
    (blob,) = rt.det_inputs
    assert blob.shape == (1, 3, 640, 640) and blob.dtype == np.float32
    # 400x600 scaled by 640/600 -> 426 wide; the rest is YOLOX's grey padding.
    assert blob[0, 0, 0, 500] == 114 and blob[0, 0, 0, 10] == 0
    (pose_in,) = rt.pose_inputs
    assert pose_in.shape == (1, 3, P.POSE_SIZE[1], P.POSE_SIZE[0])


def test_decodes_the_pose_and_saves_a_figure(fake, tmp_path):
    fake()
    result, out, progress = run(loaded(tmp_path), tmp_path)
    data = (out / "pose.png").read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    (output,) = result.outputs
    meta = output.metadata
    assert meta["kind"] == "pose" and meta["role"] == "pose"
    assert meta["source_asset_ids"] == ["ast_src"] and meta["people_found"] == 1
    assert meta["framing"] == "full_body" and meta["keypoint_format"] == "openpose18"
    assert (meta["width"], meta["height"]) == (512, 768)  # source aspect, long side 768
    kp = meta["keypoints"]
    # nose, neck (mid-shoulder), right wrist, left ankle - back in image fractions
    assert kp[0] == pytest.approx([200 / 399, 100 / 599], abs=0.01)
    assert kp[1] == pytest.approx([200 / 399, 150 / 599], abs=0.01)
    assert kp[4] == pytest.approx([145 / 399, 300 / 599], abs=0.01)
    assert kp[13] == pytest.approx([225 / 399, 560 / 599], abs=0.01)
    assert [d for _, d in progress] == [
        "Opening the photo",
        "Finding the person",
        "Reading the pose",
        "Drawing the pose",
        "Done",
    ]
    assert result.metrics["visible_keypoints"] == 18


def test_largest_person_is_used_and_count_reported(fake, tmp_path):
    ratio = 640 / IMG_H
    x1, y1, x2, y2 = (v * ratio for v in BOX)
    det = det_output(
        [((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, 0.95), (40, 40, 30, 60, 0.9)]
    )
    fake(det=det)
    result, *_ = run(loaded(tmp_path), tmp_path)
    assert result.outputs[0].metadata["people_found"] == 2
    assert result.outputs[0].metadata["keypoints"][0] == pytest.approx(
        [200 / 399, 100 / 599], abs=0.01
    )


def test_no_person_is_an_error_not_a_file(fake, tmp_path):
    fake(det=np.zeros((1, ANCHORS, 85), np.float32))
    with pytest.raises(EngineError) as exc:
        run(loaded(tmp_path), tmp_path)
    assert exc.value.code == "no_person" and not (tmp_path / "out" / "pose.png").exists()


def test_unreadable_pose_is_an_error_not_a_file(fake, tmp_path):
    fake(pose=simcc_for(COCO_PX, BOX, scores=[0.1] * 17))
    with pytest.raises(EngineError) as exc:
        run(loaded(tmp_path), tmp_path)
    assert exc.value.code == "no_pose" and not (tmp_path / "out" / "pose.png").exists()


def test_cancel_stops_before_saving(fake, tmp_path):
    fake()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Canceled):
        run(loaded(tmp_path), tmp_path, cancel=cancel)
    assert not (tmp_path / "out" / "pose.png").exists()


def test_channel_order_switch(monkeypatch):
    img = np.zeros((1, 1, 3), np.uint8)
    img[0, 0] = (10, 20, 30)
    assert P.to_model_order(img)[0, 0].tolist() == [10, 20, 30]
    monkeypatch.setattr(P, "INPUT_ORDER", "BGR")
    assert P.to_model_order(img)[0, 0].tolist() == [30, 20, 10]


def test_nms_merges_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], np.float32)
    assert P._nms(boxes, np.array([0.9, 0.8, 0.7]), 0.45) == [0, 2]


# ---- skeleton drawing and the built-in figures ----


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _png_pixels(data: bytes) -> bytes:
    idat = data[data.index(b"IDAT") + 4 : data.index(b"IEND") - 8]
    return zlib.decompress(idat)


def test_render_draws_openpose_colours_on_black():
    pts = [None] * 18
    pts[1], pts[2] = (0.5, 0.2), (0.3, 0.2)  # neck -> right shoulder, first limb (red)
    img = skeleton.render(pts, 100, 100)
    assert img.shape == (100, 100, 3) and img[90, 90].tolist() == [0, 0, 0]
    assert img[20, 40].tolist() == [153, 0, 0]  # limb at 60 % of (255, 0, 0)
    png = skeleton.encode_png(img)
    assert _png_size(png) == (100, 100)
    # The encoded rows decode back to the same pixels.
    idat = png[png.index(b"IDAT") + 4 : png.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == 100 * (1 + 300) and raw[20 * 301 + 1 + 40 * 3] == 153


def test_skeleton_validation_and_framing():
    with pytest.raises(ValueError):
        skeleton.validate([(0.5, 0.5)] * 17)
    pts = skeleton.validate([(2.0, -1.0)] + [None] * 17)
    assert pts[0] == (1.0, 0.0)
    head = [(0.5, 0.3), (0.5, 0.5)] + [None] * 16
    assert skeleton.framing(head) == "portrait"


def test_built_in_figures_are_committed_and_match_the_script():
    folder = ROOT / "frontend" / "public" / "poses"
    index = json.loads((folder / "poses.json").read_text())
    ids = {p["id"] for p in index}
    assert {"standing-front", "three-quarter", "seated", "portrait"} <= ids
    for p in index:
        data = (folder / p["file"]).read_bytes()
        assert _png_size(data) == (p["width"], p["height"])
        # Re-drawing from the stored keypoints gives the committed pixels exactly
        # (compared decoded: zlib builds may compress differently).
        points = [None if k is None else tuple(k) for k in p["keypoints"]]
        redrawn = skeleton.encode_png(skeleton.render(points, p["width"], p["height"]))
        assert _png_pixels(redrawn) == _png_pixels(data)
