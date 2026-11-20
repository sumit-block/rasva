"""Engine classes for installer tests, created at runtime with a local server URL."""

from __future__ import annotations

import sys
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    ModelArtifact,
    OutputFile,
    RunContext,
    RunResult,
)


class _Base(Engine):
    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return inputs

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        (ctx.output_dir / "x.txt").write_text("x")
        return RunResult(outputs=[OutputFile(filename="x.txt", media_type="text/plain")])


def make_engine(
    name: str,
    artifacts: list[ModelArtifact],
    *,
    license_class: str = "permissive",
    requirements_lock: str | None = None,
    verify_ok: bool = True,
) -> str:
    """Define ``tests.install_engines.<name>`` and return its import path."""
    descriptor = EngineDescriptor(
        id=f"test-{name.lower()}",
        display_name=f"Test {name}",
        summary="Installer test engine.",
        version="1.0.0",
        modality="audio",
        tasks=["test.installed"],
        license="Test licence",
        license_class=license_class,  # type: ignore[arg-type]
        artifacts=artifacts,
        requirements_lock=requirements_lock,
    )
    cls = type(
        name,
        (_Base,),
        {"descriptor": descriptor, "verify": lambda self, ctx: verify_ok},
    )
    setattr(sys.modules[__name__], name, cls)
    return f"{__name__}:{name}"
