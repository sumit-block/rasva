"""Same person, new pose (phase I2) on the I1 editing engine (FLUX.2 Klein 4B, mflux).

Klein has no pose ControlNet. Pose is steered through its multi-reference editing:
the person photo and a pose image go in as references, in that order, and the
prompt says which is which. This module turns a pose job's inputs into the
Klein engine's own input shape, so the engine runs it like any other edit:

- ``is_pose_edit(inputs)``: an ``image.edit`` job with a ``pose`` reference.
- ``validate(inputs)`` (API process, pure, no ML imports) returns the engine's
  inputs: the full instruction as ``prompt``, the user's own words as
  ``user_prompt``, the output size from the pose's aspect ratio, the seed, and
  the references in order, each with the ``max_side`` it is sent at.
- ``output_metadata(inputs)``: the pose fields the engine adds to the PNG's
  metadata. The prompt itself never goes into metadata or logs.

Integration in ``rasva.engines.flux2klein`` (three small hooks, marked "I2"):
``validate_input`` hands pose jobs to ``validate``; ``_prepare_references``
honours each reference's ``max_side``; ``run`` merges ``output_metadata``.

Unverified (docs/engines/pose-control.md): whether Klein follows an OpenPose
stick figure (``pose_source`` lets the user send the original pose photo
instead), and whether the identity settings make a visible difference.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rasva.engines.contract import EngineInputError

TASK = "image.edit"
MAX_SEED = 2**32 - 1
MAX_EXTRA_CHARS = 500
# Same limits as the Klein engine (a test keeps them in step). Klein 4B is
# distilled for 4 steps at guidance 1.0.
RANGES = {"steps": (1, 8), "guidance": (1.0, 5.0)}
DEFAULTS = {"steps": 4, "guidance": 1.0}
# Output long side: the Klein engine's Standard size; sides are multiples of 16.
OUT_LONG_SIDE = 768
MIN_SIDE = 256
BACKGROUND_SIDE = 1024


@dataclass(frozen=True)
class Identity:
    id: str
    label: str
    description: str
    person_side: int  # long side of the person photo sent to the model
    pose_side: int  # long side of the pose image
    emphasis: str  # sentence added to the instruction


IDENTITY = {
    i.id: i
    for i in (
        Identity("keep_face", "Keep the face closely",
                 "Stays closest to the person in your photo; the pose may be looser.",
                 1024, 512,
                 "Preserve the face, facial features, skin tone and hair exactly."),
        Identity("balanced", "Balanced", "A good mix of likeness and pose.",
                 1024, 768,
                 "Keep the person recognisable while matching the pose."),
        Identity("follow_pose", "Follow the pose closely",
                 "Matches the pose most exactly; the likeness may drift a little.",
                 768, 1024,
                 "Match the position of every limb, the head angle and the body turn precisely."),
    )
}  # fmt: skip
DEFAULT_IDENTITY = "balanced"
POSE_SOURCES = ("figure", "photo")
ROLE_ORDER = ("person", "pose", "background")


def is_pose_edit(inputs: dict[str, Any]) -> bool:
    refs = inputs.get("references")
    return isinstance(refs, list) and any(
        isinstance(r, dict) and r.get("role") == "pose" for r in refs
    )


def image_size(path: str) -> tuple[int, int] | None:
    """(width, height) of a PNG or JPEG from its header, without Pillow; else None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64 * 1024)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", head[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", head[i + 5 : i + 9])
                return (w, h) if w and h else None
            i += 2 + length
    return None


def output_size(pose_size: tuple[int, int] | None) -> tuple[int, int]:
    """Output (w, h) with the pose image's aspect ratio; portrait 2:3 if unknown.
    Phone photos are often stored rotated (EXIF); then this guess may be sideways."""
    w, h = pose_size or (2, 3)
    scale = OUT_LONG_SIDE / max(w, h)

    def side(v: float) -> int:
        return min(OUT_LONG_SIDE, max(MIN_SIDE, int(round(v * scale / 16)) * 16))

    return side(w), side(h)


def instruction(identity: str, pose_source: str, background: bool, extra: str) -> str:
    what = "the stick-figure pose diagram" if pose_source == "figure" else "the person"
    text = (
        f"Show the person from image 1 in exactly the body pose of {what} in image 2. "
        "Keep the face, skin tone, hair, body shape and clothing from image 1."
    )
    if pose_source == "figure":
        text += " Do not draw the stick figure or any lines."
    else:
        text += " Do not copy the face, body or clothing of the person in image 2."
    if background:
        text += " Place them in the setting from image 3."
    text += " " + IDENTITY[identity].emphasis
    return f"{text} {extra}" if extra else text


def validate(inputs: dict[str, Any]) -> dict[str, Any]:
    """A pose job's inputs -> the Klein engine's inputs (runs in the API process)."""
    refs = [r for r in inputs.get("references") or [] if isinstance(r, dict)]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for r in refs:
        by_role.setdefault(str(r.get("role")), []).append(r)
    if len(by_role.get("person", [])) != 1:
        raise EngineInputError("Choose one photo of the person.")
    if len(by_role.get("pose", [])) != 1:
        raise EngineInputError("Choose one pose.")
    if len(by_role.get("background", [])) > 1:
        raise EngineInputError("Choose at most one background.")
    if set(by_role) - set(ROLE_ORDER):
        raise EngineInputError(
            "A pose change uses a person photo, a pose and an optional background."
        )
    for r in refs:
        if not r.get("path") or not Path(str(r["path"])).is_file():
            raise EngineInputError("A chosen photo is missing. Choose it again.")
    # Retry passes the stored inputs back: the user's words are in user_prompt then.
    raw_extra = inputs["user_prompt"] if "user_prompt" in inputs else inputs.get("prompt")
    extra = " ".join(str(raw_extra or "").split())
    if len(extra) > MAX_EXTRA_CHARS:
        raise EngineInputError(f"Keep extra instructions under {MAX_EXTRA_CHARS} characters.")
    identity = inputs.get("identity") or DEFAULT_IDENTITY
    if identity not in IDENTITY:
        raise EngineInputError("Choose how closely to keep the face.")
    pose_ref = by_role["pose"][0]
    default_source = "figure" if pose_ref.get("pose_figure") else "photo"
    pose_source = inputs.get("pose_source") or default_source
    if pose_source not in POSE_SOURCES:
        raise EngineInputError("The pose must be a stick figure or a photo.")
    out: dict[str, Any] = {}
    try:
        out["steps"] = int(inputs.get("steps", DEFAULTS["steps"]))
        out["guidance"] = round(float(inputs.get("guidance", DEFAULTS["guidance"])), 2)
    except (TypeError, ValueError) as exc:
        raise EngineInputError("Steps and guidance must be numbers.") from exc
    for key, (lo, hi) in RANGES.items():
        if not lo <= out[key] <= hi:
            raise EngineInputError(f"{key.capitalize()} must be between {lo} and {hi}.")
    seed = inputs.get("seed")
    if seed in (None, ""):
        seed = secrets.randbelow(MAX_SEED + 1)  # stored, so retries reproduce
    try:
        out["seed"] = int(seed)
    except (TypeError, ValueError) as exc:
        raise EngineInputError("The seed must be a whole number.") from exc
    if not 0 <= out["seed"] <= MAX_SEED:
        raise EngineInputError(f"The seed must be between 0 and {MAX_SEED:,}.")

    ident = IDENTITY[identity]
    sides = {"person": ident.person_side, "pose": ident.pose_side, "background": BACKGROUND_SIDE}
    ordered = []
    for role in ROLE_ORDER:  # person = image 1, pose = image 2, background = image 3
        for r in by_role.get(role, []):
            ordered.append({"asset_id": r.get("asset_id"), "role": role, "path": str(r["path"]),
                            "max_side": sides[role]})  # fmt: skip
    width, height = output_size(image_size(str(pose_ref["path"])))
    return {
        **out,
        "prompt": instruction(identity, pose_source, "background" in by_role, extra),
        "user_prompt": extra,
        "identity": identity,
        "pose_source": pose_source,
        "width": width,
        "height": height,
        "references": ordered,
    }


def output_metadata(inputs: dict[str, Any]) -> dict[str, Any]:
    """Pose fields for the output PNG's metadata (empty for other edits)."""
    if "identity" not in inputs:
        return {}
    return {"edit": "pose", "identity": inputs["identity"], "pose_source": inputs["pose_source"]}
