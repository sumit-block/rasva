# Rasva Studio

Free, local-first studio for AI narration (Hindi, English, Hinglish) and, later,
reference-based image editing. All generation runs on your Mac; nothing is sent
to cloud services. Requirements are in the PRD in Confluence. Key design
decisions are recorded in [`docs/adr`](docs/adr).

**Current phase:** A1, Lightweight Narration Baseline. The A0 platform is done.
The Kokoro engine and Audio Studio are built, and Kokoro is pinned, installed
and measured on the M2; see [docs/engines/kokoro.md](docs/engines/kokoro.md).
Image work has started early (owner decision, [ADR-0006](docs/adr/0006-image-baseline-early.md)):
I0, Stable Diffusion 1.5, generates 512 × 512 images on the M2; see
[docs/engines/sd15.md](docs/engines/sd15.md). A built-in diagnostic engine
exercises the full pipeline: queue → worker → progress → saved file → playback.

## Run it

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/) and Node 22.12+ (or 20.19+; see `.nvmrc`).

```bash
scripts/start.sh          # builds the UI, serves everything at http://127.0.0.1:8765
scripts/dev.sh            # backend :8765 + Vite hot reload at http://127.0.0.1:5173
```

Data goes to `~/Library/Application Support/Rasva Studio`. Set `RASVA_HOME` to
put it somewhere else; `dev.sh` uses `./.rasva-dev`.

**Without the terminal (macOS).** Make the apps once:

```bash
scripts/make_mac_app.sh                 # or: --dest <folder>; default ~/Applications
```

- Open **Rasva Studio** from Finder, Launchpad or Spotlight. It builds the UI
  only when needed, starts the server unless it is already running, and opens
  http://127.0.0.1:8765.
- While Rasva runs, the app stays in the Dock. Quit it to stop Rasva, or open
  **Stop Rasva**.
- If something fails, a dialog says what went wrong and where the log is
  (`logs/launcher.log` in the data folder).
- If this folder is inside Downloads, Desktop or Documents, macOS asks each
  app once for permission to read it (again after remaking the apps).
- Uninstall: quit Rasva Studio and move both apps to the Trash. Your data is
  not touched.

Details and alternatives are in [docs/mac-launcher.md](docs/mac-launcher.md).

## Test

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run build      # type-check + production build
```

## Layout

```
backend/rasva/
  app.py               FastAPI factory, lifespan, SPA serving
  config.py            managed directory layout, worker cache redirection
  db.py, migrations/   SQLite (WAL) + forward-only migrations with pre-migration backup
  security.py          loopback-only guard: Host/Origin checks, security headers
  api/                 /api/v1: system, projects, assets, jobs (+SSE), models, settings
  jobs/                state machine, durable repository, orchestrator, event bus
  engines/             adapter contract, registry, worker subprocess, built-in engines
  engines/kokoro/      Kokoro adapter, pinned model manifest, macOS arm64 lockfile
  engines/chatterbox/  Chatterbox adapter, pinned model manifest and lockfile
  engines/indicf5/     IndicF5 draft adapter (manifest, layout and lockfile made on the Mac)
  engines/sd15/        Stable Diffusion 1.5 image adapter (I0), pinned manifest and lockfile
  audio/               script detection, sentence segmentation, pronunciation rules (Hindi-aware)
  experiments/         experiment service, PRD §20 scoring, benchmark sets (audio-v1, image-v1)
  services/            storage quota/preflight, assets, projects, system check, settings
frontend/src/          React + TS + Vite + Tailwind v4, shadcn-style components, Lucide
docs/adr/              architecture decision records
docs/engines/          per-engine decisions, limitations and licences
scripts/pin_kokoro.py  pins Kokoro's model files (commit + SHA-256) into its manifest
scripts/pin_engine.py  same for Chatterbox and IndicF5 (several repositories per engine)
scripts/lock_engine.sh builds a hash-pinned lockfile for an engine installed from GitHub (IndicF5)
scripts/make_mac_app.sh makes the Rasva Studio / Stop Rasva apps; they run scripts/mac_launcher.sh
```

## Architecture in one paragraph

The browser talks to a local FastAPI server, which accepts loopback connections
only for now. Projects, assets and jobs are stored in SQLite, and files sit
under one managed root that is measured against the 50 GB budget. A single
orchestrator runs **one heavy job at a time**. Each engine runs in its own
worker subprocess
([ADR-0001](docs/adr/0001-inference-worker-process.md)), so a crash or
out-of-memory kill fails only that job, and unloading a model is guaranteed by
the process exiting. Workers have their Hugging Face and PyTorch caches
redirected into the managed root ([ADR-0002](docs/adr/0002-managed-storage.md)),
and cannot open network connections during load or generation
([ADR-0007](docs/adr/0007-worker-network-guard.md)). Job progress streams
to the UI over Server-Sent Events.

### Memory guard (PRD §24)

Before an engine loads cold, Rasva compares the memory macOS can hand out now
(psutil's `available`: free plus inactive memory) with the engine's measured
peak (`approx_peak_memory_bytes`) plus a safety margin. If there is not enough,
it first unloads another engine that is sitting idle and checks again. If there
is still not enough, the job stays **queued** with the detail "Waiting for
memory: … Close other apps or wait for them to finish; this starts on its own",
and Rasva re-checks every 5 seconds. You can cancel a waiting job as usual.
Waiting was chosen over failing the job: memory usually frees on its own when
another app finishes, and a queued job needs no new state, survives a restart
and starts without a Retry. A job that could never fit (its engine's peak plus
the margin is more than the Mac's total memory) fails at once with a plain
message and Retry.

Jobs that load nothing new skip the check: a job for the engine already
loaded, model installs and removals, and light built-in steps such as the
mixer. These may run past a job that is waiting for memory; jobs that load an
engine keep their queue order.

| Setting | Default | Notes |
| --- | --- | --- |
| `memory.safety_margin_bytes` | 1 GB | 0–8 GB. Room left for macOS, the browser and Rasva itself after the engine's peak. An assumption, not a measurement. Changeable only on the Mac (Settings → Memory) |

The Jobs page shows free memory and, when a job is held back, how much it
needs (`GET /api/v1/system/status` → `memory`, `orchestrator.memory_wait`).

### Adding an engine

Subclass `rasva.engines.contract.Engine`:

- Give it a class-level `EngineDescriptor`: plain-language summary, licence and
  licence class, tasks, real features only.
- Implement `validate_input` and `run`, and optionally `load`, `unload`,
  `install`, `verify` and `remove`.

Register the class in `rasva/engines/registry.py`. Heavy imports belong inside
`load`. No frontend changes are needed for the engine to appear in Models and
run through Jobs.

To make an engine installable, list its weights in `artifacts`. Each artifact
needs a URL pinned to an immutable revision (for Hugging Face, a commit hash,
never `main`), its SHA-256 and its exact size. If the engine needs its own
Python packages, set `requirements_lock` to a lockfile shipped inside the
engine package. Generate it with
`uv pip compile --generate-hashes --python-platform aarch64-apple-darwin`.

## Sprint 0 status

| Item | Status |
| --- | --- |
| P0-001 React/TS shell | Done: nav rail, mobile bottom nav, live job status, dark/light, reduced motion |
| P0-002 FastAPI backend · P0-003 health | Done |
| P0-004 hardware detection | Done (`/system/check`); MLX/MPS are *detected*, and verified per engine at install |
| P0-005 directory design | Done ([ADR-0002](docs/adr/0002-managed-storage.md)) |
| P0-006 SQLite migrations | Done, with automatic backup before each migration |
| P0-007 projects API · P0-008 assets API | Done: validated, hashed, quota-checked uploads; traversal-safe downloads |
| P0-009 durable job state machine | Done: restart recovery, cooperative and forced cancel, retry lineage |
| P0-010 quota accounting | Done: 80% warn, 90% download block, hard cap, separate macOS reserve |
| P0-011 guided onboarding | Done: 7-step first-run setup (Mac check, storage limit, starter engine with size/licence, first preview, phone-access note), resumable and skippable |
| P0-012 engine capability registry | Done |
| P0-013 model installer | Done: queued install/remove jobs, https-only, SHA-256 + size verified, resumable (Range), licence gating, storage preflight, isolated engine environments from hash-pinned lockfiles; "Software update available" rebuilds only the environment when a lockfile changes, keeping model files and rolling back on failure (ADR-0003) |
| P0-014 logging | Done: JSON logs, rotating, no request bodies; engine stderr logged per engine |
| P0-015 responsive components | Done for current pages |

## Not yet decided

- **Repository licence:** there is no `LICENSE` file yet.
- **Default macOS free-space reserve:** 15 GB is an assumption and can be
  changed in settings.

## A1 status

| Item | Status |
| --- | --- |
| A1-001 small TTS adapter | Done: Kokoro (PyTorch CPU), isolated Python 3.12 env, local-files-only, model files pinned |
| A1-002 script editor | Done: autosave per project, Devanagari detection, length limit |
| A1-003 voice picker | Done: voices by language (English / Hindi) |
| A1-004 synthesis endpoint | Done: `audio.narrate` jobs; preview = first sentence |
| A1-005 output saving · A1-006 player · A1-007 WAV download | Done |
| A1-008 history | Done for each project's results; versioning/compare comes with A4 |
| A1-009 runtime and memory measurement | Done: measured on the M2 (2026-11-20): cold load 17.3 s, median real-time factor 0.200 (about 5× faster than real time), peak worker memory 2.16 GB, installed 1.03 GB ([details](docs/engines/kokoro.md#measurements-on-the-m2-a1-009-a1-011)) |
| A1-010 experiment records | Done: fixed inputs x candidates, metrics, 1-5 reviews, PRD §20 weights and gates, decisions |
| A1-011 benchmark | Done: 60-sentence `audio-v1` set (PRD §29.2), run on the M2 with Kokoro (`af_heart` and `hf_alpha`): 85 completed, 0 failed, 35 expected skips. The set still has 15 Roman Hinglish cases, which are out of scope under ADR-0005 |
| A1-012 acceptance review | Needs the M2 |

## A2 status (started early; engine-independent parts)

| Item | Status |
| --- | --- |
| A2-005 pronunciation dictionary | Done: written forms → "say it as", global or per project (project wins), whole-word Devanagari-aware matching. Entries are chosen by the voice's language, or, on engines that speak both (Chatterbox, including cloned voices), by each sentence's language: English entries for Latin-script sentences, Hindi entries for sentences with Devanagari |
| A2-006 pronunciation preview | Done: hear an entry (approved or not) with any installed voice, alone or in an example sentence |
| A2-009 approved-correction persistence | Done: only approved entries apply; original text, engine text and every substitution (with the language it was chosen for) are stored on the job; retries reuse the same choices |
| A2-002 language detection | Done: per sentence (Hindi, Roman Hinglish, English, mixed, unclear) with confidence. **Manual override:** in Audio Studio each chapter sentence can be set to Automatic, English or Hindi. The choice is saved with the chapter and kept when other sentences are edited. Engines that speak both read the sentence in that language and the dictionary uses it. Voices that read everything in one language keep the choice for later and say so |
| A2-003 mixed-language segmentation | Done: Devanagari/Latin spans with exact offsets |
| A2-004 transliteration suggestions | Done (basic, rule-based). **Roman Hinglish input is out of scope for now** ([ADR-0005](docs/adr/0005-input-scripts.md)): write English or Devanagari |
| A2-001 Indic engine | Draft: IndicF5 adapter (no remote code, local files, Devanagari only, own voice). **Not run; manifest, file layout and lockfile must be made on the Mac** ([notes](docs/engines/indicf5.md)) |
| A2-007/008 comparison and benchmark | Need IndicF5 (or another Indic engine) installed and run on the M2 |

## A4 status: long-form narration and mixing

| Item | Status |
| --- | --- |
| Automatic segmentation | Done: sentences grouped into ~200-character segments within paragraphs (PRD §12.1) |
| Durable per-segment jobs, failure resumption | Done: each segment is its own queued job; a failure or crash affects only that segment; "Retry failed" and restart recovery |
| Selective regeneration and versions | Done: every generation is a new take; the first becomes current automatically, later ones wait for review (keep current / use new); script edits keep takes of unchanged sentences; voice changes mark segments outdated without deleting audio |
| Music and effects | Done: your own files; music looped with fades and ducked under speech (look-ahead), effects placed at a sentence; Simple presets (Quiet/Balanced/Present) set levels only |
| Assembly and export | Done: block-based renderer (constant memory for long chapters), per-segment levelling, click-free joins, -1 dBFS peak ceiling (no clipping), WAV, MP3 via FFmpeg, optional stems that sum to the mix |
| Long-script and failure tests | Done: 60-minute script segmentation, injected failures and crashes, interruption and restart, cancel, 15-minute block-render memory check |

Known limits: loudness is levelled by speech RMS and limited by peak, not
measured in LUFS. The built-in resampler is linear, which is fine for speech
and upsampling; compressed music is decoded and resampled by FFmpeg. Changing a
pronunciation entry does not mark existing takes as outdated.

## A5 status: audio polish and release

| Item | Status |
| --- | --- |
| Paired home Wi-Fi access | Done: local CA, encrypted HTTPS port, QR + 6-digit pairing, device list and removal, owner-only actions ([ADR-0004](docs/adr/0004-lan-access-security.md)) |
| Quality presets · automatic engine choice | Done: per-language recommendation from your own ratings (else default voice) with the reason shown; presets only for engines that have them; export presets |
| Storage cleanup | Done: previews, removed-sentence audio, temp files, replaced versions, older exports, paused downloads, unrecognised files; originals and in-use audio never offered; missing files reported |
| Privacy audit | Done (automated): private text never reaches logs; engine logs rotate; Kokoro's text-quoting warnings suppressed |
| Offline test | Done (automated): no outbound connections from the app process during normal use; engine workers run with offline flags **and cannot open network connections or look up hosts during load and generation** (Python-level socket guard, blocked attempts named in the engine log; [ADR-0007](docs/adr/0007-worker-network-guard.md)); UI bundle loads nothing remote. **Still to do on the M2:** a real run with Wi-Fi to the internet switched off, and loading each installed engine under the guard |
| Mac sleep guidance | Shown when Wi-Fi access is on |
| Memory guard (PRD §24) | Done (automated tests with mocked memory readings): cold starts wait until available memory covers the engine's peak plus a 1 GB margin, after unloading an idle other engine; status and Jobs page show memory ([details](#memory-guard-prd-24)). **Still to do on the M2:** real engines under the guard, and how quickly `available` recovers after an engine exits. With the measured peaks, Stable Diffusion needs 10 GB available and Chatterbox 7 GB; on 2026-11-20, with other work open, psutil reported 5.9 GB, so both would have waited, and even a 0 margin would not have let Stable Diffusion (9 GB) start. Whether that is acceptable is an owner decision. While a job waits, other jobs that load an engine (for example a Kokoro narration that would fit) wait behind it until it starts or is cancelled |
| App launches without the terminal | Built: `scripts/make_mac_app.sh` makes Rasva Studio and Stop Rasva apps ([details](docs/mac-launcher.md)). Tested with `open` on a dev port from a copy outside Downloads. **Still to do on the M2:** a Finder double-click, and allowing access to Downloads on first launch |
| Usability testing · release checklist | Needs people and the M2 |

To use Rasva from a phone: on the Mac, open Settings → Phone & other devices →
Allow devices on my Wi-Fi → Pair a device, then scan the QR code with the
phone. macOS may ask once whether to allow incoming connections; choose Allow.

## A3 status: voice profiles

| Item | Status |
| --- | --- |
| Consent | Done: own voice or named speaker's permission plus an explicit statement, stored with the profile; consent history survives deletion (no audio or text in it) |
| Reference recording | Done: lossless WAV recorded in the browser (no FFmpeg needed) or an uploaded file; quality feedback on length, loudness, clipping, background noise and silence |
| Transcript | Done: entered and confirmed by you; plausibility check against the speech length (no speech recognition yet) |
| Reusable profiles, deletion | Done: deleting a voice deletes its recording immediately; refused while jobs use it |
| Engine plumbing | Done: engines declaring `voice_cloning` receive the reference path, transcript and language for narration, chapters and previews |
| Cloning engine | Chatterbox Multilingual runs on the M2 (CPU) with its built-in voice and with your own voice, PerTh watermark. English measured on 2026-11-20: cold load median 18.3 s, median real-time factor 6.7 (about 7× slower than real time), peak worker memory 6.27 GB, installed 4.25 GB. Devanagari deferred ([notes](docs/engines/chatterbox.md)) |
| Listening tests | Ready for review: English `audio-v1`, English sentences with Mahabharata names (Chatterbox and Kokoro) and one cloned narration |

## I0 status: image baseline (started early, ADR-0006)

| Item | Status |
| --- | --- |
| Engine | Stable Diffusion 1.5, diffusers on the Apple GPU (fp16), pinned files, hash-pinned lockfile, no safety checker (owner decision) |
| Image Studio | Done (first version): description, Quick / Balanced / Detailed, and in Advanced mode Avoid, seed, steps and guidance; results show the image, seed and time |
| Measurements (M2, 2026-11-20) | 6 of 6 completed; cold load 14.5 s; median 52.6 s per 512 × 512 image at 25 steps; GPU memory reserved 8.5 GB; installed 2.75 GB ([details](docs/engines/sd15.md)) |
| Exit-gate checks | Saved outputs are reproducible (same seed gives an identical file); the engine unloads, and Kokoro narrated normally afterwards; no network use; prompts stay out of logs |
| Memory guard | Image jobs wait for about 10 GB available memory (9 GB measured peak + 1 GB margin) before a cold start ([details](#memory-guard-prd-24)) |
| Image experiments | Built, not yet run on the M2: `image.generate` in Experiments (Advanced mode) with the 15-prompt `image-v1` set (objects, scenes, styles, words in the picture, people and hands; fixed seed per prompt) or your own descriptions, a thumbnail grid with 1–5 reviews, PRD §20.2 weights and the PRD's six quality subdimensions, memory gate on process + GPU memory ([details](docs/engines/sd15.md#image-experiments)) |
| Still open | Quality review by eye (run `image-v1`); checking the memory guard with the real engine on the M2 |

## I1 status: edit from reference photos (ADR-0007)

| Item | Status |
| --- | --- |
| Engine | FLUX.2 Klein 4B on mflux/MLX: official files quantised to 4-bit on the Mac by the installer's new one-time prepare step. Text-to-image and editing with 1–4 photos |
| Reference photos | Done: upload with a role (person, clothing, pose, background), change roles, choose up to 4; checked on create and retry ([references.py](backend/rasva/services/references.py)) |
| Image Studio | Done: "Create from text" and "Edit a photo", engine choice in Advanced mode |
| Measurements (M2, 2026-11-20) | 768 px: 50–53 s from text, 124 s with 1 photo, 167 s with 2; MLX peak 4.9–7.3 GB; installed 21.9 GB including the 16 GB originals ([details](docs/engines/flux2klein.md)) |
| Still open | Owner review of quality; tests on photos of people; whether to delete the 16 GB originals; versions and before/after (I4 branch) |
