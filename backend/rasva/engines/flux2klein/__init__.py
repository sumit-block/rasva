"""FLUX.2 Klein 4B image engine: generation and reference-guided editing (phase I1).

Runs on Apple's MLX through mflux (ADR-0007). What it relies on (mflux 0.24.0):

- The official bf16 files from ``black-forest-labs/FLUX.2-klein-4B`` (pinned, 16 GB)
  are quantised to 4-bit once at install: ``prepare`` builds
  ``Flux2Klein(model_path=<model dir>, quantize=4)`` and ``save_model`` writes the
  result to ``<model dir>/q4``. The quantisation level is stored in the weights.
- ``Flux2KleinEdit(model_path=<q4 dir>)`` loads local files only;
  ``generate_image(seed, prompt, num_inference_steps, height, width, guidance,
  image_paths)`` generates from text, or edits when reference images are given
  (in order). An in-loop callback reports progress and stops on cancel.
- The returned image's ``.image`` (PIL) is saved by Rasva without metadata; mflux's
  own save would embed the prompt and the reference paths.

Model files come from manifest.json, created by ``scripts/pin_engine.py flux2klein``.
Module import must stay light (no mlx here).
"""

from __future__ import annotations

import json
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

ENGINE_ID = "flux2-klein-4b"
REPO_ID = "black-forest-labs/FLUX.2-klein-4B"
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
TASKS = ("image.generate", "image.edit")
BITS = 4
Q_DIR = f"q{BITS}"
MARKER = "rasva-prepared.json"
MAX_PROMPT_CHARS = 2_000
MAX_SEED = 2**32 - 1
MAX_REFERENCES = 4
REFERENCE_MAX_SIDE = 1024  # phone photos are large; bigger adds memory, not detail
SIZE_RANGE = (256, 1024)
DEFAULTS = {"steps": 4, "guidance": 1.0, "width": 768, "height": 768}
RANGES = {"steps": (1, 8), "guidance": (1.0, 5.0)}
# References are passed to the model in this order, so instructions can rely on it.
ROLE_ORDER = {"person": 0, "garment": 1, "pose": 2, "background": 3}

PRESETS = [
    Preset(id="draft", label="Draft", description="512 × 512. Quickest, to check the idea.",
           settings={"width": 512, "height": 512}),
    Preset(id="standard", label="Standard", description="768 × 768. Good detail.",
           settings={"width": 768, "height": 768}),
    Preset(id="large", label="Large", description="1024 × 1024. Most detail, slowest.",
           settings={"width": 1024, "height": 1024}),
]  # fmt: skip

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID})
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST)
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS)
_Q4_ESTIMATE = 4_700 * 1024**2  # mflux's published 4-bit size; measured after install
_ENV_ESTIMATE = 1_500 * 1024**2  # mlx + mflux + torch + opencv; measured after install


class Flux2KleinEngine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="FLUX.2 Klein",
        summary=(
            "Makes pictures from a description and edits your photos: change clothes, "
            "background or style while keeping the person, using up to four photos."
        ),
        version="4B-q4",
        modality="image",
        tasks=list(TASKS),
        features=["prompt", "seed", "references", "presets"],
        license="Apache-2.0 (model); mflux is MIT",
        license_class="permissive",
        license_notes=[
            "Runs without a content filter. Black Forest Labs built in safety training and "
            "encourages filters; what you make, and how you use it, is your responsibility.",
            "Quantised to 4-bit on this Mac to fit in memory: slightly less fine detail "
            "than the full model.",
            "Images are labelled as AI-generated in their details. Your photos stay on this Mac.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _Q4_ESTIMATE + _ENV_ESTIMATE,
        approx_peak_memory_bytes=8 * 1024**3,  # estimate; replaced by the M2 measurement
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.12",
        presets=PRESETS,
    )

    def __init__(self) -> None:
        self._model: Any = None
        self._job: dict[str, Any] = {}

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not self.descriptor.artifacts:
            reasons.append(
                "FLUX.2 Klein's download list has not been pinned yet. "
                "Run scripts/pin_engine.py flux2klein once."
            )
        if not system.apple_silicon:
            reasons.append("FLUX.2 Klein runs on Apple's MLX, which needs an Apple Silicon Mac.")
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
        _quiet_library_logs()
        from mflux.models.flux2.variants import Flux2Klein

        tmp = ctx.model_dir / f".{Q_DIR}.partial"
        shutil.rmtree(tmp, ignore_errors=True)
        started = time.perf_counter()
        model = Flux2Klein(model_path=str(ctx.model_dir), quantize=BITS)
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
            raise EngineInputError(f"FLUX.2 Klein cannot do '{task}'.")
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
            out["guidance"] = round(float(inputs.get("guidance", DEFAULTS["guidance"])), 2)
            out["width"] = int(inputs.get("width", DEFAULTS["width"]))
            out["height"] = int(inputs.get("height", DEFAULTS["height"]))
        except (TypeError, ValueError) as exc:
            raise EngineInputError("Steps, guidance and size must be numbers.") from exc
        for key, (lo, hi) in RANGES.items():
            if not lo <= out[key] <= hi:
                raise EngineInputError(f"{key.capitalize()} must be between {lo} and {hi}.")
        lo, hi = SIZE_RANGE
        for key in ("width", "height"):
            if not lo <= out[key] <= hi or out[key] % 16:
                raise EngineInputError(
                    f"Width and height must be multiples of 16 between {lo} and {hi}."
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
        q_dir = ctx.model_dir / Q_DIR
        if _prepared(ctx.model_dir) is None:
            raise EngineError(
                "not_prepared",
                "FLUX.2 Klein was not prepared on this Mac. Remove it and install it again.",
            )
        _quiet_library_logs()
        from mflux.models.flux2.variants import Flux2KleinEdit

        engine = self

        class Steps:  # mflux in-loop callback: progress and cooperative cancel
            def call_in_loop(self, t: int, seed: int, prompt: str, latents: Any,
                             config: Any, time_steps: Any) -> None:  # fmt: skip
                job = engine._job
                if not job:
                    return
                job["ctx"].raise_if_canceled()
                n = job["steps"]
                job["ctx"].report_progress((t + 1) / (n + 1), f"Step {t + 1} of {n}")

        self._model = Flux2KleinEdit(model_path=str(q_dir))
        self._model.callbacks.register(Steps())

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._model is not None, "load() not called"
        import mlx.core as mx
        import numpy as np

        refs = inputs.get("references") or []
        ref_dir = ctx.output_dir / ".references"
        try:
            paths = _prepare_references(refs, ref_dir) if refs else None
            ctx.report_progress(0.0, "Reading your photos" if refs else "Starting")
            _reset_peak(mx)
            self._job = {"ctx": ctx, "steps": inputs["steps"]}
            started = time.perf_counter()
            result = self._model.generate_image(
                seed=inputs["seed"], prompt=inputs["prompt"],
                num_inference_steps=inputs["steps"], guidance=inputs["guidance"],
                width=inputs["width"], height=inputs["height"], image_paths=paths,
            )  # fmt: skip
            elapsed = time.perf_counter() - started
        finally:
            self._job = {}
            shutil.rmtree(ref_dir, ignore_errors=True)
        image = result.image.convert("RGB")
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
                    "seed": inputs["seed"], "steps": inputs["steps"],
                    "guidance": inputs["guidance"],
                    "source_asset_ids": [r["asset_id"] for r in refs],
                    "roles": [r["role"] for r in refs],
                    "synthetic": True, "content_filter": False,
                },
            )],
            metrics={
                "generation_s": round(elapsed, 3),
                "s_per_step": round(elapsed / inputs["steps"], 4),
                "steps": inputs["steps"], "width": inputs["width"], "height": inputs["height"],
                "references": len(refs), "device": "mlx", "quantization": BITS,
                "gpu_peak_bytes": _peak(mx),
            },
        )  # fmt: skip

    def unload(self) -> None:
        self._model = None


def _prepared(model_dir: Path) -> dict[str, Any] | None:
    marker = model_dir / Q_DIR / MARKER
    try:
        info = json.loads(marker.read_text())
    except (OSError, ValueError):
        return None
    return info if info.get("bits") == BITS else None


def _prepare_references(refs: list[dict[str, Any]], out_dir: Path) -> list[str]:
    """Upright (EXIF rotation applied), RGB, at most 1024 px on the long side."""
    from PIL import Image, ImageOps

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, ref in enumerate(refs):
        with Image.open(ref["path"]) as img:
            pic = ImageOps.exif_transpose(img).convert("RGB")
        pic.thumbnail((REFERENCE_MAX_SIDE, REFERENCE_MAX_SIDE))
        target = out_dir / f"{k}.png"
        pic.save(target, format="PNG")
        paths.append(str(target))
    return paths


def _reset_peak(mx: Any) -> None:
    for owner in (mx, getattr(mx, "metal", None)):
        if owner is not None and hasattr(owner, "reset_peak_memory"):
            owner.reset_peak_memory()
            return


def _peak(mx: Any) -> int | None:
    for owner in (mx, getattr(mx, "metal", None)):
        if owner is not None and hasattr(owner, "get_peak_memory"):
            return int(owner.get_peak_memory())
    return None


def _quiet_library_logs() -> None:
    """Keep library chatter (which can quote prompts) out of engine logs."""
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
