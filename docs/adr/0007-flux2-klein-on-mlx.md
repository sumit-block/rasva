# ADR-0007: FLUX.2 Klein 4B on MLX for reference-guided editing (I1)

- Status: Accepted (owner decisions)
- Date: 2026-11-20
- PRD refs: §16, §18 (FLUX.2 Klein 4B), §19 (I1), §24, §28, §34.2

## Context

I1 needs an editor that takes one or more reference photos and a plain instruction.
The PRD names FLUX.2 Klein 4B, "prefer verified quantized Apple Silicon
implementation; measure actual M2 memory". The model is Apache-2.0 and not
gated. At full precision it is a 7.75 GB transformer plus an 8.0 GB Qwen3
text encoder: about 16 GB of weights, which is the whole memory of the 16 GB
target Mac. diffusers (the Stable Diffusion runtime) has no supported way to
quantise it on the Apple GPU.

## Decisions

1. **Runtime: mflux 0.24.0 on Apple's MLX** (MIT licence), in the engine's own
   Python 3.12 environment from a hash-pinned lockfile. It supports FLUX.2 Klein
   generation and multi-reference editing, quantised. Its lock targets macOS 14 or
   newer, because MLX publishes no older wheels.
2. **Weights: the official bf16 files** from `black-forest-labs/FLUX.2-klein-4B`,
   pinned at commit `e7b7dc2` (19 files, 15.98 GB). The 7.75 GB single-file
   duplicate and the sample images are left out. The owner chose this over
   community pre-quantised copies, so the source is the original publisher.
3. **Quantise to 4-bit on the Mac at install.** The installer's new one-time
   `prepare` step runs `Flux2Klein(model_path, quantize=4).save_model(...)` into
   `<model dir>/q4`, in the engine's environment with network access blocked. It
   writes atomically and is idempotent. mflux puts 4-bit weights at about
   4.6 GB in memory, against 8.6 GB for 8-bit.
4. **The 16 GB originals are kept for now.** They allow an 8-bit comparison
   (PRD I1: "quantization quality"). Deleting them after a verified prepare
   would save about 16 GB of the 50 GB budget, but that is the owner's call.
5. **No content filter and no consent check** for photos of people. This is the
   owner's explicit choice. Black Forest Labs built in safety training and
   encourages input and output filters for the 4B model. The PRD image checklist
   says "authorized person reference uploads"; Rasva does not record that consent.
6. **Contract for I1–I3.** Reference photos are image-project uploads with a role
   (`person`, `garment`, `pose`, `background`). `image.edit` jobs carry
   `references: [{asset_id, role}]`, which the jobs API turns into file paths on
   create and again on retry. There are at most 4 references. The engine orders
   them person, garment, pose, background, makes them upright and caps them at
   1024 px.

## Consequences

- Install downloads 16 GB and then spends minutes quantising. Its memory peak
  must be measured on the M2 before release (see `docs/engines/flux2klein.md`).
- The engine saves pixels itself, because mflux's image save would embed the
  prompt and the reference paths in the file.
- Without a consent record, using other people's photos depends on the user's
  own judgement. Revisit this before any shared or packaged release.
