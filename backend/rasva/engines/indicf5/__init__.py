"""IndicF5 voice engine for Indian languages (phase A2 candidate, PRD §14).

Draft adapter, written against the f5_tts package in github.com/AI4Bharat/IndicF5
(setup.py name "f5_tts"); not yet run on the target Mac.

Why this is more cautious than the other engines:

- The official usage is ``AutoModel.from_pretrained(..., trust_remote_code=True)``,
  which executes Python fetched from Hugging Face. PRD §28 rules that out. This
  adapter instead uses the GitHub package's own functions with local files:
  ``load_model(DiT, model_cfg, vocab_file=...)``, ``load_checkpoint(...)``,
  ``load_vocoder("vocos", is_local=True, local_path=...)`` and
  ``infer_process(ref_audio, ref_text, gen_text, model, vocoder, ...)``.
- Which checkpoint/vocab files and model config the release uses could not be
  confirmed here (huggingface.co is not reachable from the build environment).
  ``scripts/pin_engine.py indicf5`` lists the repository's files; the maintainer
  fills ``layout`` in manifest.json after checking them. Without a layout the
  engine is reported as not installable.
- The repository has no licence file, and its training data includes corpora
  with their own terms, so the licence is "unknown" and installing requires an
  explicit confirmation.
- Its dependencies are installed from GitHub, which the build environment
  cannot reach, so the lockfile is generated on the Mac
  (``scripts/lock_engine.sh indicf5``).

IndicF5 is a reference-voice ("zero-shot") model: it always speaks in the voice
of a recording, so it uses your voices from My voices.
"""

from __future__ import annotations

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
)

ENGINE_ID = "indicf5"
REPO_ID = "ai4bharat/IndicF5"
VOCODER_REPO = "charactr/vocos-mel-24khz"
VOCODER_DIR = "vocos"
MAX_CHARS = 5_000
MANIFEST_PATH = Path(__file__).with_name("manifest.json")
LOCKFILE = Path(__file__).with_name("requirements.lock")
REQUIRED_LAYOUT = ("checkpoint", "vocab", "model_cfg")

PRESETS = [
    Preset(id="fast", label="Faster", description="Half the steps: quicker, slightly rougher.",
           settings={"nfe_step": 16}),
    Preset(id="best", label="Best", description="Full quality (32 steps). Slower.",
           settings={"nfe_step": 32}),
]  # fmt: skip

_MANIFEST = manifest_mod.load(MANIFEST_PATH, ENGINE_ID, {REPO_ID, VOCODER_REPO})
_LAYOUT: dict[str, Any] = (_MANIFEST or {}).get("layout") or {}
_READY = bool(_MANIFEST) and all(k in _LAYOUT for k in REQUIRED_LAYOUT)
_ARTIFACTS = manifest_mod.artifacts(_MANIFEST) if _READY else []
_WEIGHTS = sum(a.size_bytes for a in _ARTIFACTS)
_ENV_ESTIMATE = 2_500 * 1024**2  # torch, transformers<4.50, datasets, librosa, vocos...


class IndicF5Engine(Engine):
    descriptor = EngineDescriptor(
        id=ENGINE_ID,
        display_name="IndicF5",
        summary=(
            "AI4Bharat's engine for Indian languages. Speaks Hindi (in Devanagari) in the "
            "voice of one of your recordings."
        ),
        version="2025.03",
        modality="audio",
        tasks=["audio.narrate"],
        languages=["Hindi"],
        features=["voice_cloning", "preview", "speed"],
        license="Unverified: no licence file in the IndicF5 repository",
        license_class="unknown",
        license_notes=[
            "The repository has no licence file, and its training data (Rasa, IndicTTS, "
            "LIMMITS, IndicVoices-R) comes with its own terms. Check them before using results "
            "beyond personal use.",
            "Draft adapter: not yet run on an M2 Mac. Speed and memory are unmeasured.",
        ],
        source_url=f"https://huggingface.co/{REPO_ID}",
        approx_download_bytes=_WEIGHTS,
        approx_installed_bytes=_WEIGHTS + _ENV_ESTIMATE,
        approx_peak_memory_bytes=4 * 1024**3,
        artifacts=_ARTIFACTS,
        requirements_lock="requirements.lock",
        python_version="3.10",
        voices=[],  # always speaks in the voice of your recording
        presets=PRESETS,
    )

    def __init__(self) -> None:
        self._model: Any = None
        self._vocoder: Any = None
        self._fn: dict[str, Any] = {}
        self._ref: tuple[str, str] | None = None
        self._prepared: tuple[str, str] | None = None
        self.device = "cpu"

    # ---- control plane ----

    def check_compatibility(self, system: SystemFacts) -> CompatibilityReport:
        reasons = []
        if not _MANIFEST:
            reasons.append(
                "IndicF5's download list has not been pinned yet. "
                "Run scripts/pin_engine.py indicf5 once."
            )
        elif not _READY:
            reasons.append(
                "IndicF5's file layout still needs confirming in its manifest "
                "(checkpoint, vocabulary and model settings)."
            )
        if not LOCKFILE.is_file():
            reasons.append(
                "IndicF5's software list has not been generated yet. "
                "Run scripts/lock_engine.sh indicf5 on the Mac."
            )
        if not system.apple_silicon:
            reasons.append("IndicF5's software package is built for Apple Silicon Macs.")
        return CompatibilityReport(compatible=not reasons, reasons=reasons)

    def verify(self, ctx: InstallContext) -> bool:
        needed = [_LAYOUT.get("checkpoint"), _LAYOUT.get("vocab"),
                  f"{VOCODER_DIR}/config.yaml", f"{VOCODER_DIR}/pytorch_model.bin"]  # fmt: skip
        return all(n and (ctx.model_dir / n).is_file() for n in needed)

    # ---- runtime ----

    def validate_input(self, task: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if task != "audio.narrate":
            raise EngineInputError(f"IndicF5 cannot do '{task}'.")
        text = str(inputs.get("text") or "").strip()
        if not text:
            raise EngineInputError("Write or paste some text to narrate.")
        if len(text) > MAX_CHARS:
            raise EngineInputError(f"IndicF5 narrates up to {MAX_CHARS:,} characters at a time.")
        counts = script_counts(text)
        if counts["devanagari"] == 0 or counts["latin"] > counts["devanagari"]:
            raise EngineInputError(
                "IndicF5 reads Hindi in Devanagari. Use “Suggest Devanagari” in Audio Studio to "
                "convert Roman Hinglish, or choose Kokoro or Chatterbox for English."
            )
        ref = inputs.get("reference")
        if not ref:
            raise EngineInputError(
                "IndicF5 speaks in the voice of a recording: choose one of your voices."
            )
        if (
            not Path(str(ref.get("path", ""))).is_file()
            or not str(ref.get("transcript", "")).strip()
        ):
            raise EngineInputError("This voice's recording is missing. Record it again.")
        try:
            speed = float(inputs.get("speed", 1.0))
            steps = int(inputs.get("nfe_step", 32))
        except (TypeError, ValueError) as exc:
            raise EngineInputError("Speed and steps must be numbers.") from exc
        if not 0.5 <= speed <= 2.0:
            raise EngineInputError("Speed must be between 0.5× and 2×.")
        if steps not in (16, 24, 32, 48, 64):
            raise EngineInputError("Steps must be 16, 24, 32, 48 or 64.")
        return {
            "text": text, "voice_id": "own", "preview": bool(inputs.get("preview")),
            "speed": round(speed, 2), "nfe_step": steps,
            "reference": {"path": str(ref["path"]), "transcript": str(ref["transcript"]),
                          "language": ref.get("language", "hi")},
        }  # fmt: skip

    def load(self, ctx: RuntimeContext) -> None:
        import torch
        from f5_tts.infer import utils_infer as ui
        from f5_tts.model import DiT

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = ui.load_model(
            DiT, dict(_LAYOUT["model_cfg"]),
            vocab_file=str(ctx.model_dir / _LAYOUT["vocab"]), device=self.device,
        )  # fmt: skip
        self._model = ui.load_checkpoint(
            model, str(ctx.model_dir / _LAYOUT["checkpoint"]), self.device,
            use_ema=bool(_LAYOUT.get("use_ema", True)),
        )  # fmt: skip
        self._vocoder = ui.load_vocoder(
            "vocos", is_local=True, local_path=str(ctx.model_dir / VOCODER_DIR), device=self.device
        )
        self._fn = {"prep": ui.preprocess_ref_audio_text, "infer": ui.infer_process}

    def run(self, task: str, inputs: dict[str, Any], ctx: RunContext) -> RunResult:
        assert self._model is not None, "load() not called"
        segments = segment(inputs["text"])[: 1 if inputs["preview"] else None]
        if not segments:
            raise EngineInputError("There is nothing to narrate in this text.")
        ref = inputs["reference"]
        key = (ref["path"], ref["transcript"])
        if key != self._ref:  # trimming/normalising the reference is reused across sentences
            # The library prints progress; pass a silent callback to keep logs free of text.
            self._prepared = self._fn["prep"](
                ref["path"], ref["transcript"], show_info=lambda *_: None
            )
            self._ref = key
        ref_audio, ref_text = self._prepared  # type: ignore[misc]
        started = time.perf_counter()
        out: Assembler | None = None
        for k, seg in enumerate(segments):
            ctx.raise_if_canceled()
            ctx.report_progress(k / len(segments), f"Sentence {k + 1} of {len(segments)}")
            wave, rate, _ = self._fn["infer"](
                ref_audio, ref_text, seg.text, self._model, self._vocoder,
                show_info=lambda *_: None, nfe_step=inputs["nfe_step"],
                speed=inputs["speed"], device=self.device,
            )  # fmt: skip
            out = out or Assembler(int(rate))
            out.add(seg.index, seg.text, wave, seg.pause_after_s, last=k == len(segments) - 1)
        ctx.report_progress(1.0, "Saving audio")
        assert out is not None
        try:
            stats = out.write(ctx.output_dir / "narration.wav")
        except ValueError as exc:
            raise EngineError("silent", "IndicF5 produced no sound for this text.") from exc
        elapsed = time.perf_counter() - started
        return RunResult(
            outputs=[OutputFile(
                filename="narration.wav", media_type="audio/wav",
                metadata={
                    "kind": "preview" if inputs["preview"] else "narration",
                    "engine": ENGINE_ID,
                    "model_revision": manifest_mod.revisions(_MANIFEST).get(REPO_ID),
                    "voice_id": "own", "cloned": True, "duration_s": stats["duration_s"],
                    "sample_rate": out.rate, "segments": out.timeline, "synthetic": True,
                    "nfe_step": inputs["nfe_step"],
                },
            )],
            metrics={
                "audio_s": stats["duration_s"], "synthesis_s": round(elapsed, 3),
                "real_time_factor": round(elapsed / stats["duration_s"], 4),
                "segments": len(segments), "device": self.device, "nfe_step": inputs["nfe_step"],
                "peak_before_limit": stats["peak_before_limit"],
            },
        )  # fmt: skip

    def unload(self) -> None:
        self._model = None
        self._vocoder = None
        self._ref = None
        self._prepared = None
