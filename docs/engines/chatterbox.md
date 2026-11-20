# Chatterbox Multilingual (phase A3 candidate: voice cloning)

Status: **draft adapter, not yet run.** Adapter, hash-pinned lockfile, tests
with a fake runtime and UI support are done. Model files are **not pinned yet**,
so Chatterbox shows on the Models page but cannot be installed. Nothing here has
been measured on the M2.

## Enable it

1. On a machine that can reach huggingface.co:
   `python3 scripts/pin_engine.py chatterbox`
2. Review `backend/rasva/engines/chatterbox/manifest.json` (one commit, each
   file's SHA-256 and size; five files: `ve.pt`, `t3_mtl23ls_v2.safetensors`,
   `s3gen.pt`, `grapheme_mtl_merged_expanded_v1.json`, `conds.pt`). Recheck the
   licence on the model card at that commit. Commit the file.
3. On the Mac: Models → Chatterbox → Install. The Python 3.11 environment comes
   from `requirements.lock` (110 wheels, all with hashes).
4. Run the A3 checks below before relying on it.

## Decisions

| Decision | Why |
| --- | --- |
| `chatterbox-tts==0.1.7`, its exact pins (torch 2.6.0, transformers 5.2.0, numpy 1.26.4) | The package pins these itself; the lock follows them. |
| `ChatterboxMultilingualTTS.from_local(dir, device)` | Loads only the five local files. `from_pretrained` would call the Hub. The worker also runs with `HF_HUB_OFFLINE=1`. |
| Apple GPU (MPS) first, CPU fallback | If loading on MPS raises, it reloads on CPU instead of failing. Override with `RASVA_TORCH_DEVICE=cpu`. Which is faster on the M2 is unmeasured. |
| Language per sentence | A sentence with any Devanagari is sent as `hi`, otherwise `en`, so mixed scripts read with the right front end. Roman Hinglish is read as English. |
| Voice conditioning cached | `prepare_conditionals` runs once per (recording, expressiveness), not per sentence or job. Switching back to a built-in voice restores the built-in conditioning. |
| No speed control | Chatterbox has none; the speed slider is hidden. Pacing comes from the presets (`cfg_weight`). |
| Presets Steady / Natural / Expressive | Map to `exaggeration`, `cfg_weight`, `temperature`. Values are the upstream defaults and suggestions, not tuned for Hindi yet. |

## What is not known yet (check on the Mac)

- Whether `from_local` with `t3_mtl23ls_v2.safetensors` loads as written (read from
  0.1.7's source, not run).
- Hindi quality, cloned-voice similarity, speed, peak memory (estimated 5 GB) and
  installed size (estimated 2.2 GB plus weights).
- Whether MPS works for all three sub-models.

## Watermark and licences

Every output passes through Resemble AI's **PerTh** watermarker, an inaudible
mark that the audio is AI-generated. Rasva also labels it synthetic in the file
details. Chatterbox code is MIT; the model card states MIT for the weights; the
pin step should recheck that. The package depends on **gradio** (Apache-2.0)
even though Rasva never starts its web UI, which accounts for part of the
environment size.
