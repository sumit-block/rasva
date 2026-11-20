# Kokoro-82M (phase A1 baseline engine)

Status: adapter, environment lockfile and UI are done. **Model files are not
pinned yet** (see "Enable it"), so Kokoro shows on the Models page but cannot be
installed. It has not yet been run on the target M2. The A1 measurements (load
time, real-time factor, peak memory, disk) still need to be taken there.

## Enable it

1. On a machine that can reach huggingface.co, run:
   `python3 scripts/pin_kokoro.py`
2. Review the generated `backend/rasva/engines/kokoro/manifest.json`. It pins
   one commit and lists each file's SHA-256 and size. Commit the file.
3. On the Mac, go to Models, then Kokoro, then Install. The installer downloads
   about 350 MB of weights and verifies every file. It then builds Kokoro's own
   Python 3.12 environment from `requirements.lock` (installed size estimated
   at about 1.6 GB; the real size is measured and shown after install).

## Decisions

| Decision | Why |
| --- | --- |
| Official `kokoro` 0.9.4 package on **PyTorch, CPU** | This is the reference implementation, and an 82M model is fast on M2 CPU cores. The Apple GPU path (MPS) needs PyTorch's CPU fallback for some operations, and MLX ports change quickly. Both are candidates for an A1 benchmark, not the default. |
| Separate **Python 3.12** environment | kokoro requires Python below 3.13. Its large dependencies (torch, spaCy, transformers) stay out of the app (ADR-0003). |
| `transformers>=4.40,<5` | kokoro 0.9.4 (April 2025) subclasses `AlbertModel` and was never released against transformers 5. |
| `en_core_web_sm` pinned in the lockfile by URL and SHA-256 | misaki otherwise calls `spacy.cli.download()` the first time English is used, which needs the internet at generation time. |
| `--only-binary :all:` | Nothing compiles on the user's Mac. Every package, espeakng-loader included, ships an Apple Silicon wheel. |
| Rasva segments the text, not kokoro | kokoro splits non-English text only on `.`, `!` and `?`. Long Hindi passages ending in `।` would be one chunk, and anything past 510 phonemes would be **silently truncated**. Rasva splits on `।`, `॥`, `?`, `!`, `.` and newlines, keeps abbreviations and initials together, and caps segments at 220 characters. A `truncated_segments` metric flags any segment that still hits the limit. |
| English voices reject any Devanagari | misaki's English G2P skips words it cannot read (`unk=''`), so Hindi words would silently disappear from the audio. |
| Local files only | `KModel(config=..., model=...)` and `.pt` voice paths are passed directly. The worker also runs with `HF_HUB_OFFLINE=1`, so any accidental hub lookup fails instead of downloading. |

## Limitations to state honestly

- Hindi uses espeak-ng phonemes, so expect basic quality. Proper names such as
  कृष्ण or युधिष्ठिर need the A2 pronunciation dictionary and an Indic engine.
- Roman Hinglish is read with English pronunciation rules.
- There is no voice cloning. That is phase A3.
- Up to 5,000 characters per job. Long-form chapters with per-segment jobs and
  selective regeneration are phase A4.

## Licences

The kokoro code and model weights are Apache-2.0. **phonemizer-fork (GPL-3.0+)
and espeak-ng (GPL-3.0, bundled by espeakng-loader)** are dependencies. They are
installed on the user's Mac from PyPI into Kokoro's own environment, and Rasva
calls them only from the separate worker process. That is fine for personal
local use. Redistributing a bundle that contains them brings GPL obligations,
so review this before any packaged release. The note is shown on the Models
page.
