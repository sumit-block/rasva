"""Test-only narrator: Kokoro's validation and voices, tones instead of speech.

Importable by engine workers (tests/ is under the backend root), so it exercises
the real queue -> worker -> output path for ``audio.narrate`` jobs.
"""

from __future__ import annotations

import math
import os
import struct
import wave
from typing import Any

from rasva.audio.text import segment
from rasva.engines.contract import (
    EngineDescriptor,
    EngineError,
    OutputFile,
    RunContext,
    RunResult,
    RuntimeContext,
)
from rasva.engines.kokoro import VOICE_CATALOGUE, KokoroEngine


class FakeNarrator(KokoroEngine):
    descriptor = EngineDescriptor(
        **{
            **KokoroEngine.descriptor.model_dump(),
            "id": "test-narrator",
            "display_name": "Test narrator",
            "summary": "Plays tones instead of speech (tests only).",
            "requires_install": False,
            "artifacts": [],
            "requirements_lock": None,
            "voices": [v.model_dump() for v in VOICE_CATALOGUE],
        }
    )

    def load(self, ctx: RuntimeContext) -> None:
        pass

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        # Fault markers for long-form recovery tests.
        if "[[fail]]" in inputs["text"]:
            raise EngineError("engine_failed", "Injected failure.")
        if "[[crash]]" in inputs["text"]:
            os._exit(3)
        segs = segment(inputs["text"])[: 1 if inputs["preview"] else None]
        rate = 8000
        frames = bytearray()
        for k, seg in enumerate(segs):
            ctx.raise_if_canceled()
            n = int(rate * min(0.05 * len(seg.text), 1.0))
            frames += b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 330 * i / rate)))
                for i in range(n)
            )
            ctx.report_progress((k + 1) / len(segs), f"Sentence {k + 1} of {len(segs)}")
        with wave.open(str(ctx.output_dir / "narration.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
        duration = len(frames) / 2 / rate
        return RunResult(
            outputs=[OutputFile(filename="narration.wav", media_type="audio/wav",
                                metadata={"voice_id": inputs["voice_id"], "synthetic": True})],
            metrics={"audio_s": round(duration, 3), "segments": len(segs)},
        )  # fmt: skip
