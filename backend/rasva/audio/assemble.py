"""Join synthesized sentences into one clean WAV (shared by narration engines).

Runs inside engine workers (numpy is available there and in the app).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np

PEAK_CEILING = 0.97  # keep exports below clipping (PRD §29.3)


class Assembler:
    def __init__(self, sample_rate: int) -> None:
        self.rate = sample_rate
        self.chunks: list[np.ndarray] = []
        self.timeline: list[dict[str, Any]] = []
        self.cursor = 0

    def add(self, index: int, text: str, audio: Any, pause_after_s: float, last: bool) -> None:
        a = np.asarray(audio, dtype=np.float32).reshape(-1)
        self.chunks.append(a)
        self.timeline.append({
            "index": index, "text": text,
            "start_s": round(self.cursor / self.rate, 3),
            "end_s": round((self.cursor + len(a)) / self.rate, 3),
        })  # fmt: skip
        self.cursor += len(a)
        if not last:
            pause = np.zeros(int(pause_after_s * self.rate), dtype=np.float32)
            self.chunks.append(pause)
            self.cursor += len(pause)

    def write(self, path: Path) -> dict[str, float]:
        signal = np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)
        if signal.size == 0 or not np.any(signal):
            raise ValueError("silent")
        peak = float(np.max(np.abs(signal)))
        if peak > PEAK_CEILING:
            signal = signal * (PEAK_CEILING / peak)
        pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(pcm.tobytes())
        return {"duration_s": round(len(pcm) / self.rate, 3), "peak_before_limit": round(peak, 4)}
