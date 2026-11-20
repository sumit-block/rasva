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
  happen only through the explicit installer, after preflight.
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
- Still to do in P0-013, the model installer:
  - resumable downloads using a `.part` file with HTTP Range requests,
  - pinned revisions and SHA-256 verification,
  - an atomic rename into `models/`,
  - cleanup of orphaned partial files on startup.
