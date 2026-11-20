"""Stable Diffusion 1.5 image engine (phase I0 baseline, PRD §19, ADR-0006).

Prompt -> one 512 x 512 PNG, generated locally with diffusers. What it relies on:

- ``StableDiffusionPipeline.from_pretrained(model_dir, variant="fp16",
  local_files_only=True, use_safetensors=True)`` loads the pinned fp16 files
  from the local folder only. The worker also runs with ``HF_HUB_OFFLINE=1``.
- No safety checker (owner decision, ADR-0006): it is not downloaded and the
  pipeline is built with ``safety_checker=None``.
- The seed is chosen in ``validate_input`` (API process) and stored with the
  job, so a retry or a "same seed" rerun gives the same image.

Model files come from manifest.json (pinned commit + SHA-256), created by
``scripts/pin_engine.py sd15``. Module import must stay light (no torch here).
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import traceback
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

ENGINE_ID = "sd15"
REPO_ID = "stable-diffusion-v1-5/stable-diffusion-v1-5"
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
TASK = "image.generate"
SIZE = 512  # conservative previews on the 16 GB M2 (PRD §19, I0)
MAX_PROMPT_CHARS = 1_000
MAX_SEED = 2**32 - 1
# CLIP reads at most 77 tokens, including the start and end markers.
MAX_TOKENS = 77
RANGES = {"steps": (10, 50), "guidance": (1.0, 15.0)}
DEFAULTS = {"steps": 25, "guidance": 7.5}

PRESETS = [
    Preset(id="quick", label="Quick", description="Fast draft to check the idea.",
           settings={"steps": 15}),
    Preset(id="balanced", label="Balanced", description="Good detail in reasonable time.",
           settings={"steps": 25}),
    Preset(id="detailed", label="Detailed", description="More steps; slower, sometimes cleaner.",
           settings={"steps": 40}),
]  # fmt: skip

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID})
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST)
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS)
# torch + diffusers + transformers + Python 3.12; estimate, replaced by the
# measured size after installation.
_ENV_ESTIMATE = 750 * 1024**2


class StableDiffusion15Engine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="Stable Diffusion 1.5",
        summary=(
            "A small image engine for quick 512 × 512 pictures from a description. "
            "A baseline, not the final image quality."
        ),
        version="1.5",
        modality="image",
        tasks=[TASK],
        features=["prompt", "negative_prompt", "seed", "presets"],
        license="CreativeML OpenRAIL-M (open weights with use restrictions)",
        license_class="restricted",
        license_notes=[
            "The licence forbids some uses (for example harming people, impersonation, "
            "or medical advice). Read it on the model page before installing.",
            "Rasva installs Stable Diffusion 1.5 without its safety checker, so images are "
            "not screened. What you make, and how you use it, is your responsibility.",
            "Images are labelled as AI-generated in their details.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _ENV_ESTIMATE,
        # Measured on the M2: 8.5 GB of GPU memory reserved (fp16, MPS) + 0.5 GB process.
        approx_peak_memory_bytes=9 * 1024**3,
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.12",
        presets=PRESETS,
    )

    def __init__(self) -> None:
        self._pipe: Any = None
        self.device = "cpu"
        self.dtype = "float32"

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not self.descriptor.artifacts:
            reasons.append(
                "Stable Diffusion 1.5's download list has not been pinned yet. "
                "Run scripts/pin_engine.py sd15 once."
            )
        if not system.apple_silicon:
            reasons.append("This image engine's software package is built for Apple Silicon Macs.")
        return CompatibilityReport(compatible=not reasons, reasons=reasons)

    def verify(self, ctx: InstallContext) -> bool:
        return all((ctx.model_dir / a.path).is_file() for a in self.descriptor.artifacts) and bool(
            self.descriptor.artifacts
        )

    # ---- runtime ----

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != TASK:
            raise EngineInputError(f"Stable Diffusion 1.5 cannot do '{task}'.")
        prompt = " ".join(str(inputs.get("prompt") or "").split())
        if not prompt:
            raise EngineInputError("Describe the picture you want.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise EngineInputError(
                f"Keep the description under {MAX_PROMPT_CHARS:,} characters. "
                "Only about the first 60 words are used anyway."
            )
        negative = " ".join(str(inputs.get("negative_prompt") or "").split())
        if len(negative) > MAX_PROMPT_CHARS:
            raise EngineInputError(f"Keep 'avoid' under {MAX_PROMPT_CHARS:,} characters.")
        out: dict[str, Any] = {"prompt": prompt, "negative_prompt": negative}
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

    def load(self, ctx: RuntimeContext) -> None:
        _quiet_library_logs()
        import torch
        from diffusers import DPMSolverMultistepScheduler, StableDiffusionPipeline

        preferred = os.environ.get("RASVA_TORCH_DEVICE") or (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        if preferred == "mps" and not torch.backends.mps.is_available():
            preferred = "cpu"

        def build(device: str) -> Any:
            # fp16 on the Apple GPU; the CPU computes in float32 (fp16 there is slow).
            dtype = torch.float16 if device == "mps" else torch.float32
            pipe = StableDiffusionPipeline.from_pretrained(
                str(ctx.model_dir), torch_dtype=dtype, variant="fp16", use_safetensors=True,
                local_files_only=True, safety_checker=None, feature_extractor=None,
                requires_safety_checker=False,
            )  # fmt: skip
            # DPM++ 2M: good results in 20-30 steps (the default PNDM wants ~50).
            pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
            pipe.set_progress_bar_config(disable=True)
            # No attention slicing: on the M2 (torch 2.11, fp16 on MPS) it turned every
            # latent into NaN, so each image came out blank. PyTorch's built-in attention
            # is already memory-efficient.
            pipe = pipe.to(device)
            return pipe, str(dtype).removeprefix("torch.")

        try:
            self._pipe, self.dtype = build(preferred)
            self.device = preferred
        except Exception:
            if preferred == "cpu":
                raise
            traceback.print_exc()
            print(f"SD 1.5: loading on {preferred} failed; retrying on cpu", file=sys.stderr)
            self._pipe, self.dtype = build("cpu")
            self.device = "cpu"

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._pipe is not None, "load() not called"
        import numpy as np
        import torch
        from PIL import Image

        steps = inputs["steps"]
        gpu_peak = 0

        def on_step(pipe: Any, i: int, t: Any, kw: dict[str, Any]) -> dict[str, Any]:
            nonlocal gpu_peak
            ctx.raise_if_canceled()
            ctx.report_progress((i + 1) / steps, f"Step {i + 1} of {steps}")
            if self.device == "mps":
                gpu_peak = max(gpu_peak, int(torch.mps.driver_allocated_memory()))
            return kw

        tokens = len(self._pipe.tokenizer(inputs["prompt"]).input_ids)
        ctx.report_progress(0.0, "Starting")
        started = time.perf_counter()
        # A CPU generator gives the same noise for a seed on any device.
        generator = torch.Generator(device="cpu").manual_seed(inputs["seed"])
        result = self._pipe(
            inputs["prompt"], negative_prompt=inputs["negative_prompt"] or None,
            num_inference_steps=steps, guidance_scale=inputs["guidance"],
            height=SIZE, width=SIZE, generator=generator,
            callback_on_step_end=on_step, output_type="np",
        )  # fmt: skip
        elapsed = time.perf_counter() - started
        image = np.asarray(result.images[0], dtype=np.float32)
        if not np.isfinite(image).all() or float(image.max()) < 0.02:
            # fp16 overflow shows up as NaNs or an all-black picture: never save it.
            raise EngineError(
                "blank_image",
                "The image came out blank. Try again; if it keeps happening, "
                "report it (the engine may need to run in full precision).",
            )
        ctx.report_progress(1.0, "Saving image")
        out_path = ctx.output_dir / "image.png"
        Image.fromarray((np.clip(image, 0, 1) * 255).round().astype(np.uint8)).save(out_path)
        if self.device == "mps":
            gpu_peak = max(gpu_peak, int(torch.mps.driver_allocated_memory()))
        return RunResult(
            outputs=[OutputFile(
                filename="image.png", media_type="image/png",
                metadata={
                    "kind": "image",
                    "engine": ENGINE_ID,
                    "model_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
                    "width": SIZE, "height": SIZE,
                    "seed": inputs["seed"], "steps": steps, "guidance": inputs["guidance"],
                    "scheduler": "DPM++ 2M",
                    "synthetic": True, "safety_checker": False,
                },
            )],
            metrics={
                "generation_s": round(elapsed, 3),
                "s_per_step": round(elapsed / steps, 4),
                "steps": steps,
                "device": self.device, "dtype": self.dtype,
                "gpu_peak_bytes": gpu_peak or None,
                "prompt_tokens": tokens,
                "truncated_prompt": tokens > MAX_TOKENS,
            },
        )  # fmt: skip

    def unload(self) -> None:
        self._pipe = None


def _quiet_library_logs() -> None:
    """diffusers and transformers can quote the cut-off end of a long prompt in a
    warning; keep them at error level so engine logs never hold the user's text."""
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("DIFFUSERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    try:
        from diffusers.utils import logging as dl
        from transformers.utils import logging as tl
    except ImportError:
        return
    dl.set_verbosity_error()
    tl.set_verbosity_error()
