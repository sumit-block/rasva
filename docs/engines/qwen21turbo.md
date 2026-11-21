# Qwen-Image 2.1 Turbo (owner's private use; ADR-0010)

Status: installed and working on the target M2, but **far heavier than the Mac's
memory**: one image takes 8–14 minutes while macOS swaps 9–11 GB. Quality has not
been reviewed by the owner. Decisions are in
[ADR-0010](../adr/0010-qwen-image-21-turbo.md).

## What it does

- **Create from text:** Landscape 1152 × 896, Square 1024 × 1024, Portrait 896 × 1152.
- **Edit a photo:** 1–4 photos with roles, ordered person, clothing, pose,
  background. Optional **Improve my instruction**: Qwen's own text model rewrites
  the instruction from the photos first.
- Steps 1–8 (default 8). Guidance is fixed at 1 (Turbo). There is no "avoid" field.

## How it is installed

1. Download the official bf16 files (32.5 GB, 16 files, `Qwen/Qwen-Image-2.1-Turbo`
   pinned at `fc13ba1`, each checked by SHA-256). Downloads can be paused and resumed.
2. Build its Python 3.12 environment (the same hash-locked mflux 0.24.0 / MLX
   0.32.4 lock as FLUX.2 Klein).
3. **Prepare (one time):** quantise to 4-bit with `QwenImage21Edit.save_model`,
   using the Turbo config, into `q4/`, with network access blocked.

## Measurements on the M2 (2026-11-21)

M2, 16 GB, through the app. Other apps were open: macOS reported 8.9 GB available
before the first job.

| Measure | Result |
| --- | --- |
| Install | Download resumable; the last 16.2 GB arrived in 600 s. Environment 2.5 s. Prepare (4-bit) 141 s (quantise and save 113 s) |
| Installed size | 43.7 GB: 32.5 GB originals, 9.3 GB 4-bit copy, environment |
| Model load (cold) | 14.9 s |
| Text-to-image, 1024 × 1024, 8 steps | **466.6 s** (58 s per step) |
| Edit, 2 photos, 896 × 1152, 8 steps | **795.6 s** (99 s per step), plus **29.3 s** to improve the instruction |
| Memory | MLX peak 21.6 GB (text) and 22.7 GB (edit), more than the Mac's 17.2 GB. Worker footprint 15 GB, peak 19 GB. Swap in use 9.2 GB, rising to 11.3 GB |
| Compared with FLUX.2 Klein (same Mac) | FLUX: 70 s per 768 px image, outfit edit 278 s, MLX peak 5–8.4 GB |

What was checked by eye:

- **Text** ("A birthday cake with the words HAPPY BIRTHDAY written in icing"):
  both models spelled it right. Qwen's cake was more decorative; neither was
  clearly better at this size.
- **Outfit edit** (synthetic woman, green embroidered kurta from a garment photo):
  both kept the person, pose, jeans and background. Qwen added embroidery on
  the sleeves that the garment photo does not have. Its improved instruction had
  said "embroidery along the V-neckline and sleeves", so the rewrite invented a
  detail. FLUX copied the garment more faithfully.
- The improved instruction read well and named what to keep (identity, pose,
  bare feet, background, lighting).

## Why it is slow here

The 4-bit weights are 9.3 GB on disk. While generating, MLX holds the text
encoder, the transformer, the VAE (fp32) and the attention activations at about
1 MP, and peaks at about 22 GB. On 16 GB, macOS swaps the rest to disk and reads
it back on every step. The memory guard's estimate (`approx_peak_memory_bytes`,
12 GB) is kept below the Mac's total on purpose. With the measured peak, the
guard would refuse every job as one that can never fit, and the owner chose to
try the model.

Options not taken yet (owner decision):

- Free the text encoder after the prompt is encoded (mflux memory saver). That
  saves about 4.5 GB during the denoising steps, at the cost of reloading it for
  each job, and turns off Improve my instruction.
- mflux's experimental small text encoder (0.8B). It needs a 2.1 GB extra
  download, is English only and cannot improve instructions.
- Remove the model (frees 43.7 GB).

## Licence

Qwen Research License: research or evaluation only, and commercial use needs a
separate licence from Qwen. Installed for the owner's private, non-commercial use
(ADR-0010). mflux and MLX: MIT.
