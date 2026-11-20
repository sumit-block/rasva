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
            "default_voices": {"en": "af_heart", "hi": "hf_alpha"},
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


class PresetNarrator(FakeNarrator):
    """Like FakeNarrator, but with quality presets (tests only)."""

    descriptor = EngineDescriptor(
        **{
            **FakeNarrator.descriptor.model_dump(),
            "id": "test-preset-narrator",
            "display_name": "Preset narrator",
            "presets": [
                {
                    "id": "fast",
                    "label": "Fast",
                    "description": "Quicker, rougher.",
                    "settings": {"speed": 1.25},
                },
                {
                    "id": "best",
                    "label": "Best",
                    "description": "Slower, cleaner.",
                    "settings": {"speed": 0.9},
                },
            ],
        }
    )


class CloningNarrator(FakeNarrator):
    """Accepts a voice profile reference instead of a preset voice (tests only)."""

    descriptor = EngineDescriptor(
        **{
            **FakeNarrator.descriptor.model_dump(),
            "id": "test-cloner",
            "display_name": "Cloning narrator",
            "features": ["preset_voices", "voice_cloning", "preview"],
        }
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        ref = inputs.get("reference")
        if not ref:
            return super().validate_input(task, inputs)
        from pathlib import Path

        if not Path(ref["path"]).is_file() or not ref.get("transcript"):
            raise EngineError("invalid_reference", "The voice recording is missing.")
        text = str(inputs.get("text") or "").strip()
        if not text:
            raise EngineError("invalid_input", "Write or paste some text to narrate.")
        return {
            "text": text, "reference": ref, "voice_id": "cloned", "speed": 1.0,
            "preview": bool(inputs.get("preview")), "language": ref["language"], "script": "",
        }  # fmt: skip


class BilingualNarrator(FakeNarrator):
    """Chatterbox-shaped: speaks English and Hindi, each sentence in its own
    language, with built-in or cloned voices (tests only)."""

    descriptor = EngineDescriptor(
        **{
            **FakeNarrator.descriptor.model_dump(),
            "id": "test-bilingual",
            "display_name": "Bilingual narrator",
            "features": ["preset_voices", "voice_cloning", "preview", "language_per_sentence"],
            "voices": [
                {"id": "default_en", "display_name": "Voice", "language": "en"},
                {"id": "default_hi", "display_name": "Voice", "language": "hi"},
            ],
            "default_voices": {"en": "default_en", "hi": "default_hi"},
        }
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path

        text = str(inputs.get("text") or "").strip()
        if not text:
            raise EngineError("invalid_input", "Write or paste some text to narrate.")
        out: dict[str, Any] = {"text": text, "preview": bool(inputs.get("preview")), "speed": 1.0}
        ref = inputs.get("reference")
        if ref:
            if not Path(ref["path"]).is_file() or not ref.get("transcript"):
                raise EngineError("invalid_reference", "The voice recording is missing.")
            out.update(voice_id="own", reference=ref)
        elif inputs.get("voice_id") in ("default_en", "default_hi"):
            out["voice_id"] = inputs["voice_id"]
        else:
            raise EngineError("invalid_input", "Choose a voice.")
        language = inputs.get("language")
        if language not in (None, "", "en", "hi"):
            raise EngineError("invalid_input", "Language must be English or Hindi.")
        if language:
            out["language"] = language
        return out

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        from rasva.audio.text import reading_language

        result = super().run(task, inputs, ctx)
        # What each sentence was read as, the way Chatterbox chooses it.
        result.outputs[0].metadata["languages"] = [
            inputs.get("language") or reading_language(s.text) for s in segment(inputs["text"])
        ]
        return result
