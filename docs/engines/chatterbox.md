# Chatterbox Multilingual (phase A3 candidate: voice cloning)

Status: installed and narrating on the target M2, on CPU, with the built-in
voice and with a cloned voice. Three fixes were needed on the first run (see
"Decisions"). Quality has not been reviewed by ear yet.

## Measurements on the M2 (2026-11-20)

The Mac is an M2 with 16 GB of memory, running chatterbox-tts 0.1.7 on PyTorch
CPU, with the model files pinned at `5bb1f6e`. The runs used the built-in voice
and the Natural preset (the defaults), through the API after the reinstall.
**Scope is English only for now (owner decision, 2026-11-20).** The
`audio-v1` run was stopped after its English cases: 30 Devanagari cases were
cancelled, and 5 Hindi cases that had already finished are not counted here.
Devanagari will be measured later.

| Measure | Result | How it was measured |
| --- | --- | --- |
| Runs | 10 English `audio-v1` cases and 10 English sentences with Mahabharata names: 20 completed, 0 failed | The names sentences were a separate custom experiment, compared against Kokoro `af_heart` |
| Model load (cold) | median 18.3 s (range 16.4 to 28.1 s, 12 cold starts) | Each includes starting the worker and loading three sub-models on CPU. The names experiment alternated engines, so the worker restarted often |
| Real-time factor | median 6.71 on the `audio-v1` English cases (range 6.17 to 7.33); median 6.69 on the names sentences | Generation time ÷ audio length, so this is about 7× **slower** than real time. Kokoro's is 0.20. The 15 Roman Hinglish cases (out of scope) also ran, at a median of 6.73 |
| Peak memory | 6.27 GB in the app (6,265,618,432 bytes, highest over 43 jobs); 7.02 GB in a standalone CPU test | The engine worker's lifetime high-water mark (`ru_maxrss`) on CPU, excluding the Rasva server process. The standalone figure is from `/usr/bin/time -l` on a scratch run of the same adapter that narrated four short texts |
| Installed size | 4.25 GB (4,247,867,392 bytes) | Disk blocks measured by the installer: model files 3.21 GB, plus the Python 3.11 environment |
| Network use | none with the built-in voice | A load-and-generate run (English and Devanagari) with every socket blocked made 0 attempts, after the segmenter fix below. The cloning path (`prepare_conditionals`) was not run under that guard |
| Cloned voice | runs: 10.8 s of English audio from a consented 11.6 s recording, real-time factor 6.53, marked `cloned` and `synthetic`, PerTh watermark | The recording passed the quality check except for one "a bit quiet" warning. Similarity has not been judged by ear yet |

What to listen for: some clips are much longer than their text needs. For
example, "She whispered, 'I'll be back before midnight.'" came out as 9.2 s, and
the same two Hindi sentences ranged from 8.6 s to 17.4 s over six runs. The
engine log shows Chatterbox forcing an early stop on detected repetition or
long tails. These clips may have trailing noise or babble.

## Enable it

1. On a machine that can reach huggingface.co:
   `python3 scripts/pin_engine.py chatterbox`
2. Review `backend/rasva/engines/chatterbox/manifest.json` (one commit, each
   file's SHA-256 and size; five files: `ve.pt`, `t3_mtl23ls_v2.safetensors`,
   `s3gen.pt`, `grapheme_mtl_merged_expanded_v1.json`, `conds.pt`). Recheck the
   licence on the model card at that commit. Commit the file.
3. On the Mac: Models → Chatterbox → Install. The Python 3.11 environment comes
   from `requirements.lock` (111 wheels, all with hashes).
4. Run the A3 checks below before relying on it.

## Decisions

| Decision | Why |
| --- | --- |
| `chatterbox-tts==0.1.7`, its exact pins (torch 2.6.0, transformers 5.2.0, numpy 1.26.4) | The package pins these itself; the lock follows them. |
| `ChatterboxMultilingualTTS.from_local(dir, device)` | Loads only the five local files. `from_pretrained` would call the Hub. The worker also runs with `HF_HUB_OFFLINE=1`. |
| CPU by default; Apple GPU (MPS) opt-in | Measured on the M2 on 2026-11-20 with the same two-sentence texts, three runs each. English took 27–46 s on CPU and 32–51 s on MPS. Hindi took 50–86 s on CPU and 65–166 s on MPS. MPS was no faster and much less consistent, and the RSS metric does not count its GPU memory. Set `RASVA_TORCH_DEVICE=mps` to try it. If loading on MPS fails, the engine logs the reason and reloads on CPU. |
| `setuptools<81` in the lockfile | `resemble-perth` (the watermarker) imports `pkg_resources` without declaring setuptools. Without it, the watermarker is silently `None` and loading fails with `'NoneType' object is not callable`. |
| Chinese word segmenter disabled | On every load, Chatterbox's tokenizer creates a `spacy_pkuseg` segmenter, which downloads a 34 MB model from GitHub into `~/.pkuseg`. That download is unpinned, unverified and outside the managed folder, and `HF_HUB_OFFLINE` does not stop it. Rasva sends only `en` and `hi`, so the adapter marks the module as missing, and the tokenizer skips Chinese segmentation. A load-and-generate run with every network call blocked made no connection attempts. |
| Language per sentence | A sentence with any Devanagari is sent as `hi`, otherwise `en`, so mixed scripts read with the right front end. Roman Hinglish is read as English. A sentence the user marks English or Hindi in Audio Studio is sent with that language instead (optional `language` input, `en` or `hi`, applied to every sentence of the job). The engine declares the `language_per_sentence` feature. |
| Pronunciation entries per sentence | Because of the rule above, dictionary entries are chosen by each sentence's language (or the user's choice), not by the voice's label. A voice cloned from a Hindi recording gets English entries for English sentences, and the built-in "Hindi" voice behaves the same. |
| Voice conditioning cached | `prepare_conditionals` runs once per (recording, expressiveness), not per sentence or job. Switching back to a built-in voice restores the built-in conditioning. |
| No speed control | Chatterbox has none; the speed slider is hidden. Pacing comes from the presets (`cfg_weight`). |
| Presets Steady / Natural / Expressive | Map to `exaggeration`, `cfg_weight`, `temperature`. Values are the upstream defaults and suggestions, not tuned for Hindi yet. |

## What is not known yet

- Devanagari: measurements and quality (deferred by the owner, 2026-11-20).
- Listening review: English quality, the long-clip problem above,
  how Mahabharata names are pronounced, and cloned-voice similarity.
- Whether the presets need tuning for this voice and for Hindi.

## Watermark and licences

Every output passes through Resemble AI's **PerTh** watermarker, an inaudible
mark that the audio is AI-generated. Rasva also labels it synthetic in the file
details. Chatterbox code is MIT; the model card states MIT for the weights; the
pin step should recheck that. The package depends on **gradio** (Apache-2.0)
even though Rasva never starts its web UI, which accounts for part of the
environment size.
