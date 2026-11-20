"""Fault-injection engine for automated tests.

Registered only when RASVA_ENABLE_TEST_ENGINES=1. Never shown to users.
"""

from __future__ import annotations

import os
import time
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    EngineError,
    EngineInputError,
    OutputFile,
    RunContext,
    RunResult,
)

BEHAVIOURS = {"ok", "error", "crash", "slow", "hang", "bad_output"}


class FaultEngine(Engine):
    descriptor = EngineDescriptor(
        id="test-fault",
        display_name="Test fault injector",
        summary="Internal test engine.",
        version="1.0.0",
        modality="diagnostic",
        tasks=["test.run"],
        license="Built into Rasva Studio (tests only)",
        license_class="permissive",
        requires_install=False,
        experimental=True,
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        behaviour = inputs.get("behaviour", "ok")
        if behaviour not in BEHAVIOURS:
            raise EngineInputError(f"Unknown behaviour {behaviour!r}")
        return {"behaviour": behaviour, "seconds": float(inputs.get("seconds", 0.2))}

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        behaviour = inputs["behaviour"]
        if behaviour == "error":
            raise EngineError("engine_failed", "The test engine failed on purpose.")
        if behaviour == "crash":
            os._exit(17)
        if behaviour == "hang":
            # Ignores cancellation to exercise the orchestrator's kill path.
            time.sleep(3600)
        if behaviour == "slow":
            steps = 20
            for i in range(steps):
                ctx.raise_if_canceled()
                time.sleep(inputs["seconds"] / steps)
                ctx.report_progress((i + 1) / steps, "Working")
        if behaviour == "bad_output":
            return RunResult(
                outputs=[OutputFile(filename="../escape.txt", media_type="text/plain")]
            )
        (ctx.output_dir / "result.txt").write_text("ok", encoding="utf-8")
        return RunResult(outputs=[OutputFile(filename="result.txt", media_type="text/plain")])
