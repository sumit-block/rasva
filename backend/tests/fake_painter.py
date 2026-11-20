"""Test-only image engine: SD 1.5's validation, flat-colour PNGs instead of pictures.

Runs on the app interpreter (no lockfile), so it exercises the real queue ->
worker -> output path for ``image.generate`` and ``image.edit`` (shared I1–I4
contract: ``references: [{asset_id, role}]``). PNGs are written with the
standard library. Also lists the ``low_memory`` feature and records whether it
was asked for. Set ``FAKE_PAINTER_HANG=1`` to make runs wait until canceled.
"""

from __future__ import annotations

import os
import struct
import time
import zlib
from typing import Any

from rasva.engines.contract import (
    EngineDescriptor,
    EngineInputError,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)
from rasva.engines.sd15 import StableDiffusion15Engine

ROLES = {"person", "garment", "pose", "background"}
SIZE = 8


def png_bytes(rgb: tuple[int, int, int], size: int = SIZE) -> bytes:
    def chunk(t: bytes, b: bytes) -> bytes:
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b))

    row = b"\x00" + bytes(rgb) * size
    return b"".join([
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(row * size)),
        chunk(b"IEND", b""),
    ])  # fmt: skip


class FakePainter(StableDiffusion15Engine):
    descriptor = EngineDescriptor(
        **{
            **StableDiffusion15Engine.descriptor.model_dump(),
            "id": "test-painter",
            "display_name": "Test painter",
            "summary": "Paints flat colours instead of pictures (tests only).",
            "tasks": ["image.generate", "image.edit"],
            "features": ["prompt", "negative_prompt", "seed", "presets", "low_memory"],
            "requires_install": False,
            "artifacts": [],
            "requirements_lock": None,
            "python_version": None,
            "license_class": "permissive",
        }
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        # Engines that list "low_memory" keep the flag: the worker validates again.
        flag = {"low_memory": True} if inputs.get("low_memory") is True else {}
        return {**self._validate(task, inputs), **flag}

    def _validate(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task == "image.edit":
            refs = inputs.get("references")
            if not isinstance(refs, list) or not refs:
                raise EngineInputError("Add at least one reference photo.")
            clean = []
            for r in refs:
                if not isinstance(r, dict) or r.get("role") not in ROLES or not r.get("asset_id"):
                    raise EngineInputError("Each reference needs a photo and a role.")
                clean.append({"asset_id": str(r["asset_id"]), "role": r["role"]})
            return {**super().validate_input("image.generate", inputs), "references": clean}
        return super().validate_input(task, inputs)

    def load(self, ctx: RuntimeContext) -> None:
        pass

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        if os.environ.get("FAKE_PAINTER_HANG") == "1":
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                ctx.raise_if_canceled()
                time.sleep(0.02)
        seed = inputs["seed"]
        rgb = (seed % 251 + 4, (seed // 251) % 251 + 4, len(inputs["prompt"]) % 251 + 4)
        (ctx.output_dir / "image.png").write_bytes(png_bytes(rgb))
        ctx.report_progress(1.0, "Saving image")
        refs = inputs.get("references") or []
        return RunResult(
            outputs=[OutputFile(filename="image.png", media_type="image/png", metadata={
                "kind": "image", "engine": self.descriptor.id, "model_revision": "0" * 40,
                "width": SIZE, "height": SIZE, "seed": seed, "steps": inputs["steps"],
                "synthetic": True, "low_memory": bool(inputs.get("low_memory")),
                "source_asset_ids": [r["asset_id"] for r in refs],
            })],
            metrics={"generation_s": 0.01, "low_memory": bool(inputs.get("low_memory"))},
        )  # fmt: skip
