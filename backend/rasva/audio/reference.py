"""Reference-recording quality checks for voice profiles (PRD §11 step 3).

Measures what matters for voice cloning and turns it into plain advice:
length, loudness, clipping, background noise and silence. "error" issues block
enrollment; "warning" issues are advice.
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np

FRAME_S = 0.02
IDEAL = (5.0, 15.0)
MIN_S, MAX_S = 3.0, 60.0


def _db(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-9)))


def load_mono(path: str, ffmpeg: str | None, rate: int = 24_000) -> tuple[np.ndarray, int]:
    from rasva.engines.builtin.mixer import read_wav, resample

    try:
        data, src = read_wav(path)
        return resample(data.mean(axis=1, keepdims=True), src, rate)[:, 0], rate
    except (wave.Error, EOFError, Exception) as exc:  # not plain PCM WAV
        if not ffmpeg:
            raise ValueError(
                "This recording isn't a plain WAV file, and FFmpeg isn't installed to read it. "
                "Record in Rasva or upload a WAV."
            ) from exc
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ref.f32"
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", path, "-f", "f32le", "-ac", "1",
             "-ar", str(rate), str(out)],
            capture_output=True, timeout=120, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            raise ValueError("This recording couldn't be read. Try recording again.")
        return np.fromfile(out, dtype="<f4"), rate


def analyze(samples: np.ndarray, rate: int) -> dict[str, Any]:
    duration = len(samples) / rate if rate else 0.0
    frame = int(FRAME_S * rate)
    n = len(samples) // frame
    issues: list[dict[str, str]] = []
    if n == 0:
        return {"duration_s": round(duration, 2), "level": "error",
                "issues": [{"severity": "error", "code": "empty", "message": "The recording is empty."}]}  # fmt: skip
    frames = samples[: n * frame].reshape(n, frame)
    frame_rms = np.sqrt(np.mean(frames**2, axis=1))
    frame_db = 20 * np.log10(np.maximum(frame_rms, 1e-9))
    noise_floor = float(np.percentile(frame_db, 10))
    speech_level = float(np.percentile(frame_db, 90))
    voiced = frame_db > max(noise_floor + 10, -50)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    clipping = float(np.mean(np.abs(samples) >= 0.999))
    silence = float(1 - np.mean(voiced))
    lead = int(np.argmax(voiced)) * FRAME_S if voiced.any() else duration
    trail = int(np.argmax(voiced[::-1])) * FRAME_S if voiced.any() else 0.0
    speech_s = max(0.0, duration - lead - trail)
    rms_voiced = _db(float(np.sqrt(np.mean(frames[voiced] ** 2)))) if voiced.any() else -120.0
    snr = speech_level - noise_floor

    def issue(sev: str, code: str, msg: str) -> None:
        issues.append({"severity": sev, "code": code, "message": msg})

    if speech_s < MIN_S:
        issue(
            "error",
            "too_short",
            f"Only {speech_s:.1f} s of speech. Record at least 5 seconds, ideally 10.",
        )
    elif speech_s < IDEAL[0]:
        issue("warning", "short", "A little short. 5–15 seconds of speech works best.")
    if duration > MAX_S:
        issue("error", "too_long", "Too long. Keep the recording under a minute (5–15 s is ideal).")
    elif speech_s > 30:
        issue("warning", "long", "Long recordings are usually trimmed. 5–15 seconds works best.")
    if clipping > 0.001 or peak >= 0.999:
        issue(
            "error",
            "clipping",
            "The sound is distorted (too loud). Move back a little or lower the mic level.",
        )
    if rms_voiced < -45:
        issue("error", "too_quiet", "Much too quiet. Move closer to the microphone.")
    elif rms_voiced < -32:
        issue("warning", "quiet", "A bit quiet. Move a little closer to the microphone.")
    if snr < 10:
        issue("error", "noisy", "Too much background noise. Record in a quieter room.")
    elif snr < 20:
        issue("warning", "noise", "Some background noise. A quieter room will sound cleaner.")
    if silence > 0.5 and speech_s >= MIN_S:
        issue("warning", "silence", "Lots of silence. Speak continuously, with natural pauses.")
    level = (
        "error" if any(i["severity"] == "error" for i in issues)
        else "warning" if issues else "good"
    )  # fmt: skip
    return {
        "duration_s": round(duration, 2),
        "speech_s": round(speech_s, 2),
        "peak_dbfs": round(_db(peak), 1),
        "speech_dbfs": round(rms_voiced, 1),
        "noise_floor_dbfs": round(noise_floor, 1),
        "snr_db": round(snr, 1),
        "clipping_ratio": round(clipping, 5),
        "silence_ratio": round(silence, 3),
        "level": level,
        "issues": issues,
    }


def transcript_check(transcript: str, speech_s: float) -> list[dict[str, str]]:
    """Plausibility of a transcript against the speech length (no speech recognition)."""
    text = " ".join(transcript.split())
    if not text:
        return [{"severity": "error", "code": "no_transcript",
                 "message": "Type exactly what is said in the recording."}]  # fmt: skip
    if speech_s <= 0:
        return []
    rate = len(text) / speech_s
    if rate < 4:
        return [{"severity": "warning", "code": "transcript_short",
                 "message": "The transcript looks shorter than the recording. Is every word there?"}]  # fmt: skip
    if rate > 30:
        return [{"severity": "warning", "code": "transcript_long",
                 "message": "The transcript looks longer than the recording. Does it match exactly?"}]  # fmt: skip
    return []
