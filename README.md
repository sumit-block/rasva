# Rasva Studio

Free, local-first studio for AI narration (Hindi, English, Hinglish) and, later,
reference-based image editing. All generation runs on your Mac; nothing is sent
to cloud services. Requirements are in the PRD in Confluence. Key design
decisions are recorded in [`docs/adr`](docs/adr).

**Current phase:** A0, Shared Platform Foundation. There are no AI engines yet.
A built-in diagnostic engine exercises the full pipeline: queue → worker →
progress → saved file → playback.

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
  services/            storage quota/preflight, assets, projects, system check, settings
frontend/src/          React + TS + Vite + Tailwind v4, shadcn-style components, Lucide
docs/adr/              architecture decision records
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
| P0-011 guided onboarding | **Partial:** readiness and quick system test on Home; the first-run wizard is not built yet |
| P0-012 engine capability registry | Done |
| P0-013 model installer | **Not started:** resumable, checksum-verified downloads (needed before A1) |
| P0-014 logging | Done: JSON logs, rotating, no request bodies; engine stderr logged per engine |
| P0-015 responsive components | Done for current pages |

## Not yet decided

- **Repository licence:** there is no `LICENSE` file yet.
- **Home Wi-Fi transport:** the recommendation is in
  [ADR-0004](docs/adr/0004-lan-access-security.md); the decision is due at A5.
- **Default macOS free-space reserve:** 15 GB is an assumption and can be
  changed in settings.
