# ADR-0001: Run every engine in a supervised worker subprocess

- Status: Accepted
- Date: 2026-11-20
- PRD refs: §21.1, §21.2, §24, NFR-04, NFR-09

## Context

Rasva runs heavy local inference (PyTorch MPS, MLX) on a 16 GB unified-memory
M2 Air. Three properties of those runtimes drive this decision:

1. They do not reliably return memory to the OS after `del model`; MPS and MLX
   caches can retain gigabytes until the process exits.
2. Memory exhaustion is often fatal (macOS jetsam sends SIGKILL), and native
   extensions can segfault. Either would take down an in-process API server and
   every connected browser.
3. Long synchronous inference calls in the API process compete with request
   handling and the event loop (NFR-09: UI responsive during heavy jobs).

## Decision

- The FastAPI process never imports ML frameworks. It owns the database, files,
  queue and HTTP.
- Each engine runs in its own child process (`python -m rasva.engines.worker`),
  speaking newline-delimited JSON over stdin/stdout. Worker fd 1 is redirected to
  stderr so stray library `print`s cannot corrupt the protocol.
- One orchestrator task runs **one heavy job at a time**. A worker stays resident
  between jobs for the same engine (warm start), and is shut down before a
  different engine loads or after an idle timeout (default 5 min). Unload means
  process exit, which is the only guaranteed way to free memory.
- Cancellation is cooperative (engine checks `ctx.raise_if_canceled()` at safe
  points), then forced: after a grace period (default 10 s) the worker is killed.
- A worker exit during a job fails **that job only** (`engine_crashed`, or
  `out_of_memory` on SIGKILL); completed outputs and other jobs are untouched.
- Workers write only to a per-job staging directory. The parent validates each
  declared output name, then moves it into the project and records it.

## Consequences

- Pros: crash isolation, guaranteed unload, a responsive API, and a natural seam
  for per-engine Python environments (ADR-0003).
- Cons: cold start pays process start plus model load; mitigated by keeping the
  worker resident between jobs. Peak RSS in the worker does not fully capture
  Metal/MLX GPU allocations. Adapters should add framework-specific peak-memory
  metrics (for example MLX's peak-memory counter) to `RunResult.metrics`.
- `validate_input` must stay pure and lightweight, because the API process also
  calls it to reject bad input before a job is queued.

## Alternatives considered

- **In-process inference in a thread:** rejected; no crash isolation and no
  reliable unload.
- **Separate always-on inference server (e.g., a second HTTP service):** more
  moving parts than a stdin/stdout child, with no benefit for a single-user app.
