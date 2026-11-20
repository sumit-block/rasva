"""Pose finder (phase I2): photo -> body pose -> OpenPose-style stick figure.

DRAFT, not installed. The download list (manifest.json) and the software lock
(requirements.lock) are deliberately missing until the owner confirms the
licences in docs/engines/pose-control.md. Until then the engine reports itself
incompatible with the exact steps to finish it.

How it works (DWPose, ONNX, run with onnxruntime; no torch, no pickle files):

1. A YOLOX-L person detector finds people in the photo. The largest person is
   used; the job says how many were found.
2. The DWPose whole-body model (RTMPose-style, SimCC output) reads 133 keypoints
   for that person. The 17 COCO body points are mapped to the 18 OpenPose points
   (the neck is the middle of the shoulders).
3. The skeleton is drawn with ``skeleton.render`` and saved as ``pose.png``.
   Keypoints, framing and the source photo id go into the output's metadata so
   the figure can be shown, reused as a ``pose`` reference, and traced back.

Pre/post-processing is written to follow DWPose's ONNX code (Apache-2.0,
IDEA-Research/DWPose ``annotator/dwpose/onnxdet.py`` and ``onnxpose.py``), with
the affine crop done by Pillow instead of OpenCV so the environment stays small.
It was compared with rtmlib 0.0.16 (same lineage) on 2026-11-20: the YOLOX grid
decode, 114-grey letterbox, 1.25 box padding, aspect fix, mean/std, SimCC split
2.0 and rescaling agree. Deliberate differences: only the "person" class is kept
(rtmlib runs NMS over all classes), detector threshold 0.3 (rtmlib 0.7), and a
keypoint's score is the minimum of its x/y peaks with threshold 0.3 as in DWPose
(rtmlib: the mean, threshold 0.43). Channel order is unsettled: the ControlNet
DWPose annotator passes RGB, rtmlib passes OpenCV's BGR. ``INPUT_ORDER`` is the
one switch; check it first with real weights (test plan §1).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from rasva.engines import manifest as manifest_mod
from rasva.engines.contract import (
    CompatibilityReport,
    Engine,
    EngineDescriptor,
    EngineError,
    EngineInputError,
    InstallContext,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.pose import skeleton

ENGINE_ID = "dwpose"
REPO_ID = "yzd-v/DWPose"
DETECTOR_FILE = "yolox_l.onnx"
POSE_FILE = "dw-ll_ucoco_384.onnx"
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
LOCKFILE = Path(__file__).with_name("requirements.lock")
TASK = "image.pose"

DET_SIZE = (640, 640)  # (h, w) YOLOX input
POSE_SIZE = (288, 384)  # (w, h) DWPose input
SIMCC_SPLIT = 2.0
BBOX_PADDING = 1.25
DET_SCORE = 0.3
NMS_IOU = 0.45
KEYPOINT_SCORE = 0.3
MIN_VISIBLE = 4  # fewer points than this is "no usable pose"
OUT_LONG_SIDE = 768
MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)
# COCO-17 index for each OpenPose-18 point; None = neck (mid-shoulder).
INPUT_ORDER = "RGB"  # or "BGR"; see the module docstring
COCO_TO_OPENPOSE = (0, None, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3)

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID})
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST)
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS) or 351_145_849  # HF listing, 2026-11-20
_ENV_ESTIMATE = 120 * 1024**2  # onnxruntime + numpy + pillow; replaced after install


class PoseFinderEngine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="Pose finder (DWPose)",
        summary="Reads the body pose from a photo and draws it as a stick figure you can reuse.",
        version="1.0",
        modality="image",
        tasks=[TASK],
        features=["pose_extraction"],
        license="Apache-2.0 (code and weights; training-data terms not yet checked)",
        # 'unknown' until the owner confirms (docs/engines/pose-control.md), so the
        # installer asks before downloading.
        license_class="unknown",
        license_notes=[
            "DWPose's code and model files are published under Apache-2.0. The terms of "
            "the datasets it was trained on (COCO-WholeBody and others) have not been "
            "checked yet.",
            "Only the stick figure leaves the engine: the photo stays in your project.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _ENV_ESTIMATE,
        approx_peak_memory_bytes=1 * 1024**3,  # estimate, not measured
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.12",
    )

    def __init__(self) -> None:
        self._det: Any = None
        self._pose: Any = None
        self.providers: list[str] = []

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not self.descriptor.artifacts:
            reasons.append(
                "The pose finder's download list has not been pinned yet. Confirm its "
                "licence, then run scripts/pin_engine.py dwpose once."
            )
        if not LOCKFILE.is_file():
            reasons.append(
                "The pose finder's software list has not been generated yet. See "
                "backend/rasva/engines/pose/requirements.in for the command."
            )
        if not system.apple_silicon:
            reasons.append("The pose finder's software package is set up for Apple Silicon Macs.")
        return CompatibilityReport(compatible=not reasons, reasons=reasons)

    def verify(self, ctx: InstallContext) -> bool:
        return all((ctx.model_dir / f).is_file() for f in (DETECTOR_FILE, POSE_FILE))

    # ---- runtime ----

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != TASK:
            raise EngineInputError(f"The pose finder cannot do '{task}'.")
        refs = inputs.get("references") or []
        if not isinstance(refs, list) or len(refs) != 1:
            raise EngineInputError("Choose one photo to read the pose from.")
        (ref,) = refs
        if not isinstance(ref, dict) or not ref.get("path"):
            raise EngineInputError("Choose one photo to read the pose from.")
        if not Path(str(ref["path"])).is_file():
            raise EngineInputError("That photo is missing. Upload it again.")
        return {"references": [{"asset_id": ref.get("asset_id"), "role": ref.get("role") or "pose",
                                "path": str(ref["path"])}]}  # fmt: skip

    def load(self, ctx: RuntimeContext) -> None:
        import onnxruntime as ort

        for name in (DETECTOR_FILE, POSE_FILE):
            if not (ctx.model_dir / name).is_file():
                raise EngineError("missing_files", "The pose finder's files are missing. "
                                  "Reinstall it from Models.")  # fmt: skip
        # CPU by default. RASVA_ONNX_COREML=1 tries Apple's Core ML first (operator
        # coverage for these models is not verified yet).
        providers = ["CPUExecutionProvider"]
        if os.environ.get("RASVA_ONNX_COREML") == "1":
            available = set(ort.get_available_providers())
            if "CoreMLExecutionProvider" in available:
                providers.insert(0, "CoreMLExecutionProvider")
        opts = ort.SessionOptions()
        self._det = ort.InferenceSession(
            str(ctx.model_dir / DETECTOR_FILE), opts, providers=providers
        )
        self._pose = ort.InferenceSession(str(ctx.model_dir / POSE_FILE), opts, providers=providers)
        self.providers = providers

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._det is not None and self._pose is not None, "load() not called"
        (ref,) = inputs["references"]
        started = time.perf_counter()
        ctx.report_progress(0.0, "Opening the photo")
        img = to_model_order(load_rgb(ref["path"]))
        h, w, _ = img.shape
        ctx.raise_if_canceled()
        ctx.report_progress(0.2, "Finding the person")
        boxes = detect_people(self._det, img)
        if len(boxes) == 0:
            raise EngineError("no_person", "No person was found in this photo. Try one where "
                              "the whole person is clearly visible.")  # fmt: skip
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        box = boxes[int(np.argmax(areas))]
        ctx.raise_if_canceled()
        ctx.report_progress(0.5, "Reading the pose")
        kpts, scores = estimate_pose(self._pose, img, box)
        points = to_openpose(kpts, scores, w, h)
        if skeleton.visible(points) < MIN_VISIBLE:
            raise EngineError("no_pose", "The pose could not be read from this photo. Try one "
                              "with more of the body in view.")  # fmt: skip
        ctx.raise_if_canceled()
        ctx.report_progress(0.8, "Drawing the pose")
        scale = OUT_LONG_SIDE / max(w, h)
        out_w, out_h = max(64, round(w * scale)), max(64, round(h * scale))
        figure = skeleton.render(points, out_w, out_h)
        (ctx.output_dir / "pose.png").write_bytes(skeleton.encode_png(figure))
        elapsed = time.perf_counter() - started
        ctx.report_progress(1.0, "Done")
        return RunResult(
            outputs=[OutputFile(
                filename="pose.png", media_type="image/png",
                metadata={
                    "kind": "pose", "role": "pose", "engine": ENGINE_ID,
                    "model_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
                    "keypoint_format": "openpose18",
                    "keypoints": skeleton.to_json(points),
                    "framing": skeleton.framing(points),
                    "people_found": len(boxes),
                    "source_asset_ids": [ref["asset_id"]] if ref.get("asset_id") else [],
                    "width": out_w, "height": out_h,
                    # A drawing of the measured pose, not an AI-generated picture.
                    "synthetic": False,
                },
            )],
            metrics={"extraction_s": round(elapsed, 3), "people_found": len(boxes),
                     "visible_keypoints": skeleton.visible(points), "providers": self.providers},
        )  # fmt: skip

    def unload(self) -> None:
        self._det = self._pose = None


# ---------------------------------------------------------------- image helpers
# Pillow is imported lazily: it exists in the engine environment, not the app's.


def load_rgb(path: str) -> np.ndarray:
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)  # phone photos are often stored rotated
        return np.asarray(im.convert("RGB"), dtype=np.uint8)


def to_model_order(img: np.ndarray) -> np.ndarray:
    """RGB pixels -> the channel order both models are fed (INPUT_ORDER)."""
    return np.ascontiguousarray(img[..., ::-1]) if INPUT_ORDER == "BGR" else img


def resize(img: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.fromarray(img).resize((width, height), Image.BILINEAR), dtype=np.uint8)


def affine_crop(
    img: np.ndarray, center: np.ndarray, scale: np.ndarray, size: tuple[int, int]
) -> np.ndarray:
    """Crop the box (center, scale) and resize it to size=(w, h); pads with black."""
    from PIL import Image

    w, h = size
    sx, sy = float(scale[0]) / w, float(scale[1]) / h
    x0, y0 = float(center[0] - scale[0] / 2), float(center[1] - scale[1] / 2)
    # Pillow wants the inverse map: output (x, y) -> input (sx*x + x0, sy*y + y0).
    out = Image.fromarray(img).transform(
        (w, h), Image.AFFINE, (sx, 0, x0, 0, sy, y0), Image.BILINEAR
    )
    return np.asarray(out, dtype=np.uint8)


# ---------------------------------------------------------------- detection


def _yolox_grid(det_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        hs, ws = det_size[0] // stride, det_size[1] // stride
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        grid = np.stack((xv, yv), 2).reshape(-1, 2)
        grids.append(grid)
        strides.append(np.full((grid.shape[0], 1), stride))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        xx1, yy1 = np.maximum(x1[i], x1[order[1:]]), np.maximum(y1[i], y1[order[1:]])
        xx2, yy2 = np.minimum(x2[i], x2[order[1:]]), np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        overlap = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][overlap <= iou]
    return keep


def detect_people(session: Any, img: np.ndarray) -> np.ndarray:
    """Person boxes (N, 4) as x1, y1, x2, y2 in image pixels."""
    h, w, _ = img.shape
    ratio = min(DET_SIZE[0] / h, DET_SIZE[1] / w)
    nh, nw = max(1, int(h * ratio)), max(1, int(w * ratio))
    padded = np.full((DET_SIZE[0], DET_SIZE[1], 3), 114, dtype=np.uint8)
    padded[:nh, :nw] = resize(img, nw, nh)
    blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
    (raw,) = session.run(None, {session.get_inputs()[0].name: blob})[:1]
    pred = np.array(raw[0], dtype=np.float32)  # (anchors, 5 + classes)
    grid, stride = _yolox_grid(DET_SIZE)
    xy = (pred[:, :2] + grid) * stride
    wh = np.exp(pred[:, 2:4]) * stride
    person = pred[:, 4] * pred[:, 5]  # objectness x class 0 ("person")
    mask = person > DET_SCORE
    if not mask.any():
        return np.zeros((0, 4), dtype=np.float32)
    xy, wh, person = xy[mask], wh[mask], person[mask]
    boxes = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1) / ratio
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
    return boxes[_nms(boxes, person, NMS_IOU)]


# ---------------------------------------------------------------- pose


def box_to_center_scale(box: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x1, y1, x2, y2 = (float(v) for v in box)
    center = np.array([(x1 + x2) / 2, (y1 + y2) / 2], dtype=np.float32)
    scale = np.array([(x2 - x1) * BBOX_PADDING, (y2 - y1) * BBOX_PADDING], dtype=np.float32)
    aspect = POSE_SIZE[0] / POSE_SIZE[1]
    if scale[0] > scale[1] * aspect:
        scale[1] = scale[0] / aspect
    else:
        scale[0] = scale[1] * aspect
    return center, scale


def estimate_pose(session: Any, img: np.ndarray, box: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Keypoints (K, 2) in image pixels and their scores (K,)."""
    center, scale = box_to_center_scale(box)
    crop = affine_crop(img, center, scale, POSE_SIZE).astype(np.float32)
    blob = ((crop - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    simcc_x, simcc_y = session.run(None, {session.get_inputs()[0].name: blob})[:2]
    simcc_x, simcc_y = np.asarray(simcc_x[0]), np.asarray(simcc_y[0])  # (K, Wx), (K, Wy)
    locs = np.stack([simcc_x.argmax(1), simcc_y.argmax(1)], axis=1).astype(np.float32)
    scores = np.minimum(simcc_x.max(1), simcc_y.max(1))
    kpts = locs / SIMCC_SPLIT / np.array(POSE_SIZE, dtype=np.float32) * scale + center - scale / 2
    kpts[scores <= 0] = -1
    return kpts, scores


def to_openpose(
    kpts: np.ndarray, scores: np.ndarray, width: int, height: int
) -> list[skeleton.Point]:
    """COCO/whole-body keypoints in pixels -> 18 OpenPose points as fractions."""

    def frac(i: int) -> skeleton.Point:
        if scores[i] <= KEYPOINT_SCORE:
            return None
        return (float(kpts[i, 0]) / max(width - 1, 1), float(kpts[i, 1]) / max(height - 1, 1))

    points: list[skeleton.Point] = []
    for src in COCO_TO_OPENPOSE:
        if src is None:
            right, left = frac(6), frac(5)
            mid = (
                None
                if right is None or left is None
                else ((right[0] + left[0]) / 2, (right[1] + left[1]) / 2)
            )
            points.append(mid)
        else:
            points.append(frac(src))
    return skeleton.validate(points)
