"""Test-only ``image.edit`` engine standing in for I1's FLUX.2 Klein engine.

It routes pose jobs through ``rasva.engines.pose.edit`` exactly as the real I1
engine is meant to, with a fake pipeline that returns a grey picture. Importable
by engine workers, so it exercises the API -> queue -> worker -> asset path.
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    EngineInputError,
    RunContext,
    RunResult,
    RuntimeContext,
)
from rasva.engines.pose import edit


class _Torch:
    class Generator:
        def __init__(self, device: str) -> None:
            assert device == "cpu"

        def manual_seed(self, seed: int) -> _Torch.Generator:
            return self

    mps = types.SimpleNamespace(driver_allocated_memory=lambda: 0)


class _Pipe:
    def __call__(self, **kw: Any) -> Any:
        for i in range(kw["num_inference_steps"]):
            kw["callback_on_step_end"](self, i, i, {})
        h, w = kw["height"], kw["width"]
        return types.SimpleNamespace(images=[np.full((h, w, 3), 0.5, dtype=np.float32)])


class FakeEditor(Engine):
    descriptor = EngineDescriptor(
        id="test-editor",
        display_name="Test editor",
        summary="Grey pictures instead of edits (tests only).",
        version="0",
        modality="image",
        tasks=[edit.TASK],
        features=["references", "pose"],
        license="none",
        license_class="permissive",
        requires_install=False,
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if not edit.is_pose_edit(inputs):
            raise EngineInputError("The test editor only does pose changes.")
        return edit.validate(inputs)

    def load(self, ctx: RuntimeContext) -> None:
        self._pipe = _Pipe()

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        pose = next(r for r in inputs["references"] if r["role"] == "pose")
        p = edit.plan(inputs, pose_size=edit.image_size(pose["path"]))
        return edit.run(
            self._pipe, p, ctx, torch=_Torch, device="cpu",
            load_image=lambda path, side: path, engine_id=self.descriptor.id,
        )  # fmt: skip
