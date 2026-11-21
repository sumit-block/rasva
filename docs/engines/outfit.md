# I3 clothing replacement ("Change outfit"): engine choice

Status: **draft recommendation, 2026-11-20**, updated after I1 merged. No model
has been downloaded or run for I3. Every licence below needs the owner's confirmation. Sizes come from
the Hugging Face file listings. Memory and quality are not measured yet: see
the [test plan](outfit-test-plan.md).

## What the feature does

The user picks a photo of a person, then either a photo of a garment or a
description of it ("a maroon silk saree with a gold border"). Rasva asks an
editing engine to dress the person in that garment while keeping their face,
body, pose and background. The result is shown next to the original.

## Options compared

| | A. General reference editing (FLUX.2 klein 4B on mflux) | B. Dedicated try-on model | C. Hybrid (A + automatic garment mask, or A + a try-on LoRA) |
| --- | --- | --- | --- |
| Licence | Apache-2.0 (weights and code). Commercial use allowed | All checked are **non-commercial** (CC BY-NC-SA 4.0 or similar), or inherit the non-commercial FLUX.1-dev / Fill / Kontext licence | Depends on the added part (below) |
| Size | ~16 GB in bf16: transformer 7.75 GB + Qwen3-4B text encoder 8.04 GB + VAE 0.17 GB | CatVTON ~2 GB fp16 plus preprocessors; IDM-VTON ~28 GB; FLUX-based ones 28–34 GB | A + 0.1–1.6 GB |
| Safetensors | Yes, all files | CatVTON's try-on weights yes, but its DensePose (`.pkl`) and SCHP (`.pth`) preprocessors are pickle; IDM-VTON, OOTDiffusion and Leffa need `.bin`/`.pth` files | Yes for SAM 2.1, Grounding DINO, CLIPSeg and the klein try-on LoRA |
| Garment mask needed | No. The model reads the person and garment photos and an instruction | Yes for most (agnostic mask from DensePose + human parsing); CatVTON's mask-free variant needs none | Optional |
| Text fallback | Yes: same engine, person photo + description | No: try-on models need a garment photo | Yes (via A) |
| Apple Silicon | Runs on Apple MLX through mflux 0.24.0 (owner decision, I1), with the official bf16 files quantised to 4-bit at install. Speed and memory on the M2: not measured yet | No evidence; IDM-VTON's issue tracker says the code is written for CUDA; OOTDiffusion's maintainers say Linux/CUDA only | Same as A; SAM 2.1's own notebook calls MPS support "preliminary" |
| Shared with other phases | Same engine as I1 and probably I2: nothing extra to install | A second large model just for clothes | Small additions to A |
| Maintenance | mflux `Flux2KleinEdit` (MIT); diffusers also has `Flux2KleinPipeline` | CatVTON's GitHub code repository now returns 404 (checked 2026-11-20); only the HF weights remain | |

## Recommendation: A, general reference editing with FLUX.2 klein 4B

1. **Licence.** It is the only candidate that is commercially usable, ships only
   safetensors and needs no remote code. Every dedicated try-on model found is
   non-commercial, needs pickle files, or both.
2. **No extra install.** I1 already brings this engine. I3 adds a workflow
   (instruction wording, reference roles, validation, UI), not a model.
3. **Text fallback comes free.** The same engine handles "describe the garment".
4. **No mask.** That removes the preprocessors (DensePose, human parsing,
   OpenPose) whose licences and pickle formats were the biggest problems.

**Risks to check in the test plan:**

- **Memory.** The bf16 files are ~16 GB, so I1 quantises them to 4-bit on the
  Mac at install (mflux). I3 adds no model, so it inherits whatever I1
  measures. Two reference photos (person + garment) cost more per step than
  one; measure both.
- **Quality.** Zero-shot try-on quality of a general editor is unknown,
  especially for long and draped garments (saree, lehenga). If it is not good
  enough, try option C2 below before considering B.

### Later options (not built)

- **C1: automatic garment area.** If the engine changes too much (the face, the
  background), a garment mask can limit the edit, either through an engine that
  reads masks or by pasting the original back outside the mask. Not built: the
  shared contract has no `mask` role, and mflux's klein edit does not take a
  mask. Candidates to make one
  automatically: **Grounding DINO** (tiny 0.69 GB / base 0.93 GB) to find "shirt",
  "saree" from text, plus **SAM 2.1** (0.16–0.90 GB) to cut it out. Both are
  Apache-2.0 with safetensors. CLIPSeg (0.60 GB, Apache-2.0) is a simpler
  alternative. **Avoid the SegFormer "clothes" models** (for example
  `mattmdjaga/segformer_b2_clothes`): they inherit NVIDIA's SegFormer licence
  (non-commercial, research or evaluation only), the ATR training set has no
  clear licence, and the repositories also contain pickle files.
- **C2: try-on LoRA for klein 4B.** `xocialize/tryon-FLUX.2-klein-4B-lora` is
  Apache-2.0 and a 92 MB safetensors file. It expects a clothing-agnostic person
  image plus separate top and bottom crops, so it needs the segmentation step of
  C1. Its author built the inputs with the non-commercial SegFormer model, and
  its training data was not checked. Treat it as an experiment only.
- **B, for comparison only.** If the owner accepts a non-commercial licence for
  a local test, CatVTON (CC BY-NC-SA 4.0, ~2 GB fp16 on SD 1.5 inpainting) is
  the smallest. It still needs pickle preprocessors for its mask (or its
  mask-free variant), and its source code is no longer on GitHub. Not
  recommended for the product.

## What is built (I3 workflow layer)

- `backend/rasva/services/outfit.py`: checks the choices and builds the
  instruction and references. Person = image 1, garment photo = image 2; mflux
  receives `image_paths` in that order, and the instruction names them by it. The instruction always asks to keep the
  face, identity, expression, hair, skin tone, body shape, hands and pose, and
  (by default) the background, lighting and framing. "What to change" is
  whatever the garment is / top only / bottom only / whole outfit.
- **Garment photo and description together:** the photo is the garment, and
  the words add details. The job's `inputs.workflow.garment_source` records
  `photo`, `text` or `photo_and_text`, plus `instruction_version`.
- `POST /api/v1/projects/{id}/outfit`: looks up the assets (same project,
  uploaded PNG/JPEG/WebP references), picks an installed `image.edit` engine,
  applies a preset, resolves the references with I1's `services/references.py`
  and creates the job. The roles come from the outfit request, not from the
  roles stored with the uploads. Retry keeps the `workflow` record.
- UI: `frontend/src/components/image/OutfitPanel.tsx`, mounted at the end of
  Image Studio.
- Tests: `backend/tests/test_outfit.py`, using I1's fake `image.edit` engine
  (`backend/tests/fake_image_editor.py`) through the real queue and worker.
- The instruction wording is a first draft. It has not been tried on a real
  model. Change `INSTRUCTION_VERSION` when it changes.

## Licences for the owner to confirm

| Item | Licence | Source |
| --- | --- | --- |
| mflux 0.24.0 (runtime, from I1) | MIT (PyPI metadata) | pypi.org/project/mflux/0.24.0 |
| FLUX.2 klein 4B (recommended, from I1) | Apache-2.0 (LICENSE.md is the Apache 2.0 text; not gated) | huggingface.co/black-forest-labs/FLUX.2-klein-4B |
| Its text encoder (Qwen3-4B architecture, shipped in the same repo) | Covered by the repo's Apache-2.0; upstream Qwen3-4B is also Apache-2.0 | same repo; huggingface.co/Qwen/Qwen3-4B |
| FLUX.2 klein **9B** | FLUX non-commercial licence: **do not use** | huggingface.co/black-forest-labs/FLUX.2-klein-9B |
| Later, C1: SAM 2.1 / Grounding DINO / CLIPSeg | Apache-2.0 | huggingface.co/facebook/sam2.1-hiera-small, IDEA-Research/grounding-dino-tiny, CIDAS/clipseg-rd64-refined |
| Later, C2: klein try-on LoRA | Apache-2.0 (training data not checked) | huggingface.co/xocialize/tryon-FLUX.2-klein-4B-lora |
| Comparison only, B: CatVTON | CC BY-NC-SA 4.0 (non-commercial) | huggingface.co/zhengchong/CatVTON |

Using photos of real people raises consent and likeness questions whatever the
licence. By the owner's choice there is no consent check and no content filter;
the panel shows a one-line reminder, and results are labelled as AI-generated.

## Not verified

- Memory and speed of klein 4B (4-bit, mflux) on the M2 with two reference
  photos.
- Try-on quality of klein 4B for formal, casual, traditional Indian and long
  garments, and across poses.
- Whether klein follows "image 1 / image 2" references in instructions as
  expected (the wording assumes it does).
- MPS support for SAM 2.1 and Grounding DINO (only needed for C1).
- Training-data licences of every model above (ATR, LIP, VITON-HD, DressCode).
  This needs a legal reading, not an engineering one.
