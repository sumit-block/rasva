# IndicF5 (phase A2 candidate: Indian-language engine)

Status: **draft adapter, not yet run, with three gates still closed.** Adapter,
tests with a fake runtime and UI support are done. It cannot be installed until
all of these are done on a machine that can reach huggingface.co and github.com:

1. `python3 scripts/pin_engine.py indicf5` – pins the IndicF5 repository and the
   Vocos vocoder (`charactr/vocos-mel-24khz`, into `vocos/`) by commit and SHA-256.
   If the snapshot contains `.py` files, the script stops and lists them; read
   them, then re-run with `--reviewed-code`. Rasva never runs them.
2. Fill `"layout"` in `backend/rasva/engines/indicf5/manifest.json` after
   checking the repository: which file is the checkpoint, where `vocab.txt` is,
   the DiT settings, and whether to use the EMA weights. Example shape:
   `{"checkpoint": "model.safetensors", "vocab": "checkpoints/vocab.txt",
   "model_cfg": {"dim": 1024, "depth": 22, "heads": 16, "ff_mult": 2,
   "text_dim": 512, "conv_layers": 4}, "use_ema": true}` – **these values are
   F5-TTS base defaults, not confirmed for IndicF5.**
3. `scripts/lock_engine.sh indicf5 <full commit sha>` – builds
   `requirements.lock` (Python 3.10, Apple Silicon wheels, hashes) from
   `requirements.in`, with the GitHub package pinned to that commit. Review the
   diff, then commit.

## Decisions

| Decision | Why |
| --- | --- |
| No `trust_remote_code` | The official usage `AutoModel.from_pretrained(..., trust_remote_code=True)` runs Python downloaded from the Hub. PRD §28 rules that out. The adapter uses the GitHub package's own `load_model`, `load_checkpoint`, `load_vocoder(is_local=True)` and `infer_process` with local files. |
| Reference voice required | IndicF5 always speaks in the voice of a recording, so it lists no built-in voices; Audio Studio shows only "Mine" for it. |
| Devanagari only | Text that is mostly Latin is rejected with a pointer to "Suggest Devanagari" or to Kokoro/Chatterbox. A few English words inside Hindi are allowed. |
| Reference prepared once | `preprocess_ref_audio_text` runs once per recording and transcript, with a silent callback so nothing is printed. |
| Presets Faster (16 steps) / Best (32) | `nfe_step` trades speed for quality. Unmeasured on the M2. |

## Licence: unknown

The repository has no licence file, and its training data (Rasa, IndicTTS,
LIMMITS, IndicVoices-R) has its own terms. The engine is marked "unknown", so
installing requires an explicit confirmation, and the Models page says to check
the terms before using results beyond personal use.
