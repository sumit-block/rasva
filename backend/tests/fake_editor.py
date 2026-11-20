"""Test-only ``image.edit`` engines for the I3 "Change outfit" flow.

They follow the shared I1 contract (``prompt``, ``references: [{path, role}]``,
``seed``, ``steps``, ``guidance``) without any model: the "edited" image is the
person photo copied unchanged. Importable by engine workers (tests/ is under the
backend root), so jobs go through the real queue -> worker -> output path.
"""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    EngineInputError,
    OutputFile,
    Preset,
    RunContext,
    RunResult,
)

ROLES = {"person", "garment", "pose", "background", "mask"}


class FakeEditor(Engine):
    descriptor = EngineDescriptor(
        id="test-editor",
        display_name="Test editor",
        summary="Copies the person photo instead of editing it (tests only).",
        version="0",
        modality="image",
        tasks=["image.edit"],
        features=["references", "seed", "presets"],
        license="none",
        license_class="permissive",
        requires_install=False,
        presets=[
            Preset(id="quick", label="Quick", description="Few steps.", settings={"steps": 4}),
            Preset(id="balanced", label="Balanced", description="More.", settings={"steps": 8}),
        ],
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "image.edit":
            raise EngineInputError("The test editor only edits images.")
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            raise EngineInputError("Say what to change.")
        refs = inputs.get("references") or []
        if not refs or not all(isinstance(r, dict) and r.get("path") for r in refs):
            raise EngineInputError("Add at least one photo.")
        if any(r.get("role") not in ROLES for r in refs):
            raise EngineInputError("Unknown photo role.")
        seed = inputs.get("seed")
        return {
            "prompt": prompt,
            "references": [
                {"path": r["path"], "role": r["role"], "asset_id": r.get("asset_id")} for r in refs
            ],
            "seed": int(seed) if seed is not None else secrets.randbelow(2**32),
            "steps": int(inputs.get("steps", 8)),
            "guidance": float(inputs.get("guidance", 1.0)),
        }

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        person = next(r for r in inputs["references"] if r["role"] == "person")
        ctx.report_progress(0.5, "Editing")
        shutil.copyfile(Path(person["path"]), ctx.output_dir / "image.png")
        return RunResult(
            outputs=[
                OutputFile(
                    filename="image.png",
                    media_type="image/png",
                    metadata={
                        "kind": "image",
                        "engine": self.descriptor.id,
                        "source_asset_ids": [r["asset_id"] for r in inputs["references"]],
                        "roles": [r["role"] for r in inputs["references"]],
                        "seed": inputs["seed"],
                        "model_revision": "test",
                        "synthetic": True,
                    },
                )
            ],
            metrics={"steps": inputs["steps"]},
        )


class FakeMaskEditor(FakeEditor):
    """Like a dedicated try-on engine: can be told which area to change."""

    descriptor = FakeEditor.descriptor.model_copy(
        update={
            "id": "test-mask-editor",
            "display_name": "Test try-on editor",
            "features": [*FakeEditor.descriptor.features, "mask"],
        }
    )
