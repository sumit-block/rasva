# Rasva Studio

Free, local-first studio for AI narration (Hindi, English, Hinglish) and, later,
reference-based image editing. All generation runs on your Mac; nothing is sent
to cloud services. Requirements are in the PRD in Confluence. Key design
decisions are recorded in [`docs/adr`](docs/adr).

**Current phase:** A1, Lightweight Narration Baseline. The A0 platform is done.
The Kokoro engine and Audio Studio are built, but Kokoro's model files still
need pinning before it can be installed; see
[docs/engines/kokoro.md](docs/engines/kokoro.md). A built-in diagnostic engine
exercises the full pipeline: queue → worker → progress → saved file → playback.

## Run it

Requires Python 3.11+, [`uv`](https://docs.astral.sh/uv/) and Node 20+.

```bash
scripts/start.sh          # builds the UI, serves everything at http://127.0.0.1:8765
scripts/dev.sh            # backend :8765 + Vite hot reload at http://127.0.0.1:5173
```

Data goes to `~/Library/Application Support/Rasva Studio`. Set `RASVA_HOME` to
put it somewhere else; `dev.sh` uses `./.rasva-dev`.

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
  engines/chatterbox/  Chatterbox draft adapter and lockfile (model files not pinned)
  engines/indicf5/     IndicF5 draft adapter (manifest, layout and lockfile made on the Mac)
  audio/               script detection, sentence segmentation, pronunciation rules (Hindi-aware)
  experiments/         experiment service, PRD §20 scoring, benchmark sets (audio-v1)
  services/            storage quota/preflight, assets, projects, system check, settings
frontend/src/          React + TS + Vite + Tailwind v4, shadcn-style components, Lucide
docs/adr/              architecture decision records
docs/engines/          per-engine decisions, limitations and licences
scripts/pin_kokoro.py  pins Kokoro's model files (commit + SHA-256) into its manifest
scripts/pin_engine.py  same for Chatterbox and IndicF5 (several repositories per engine)
scripts/lock_engine.sh builds a hash-pinned lockfile for an engine installed from GitHub (IndicF5)
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
redirected into the managed root, and are blocked from downloading during
inference ([ADR-0002](docs/adr/0002-managed-storage.md)). Job progress streams
to the UI over Server-Sent Events.

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
| P0-013 model installer | Done: queued install/remove jobs, https-only, SHA-256 + size verified, resumable (Range), licence gating, storage preflight, isolated engine environments from hash-pinned lockfiles |
| P0-014 logging | Done: JSON logs, rotating, no request bodies; engine stderr logged per engine |
| P0-015 responsive components | Done for current pages |

## Not yet decided

- **Repository licence:** there is no `LICENSE` file yet.
- **Default macOS free-space reserve:** 15 GB is an assumption and can be
  changed in settings.

## A1 status

| Item | Status |
| --- | --- |
| A1-001 small TTS adapter | Done: Kokoro (PyTorch CPU), isolated Python 3.12 env, local-files-only. **Model manifest not pinned yet** |
| A1-002 script editor | Done: autosave per project, Devanagari detection, length limit |
| A1-003 voice picker | Done: voices by language (English / Hindi) |
| A1-004 synthesis endpoint | Done: `audio.narrate` jobs; preview = first sentence |
| A1-005 output saving · A1-006 player · A1-007 WAV download | Done |
| A1-008 history | Done for each project's results; versioning/compare comes with A4 |
| A1-009 runtime and memory measurement | Metrics recorded per job (RTF, load time, peak RSS); **not yet measured on the M2** |
| A1-010 experiment records | Done: fixed inputs x candidates, metrics, 1-5 reviews, PRD §20 weights and gates, decisions |
| A1-011 benchmark | Done: 60-sentence `audio-v1` set (PRD §29.2); **needs running on the M2 with Kokoro** |
| A1-012 acceptance review | Needs the M2 |

## A2 status (started early; engine-independent parts)

| Item | Status |
| --- | --- |
| A2-005 pronunciation dictionary | Done: written forms → "say it as", per voice language, global or per project (project wins), whole-word Devanagari-aware matching |
| A2-006 pronunciation preview | Done: hear an entry (approved or not) with any installed voice, alone or in an example sentence |
| A2-009 approved-correction persistence | Done: only approved entries apply; original text, engine text and every substitution are stored on the job; retries reuse the same choices |
| A2-002 language detection | Done: per sentence (Hindi, Roman Hinglish, English, mixed, unclear) with confidence |
| A2-003 mixed-language segmentation | Done: Devanagari/Latin spans with exact offsets |
| A2-004 transliteration suggestions | Done: confident for names and common words, rule-based guesses marked; nothing applied without review |
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
| Offline test | Done (automated, app process): no outbound connections during normal use; engine workers run with offline flags; UI bundle loads nothing remote. **Still to do on the M2:** a real run with Wi-Fi to the internet switched off |
| Mac sleep guidance | Shown when Wi-Fi access is on |
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
| Cloning engine | Draft: Chatterbox Multilingual adapter, hash-pinned lockfile, built-in and own voices, PerTh watermark. **Not run; model files not pinned** ([notes](docs/engines/chatterbox.md)) |
| Listening tests | Need Chatterbox installed on the M2 |
