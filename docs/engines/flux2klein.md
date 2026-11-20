# FLUX.2 Klein 4B (phase I1: generation and reference-guided editing)

Status: adapter, prepare step, lockfile, tests with a fake runtime and the
Image Studio "Edit a photo" flow are done. Real-model measurements on the M2
are being taken; see below. Decisions are in
[ADR-0007](../adr/0007-flux2-klein-on-mlx.md).

## What it does

- **Create from text:** a description in, one PNG out, 512 to 1024 px.
- **Edit a photo:** one to four of your photos plus an instruction ("keep the same
  person; put them in the blue kurta from the clothing photo; garden background").
  Photos have roles and reach the model in the order person, clothing, pose,
  background. Each is made upright and limited to 1024 px on its long side.
- 4 steps by default (it is a distilled model). Draft / Standard / Large set
  512 / 768 / 1024 px.

## How it is installed

1. Download the official bf16 files (15.98 GB, 19 files, pinned commit
   `e7b7dc2`, each file checked by SHA-256).
2. Build its Python 3.12 environment: mflux 0.24.0, MLX 0.32.4 and their
   dependencies, all hash-pinned.
3. **Prepare (one time):** quantise to 4-bit into `q4/` in the engine's
   environment, with network access blocked. A failed prepare keeps the
   downloads, so installing again skips the download.
4. Verify that the downloads and the prepared copy are present.

## Measurements on the M2

Pending.

## Decisions

| Decision | Why |
| --- | --- |
| mflux on MLX, 4-bit | The full model needs about 16 GB, which is the whole Mac. 4-bit is about 4.6 GB in memory according to mflux |
| Official files, quantised here | Owner decision: the source is the original publisher, not a community copy |
| Originals kept | They allow an 8-bit comparison. Deleting them would save about 16 GB; the owner decides |
| Pixels saved by Rasva | mflux's own save embeds the prompt and the reference paths in the PNG |
| Progress and cancel through an mflux in-loop callback | Cancel stops between steps. mflux only catches Ctrl-C in its loop, so Rasva's cancel goes straight up |
| No content filter, no consent check | Owner decision (ADR-0007) |

## Licence

Model: **Apache-2.0** (Black Forest Labs; open weights, commercial use allowed).
mflux: MIT. MLX: MIT.
