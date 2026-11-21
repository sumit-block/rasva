"""Qwen-Image 2.1 Turbo: text-to-image and reference editing on MLX (owner's private use).

Runs through mflux 0.24.0 on Apple's MLX, like FLUX.2 Klein (ADR-0010). What it
relies on:

- The official bf16 files from ``Qwen/Qwen-Image-2.1-Turbo`` (pinned, 32.5 GB) are
  quantised to 4-bit once at install: ``prepare`` builds
  ``QwenImage21Edit(model_path=<model dir>, quantize=4, model_config=<Turbo>)`` and
  ``save_model`` writes ``<model dir>/q4``. The edit class is used because its save
  keeps the vision tower that editing needs; the text-only export drops it.
- ``QwenImage21Edit`` does both tasks: no ``image_paths`` makes a picture from the
  description, 1-4 reference photos edit. One class stays loaded for both.
- Turbo runs its own 8-node schedule with guidance fixed at 1 (mflux refuses other
  values), so Rasva offers steps (1-8) and size only. Sizes are multiples of 32;
  below 1024 px Turbo turns fine detail into a pixel grid.
- Rasva passes explicit width and height (mflux would otherwise take the aspect
  ratio of the last reference) and keeps prompt rewriting, auto-masks and
  self-verification off: they would run extra text generation over the owner's words.
- The output is RGBA; Rasva flattens it onto white and saves the pixels itself,
  so the prompt and reference paths never go into the file.

Licence: Qwen Research License ("research or evaluation purposes only"). The
owner installs it for private, non-commercial use (ADR-0010); it is classed
``restricted`` so the installer asks for confirmation.

Model files come from manifest.json, created by ``scripts/pin_engine.py qwen21turbo``.
Module import must stay light (no mlx here).
"""

from __future__ import annotations

import json
import math
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from rasva.engines import manifest as manifest_mod
from rasva.engines.contract import (
    CompatibilityReport,
    Engine,
    EngineDescriptor,
    EngineError,
    EngineInputError,
    InstallContext,
    OutputFile,
    Preset,
    RunContext,
    RunResult,
    RuntimeContext,
    SystemFacts,
)
from rasva.engines.mlx_common import (
    CACHE_LIMIT_BYTES,
    call,
    peak,
    prepare_references,
    quiet_library_logs,
    reset_peak,
    step_callback,
)

ENGINE_ID = "qwen-image-2.1-turbo"
REPO_ID = "Qwen/Qwen-Image-2.1-Turbo"
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
TASKS = ("image.generate", "image.edit")
BITS = 4
Q_DIR = f"q{BITS}"
MARKER = "rasva-prepared.json"
MAX_PROMPT_CHARS = 2_000
MAX_SEED = 2**32 - 1
MAX_REFERENCES = 4  # Rasva's limit; the model takes up to 10
SIZE_STEP = 32
SIZE_RANGE = (768, 1536)
EDIT_AREA = 1024 * 1024  # an edit without a size keeps the main photo's shape at this area
DEFAULTS = {"steps": 8, "width": 1024, "height": 1024}
RANGES = {"steps": (1, 8)}
ROLE_ORDER = {"person": 0, "garment": 1, "pose": 2, "background": 3}

PRESETS = [
    Preset(id="landscape", label="Landscape", description="1152 × 896.",
           settings={"width": 1152, "height": 896}),
    Preset(id="square", label="Square", description="1024 × 1024.",
           settings={"width": 1024, "height": 1024}),
    Preset(id="portrait", label="Portrait", description="896 × 1152.",
           settings={"width": 896, "height": 1152}),
]  # fmt: skip

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID})
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST)
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS)
_Q4_ESTIMATE = 9 * 1024**3  # 4-bit transformer + text encoder + fp32 VAE; measured after install
_ENV_ESTIMATE = 1_500 * 1024**2  # same packages as FLUX.2 Klein (mflux 0.24.0)


class Qwen21TurboEngine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="Qwen-Image 2.1 Turbo",
        summary=(
            "Newer, larger model: makes pictures from a description and edits your photos, "
            "with strong text in pictures. Slower and heavier than FLUX.2 Klein."
        ),
        version="2.1-turbo-q4",
        modality="image",
        tasks=list(TASKS),
        features=["prompt", "seed", "references", "presets"],
        license="Qwen Research License (research or evaluation only); mflux is MIT",
        license_class="restricted",
        license_notes=[
            "Licensed for research or evaluation only; commercial use needs a separate "
            "licence from Qwen. Installed for the owner's private, non-commercial use.",
            "Runs without a content filter; what you make, and how you use it, is your "
            "responsibility.",
            "Quantised to 4-bit on this Mac to fit in memory: less fine detail than the "
            "full model. Pictures smaller than 1024 px look blocky, so sizes start at 768.",
            "Images are labelled as AI-generated in their details. Your photos stay on this Mac.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _Q4_ESTIMATE + _ENV_ESTIMATE,
        # Estimate until measured on the M2 (docs/engines/qwen21turbo.md).
        approx_peak_memory_bytes=12 * 1024**3,
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.12",
        presets=PRESETS,
        input_ranges={"steps": [1, 8, DEFAULTS["steps"]]},
    )

    def __init__(self) -> None:
        self._model: Any = None
        self._job: dict[str, Any] = {}

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not self.descriptor.artifacts:
            reasons.append(
                "Qwen-Image 2.1 Turbo's download list has not been pinned yet. "
                "Run scripts/pin_engine.py qwen21turbo once."
            )
        if not system.apple_silicon:
            reasons.append(
                "Qwen-Image 2.1 Turbo runs on Apple's MLX, which needs an Apple Silicon Mac."
            )
        return CompatibilityReport(compatible=not reasons, reasons=reasons)

    def verify(self, ctx: InstallContext) -> bool:
        return (
            bool(self.descriptor.artifacts)
            and all((ctx.model_dir / a.path).is_file() for a in self.descriptor.artifacts)
            and _prepared(ctx.model_dir) is not None
        )

    def prepare(self, ctx: InstallContext) -> None:
        """Quantise the bf16 files to 4-bit once, into ``<model dir>/q4`` (atomic)."""
        if _prepared(ctx.model_dir) is not None:
            return
        quiet_library_logs()
        edit_cls, turbo = _mflux()

        tmp = ctx.model_dir / f".{Q_DIR}.partial"
        shutil.rmtree(tmp, ignore_errors=True)
        started = time.perf_counter()
        model = edit_cls(model_path=str(ctx.model_dir), quantize=BITS, model_config=turbo)
        model.save_model(str(tmp))
        del model
        (tmp / MARKER).write_text(json.dumps({
            "bits": BITS, "source_repo": REPO_ID,
            "source_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
            "seconds": round(time.perf_counter() - started, 1),
        }))  # fmt: skip
        target = ctx.model_dir / Q_DIR
        shutil.rmtree(target, ignore_errors=True)
        os.replace(tmp, target)

    # ---- runtime ----

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task not in TASKS:
            raise EngineInputError(f"Qwen-Image 2.1 Turbo cannot do '{task}'.")
        prompt = " ".join(str(inputs.get("prompt") or "").split())
        if not prompt:
            raise EngineInputError(
                "Describe the change you want." if task == "image.edit"
                else "Describe the picture you want."
            )  # fmt: skip
        if len(prompt) > MAX_PROMPT_CHARS:
            raise EngineInputError(f"Keep the description under {MAX_PROMPT_CHARS:,} characters.")
        out: dict[str, Any] = {"prompt": prompt}
        try:
            out["steps"] = int(inputs.get("steps", DEFAULTS["steps"]))
        except (TypeError, ValueError) as exc:
            raise EngineInputError("Steps must be a whole number.") from exc
        lo, hi = RANGES["steps"]
        if not lo <= out["steps"] <= hi:
            raise EngineInputError(f"Steps must be between {lo} and {hi}.")

        sized = task == "image.generate" or "width" in inputs or "height" in inputs
        if sized:
            try:
                out["width"] = int(inputs.get("width", DEFAULTS["width"]))
                out["height"] = int(inputs.get("height", DEFAULTS["height"]))
            except (TypeError, ValueError) as exc:
                raise EngineInputError("Width and height must be whole numbers.") from exc
            lo, hi = SIZE_RANGE
            for key in ("width", "height"):
                if not lo <= out[key] <= hi or out[key] % SIZE_STEP:
                    raise EngineInputError(
                        f"Width and height must be multiples of {SIZE_STEP} between {lo} and {hi}."
                    )

        seed = inputs.get("seed")
        if seed in (None, ""):
            seed = secrets.randbelow(MAX_SEED + 1)  # stored, so retries reproduce
        try:
            out["seed"] = int(seed)
        except (TypeError, ValueError) as exc:
            raise EngineInputError("The seed must be a whole number.") from exc
        if not 0 <= out["seed"] <= MAX_SEED:
            raise EngineInputError(f"The seed must be between 0 and {MAX_SEED:,}.")

        refs = inputs.get("references") or []
        if task == "image.generate" and refs:
            raise EngineInputError("To use photos, edit instead of generating from text.")
        if task == "image.edit":
            if not refs:
                raise EngineInputError("Choose at least one photo to edit.")
            if len(refs) > MAX_REFERENCES:
                raise EngineInputError(f"Use at most {MAX_REFERENCES} photos at once.")
            clean = []
            for ref in refs:
                path, role = str(ref.get("path", "")), str(ref.get("role") or "person")
                if not path or not Path(path).is_file():
                    raise EngineInputError("A chosen photo is missing. Choose it again.")
                if role not in ROLE_ORDER:
                    raise EngineInputError(f"Unknown photo role {role!r}.")
                clean.append({"asset_id": ref.get("asset_id"), "role": role, "path": path})
            out["references"] = sorted(clean, key=lambda r: ROLE_ORDER[r["role"]])
        return out

    def load(self, ctx: RuntimeContext) -> None:
        if _prepared(ctx.model_dir) is None:
            raise EngineError(
                "not_prepared",
                "Qwen-Image 2.1 Turbo was not prepared on this Mac. "
                "Remove it and install it again.",
            )
        quiet_library_logs()
        import mlx.core as mx

        call(mx, "set_cache_limit", CACHE_LIMIT_BYTES)
        edit_cls, turbo = _mflux()
        self._model = edit_cls(model_path=str(ctx.model_dir / Q_DIR), model_config=turbo)
        self._model.callbacks.register(step_callback(self))

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._model is not None, "load() not called"
        import mlx.core as mx
        import numpy as np
        from PIL import Image

        refs = inputs.get("references") or []
        ref_dir = ctx.output_dir / ".references"
        try:
            paths = prepare_references(refs, ref_dir) if refs else []
            width, height = inputs.get("width"), inputs.get("height")
            if not width or not height:
                with Image.open(paths[0]) as main:  # the person / main photo leads
                    width, height = fit_size(main.width, main.height)
            ctx.report_progress(0.0, "Reading your photos" if refs else "Starting")
            reset_peak(mx)
            self._job = {"ctx": ctx, "steps": inputs["steps"]}
            started = time.perf_counter()
            result = self._model.generate_image(
                seed=inputs["seed"], prompt=inputs["prompt"],
                num_inference_steps=inputs["steps"], guidance=1.0,
                width=width, height=height, image_paths=paths or None,
                enhance_prompt=False, verify=False,
            )  # fmt: skip
            elapsed = time.perf_counter() - started
        finally:
            self._job = {}
            shutil.rmtree(ref_dir, ignore_errors=True)
        image = _flatten(result.image)
        pixels = np.asarray(image, dtype=np.float32)
        if not np.isfinite(pixels).all() or float(pixels.max()) < 5:
            raise EngineError("blank_image", "The image came out blank. Try again.")
        ctx.report_progress(1.0, "Saving image")
        image.save(ctx.output_dir / "image.png", format="PNG")  # pixels only, no metadata
        return RunResult(
            outputs=[OutputFile(
                filename="image.png", media_type="image/png",
                metadata={
                    "kind": "image", "engine": ENGINE_ID, "task": task,
                    "model_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
                    "quantization": BITS, "width": image.width, "height": image.height,
                    "seed": inputs["seed"], "steps": inputs["steps"], "guidance": 1.0,
                    "source_asset_ids": [r["asset_id"] for r in refs],
                    "roles": [r["role"] for r in refs],
                    "synthetic": True, "content_filter": False,
                },
            )],
            metrics={
                "generation_s": round(elapsed, 3),
                "s_per_step": round(elapsed / inputs["steps"], 4),
                "steps": inputs["steps"], "width": width, "height": height,
                "references": len(refs), "device": "mlx", "quantization": BITS,
                "gpu_peak_bytes": peak(mx),
            },
        )  # fmt: skip

    def unload(self) -> None:
        self._model = None


def fit_size(width: int, height: int, area: int = EDIT_AREA) -> tuple[int, int]:
    """The main photo's shape at about ``area`` pixels, in multiples of 32, within range."""
    lo, hi = SIZE_RANGE
    ratio = width / height if height else 1.0
    w = math.sqrt(area * ratio)
    h = w / ratio

    def snap(v: float) -> int:
        return min(hi, max(lo, int(round(v / SIZE_STEP)) * SIZE_STEP))

    return snap(w), snap(h)


def _flatten(image: Any) -> Any:
    """RGBA output onto white, as the model's own vision encoder sees references."""
    from PIL import Image

    if getattr(image, "mode", "RGB") != "RGBA":
        return image.convert("RGB")
    canvas = Image.new("RGB", image.size, (255, 255, 255))
    canvas.paste(image, mask=image.getchannel("A"))
    return canvas


def _mflux() -> tuple[Any, Any]:
    from mflux.models.common.config import ModelConfig
    from mflux.models.qwen21.variants.edit.qwen_image_21_edit import QwenImage21Edit

    return QwenImage21Edit, ModelConfig.qwen_image_21_turbo()


def _prepared(model_dir: Path) -> dict[str, Any] | None:
    marker = model_dir / Q_DIR / MARKER
    try:
        info = json.loads(marker.read_text())
    except (OSError, ValueError):
        return None
    return info if info.get("bits") == BITS else None
