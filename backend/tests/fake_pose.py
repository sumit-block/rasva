"""Test-only pose finder: the real validation, a fixed pose instead of ONNX models.

Importable by engine workers (tests/ is under the backend root), so it exercises
the real queue -> worker -> output asset path for ``image.pose`` jobs.
"""

from __future__ import annotations

from typing import Any

from rasva.engines.contract import (
    EngineDescriptor,
    EngineError,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)
from rasva.engines.pose import PoseFinderEngine, skeleton

STANDING = [
    (0.5, 0.14), (0.5, 0.22), (0.4, 0.23), (0.37, 0.36), (0.36, 0.48), (0.6, 0.23),
    (0.63, 0.36), (0.64, 0.48), (0.44, 0.5), (0.44, 0.68), (0.44, 0.86), (0.56, 0.5),
    (0.56, 0.68), (0.56, 0.86), (0.48, 0.125), (0.52, 0.125), (0.46, 0.135), (0.54, 0.135),
]  # fmt: skip


class FakePoseFinder(PoseFinderEngine):
    descriptor = EngineDescriptor(
        **{
            **PoseFinderEngine.descriptor.model_dump(),
            "id": "test-pose",
            "display_name": "Test pose finder",
            "requires_install": False,
            "artifacts": [],
            "requirements_lock": None,
            "python_version": None,
        }
    )

    def load(self, ctx: RuntimeContext) -> None:
        pass

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        (ref,) = inputs["references"]
        with open(ref["path"], "rb") as fh:
            if b"NOBODY" in fh.read():
                raise EngineError("no_person", "No person was found in this photo.")
        ctx.report_progress(0.5, "Reading the pose")
        (ctx.output_dir / "pose.png").write_bytes(
            skeleton.encode_png(skeleton.render(STANDING, 64, 96))
        )
        return RunResult(
            outputs=[
                OutputFile(
                    filename="pose.png",
                    media_type="image/png",
                    metadata={
                        "kind": "pose",
                        "role": "pose",
                        "keypoints": skeleton.to_json(STANDING),
                        "framing": skeleton.framing(STANDING),
                        "source_asset_ids": [ref["asset_id"]],
                        "synthetic": False,
                    },
                )
            ]
        )
