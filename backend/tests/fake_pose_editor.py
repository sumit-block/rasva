"""Test-only ``image.edit`` engine that validates pose jobs exactly like the Klein
engine (rasva.engines.pose.edit) and writes a tiny PNG. Importable by workers,
so it exercises the API -> queue -> worker -> asset path for pose changes."""

from __future__ import annotations

from typing import Any

from rasva.engines.contract import EngineDescriptor, OutputFile, RunContext, RunResult
from rasva.engines.pose import edit
from tests.fake_image_editor import PNG, FakeImageEditor


class FakePoseEditor(FakeImageEditor):
    descriptor = EngineDescriptor(
        **{
            **FakeImageEditor.descriptor.model_dump(),
            "id": "test-pose-editor",
            "display_name": "Test pose editor",
            "features": ["references", "pose"],
        }
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task == "image.edit" and edit.is_pose_edit(inputs):
            return edit.validate(inputs)
        return super().validate_input(task, inputs)

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        for k in range(inputs.get("steps", 1)):
            ctx.raise_if_canceled()
            ctx.report_progress((k + 1) / inputs.get("steps", 1), f"Step {k + 1}")
        (ctx.output_dir / "image.png").write_bytes(PNG)
        refs = inputs["references"]
        return RunResult(
            outputs=[
                OutputFile(
                    filename="image.png",
                    media_type="image/png",
                    metadata={
                        "kind": "image",
                        "synthetic": True,
                        "seed": inputs["seed"],
                        "source_asset_ids": [r["asset_id"] for r in refs],
                        "roles": [r["role"] for r in refs],
                        **edit.output_metadata(inputs),
                    },
                )
            ]
        )
