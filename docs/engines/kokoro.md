# Kokoro-82M (phase A1 baseline engine)

Status: installed and narrating on the target M2. The model files are pinned
in `manifest.json`, and the A1 measurements are below.

## Measurements on the M2 (A1-009, A1-011)

Taken on 2026-11-20 on a MacBook with an M2 and 16 GB of memory. Kokoro 0.9.4
(adapter 1.0) ran on PyTorch CPU, with the pinned model files. The data comes
from the `audio-v1` experiment, run through the API after a fresh server
restart. There were two candidates, `af_heart` (English) and `hf_alpha` (Hindi).

| Measure | Result | How it was measured |
| --- | --- | --- |
| Runs | 85 completed, 0 failed, 35 skipped, 0 truncated segments | The 35 skips are expected: they are the Devanagari cases (Hindi, mixed and names) given to the English voice, which refuses Devanagari by design |
| Model load (cold) | 17.3 s | One sample: the first job after the restart, including starting the worker and importing torch and spaCy. Warm jobs load nothing |
| Real-time factor | median 0.200 (range 0.185 to 0.239) | Generation time ÷ audio length, so lower is faster: about 5× faster than real time. The highest value is the cold first job. The median is the same with the 15 Roman Hinglish cases left out (0.200 over 55 runs) |
| Peak memory | 2.16 GB (2,160,263,168 bytes) | The engine worker's lifetime high-water mark (`ru_maxrss`), excluding the Rasva server process. It rose from 1.42 GB after the first job |
| Installed size | 1.03 GB (1,033,326,592 bytes) | Disk blocks measured by the installer: model files about 333 MB, plus Kokoro's Python environment. After the first runs, Python bytecode caches add about 60 MB. uv's download cache (683 MB, under `cache/uv`) is not included |

All the cases in this experiment are single sentences (79 used one segment and
6 used two), so long-form throughput is not covered here. Listening scores are
not part of these numbers: A1-012 needs a person to review the audio.

## Enable it

1. On a machine that can reach huggingface.co, run:
   `python3 scripts/pin_kokoro.py`
2. Review the generated `backend/rasva/engines/kokoro/manifest.json`. It pins
   one commit and lists each file's SHA-256 and size. Commit the file.
3. On the Mac, go to Models, then Kokoro, then Install. The installer downloads
   about 350 MB of weights and verifies every file. It then builds Kokoro's own
   Python 3.12 environment from `requirements.lock`. The measured installed size
   on the M2 is 1.03 GB.

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
