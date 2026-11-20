"""Chatterbox Multilingual voice engine (phase A3 candidate, PRD §14).

Draft adapter, written against chatterbox-tts 0.1.7's source and not yet run on
the target Mac. What it relies on (read from the package):

- ``ChatterboxMultilingualTTS.from_local(ckpt_dir, device)`` loads ve.pt,
  t3_mtl23ls_v2.safetensors, s3gen.pt, grapheme_mtl_merged_expanded_v1.json and
  the built-in voice conds.pt from a local folder: no network.
- ``prepare_conditionals(wav_path, exaggeration)`` clones a voice from a short
  recording; ``generate(text, language_id, exaggeration, cfg_weight,
  temperature)`` returns a (1, n) tensor at ``model.sr`` (24 kHz).
- Every output passes through Resemble AI's PerTh implicit watermarker, an
  inaudible mark that the audio is AI-generated (PRD §28 disclosure).

Model files come from manifest.json (pinned commit + SHA-256), created by
``scripts/pin_engine.py chatterbox``. Until then the engine is listed but not
installable. Module import must stay light (no torch here).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from rasva.audio.assemble import Assembler
from rasva.audio.text import script_counts, segment
from rasva.engines import manifest as manifest_mod
from rasva.engines.contract import (
    CompatibilityReport,
    Engine,
    EngineDescriptor,
    EngineError,
    EngineInputError,
    InstallContext,
    OutputFile,
    Preset,
    RunContext,
    RunResult,
    RuntimeContext,
    SystemFacts,
    Voice,
)

ENGINE_ID = "chatterbox-multilingual"
REPO_ID = "ResembleAI/chatterbox"
MODEL_FILES = [
    "ve.pt",
    "t3_mtl23ls_v2.safetensors",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "conds.pt",  # the built-in voice
]
MAX_CHARS = 5_000
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
LOCKFILE = Path(__file__).with_name("requirements.lock")

PRESETS = [
    Preset(id="steady", label="Steady", description="Calm, even narration.",
           settings={"exaggeration": 0.4, "cfg_weight": 0.5, "temperature": 0.7}),
    Preset(id="natural", label="Natural", description="Balanced, like normal reading.",
           settings={"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8}),
    Preset(id="expressive", label="Expressive", description="More emotion, slower pacing.",
           settings={"exaggeration": 0.7, "cfg_weight": 0.3, "temperature": 0.8}),
]  # fmt: skip
DEFAULTS = {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8}
VOICES = [
    # The same built-in voice, offered for each language Rasva narrates in.
    Voice(id="default_en", display_name="Chatterbox voice", language="en"),
    Voice(id="default_hi", display_name="Chatterbox voice", language="hi"),
]

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID})
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST)
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS)
# torch + transformers + gradio (a required dependency) + Python 3.11; estimate,
# replaced by the measured size after installation.
_ENV_ESTIMATE = 2_200 * 1024**2


class ChatterboxEngine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="Chatterbox",
        summary=(
            "Expressive voice engine that can speak in your own recorded voice, in Hindi "
            "and English. Slower and larger than Kokoro."
        ),
        version="0.1.7",
        modality="audio",
        tasks=["audio.narrate"],
        languages=["Hindi", "English"],
        features=["voice_cloning", "preset_voices", "preview", "expressiveness"],
        license="MIT (code and model weights, per Resemble AI; recheck when pinning)",
        license_class="permissive",
        license_notes=[
            "Every clip carries Resemble AI's inaudible PerTh watermark, which marks it as "
            "AI-generated. Rasva also labels it synthetic.",
            "Draft adapter: not yet run on an M2 Mac. Speed and memory are unmeasured.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _ENV_ESTIMATE,
        approx_peak_memory_bytes=5 * 1024**3,
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.11",
        voices=VOICES,
        default_voices={"en": "default_en", "hi": "default_hi"},
        presets=PRESETS,
        experimental=False,
    )

    def __init__(self) -> None:
        self._model: Any = None
        self._builtin_conds: Any = None
        self._ref_key: tuple[str, float] | None = None
        self.device = "cpu"

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not self.descriptor.artifacts:
            reasons.append(
                "Chatterbox's download list has not been pinned yet. "
                "Run scripts/pin_engine.py chatterbox once."
            )
        if not system.apple_silicon:
            reasons.append("Chatterbox's software package is built for Apple Silicon Macs.")
        return CompatibilityReport(compatible=not reasons, reasons=reasons)

    def verify(self, ctx: InstallContext) -> bool:
        return all((ctx.model_dir / f).is_file() for f in MODEL_FILES)

    # ---- runtime ----

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "audio.narrate":
            raise EngineInputError(f"Chatterbox cannot do '{task}'.")
        text = str(inputs.get("text") or "").strip()
        if not text:
            raise EngineInputError("Write or paste some text to narrate.")
        if len(text) > MAX_CHARS:
            raise EngineInputError(f"Chatterbox narrates up to {MAX_CHARS:,} characters at a time.")
        ref = inputs.get("reference")
        if ref:
            if not Path(str(ref.get("path", ""))).is_file() or not ref.get("transcript"):
                raise EngineInputError("This voice's recording is missing. Record it again.")
            voice_id = "own"
        else:
            voice_id = str(inputs.get("voice_id") or "")
            if voice_id not in {v.id for v in VOICES}:
                raise EngineInputError("Choose a Chatterbox voice or one of your own voices.")
        out: dict[str, Any] = {
            "text": text,
            "voice_id": voice_id,
            "preview": bool(inputs.get("preview")),
        }
        if ref:
            out["reference"] = {"path": str(ref["path"]), "transcript": str(ref["transcript"]),
                                "language": ref.get("language", "any")}  # fmt: skip
        ranges = {"exaggeration": (0.25, 2.0), "cfg_weight": (0.0, 1.0), "temperature": (0.05, 2.0)}
        for key, (lo, hi) in ranges.items():
            try:
                value = float(inputs.get(key, DEFAULTS[key]))
            except (TypeError, ValueError) as exc:
                raise EngineInputError(f"{key} must be a number.") from exc
            if not lo <= value <= hi:
                raise EngineInputError(f"{key} must be between {lo} and {hi}.")
            out[key] = round(value, 3)
        out["speed"] = 1.0  # Chatterbox has no speed control; pacing comes from cfg_weight
        return out

    def load(self, ctx: RuntimeContext) -> None:
        _quiet_library_logs()
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        preferred = os.environ.get("RASVA_TORCH_DEVICE") or (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        try:
            self._model = ChatterboxMultilingualTTS.from_local(ctx.model_dir, preferred)
            self.device = preferred
        except Exception:
            if preferred == "cpu":
                raise
            # The Apple GPU path is newer; fall back to the CPU rather than fail.
            self._model = ChatterboxMultilingualTTS.from_local(ctx.model_dir, "cpu")
            self.device = "cpu"
        self._builtin_conds = self._model.conds

    def _use_voice(self, inputs: dict[str, Any]) -> None:
        ref = inputs.get("reference")
        if ref is None:
            if self._ref_key is not None:
                self._model.conds = self._builtin_conds  # back to the built-in voice
                self._ref_key = None
            return
        key = (ref["path"], inputs["exaggeration"])
        if key != self._ref_key:  # conditioning is costly: reuse it across sentences and jobs
            self._model.prepare_conditionals(ref["path"], exaggeration=inputs["exaggeration"])
            self._ref_key = key

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._model is not None, "load() not called"
        segments = segment(inputs["text"])[: 1 if inputs["preview"] else None]
        if not segments:
            raise EngineInputError("There is nothing to narrate in this text.")
        self._use_voice(inputs)
        started = time.perf_counter()
        out = Assembler(int(self._model.sr))
        for k, seg in enumerate(segments):
            ctx.raise_if_canceled()
            ctx.report_progress(k / len(segments), f"Sentence {k + 1} of {len(segments)}")
            language = "hi" if script_counts(seg.text)["devanagari"] else "en"
            wav = self._model.generate(
                seg.text, language_id=language, exaggeration=inputs["exaggeration"],
                cfg_weight=inputs["cfg_weight"], temperature=inputs["temperature"],
            )  # fmt: skip
            out.add(seg.index, seg.text, wav.squeeze(0).cpu().numpy(), seg.pause_after_s,
                    last=k == len(segments) - 1)  # fmt: skip
        ctx.report_progress(1.0, "Saving audio")
        try:
            stats = out.write(ctx.output_dir / "narration.wav")
        except ValueError as exc:
            raise EngineError("silent", "Chatterbox produced no sound for this text.") from exc
        elapsed = time.perf_counter() - started
        return RunResult(
            outputs=[OutputFile(
                filename="narration.wav", media_type="audio/wav",
                metadata={
                    "kind": "preview" if inputs["preview"] else "narration",
                    "engine": ENGINE_ID,
                    "model_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
                    "voice_id": inputs["voice_id"],
                    "cloned": "reference" in inputs,
                    "duration_s": stats["duration_s"], "sample_rate": out.rate,
                    "segments": out.timeline, "synthetic": True, "watermark": "perth",
                },
            )],
            metrics={
                "audio_s": stats["duration_s"], "synthesis_s": round(elapsed, 3),
                "real_time_factor": round(elapsed / stats["duration_s"], 4),
                "segments": len(segments), "device": self.device,
                "peak_before_limit": stats["peak_before_limit"],
            },
        )  # fmt: skip

    def unload(self) -> None:
        self._model = None
        self._builtin_conds = None


def _quiet_library_logs() -> None:
    """Keep transformers/diffusers chatter (which can quote text) out of engine logs."""
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("DIFFUSERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
