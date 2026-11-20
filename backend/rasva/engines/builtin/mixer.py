"""Chapter assembly and mixing (PRD §12.3, §13; phase A4).

Built-in tool engine: runs in a worker like any other job, so a long render can
be canceled, survives UI disconnects and never blocks the API.

Pipeline (block-based so a 60-minute chapter does not need gigabytes of RAM):
  pass 0: measure every narration segment (level) and build a speech-activity
          map for ducking; segments are levelled to the chapter median.
  pass 1: render the mix block by block and find its peak.
  pass 2: render again with one gain so the peak sits at -1 dBFS, and write
          the files. Stems use the same gain, so they add up to the mix.
Music and effects can be placed at a sentence, looped, faded, and ducked under
speech. MP3 export and compressed imports need FFmpeg.
"""

from __future__ import annotations

import contextlib
import math
import subprocess
import time
import wave
from pathlib import Path
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

MIX_RATE = 48_000
BLOCK_S = 10.0
EDGE_FADE_S = 0.008  # removes clicks at segment joins without touching speech
TAIL_S = 0.5
TARGET_PEAK = 10 ** (-1 / 20)  # -1 dBFS
FRAME_S = 0.02
SPEECH_THRESHOLD = 10 ** (-40 / 20)
DUCK_LOOKAHEAD_FRAMES = 4  # start lowering music ~80 ms before speech
DUCK_ATTACK = 0.5  # per-frame smoothing toward the ducked level
DUCK_RELEASE = 0.06
LEVEL_CLAMP = (0.5, 2.0)  # at most +/-6 dB of per-segment levelling


def _db(x: float) -> float | None:
    return round(20 * math.log10(x), 2) if x > 0 else None


def read_wav(path: str):  # -> tuple[np.ndarray (frames, channels) float32, rate]
    import numpy as np

    with wave.open(path, "rb") as w:
        channels, width, rate, n = (
            w.getnchannels(),
            w.getsampwidth(),
            w.getframerate(),
            w.getnframes(),
        )
        raw = w.readframes(n)
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (
            b[:, 0].astype(np.int32)
            | (b[:, 1].astype(np.int32) << 8)
            | (b[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float32) / 8388608
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
    else:
        raise EngineError("unsupported_audio", "One of the audio files has an unsupported format.")
    return data.reshape(-1, channels), rate


def resample(data, rate: int, target: int):
    """Linear-interpolation resampler, per channel. Adequate for speech and
    upsampling; FFmpeg is used for compressed imports when available."""
    import numpy as np

    if rate == target or len(data) == 0:
        return data
    n_out = int(round(len(data) * target / rate))
    x_old = np.arange(len(data), dtype=np.float64)
    x_new = np.linspace(0, len(data) - 1, n_out)
    return np.stack(
        [np.interp(x_new, x_old, data[:, c]) for c in range(data.shape[1])], axis=1
    ).astype(np.float32)


class _Narration:
    """Sequential narration timeline built from segment files, read in blocks."""

    def __init__(self, segments: list[dict[str, Any]], rate: int):
        self.rate = rate
        self.files = [s["path"] for s in segments]
        self.pauses = [int(round(s["pause_after_s"] * rate)) for s in segments]
        self.lengths: list[int] = []
        for path in self.files:
            with wave.open(path, "rb") as w:
                self.lengths.append(int(math.ceil(w.getnframes() * rate / w.getframerate())))
        self.starts: list[int] = []
        pos = 0
        for i, n in enumerate(self.lengths):
            self.starts.append(pos)
            pos += n + (self.pauses[i] if i < len(self.lengths) - 1 else 0)
        self.total = pos + int(TAIL_S * rate)
        self.gains = [1.0] * len(self.files)
        self._cache: dict[int, Any] = {}

    def segment(self, i: int):
        import numpy as np

        if i not in self._cache:
            if len(self._cache) > 4:
                self._cache.pop(next(iter(self._cache)))
            data, rate = read_wav(self.files[i])
            mono = resample(data.mean(axis=1, keepdims=True), rate, self.rate)[:, 0]
            mono = np.pad(mono, (0, max(0, self.lengths[i] - len(mono))))[: self.lengths[i]]
            fade = min(int(EDGE_FADE_S * self.rate), len(mono) // 2)
            if fade:
                ramp = np.linspace(0, 1, fade, dtype=np.float32)
                mono[:fade] *= ramp
                mono[-fade:] *= ramp[::-1]
            self._cache[i] = mono
        return self._cache[i] * self.gains[i]

    def read(self, start: int, n: int):
        import numpy as np

        out = np.zeros(n, dtype=np.float32)
        end = start + n
        for i, s in enumerate(self.starts):
            e = s + self.lengths[i]
            if e <= start or s >= end:
                continue
            seg = self.segment(i)
            a, b = max(s, start), min(e, end)
            out[a - start : b - start] += seg[a - s : b - s]
        return out


class _Track:
    def __init__(self, spec: dict[str, Any], data, rate: int, start: int, timeline_end: int):
        self.kind = spec["kind"]
        self.data = data  # (frames, 2) float32 at the mix rate
        self.gain = 10 ** (spec["gain_db"] / 20)
        self.loop = spec["loop"]
        self.duck = spec["duck"]
        self.start = start
        self.end = timeline_end if self.loop else min(start + len(data), timeline_end)
        self.fade_in = int(spec["fade_in_s"] * rate)
        self.fade_out = int(spec["fade_out_s"] * rate)

    def read(self, start: int, n: int):
        import numpy as np

        out = np.zeros((n, 2), dtype=np.float32)
        a, b = max(self.start, start), min(self.end, start + n)
        if a >= b or len(self.data) == 0:
            return out
        idx = np.arange(a - self.start, b - self.start)
        src = self.data[idx % len(self.data)] if self.loop else self.data[idx]
        env = np.ones(b - a, dtype=np.float32)
        if self.fade_in:
            env *= np.clip(idx / self.fade_in, 0, 1)
        if self.fade_out:
            env *= np.clip((self.end - self.start - idx) / self.fade_out, 0, 1)
        out[a - start : b - start] = src * (env * self.gain)[:, None]
        return out


class MixerEngine(Engine):
    descriptor = EngineDescriptor(
        id="audio-mixer",
        display_name="Chapter mixer",
        summary="Joins narration, music and effects into finished audio files.",
        version="1.0.0",
        modality="tool",
        tasks=["audio.assemble"],
        license="Built into Rasva Studio",
        license_class="permissive",
        requires_install=False,
    )

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "audio.assemble":
            raise EngineInputError(f"The mixer cannot do '{task}'.")
        segments = inputs.get("segments") or []
        if not segments:
            raise EngineInputError("There is no narration to export yet.")
        for s in segments:
            if not Path(str(s.get("path", ""))).is_file():
                raise EngineInputError(
                    "Some narration audio is missing. Regenerate those sentences."
                )
        tracks = []
        for t in inputs.get("tracks") or []:
            if (
                t.get("kind") not in ("music", "effect")
                or not Path(str(t.get("path", ""))).is_file()
            ):
                raise EngineInputError("A music or effect file is missing.")
            tracks.append({
                "kind": t["kind"], "path": t["path"],
                "anchor_segment": t.get("anchor_segment"),
                "offset_s": max(0.0, float(t.get("offset_s", 0))),
                "gain_db": float(t.get("gain_db", -18)),
                "fade_in_s": max(0.0, float(t.get("fade_in_s", 0))),
                "fade_out_s": max(0.0, float(t.get("fade_out_s", 0))),
                "loop": bool(t.get("loop", False)), "duck": bool(t.get("duck", True)),
                "name": str(t.get("name", "")),
            })  # fmt: skip
        formats = list(dict.fromkeys(inputs.get("formats") or ["wav"]))
        if not set(formats) <= {"wav", "mp3"} or "wav" not in formats:
            raise EngineInputError("Exports are WAV, optionally with MP3.")
        if "mp3" in formats and not inputs.get("ffmpeg"):
            raise EngineInputError("MP3 export needs FFmpeg, which was not found on this Mac.")
        return {
            "chapter_id": inputs.get("chapter_id"),
            "title": str(inputs.get("title") or "chapter"),
            "segments": [
                {"path": s["path"], "pause_after_s": float(s.get("pause_after_s", 0.3))}
                for s in segments
            ],  # fmt: skip
            "tracks": tracks,
            "duck_db": min(0.0, max(-30.0, float(inputs.get("duck_db", -12)))),
            "formats": formats,
            "stems": bool(inputs.get("stems", False)),
            "ffmpeg": inputs.get("ffmpeg"),
        }

    # ------------------------------------------------------------------ helpers

    def _decode(self, path: str, rate: int, ffmpeg: str | None, work: Path):
        import numpy as np

        try:
            data, src_rate = read_wav(path)
            data = data if data.shape[1] == 2 else np.repeat(data[:, :1], 2, axis=1)
            return resample(data, src_rate, rate)
        except (wave.Error, EOFError, EngineError):
            if not ffmpeg:
                raise EngineError(
                    "needs_ffmpeg",
                    "This music file is not a plain WAV. Convert it to WAV or install FFmpeg.",
                ) from None
        out = work / f"{Path(path).stem}.f32"
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", path, "-f", "f32le", "-ac", "2",
             "-ar", str(rate), str(out)],
            capture_output=True, timeout=600, check=False,
        )  # fmt: skip
        if result.returncode != 0:
            raise EngineError("decode_failed", "A music or effect file could not be read.")
        return np.memmap(out, dtype="<f4", mode="r").reshape(-1, 2)

    @staticmethod
    def _duck_curve(activity, duck_lin: float):
        import numpy as np

        n = len(activity)
        ahead = activity.copy()
        for k in range(1, DUCK_LOOKAHEAD_FRAMES + 1):
            ahead[: n - k] |= activity[k:]
        gains = np.empty(n, dtype=np.float32)
        g = 1.0
        for i in range(n):
            target = duck_lin if ahead[i] else 1.0
            g += (target - g) * (DUCK_ATTACK if target < g else DUCK_RELEASE)
            gains[i] = g
        return gains

    # ------------------------------------------------------------------ run

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        import numpy as np

        started = time.perf_counter()
        has_tracks = bool(inputs["tracks"])
        with wave.open(inputs["segments"][0]["path"], "rb") as w:
            narration_rate = w.getframerate()
        rate = MIX_RATE if has_tracks else narration_rate
        channels = 2 if has_tracks else 1
        narration = _Narration(inputs["segments"], rate)
        total = narration.total
        block = int(BLOCK_S * rate)
        frame = int(FRAME_S * rate)

        # ---- pass 0: level segments and map speech activity
        ctx.report_progress(0.0, "Measuring narration")
        rms = []
        for i in range(len(narration.files)):
            ctx.raise_if_canceled()
            seg = narration.segment(i)
            loud = seg[np.abs(seg) > SPEECH_THRESHOLD]
            rms.append(float(np.sqrt(np.mean(loud**2))) if loud.size else 0.0)
        voiced = [r for r in rms if r > 0]
        median = float(np.median(voiced)) if voiced else 0.0
        narration.gains = [
            float(np.clip(median / r, *LEVEL_CLAMP)) if r > 0 and median > 0 else 1.0 for r in rms
        ]
        narration._cache.clear()

        work = ctx.output_dir / "_work"
        work.mkdir(exist_ok=True)
        tracks: list[_Track] = []
        for spec in inputs["tracks"]:
            ctx.raise_if_canceled()
            data = self._decode(spec["path"], rate, inputs["ffmpeg"], work)
            anchor = spec["anchor_segment"]
            base = (
                narration.starts[anchor]
                if isinstance(anchor, int) and 0 <= anchor < len(narration.starts)
                else 0
            )
            tracks.append(_Track(spec, data, rate, base + int(spec["offset_s"] * rate), total))

        duck_gain = None
        if any(t.duck for t in tracks):
            n_frames = total // frame + 1
            activity = np.zeros(n_frames, dtype=bool)
            for start in range(0, total, block):
                ctx.raise_if_canceled()
                chunk = narration.read(start, min(block, total - start))
                k = len(chunk) // frame
                if k:
                    f_rms = np.sqrt(np.mean(chunk[: k * frame].reshape(k, frame) ** 2, axis=1))
                    activity[start // frame : start // frame + k] = f_rms > SPEECH_THRESHOLD
            duck_gain = self._duck_curve(activity, 10 ** (inputs["duck_db"] / 20))
            frame_pos = (np.arange(len(duck_gain)) + 0.5) * frame

        def render(start: int, n: int):
            voice = narration.read(start, n)
            music = np.zeros((n, 2), dtype=np.float32)
            effects = np.zeros((n, 2), dtype=np.float32)
            if tracks:
                gain = None
                if duck_gain is not None:
                    gain = np.interp(np.arange(start, start + n), frame_pos, duck_gain).astype(
                        np.float32
                    )
                for t in tracks:
                    part = t.read(start, n)
                    if t.duck and gain is not None:
                        part *= gain[:, None]
                    (music if t.kind == "music" else effects)[:] += part
                mix = voice[:, None] + music + effects
            else:
                mix = voice[:, None]
            return mix, voice, music, effects

        # ---- pass 1: peak
        peak = 0.0
        for start in range(0, total, block):
            ctx.raise_if_canceled()
            mix, *_ = render(start, min(block, total - start))
            peak = max(peak, float(np.max(np.abs(mix))) if mix.size else 0.0)
            ctx.report_progress(0.1 + 0.4 * start / total, "Checking levels")
        if peak == 0:
            raise EngineError("silent", "The narration is silent, so there is nothing to export.")
        scale = TARGET_PEAK / peak

        # ---- pass 2: write
        names = {"mix": "chapter.wav"}
        if inputs["stems"] and has_tracks:
            names.update({"voice": "narration.wav", "music": "music.wav", "effects": "effects.wav"})
            if not any(t.kind == "music" for t in tracks):
                names.pop("music")
            if not any(t.kind == "effect" for t in tracks):
                names.pop("effects")
        sum_sq = 0.0
        with contextlib.ExitStack() as stack:
            writers = {}
            for key, name in names.items():
                w = stack.enter_context(wave.open(str(ctx.output_dir / name), "wb"))
                w.setnchannels(1 if key == "voice" else channels)
                w.setsampwidth(2)
                w.setframerate(rate)
                writers[key] = w
            for start in range(0, total, block):
                ctx.raise_if_canceled()
                mix, voice, music, effects = render(start, min(block, total - start))
                parts = {"mix": mix, "voice": voice, "music": music, "effects": effects}
                sum_sq += float(np.sum((mix * scale) ** 2))
                for key, w in writers.items():
                    pcm = np.clip(parts[key] * scale, -1, 1)
                    w.writeframes((pcm * 32767).astype("<i2").tobytes())
                ctx.report_progress(0.5 + 0.45 * start / total, "Writing audio")

        duration = total / rate
        meta = {
            "kind": "chapter",
            "title": inputs["title"],
            "duration_s": round(duration, 3),
            "sample_rate": rate,
            "channels": channels,
            "segments": len(narration.files),
            "tracks": len(tracks),
            "peak_dbfs": _db(peak * scale),
            "rms_dbfs": _db(math.sqrt(sum_sq / max(1, total * channels))),
            "synthetic": True,
        }
        outputs = [OutputFile(filename="chapter.wav", media_type="audio/wav", metadata=meta)]
        for key, name in names.items():
            if key != "mix":
                stem_meta = {"kind": "stem", "stem": key, "title": inputs["title"]}
                outputs.append(
                    OutputFile(filename=name, media_type="audio/wav", metadata=stem_meta)
                )

        if "mp3" in inputs["formats"]:
            ctx.raise_if_canceled()
            ctx.report_progress(0.96, "Encoding MP3")
            result = subprocess.run(
                [inputs["ffmpeg"], "-nostdin", "-v", "error", "-y", "-i",
                 str(ctx.output_dir / "chapter.wav"), "-codec:a", "libmp3lame", "-b:a", "192k",
                 str(ctx.output_dir / "chapter.mp3")],
                capture_output=True, timeout=1800, check=False,
            )  # fmt: skip
            if result.returncode != 0:
                raise EngineError(
                    "mp3_failed", "MP3 encoding failed. The WAV export is unaffected; try WAV only."
                )
            outputs.append(
                OutputFile(
                    filename="chapter.mp3",
                    media_type="audio/mpeg",
                    metadata={**meta, "format": "mp3"},
                )
            )

        elapsed = time.perf_counter() - started
        return RunResult(
            outputs=outputs,
            metrics={
                "audio_s": round(duration, 3),
                "render_s": round(elapsed, 3),
                "peak_before_limit_dbfs": _db(peak),
                "gain_applied_db": _db(scale),
                "segments": len(narration.files),
                "tracks": len(tracks),
            },
        )
