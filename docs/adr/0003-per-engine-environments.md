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

## Amendment (2026-11-20): software updates for installed engines

Context: Chatterbox's lockfile gained `setuptools<81` to fix a load failure,
but an installed engine never learned about it. The only way to apply it was
Remove + Install, which re-downloaded 3.2 GB of unchanged model files, and
nothing noticed at run time that the environment was outdated.

Decision:

- An engine whose environment marker (`.rasva-lock-sha256`) does not match the
  SHA-256 of its current lockfile is reported by `GET /api/v1/models` as
  `software_update_available: true`. Models shows "Software update available"
  with an Update button.
- `POST /api/v1/models/{id}/update` queues a `model.update` job on the same
  single-heavy-job queue as install and remove (same progress, cancel and
  retry). It is refused while the engine has any other open job, and is
  preflighted for space: the new environment is built beside the old one.
- The job rebuilds only the environment, with the same `uv` command line as
  install (`--require-hashes --only-binary :all: --no-config`), into
  `envs/.<id>.partial`. It then moves the old environment to
  `envs/.<id>.previous`, moves the new one into place, re-runs the engine's
  `verify`, and only then deletes the old one and records the new
  `installed_bytes`. A failed build, a failed check or a cancel puts the old
  environment back, so the engine keeps working. The install record stays
  `installed` throughout. A resident worker for that engine is stopped first.
  After a crash mid-swap, start-up restores `.previous` if the environment is
  missing and deletes it otherwise.
- Model files are never downloaded, moved or deleted by an update.
- **Jobs on an outdated engine still run.** We chose this over blocking them.
  An outdated environment is usually still working (the lockfile may change
  for an unrelated engine-wide fix). The update also needs the internet, and
  Rasva is local-first: blocking would make an engine unusable offline until
  the user could update. Running is not silent: the orchestrator logs a
  warning when it starts a worker on an outdated environment. If that engine
  then fails to start (a load error, or a worker that exits before it is
  ready), the job's error adds "A software update is available
  for this engine. Open Models and choose Update, then retry."

- An update does not re-run the engine's one-time `prepare` step (for example
  FLUX.2 Klein's 4-bit quantisation into `<model_dir>/q4`): prepared files are
  model data and do not depend on the environment. If a future library change
  alters the prepared format, that engine will need a re-prepare, which this
  update action does not provide.

Limit: an engine's `verify` may check only its model files (Chatterbox's
does), so it does not prove the new environment imports. A lockfile that
installs cleanly but cannot load is caught at the next job's load, not by the
update itself.

## Consequences

- An upgrade to one engine cannot break another.
- Disk cost grows with each separate environment. To mitigate it, `uv`'s
  hard-linked cache inside the managed root avoids storing identical wheels
  twice.
- Changing a lockfile is now a supported update path; it costs one environment
  rebuild (packages, not model files) per affected install.
- Follow-up: split `rasva.engines.contract` into a minimal installable SDK so
  engine environments do not need the whole backend on `PYTHONPATH`.
