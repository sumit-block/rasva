#!/usr/bin/env python3
"""Draw the built-in pose figures for Image Studio (phase I2).

    cd backend && uv run python ../scripts/make_pose_figures.py

Writes OpenPose-style stick figures (512 x 768 PNG, coloured limbs on black) to
frontend/public/poses/, plus poses.json with their keypoints. The figures are
drawn from the hand-written keypoints below, so they are original work and carry
no third-party licence. Rerun after changing a pose; the output is deterministic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rasva.engines.pose import skeleton  # noqa: E402

WIDTH, HEIGHT = 512, 768
OUT = ROOT / "frontend" / "public" / "poses"

# Keypoints in COCO-18 order (see skeleton.KEYPOINTS), as fractions of the image.
# "Right" is the person's right, which is the left of the picture when they face us.
_FACE_FRONT = [(0.48, 0.125), (0.52, 0.125), (0.46, 0.135), (0.54, 0.135)]
_LEGS_STANDING = [
    (0.44, 0.50),
    (0.44, 0.68),
    (0.44, 0.86),
    (0.56, 0.50),
    (0.56, 0.68),
    (0.56, 0.86),
]


def _pose(nose, neck, r_arm, l_arm, legs, face):
    """Assemble 18 points: r_arm/l_arm = (shoulder, elbow, wrist); legs = 6 hip/knee/ankle."""
    rh, rk, ra, lh, lk, la = legs
    return [nose, neck, *r_arm, *l_arm, rh, rk, ra, lh, lk, la, *face]


POSES = {
    "standing-front": {
        "label": "Standing, facing forward",
        "framing": "full_body",
        "points": _pose((0.5, 0.14), (0.5, 0.22),
                        [(0.40, 0.23), (0.37, 0.36), (0.36, 0.48)],
                        [(0.60, 0.23), (0.63, 0.36), (0.64, 0.48)],
                        _LEGS_STANDING, _FACE_FRONT),
    },
    "three-quarter": {
        "label": "Standing, three-quarter turn",
        "framing": "full_body",
        "points": _pose((0.53, 0.14), (0.5, 0.22),
                        [(0.43, 0.23), (0.41, 0.36), (0.42, 0.48)],
                        [(0.57, 0.235), (0.59, 0.36), (0.58, 0.47)],
                        [(0.46, 0.50), (0.45, 0.68), (0.44, 0.86),
                         (0.54, 0.505), (0.55, 0.68), (0.56, 0.85)],
                        [(0.515, 0.125), (0.55, 0.127), (0.48, 0.135), None]),
    },
    "hands-on-hips": {
        "label": "Hands on hips",
        "framing": "full_body",
        "points": _pose((0.5, 0.14), (0.5, 0.22),
                        [(0.40, 0.23), (0.30, 0.36), (0.43, 0.48)],
                        [(0.60, 0.23), (0.70, 0.36), (0.57, 0.48)],
                        _LEGS_STANDING, _FACE_FRONT),
    },
    "waving": {
        "label": "Waving",
        "framing": "full_body",
        "points": _pose((0.5, 0.14), (0.5, 0.22),
                        [(0.40, 0.23), (0.31, 0.20), (0.30, 0.08)],
                        [(0.60, 0.23), (0.63, 0.36), (0.64, 0.48)],
                        _LEGS_STANDING, _FACE_FRONT),
    },
    "walking": {
        "label": "Walking",
        "framing": "full_body",
        "points": _pose((0.5, 0.14), (0.5, 0.22),
                        [(0.40, 0.23), (0.37, 0.35), (0.41, 0.46)],
                        [(0.60, 0.23), (0.64, 0.35), (0.62, 0.47)],
                        [(0.45, 0.50), (0.42, 0.67), (0.38, 0.85),
                         (0.55, 0.50), (0.58, 0.67), (0.62, 0.84)],
                        _FACE_FRONT),
    },
    "seated": {
        # Seen from the side, facing right, so the thighs read as horizontal.
        "label": "Seated on a chair",
        "framing": "full_body",
        "points": _pose((0.57, 0.17), (0.5, 0.25),
                        [(0.53, 0.26), (0.56, 0.39), (0.64, 0.47)],
                        [(0.47, 0.26), (0.50, 0.39), (0.59, 0.48)],
                        [(0.52, 0.53), (0.74, 0.55), (0.73, 0.80),
                         (0.46, 0.53), (0.68, 0.54), (0.67, 0.80)],
                        [(0.58, 0.155), (0.555, 0.15), None, (0.51, 0.16)]),
    },
    "portrait": {
        "label": "Head and shoulders",
        "framing": "portrait",
        "points": _pose((0.5, 0.35), (0.5, 0.55),
                        [(0.28, 0.60), None, None],
                        [(0.72, 0.60), None, None],
                        [None] * 6,
                        [(0.45, 0.31), (0.55, 0.31), (0.40, 0.34), (0.60, 0.34)]),
    },
}  # fmt: skip


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for pose_id, spec in POSES.items():
        # Draw from the rounded keypoints that poses.json stores, so the PNG can be
        # re-created exactly from the index (a test checks this).
        stored = skeleton.to_json(spec["points"])
        points = [None if k is None else (k[0], k[1]) for k in stored]
        png = skeleton.encode_png(skeleton.render(points, WIDTH, HEIGHT))
        (OUT / f"{pose_id}.png").write_bytes(png)
        index.append({
            "id": pose_id, "label": spec["label"], "framing": spec["framing"],
            "file": f"{pose_id}.png", "width": WIDTH, "height": HEIGHT,
            "keypoints": skeleton.to_json(points),
        })  # fmt: skip
        print(f"  {pose_id:16} {len(png):>7,} bytes")
    (OUT / "poses.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(index)} figures to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
