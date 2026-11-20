"""Test-only image editor for the ``image.edit`` contract (I1–I3).

Importable by engine workers (tests/ is under the backend root), so it exercises the
real queue -> worker -> output path: references arrive resolved to file paths, and
the output records which photos it used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    EngineInputError,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)

# Smallest valid PNG (1x1, grey).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010800000000"
    "3a7e9b550000000a49444154789c63680000008200816f5b3b0a0000000049454e44ae426082"
)


class FakeImageEditor(Engine):
    descriptor = EngineDescriptor(
        id="test-image-editor",
        display_name="Test image editor",
        summary="Copies a tiny image instead of editing (tests only).",
        version="0",
        modality="image",
        tasks=["image.edit"],
        features=["references"],
        license="test",
        license_class="permissive",
        requires_install=False,
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "image.edit":
            raise EngineInputError("Only image.edit.")
        prompt = str(inputs.get("prompt") or "").strip()
        refs = inputs.get("references") or []
        if not prompt or not refs:
            raise EngineInputError("Describe the change and choose a photo.")
        for ref in refs:
            if not Path(str(ref.get("path", ""))).is_file():
                raise EngineInputError("A chosen photo is missing.")
        return {"prompt": prompt, "references": refs, "seed": int(inputs.get("seed", 1))}

    def load(self, ctx: RuntimeContext) -> None:
        pass

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        (ctx.output_dir / "image.png").write_bytes(PNG)
        return RunResult(
            outputs=[OutputFile(
                filename="image.png", media_type="image/png",
                metadata={
                    "kind": "image", "synthetic": True, "seed": inputs["seed"],
                    "source_asset_ids": [r["asset_id"] for r in inputs["references"]],
                    "roles": [r["role"] for r in inputs["references"]],
                },
            )],
            metrics={"references": len(inputs["references"])},
        )  # fmt: skip
