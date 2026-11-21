"""OpenPose-style body skeletons: the 18 COCO keypoints, drawn as a stick figure.

This is the picture format pose ControlNets were trained on (coloured limbs on
black), and it is also easy for people to read. It is shared by:

- the pose extractor (photo -> keypoints -> this picture), in its own engine
  environment, and
- ``scripts/make_pose_figures.py``, which draws the built-in pose figures.

Only numpy and the standard library are used, so the app's own environment
(which has no Pillow) can draw and test figures too. Keypoints are given as
fractions of the image (0..1), or ``None`` when a joint was not seen.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence

import numpy as np

# COCO-18 order used by OpenPose and the ControlNet OpenPose annotator.
KEYPOINTS = (
    "nose", "neck",
    "right_shoulder", "right_elbow", "right_wrist",
    "left_shoulder", "left_elbow", "left_wrist",
    "right_hip", "right_knee", "right_ankle",
    "left_hip", "left_knee", "left_ankle",
    "right_eye", "left_eye", "right_ear", "left_ear",
)  # fmt: skip
N_KEYPOINTS = len(KEYPOINTS)

# Limb pairs (0-based) and colours exactly as OpenPose/ControlNet draw them, so a
# figure from here is read the same way as one from the reference annotator.
LIMBS = (
    (1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
    (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17),
)  # fmt: skip
COLORS = (
    (255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0),
    (0, 255, 0), (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255),
    (0, 0, 255), (85, 0, 255), (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85),
)  # fmt: skip

Point = tuple[float, float] | None


def validate(points: Sequence[Point]) -> list[Point]:
    """Return a clean 18-point list or raise ValueError."""
    if len(points) != N_KEYPOINTS:
        raise ValueError(f"A body pose needs {N_KEYPOINTS} keypoints, got {len(points)}.")
    out: list[Point] = []
    for p in points:
        if p is None:
            out.append(None)
            continue
        x, y = float(p[0]), float(p[1])
        if not (np.isfinite(x) and np.isfinite(y)):
            out.append(None)
            continue
        out.append((min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)))
    return out


def visible(points: Sequence[Point]) -> int:
    return sum(p is not None for p in points)


def bounding_box(points: Sequence[Point]) -> tuple[float, float, float, float] | None:
    seen = [p for p in points if p is not None]
    if not seen:
        return None
    xs, ys = [p[0] for p in seen], [p[1] for p in seen]
    return min(xs), min(ys), max(xs), max(ys)


def framing(points: Sequence[Point]) -> str:
    """'full_body' when ankles or knees are seen, 'upper_body' when hips are, else 'portrait'."""
    seen = {KEYPOINTS[i] for i, p in enumerate(points) if p is not None}
    if seen & {"right_ankle", "left_ankle", "right_knee", "left_knee"}:
        return "full_body"
    if seen & {"right_hip", "left_hip"}:
        return "upper_body"
    return "portrait"


def _disc(img: np.ndarray, cx: float, cy: float, r: float, color: tuple[int, int, int]) -> None:
    h, w, _ = img.shape
    x0, x1 = max(int(cx - r), 0), min(int(cx + r) + 1, w)
    y0, y1 = max(int(cy - r), 0), min(int(cy + r) + 1, h)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
    img[y0:y1, x0:x1][mask] = color


def _segment(img: np.ndarray, a: tuple[float, float], b: tuple[float, float], r: float,
             color: tuple[int, int, int]) -> None:  # fmt: skip
    """A thick line drawn as a capsule (distance to the segment <= r)."""
    h, w, _ = img.shape
    x0, x1 = max(int(min(a[0], b[0]) - r), 0), min(int(max(a[0], b[0]) + r) + 1, w)
    y0, y1 = max(int(min(a[1], b[1]) - r), 0), min(int(max(a[1], b[1]) + r) + 1, h)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy or 1e-6
    t = np.clip(((xx - a[0]) * dx + (yy - a[1]) * dy) / length2, 0.0, 1.0)
    dist2 = (xx - (a[0] + t * dx)) ** 2 + (yy - (a[1] + t * dy)) ** 2
    region = img[y0:y1, x0:x1]
    # OpenPose draws limbs at 60 % opacity over black.
    blended = (np.asarray(color, dtype=np.float32) * 0.6).astype(np.uint8)
    region[dist2 <= r * r] = blended


def render(points: Sequence[Point], width: int, height: int) -> np.ndarray:
    """Draw the skeleton on black; returns an (height, width, 3) uint8 array."""
    pts = validate(points)
    img = np.zeros((height, width, 3), dtype=np.uint8)
    scale = max(width, height)
    limb_r = max(2.0, scale / 128)
    joint_r = max(2.0, scale / 128)
    px = [None if p is None else (p[0] * (width - 1), p[1] * (height - 1)) for p in pts]
    for k, (i, j) in enumerate(LIMBS):
        if px[i] is not None and px[j] is not None:
            _segment(img, px[i], px[j], limb_r, COLORS[k])
    for k, p in enumerate(px):
        if p is not None:
            _disc(img, p[0], p[1], joint_r, COLORS[k])
    return img


def encode_png(img: np.ndarray) -> bytes:
    """Minimal PNG encoder (8-bit RGB) using only the standard library."""
    if img.dtype != np.uint8 or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("encode_png needs an (h, w, 3) uint8 array")
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def to_json(points: Sequence[Point]) -> list[list[float] | None]:
    """Keypoints as stored in asset metadata (rounded, JSON-friendly)."""
    return [None if p is None else [round(p[0], 4), round(p[1], 4)] for p in validate(points)]
