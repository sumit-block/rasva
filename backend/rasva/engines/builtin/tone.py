"""Diagnostic tone engine.

Not an AI model. It writes a short sine-wave WAV so the full pipeline (queue ->
worker -> progress -> output asset -> download) can be verified on a fresh
install before any large model download (A0 exit gate).
"""

from __future__ import annotations

import math
import struct
import time
import wave
from typing import Any

from rasva.engines.contract import (
    Engine,
    EngineDescriptor,
    EngineInputError,
    OutputFile,
    RunContext,
    RunResult,
)

SAMPLE_RATE = 24_000


class ToneEngine(Engine):
    descriptor = EngineDescriptor(
        id="diagnostic-tone",
        display_name="Pipeline check",
        summary="Plays a short test tone to confirm your Mac can run and save jobs.",
        version="1.0.0",
        modality="diagnostic",
        tasks=["diagnostic.tone"],
        license="Built into Rasva Studio",
        license_class="permissive",
        requires_install=False,
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "diagnostic.tone":
            raise EngineInputError(f"This engine cannot do '{task}'.")
        try:
            freq = float(inputs.get("frequency_hz", 440.0))
            duration = float(inputs.get("duration_s", 2.0))
        except (TypeError, ValueError) as exc:
            raise EngineInputError("Frequency and duration must be numbers.") from exc
        if not 20 <= freq <= 20_000:
            raise EngineInputError("Frequency must be between 20 and 20,000 Hz.")
        if not 0.1 <= duration <= 30:
            raise EngineInputError("Duration must be between 0.1 and 30 seconds.")
        return {"frequency_hz": freq, "duration_s": duration}

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        freq = inputs["frequency_hz"]
        total = int(SAMPLE_RATE * inputs["duration_s"])
        chunk = SAMPLE_RATE // 10
        started = time.perf_counter()
        out = ctx.output_dir / "tone.wav"
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            for start in range(0, total, chunk):
                ctx.raise_if_canceled()
                n = min(chunk, total - start)
                frames = b"".join(
                    struct.pack(
                        "<h",
                        int(0.3 * 32767 * math.sin(2 * math.pi * freq * (start + i) / SAMPLE_RATE)),
                    )
                    for i in range(n)
                )
                wav.writeframes(frames)
                ctx.report_progress((start + n) / total, "Writing test tone")
        elapsed = time.perf_counter() - started
        return RunResult(
            outputs=[
                OutputFile(
                    filename="tone.wav",
                    media_type="audio/wav",
                    metadata={"duration_s": inputs["duration_s"], "sample_rate": SAMPLE_RATE},
                )
            ],
            metrics={
                "processing_s": round(elapsed, 4),
                "real_time_factor": round(elapsed / inputs["duration_s"], 4),
            },
        )
