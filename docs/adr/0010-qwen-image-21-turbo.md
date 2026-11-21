# ADR-0010: Qwen-Image 2.1 Turbo as a second image model, for the owner's private use

- Status: Accepted (owner decisions, 2026-11-21)
- PRD refs: §16, §18, §19 (I1–I3), §20.2, §24, §28

## Context

FLUX.2 Klein 4B (ADR-0007) works on the 16 GB M2. Public blind-vote leaderboards
(Artificial Analysis and Arena, read 2026-11-21) rank Qwen-Image 2.1 well above
it: 1047 against 840 for text-to-image, 1082 against 941 for editing, and 1227
against 1030 on Arena. That is a 140–200 point gap, so people preferred Qwen's
image about 70–76% of the time. Those scores are for the full-precision 40-step
model.

Qwen-Image 2.1 is a 7.1B image model plus an 8B Qwen3-VL text encoder (33 GB
bf16). It creates and edits in one model and takes up to 10 reference photos.
The original Apache-2.0 Qwen-Image line (20B) does not fit 16 GB: mflux keeps its
14–15.5 GB text encoder unquantised.

**Licence.** Qwen-Image 2.1 uses the Qwen Research License (Sep 20, 2026):
"Non-Commercial shall mean for research or evaluation purposes only"; commercial
use needs a separate licence from Qwen.

## Decisions

1. **Owner decision: install it for private, non-commercial use only.** The owner
   said: "I won't use anything commercial, only for private use". The licence
   names only "research or evaluation", so the owner accepts that reading. The
   engine is classed `restricted`, and the installer asks before downloading.
2. **Turbo, 4-bit, on mflux 0.24.0** (the same runtime and hash-locked
   environment as FLUX.2 Klein). Turbo is the distilled 8-step checkpoint
   (`Qwen/Qwen-Image-2.1-Turbo`, pinned at `fc13ba1`, 32.5 GB, SHA-256 per file).
   It is quantised to 4-bit once at install, through `QwenImage21Edit.save_model`
   with the Turbo config: the text-only export drops the vision tower that editing
   needs.
3. **One class for both tasks.** `QwenImage21Edit` makes pictures without
   references and edits with 1–4 (Rasva's limit), so switching task does not reload.
4. **Turbo's constraints become the UI.** Guidance is fixed at 1 (mflux refuses
   other values), so there is no "avoid" field. Steps run 1–8, default 8. Sizes
   are multiples of 32 from 768 to 1536. Presets are Landscape, Square and
   Portrait, around 1 MP, because below 1024 px Turbo looks blocky.
5. **FLUX.2 Klein keeps pose and outfit changes.** Those workflows were built and
   tested on it (features `pose` and `outfit`). The outfit API prefers an
   engine with `outfit`, so installing Qwen does not silently reroute them.
6. **Storage budget raised to 100 GB** (owner decision): Qwen alone is 43.7 GB
   installed. The 16 GB FLUX originals and the 33 GB Qwen originals are kept, so
   re-preparing needs no download.
7. **"Improve my instruction" (edits only, optional).** Qwen's own text model
   rewrites a short edit instruction from the photos before editing, on the Mac.
   Rasva calls `rewrite_prompt`, not `enhance_prompt=True`, which logs both texts.
   The rewrite is kept in the job metrics (never logged) and shown on the result.

## Consequences

- Install downloads 32.5 GB and keeps 43.7 GB on disk.
- Per-image time and memory on the M2 are recorded in `docs/engines/qwen21turbo.md`.
- The licence still limits use to research or evaluation. Revisit it before any
  shared, published or commercial use of Rasva or its images.
- No content filter (as ADR-0006 and ADR-0007).
