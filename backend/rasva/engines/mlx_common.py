"""Helpers shared by the MLX image engines (FLUX.2 Klein, Qwen-Image 2.1 Turbo).

Module import stays light: mlx and PIL are imported inside the functions, in the
engine's own environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# MLX keeps freed buffers in a cache. On the M2 a 1 GB limit cut FLUX.2 Klein's
# peak footprint from 10.0 GB to 6.3 GB at the same speed.
CACHE_LIMIT_BYTES = 1024**3
# Phone photos are large; bigger references add memory, not detail.
REFERENCE_MAX_SIDE = 1024


def prepare_references(
    refs: list[dict[str, Any]], out_dir: Path, max_side: int = REFERENCE_MAX_SIDE
) -> list[str]:
    """Upright (EXIF rotation applied), RGB copies, at most ``max_side`` px long.

    A reference with its own ``max_side`` (I2 pose jobs) is resized to exactly that
    long side, up or down: the identity settings send person and pose at set sizes.
    """
    from PIL import Image, ImageOps

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, ref in enumerate(refs):
        with Image.open(ref["path"]) as img:
            pic = ImageOps.exif_transpose(img).convert("RGB")
        side = ref.get("max_side")
        if side:
            scale = int(side) / max(pic.size)
            size = (max(1, round(pic.width * scale)), max(1, round(pic.height * scale)))
            if size != pic.size:
                pic = pic.resize(size, Image.LANCZOS)
        else:
            pic.thumbnail((max_side, max_side))
        target = out_dir / f"{k}.png"
        pic.save(target, format="PNG")
        paths.append(str(target))
    return paths


def call(mx: Any, name: str, *args: Any) -> Any:
    """Call an MLX memory function wherever this MLX version keeps it."""
    for owner in (mx, getattr(mx, "metal", None)):
        if owner is not None and hasattr(owner, name):
            return getattr(owner, name)(*args)
    return None


def clear_cache() -> None:
    import mlx.core as mx

    call(mx, "clear_cache")


def reset_peak(mx: Any) -> None:
    call(mx, "reset_peak_memory")


def peak(mx: Any) -> int | None:
    value = call(mx, "get_peak_memory")
    return int(value) if value is not None else None


def quiet_library_logs() -> None:
    """Keep library chatter (which can quote prompts) out of engine logs."""
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")


def step_callback(engine: Any) -> Any:
    """An mflux in-loop callback: progress and cooperative cancel for ``engine._job``."""

    class Steps:
        def call_in_loop(self, t: int, seed: int, prompt: str, latents: Any,
                         config: Any, time_steps: Any) -> None:  # fmt: skip
            job = engine._job
            if not job:
                return
            job["ctx"].raise_if_canceled()
            n = job["steps"]
            job["ctx"].report_progress((t + 1) / (n + 1), f"Step {t + 1} of {n}")

    return Steps()
