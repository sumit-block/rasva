"""Test-only image engine: Stable Diffusion 1.5's validation and presets, flat
coloured PNGs instead of diffusion.

Importable by engine workers (tests/ is under the backend root), so it exercises
the real queue -> worker -> output path for ``image.generate`` jobs. The colour
comes from the seed, so the same seed gives the same picture.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

from rasva.engines.contract import (
    EngineDescriptor,
    EngineError,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)
from rasva.engines.sd15 import StableDiffusion15Engine

SIZE = 8
GPU_BYTES = 3 * 1024**3


def png_bytes(rgb: tuple[int, int, int], size: int = SIZE) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    row = b"\x00" + bytes(rgb) * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


class FakePainter(StableDiffusion15Engine):
    descriptor = EngineDescriptor(
        **{
            **StableDiffusion15Engine.descriptor.model_dump(),
            "id": "test-painter",
            "display_name": "Test painter",
            "summary": "Paints flat colours instead of pictures (tests only).",
            "license": "MIT",
            "license_class": "permissive",
            "license_notes": [],
            "requires_install": False,
            "artifacts": [],
            "requirements_lock": None,
        }
    )

    def load(self, ctx: RuntimeContext) -> None:
        pass

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        if "[[fail]]" in inputs["prompt"]:
            raise EngineError("engine_failed", "Injected failure.")
        steps = inputs["steps"]
        for i in range(steps):
            ctx.raise_if_canceled()
            ctx.report_progress((i + 1) / steps, f"Step {i + 1} of {steps}")
        seed = inputs["seed"]
        (ctx.output_dir / "image.png").write_bytes(
            png_bytes((seed % 256, (seed >> 8) % 256, (seed >> 16) % 256))
        )
        generation_s = round(0.1 * steps, 3)  # fixed, so scoring tests are exact
        return RunResult(
            outputs=[OutputFile(filename="image.png", media_type="image/png",
                                metadata={"kind": "image", "seed": seed, "synthetic": True})],
            metrics={
                "generation_s": generation_s,
                "s_per_step": round(generation_s / steps, 4),
                "steps": steps,
                "gpu_peak_bytes": GPU_BYTES,
            },
        )  # fmt: skip
