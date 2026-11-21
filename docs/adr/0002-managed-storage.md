# ADR-0002: One managed root, measured from disk, with redirected caches

- Status: Accepted
- Date: 2026-11-20
- PRD refs: §23, NFR-02

## Context

The 50 GB budget must include everything Rasva causes to be written: weights,
Python environments, Hugging Face and PyTorch caches, temp files, projects,
database and logs. By default those libraries write to `~/.cache/...`, outside
any directory the app controls, so budget accounting built from app metadata
would undercount silently.

## Decision

- All data lives under one root: `RASVA_HOME` (default
  `~/Library/Application Support/Rasva Studio`, mode 0700). Subdirectories map
  one-to-one onto the PRD §23 categories.
- Engine workers run with `HF_HOME`, `TORCH_HOME`, `PIP_CACHE_DIR`,
  `XDG_CACHE_HOME` and `TMPDIR` pointed inside the root, and with
  `HF_HUB_OFFLINE=1` so **inference can never trigger a download**. Downloads
  happen only through the explicit installer, after preflight. Because those
  flags only reach libraries that read them, workers also block all network
  sockets and lookups ([ADR-0009](0009-worker-network-guard.md)).
- Usage is measured from allocated disk blocks, not from database sums, and
  cached for about 10 s.
- Preflight takes the **peak** bytes needed (including temporary copies during
  conversion). It:
  - blocks if the write would exceed the budget,
  - blocks new downloads when the budget is 90% or more used,
  - blocks if free disk space would fall below a separate macOS reserve
    (default 15 GB, an assumption because the PRD gives no number; it can be
    changed in settings),
  - warns when the budget is 80% or more used.
- Reference uploads are flagged `protected` and are never removed by automatic
  cleanup.

## Consequences

- Accounting stays correct even when third-party libraries create unexpected
  cache files.
- Walking large model directories costs I/O, which is why results are cached
  briefly and invalidated after writes.
- Model installer (P0-013, implemented in `services/installer.py`):
  - installs and removals run as queued jobs, so they never overlap inference;
  - only https sources are accepted, and every redirect hop is checked;
  - each file's exact size and SHA-256 are verified before an atomic move into
    `models/`;
  - downloads resume with HTTP Range from `tmp/downloads/<sha256>.part`. Keying
    partial files by hash means a changed artifact never resumes onto old bytes;
  - partial files that no registered engine needs are deleted at startup, and
    installs left half-finished are marked failed.
