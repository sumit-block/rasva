# ADR-0003: Per-engine Python environments, created only when needed

- Status: Accepted (mechanism in place; first use expected in A2/A3)
- Date: 2026-11-20
- PRD refs: §14, §21.2, §23 (7 GB environments band), §28 (pinned dependencies)

## Context

Candidate engines such as Kokoro, IndicF5 and Chatterbox are likely to pin
incompatible versions of `torch`, `transformers` or audio libraries. Putting
them all in one environment risks breaking one engine whenever another is
upgraded. Each PyTorch install on macOS is over 1 GB, against a 7 GB budget for
all environments.

## Decision

- An engine runs on the app interpreter unless `envs/<engine-id>/bin/python`
  exists, in which case its worker uses that interpreter. The registry decides
  this, so there is no frontend or orchestrator change.
- Engines share one environment when their pinned dependencies are compatible.
  A separate environment is created only when a conflict is demonstrated during
  that engine's feasibility gate.
- Every engine environment is built from a lockfile with hashes (`uv`), and
  includes the small `rasva` contract package (it currently depends only on
  pydantic).
- Environment size is counted under the `environments` storage category and is
  part of the engine's install preflight.

## Consequences

- An upgrade to one engine cannot break another.
- Disk cost grows with each separate environment. To mitigate it, `uv`'s
  hard-linked cache inside the managed root avoids storing identical wheels
  twice.
- Follow-up: split `rasva.engines.contract` into a minimal installable SDK so
  engine environments do not need the whole backend on `PYTHONPATH`.
