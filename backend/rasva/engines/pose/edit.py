"""Same person, new pose (phase I2) on the I1 editing engine (FLUX.2 Klein 4B).

Klein has no pose ControlNet that diffusers can load. Pose is steered through
its multi-reference editing instead: the person photo and a pose image go in as
references and the prompt says which is which. This module holds everything
pose-specific so the I1 engine stays one engine with one ~16 GB install:

- ``is_pose_edit(inputs)`` tells the I1 engine to hand an ``image.edit`` job here
  (any job with a ``pose`` reference).
- ``validate(inputs)`` runs in the API process (pure, no torch).
- ``plan(inputs, ...)`` decides the reference order and sizes, the prompt, the
  output size and the optional pose-LoRA weight.
- ``run(pipe, plan, ctx, ...)`` calls a ``Flux2KleinPipeline``-shaped object with
  a CPU-generator seed, per-step progress and cancel, and refuses blank results.

Integration in the I1 engine (``validate_input`` / ``run``)::

    if edit.is_pose_edit(inputs):
        return edit.validate(inputs)                      # in validate_input
    ...
    if edit.is_pose_edit(inputs):                         # in run
        p = edit.plan(inputs, lora_available=self._pose_lora_loaded)
        return edit.run(self._pipe, p, ctx, torch=torch, device=self.device,
                        load_image=edit.load_image, model_revision=REVISION)

Unverified (see docs/engines/pose-control.md): whether Klein follows an
OpenPose stick figure without the pose LoRA (``pose_source`` lets the user send
the original pose photo instead), and whether the identity settings make a
visible difference. The prompt never goes into metadata or logs.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from rasva.engines.contract import (
    EngineError,
    EngineInputError,
    OutputFile,
    RunContext,
    RunResult,
)
from rasva.engines.pose import skeleton

TASK = "image.edit"
MAX_SEED = 2**32 - 1
MAX_EXTRA_CHARS = 500
# Klein 4B (distilled) is made for 4 steps at guidance 1.0 (model card). A few
# more steps are allowed for testing; guidance only matters for the base model.
RANGES = {"steps": (2, 12), "guidance": (1.0, 6.0)}
DEFAULTS = {"steps": 4, "guidance": 1.0}
# Output long side. 1024 is Klein's default; 768 is the conservative start on the
# 16 GB M2 until I1 measures memory. Sides are rounded to multiples of 16.
OUT_LONG_SIDE = 768
POSE_LORA_TRIGGER = "refcontrol"
POSE_LORA_ADAPTER = "refcontrol_pose"  # adapter name the I1 engine loads the LoRA under


@dataclass(frozen=True)
class Identity:
    id: str
    label: str
    description: str
    person_side: int  # long side of the person reference sent to the model
    pose_side: int  # long side of the pose reference
    lora_scale: float  # pose-LoRA weight, used only when the LoRA is installed
    emphasis: str  # sentence added to the prompt


IDENTITY = {
    i.id: i
    for i in (
        Identity("keep_face", "Keep the face closely",
                 "Stays closest to the person in your photo; the pose may be looser.",
                 1024, 512, 0.7,
                 "Preserve the face, facial features, skin tone and hair exactly."),
        Identity("balanced", "Balanced", "A good mix of likeness and pose.",
                 1024, 768, 0.85,
                 "Keep the person recognisable while matching the pose."),
        Identity("follow_pose", "Follow the pose closely",
                 "Matches the pose most exactly; the likeness may drift a little.",
                 768, 1024, 1.0,
                 "Match the position of every limb, the head angle and the body turn precisely."),
    )
}  # fmt: skip
DEFAULT_IDENTITY = "balanced"
POSE_SOURCES = ("figure", "photo")


def is_pose_edit(inputs: dict[str, Any]) -> bool:
    refs = inputs.get("references")
    return isinstance(refs, list) and any(
        isinstance(r, dict) and r.get("role") == "pose" for r in refs
    )


def validate(inputs: dict[str, Any]) -> dict[str, Any]:
    """Normalise a pose-edit job's inputs (runs in the API process)."""
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
    if set(by_role) - {"person", "pose", "background"}:
        raise EngineInputError(
            "A pose change uses a person photo, a pose and an optional background."
        )
    clean_refs = []
    for r in refs:
        if not r.get("path") or not Path(str(r["path"])).is_file():
            raise EngineInputError("A reference photo is missing. Choose it again.")
        clean_refs.append({
            "asset_id": r.get("asset_id"), "role": r["role"], "path": str(r["path"]),
            "pose_figure": bool(r.get("pose_figure")),
        })  # fmt: skip
    extra = " ".join(str(inputs.get("prompt") or "").split())
    if len(extra) > MAX_EXTRA_CHARS:
        raise EngineInputError(f"Keep extra instructions under {MAX_EXTRA_CHARS} characters.")
    identity = inputs.get("identity") or DEFAULT_IDENTITY
    if identity not in IDENTITY:
        raise EngineInputError("Choose how closely to keep the face.")
    pose_ref = by_role["pose"][0]
    pose_source = inputs.get("pose_source") or (
        "figure" if pose_ref.get("pose_figure") else "photo"
    )
    if pose_source not in POSE_SOURCES:
        raise EngineInputError("The pose must be a stick figure or a photo.")
    out: dict[str, Any] = {"references": clean_refs, "prompt": extra, "identity": identity,
                           "pose_source": pose_source}  # fmt: skip
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
    return out


@dataclass
class RefImage:
    path: str
    role: str
    max_side: int
    asset_id: str | None = None


@dataclass
class PosePlan:
    images: list[RefImage]
    prompt: str
    width: int
    height: int
    steps: int
    guidance: float
    seed: int
    identity: str
    pose_source: str
    lora_scale: float | None
    source_asset_ids: list[str] = field(default_factory=list)


def _round16(v: float) -> int:
    return max(256, int(round(v / 16)) * 16)


def output_size(pose_size: tuple[int, int] | None) -> tuple[int, int]:
    """Output (w, h) with the pose image's aspect ratio; portrait 2:3 if unknown."""
    w, h = pose_size or (2, 3)
    scale = OUT_LONG_SIDE / max(w, h)
    return _round16(w * scale), _round16(h * scale)


def plan(
    inputs: dict[str, Any],
    *,
    lora_available: bool = False,
    pose_size: tuple[int, int] | None = None,
) -> PosePlan:
    ident = IDENTITY[inputs["identity"]]
    refs = {r["role"]: r for r in inputs["references"]}
    person, pose, bg = refs["person"], refs["pose"], refs.get("background")
    figure = inputs["pose_source"] == "figure"
    use_lora = lora_available and figure  # the LoRA was trained on OpenPose skeletons
    person_img = RefImage(person["path"], "person", ident.person_side, person.get("asset_id"))
    pose_img = RefImage(pose["path"], "pose", ident.pose_side, pose.get("asset_id"))
    if use_lora:
        # The LoRA's own convention: image 1 = skeleton, image 2 = identity.
        images = [pose_img, person_img]
        text = f"{POSE_LORA_TRIGGER} apply pose from image 1 with reference from image 2."
        p_no, q_no = 2, 1
    else:
        images = [person_img, pose_img]
        p_no, q_no = 1, 2
        what = "the stick-figure pose diagram" if figure else "the person"
        text = (
            f"Show the person from image {p_no} standing or sitting in exactly the body pose of "
            f"{what} in image {q_no}. Keep the face, skin tone, hair, body shape and clothing "
            f"from image {p_no}."
        )
        if figure:
            text += " Do not draw the stick figure or any lines."
        else:
            text += f" Do not copy the face, body or clothing of the person in image {q_no}."
    if bg is not None:
        images.append(RefImage(bg["path"], "background", 1024, bg.get("asset_id")))
        text += f" Place them in the setting from image {len(images)}."
    text += " " + ident.emphasis
    if inputs["prompt"]:
        text += " " + inputs["prompt"]
    width, height = output_size(pose_size)
    return PosePlan(
        images=images, prompt=text, width=width, height=height, steps=inputs["steps"],
        guidance=inputs["guidance"], seed=inputs["seed"], identity=ident.id,
        pose_source=inputs["pose_source"], lora_scale=ident.lora_scale if use_lora else None,
        source_asset_ids=[i.asset_id for i in images if i.asset_id],
    )  # fmt: skip


def load_image(path: str, max_side: int) -> Any:
    """Open a reference as RGB, upright, no larger than max_side (engine env only)."""
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def image_size(path: str) -> tuple[int, int] | None:
    """(w, h) of a PNG without Pillow (pose figures are PNG); None otherwise."""
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def run(
    pipe: Any,
    p: PosePlan,
    ctx: RunContext,
    *,
    torch: Any,
    device: str,
    load_image: Callable[[str, int], Any] = load_image,
    model_revision: str | None = None,
    engine_id: str = "flux2-klein-4b",
    lora_loaded: bool = False,
) -> RunResult:
    gpu_peak = 0

    def on_step(_pipe: Any, i: int, _t: Any, kw: dict[str, Any]) -> dict[str, Any]:
        nonlocal gpu_peak
        ctx.raise_if_canceled()
        ctx.report_progress((i + 1) / p.steps, f"Step {i + 1} of {p.steps}")
        if device == "mps":
            gpu_peak = max(gpu_peak, int(torch.mps.driver_allocated_memory()))
        return kw

    ctx.report_progress(0.0, "Preparing the photos")
    images = [load_image(i.path, i.max_side) for i in p.images]
    ctx.raise_if_canceled()
    started = time.perf_counter()
    generator = torch.Generator(device="cpu").manual_seed(p.seed)  # same noise on any device
    if lora_loaded:
        # Standard diffusers LoRA API; weight 0 switches the pose LoRA off for photo poses.
        pipe.set_adapters([POSE_LORA_ADAPTER], adapter_weights=[p.lora_scale or 0.0])
    result = pipe(
        prompt=p.prompt, image=images, height=p.height, width=p.width,
        num_inference_steps=p.steps, guidance_scale=p.guidance, generator=generator,
        callback_on_step_end=on_step, output_type="np",
    )  # fmt: skip
    elapsed = time.perf_counter() - started
    image = np.asarray(result.images[0], dtype=np.float32)
    if not np.isfinite(image).all() or float(image.max()) < 0.02:
        raise EngineError(
            "blank_image",
            "The image came out blank. Try again; if it keeps happening, report it.",
        )
    ctx.report_progress(1.0, "Saving image")
    pixels = (np.clip(image, 0, 1) * 255).round().astype(np.uint8)
    (ctx.output_dir / "image.png").write_bytes(skeleton.encode_png(pixels))
    if device == "mps":
        gpu_peak = max(gpu_peak, int(torch.mps.driver_allocated_memory()))
    h, w = pixels.shape[:2]
    return RunResult(
        outputs=[OutputFile(
            filename="image.png", media_type="image/png",
            metadata={
                "kind": "image", "engine": engine_id, "model_revision": model_revision,
                "edit": "pose", "identity": p.identity, "pose_source": p.pose_source,
                "reference_roles": [i.role for i in p.images],
                "source_asset_ids": p.source_asset_ids,
                "pose_lora_scale": p.lora_scale,
                "width": w, "height": h, "seed": p.seed, "steps": p.steps,
                "guidance": p.guidance, "synthetic": True,
            },
        )],
        metrics={
            "generation_s": round(elapsed, 3), "s_per_step": round(elapsed / p.steps, 4),
            "steps": p.steps, "device": device, "gpu_peak_bytes": gpu_peak or None,
            "references": len(p.images),
        },
    )  # fmt: skip
