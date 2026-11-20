# FLUX.2 Klein 4B (phase I1: generation and reference-guided editing)

Status: installed and working on the target M2, for both text-to-image and
photo editing with one or two references. Quality has not been reviewed by
the owner yet. Decisions are in
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

## Measurements on the M2 (2026-11-20)

The Mac is an M2 with 16 GB of memory, running mflux 0.24.0 and MLX 0.32.4
with 4-bit weights quantised on this Mac from the official files (`e7b7dc2`),
4 steps and a 1 GB MLX cache limit. Unless noted, the runs went through the app
(install job and `/jobs` API). The Mac was already swapping several GB because
other apps and sessions were running, so the times are a conservative upper
bound.

| Measure | Result | How it was measured |
| --- | --- | --- |
| Install | 15.98 GB download (here re-verified in 8 s, after a checksummed download in the scratchpad); environment 2 s; prepare (4-bit) 26 s | Install job metrics. The scratch prepare run took 33 s with a peak footprint of 9.9 GB |
| Installed size | 21.9 GB (21,867,126,784 bytes) | Installer: the 16.0 GB originals, the 4.3 GB 4-bit copy and the environment. Deleting the originals would save about 16 GB (owner decision) |
| Model load (cold) | 2.5 s | First job after the worker starts |
| Text-to-image | 768 px: 50–53 s (app); 512 px: 65 s and 768 px: 89–108 s (scratch, heavier swapping) | 4 steps |
| Edit, 1 reference | 124–125 s at 768 px | App and scratch |
| Edit, 2 references | 167 s (app), 215 s (scratch) at 768 px | Each reference adds tokens to every step |
| Memory | MLX peak 4.9–5.0 GB (text), 5.2 GB (1 reference), 7.3 GB (2 references); worker RSS 3.1 GB | `mx.get_peak_memory()` per job. Without the cache limit, the process footprint reached 10.0–12.3 GB; with it, 6.3 GB (text, 768 px) and 8.0 GB (2 references) |
| Reproducible | yes | The same seed and settings gave a byte-identical PNG (app and scratch) |
| Network use | none | Prepare and generation ran with sockets blocked: 0 attempts |
| Privacy | prompts stay out of logs | Grepped every log after the runs |

What was checked by eye: the text-to-image results matched their descriptions
(mangoes, a watercolour lighthouse). Edits kept the subject's identity, the
same lighthouse with the same stripes and rocks, while changing the time of
day or season. A two-photo edit placed an object from one photo into the other
in a matching style. No photos of people have been tried yet.

## Decisions

| Decision | Why |
| --- | --- |
| mflux on MLX, 4-bit | The full model needs about 16 GB, which is the whole Mac. 4-bit is about 4.6 GB in memory according to mflux |
| Official files, quantised here | Owner decision: the source is the original publisher, not a community copy |
| Originals kept | They allow an 8-bit comparison. Deleting them would save about 16 GB; the owner decides |
| Pixels saved by Rasva | mflux's own save embeds the prompt and the reference paths in the PNG |
| Two mflux classes, one loaded at a time | `Flux2KleinEdit` needs at least one reference, so text-to-image uses `Flux2Klein`. Each holds the 4-bit weights, so switching task reloads (about 2.5 s) |
| 1 GB MLX cache limit | It cut the process footprint from 10.0 GB to 6.3 GB at the same speed |
| Progress and cancel through an mflux in-loop callback | Cancel stops between steps. mflux only catches Ctrl-C in its loop, so Rasva's cancel goes straight up |
| No content filter, no consent check | Owner decision (ADR-0007) |

## Licence

Model: **Apache-2.0** (Black Forest Labs; open weights, commercial use allowed).
mflux: MIT. MLX: MIT.
