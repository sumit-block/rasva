# I3 clothing replacement ("Change outfit"): engine choice

Status: **draft recommendation, 2026-11-20.** No model has been downloaded or
run for I3. Every licence below needs the owner's confirmation. Sizes come from
the Hugging Face file listings. Memory and quality are not measured yet: see
the [test plan](outfit-test-plan.md).

## What the feature does

The user picks a photo of a person, then either a photo of a garment or a
description of it ("a maroon silk saree with a gold border"). Rasva asks an
editing engine to dress the person in that garment while keeping their face,
body, pose and background. The result is shown next to the original.

## Options compared

| | A. General reference editing (FLUX.2 klein 4B) | B. Dedicated try-on model | C. Hybrid (A + automatic garment mask, or A + a try-on LoRA) |
| --- | --- | --- | --- |
| Licence | Apache-2.0 (weights and code). Commercial use allowed | All checked are **non-commercial** (CC BY-NC-SA 4.0 or similar), or inherit the non-commercial FLUX.1-dev / Fill / Kontext licence | Depends on the added part (below) |
| Size | ~16 GB in bf16: transformer 7.75 GB + Qwen3-4B text encoder 8.04 GB + VAE 0.17 GB | CatVTON ~2 GB fp16 plus preprocessors; IDM-VTON ~28 GB; FLUX-based ones 28–34 GB | A + 0.1–1.6 GB |
| Safetensors | Yes, all files | CatVTON's try-on weights yes, but its DensePose (`.pkl`) and SCHP (`.pth`) preprocessors are pickle; IDM-VTON, OOTDiffusion and Leffa need `.bin`/`.pth` files | Yes for SAM 2.1, Grounding DINO, CLIPSeg and the klein try-on LoRA |
| Garment mask needed | No. The model reads the person and garment photos and an instruction | Yes for most (agnostic mask from DensePose + human parsing); CatVTON's mask-free variant needs none | Optional |
| Text fallback | Yes: same engine, person photo + description | No: try-on models need a garment photo | Yes (via A) |
| Apple GPU (MPS) | Not verified. The model card only mentions switching the device to `mps` in a code comment. MLX ports exist, but that is a different runtime | No evidence; IDM-VTON's issue tracker says the code is written for CUDA; OOTDiffusion's maintainers say Linux/CUDA only | Same as A; SAM 2.1's own notebook calls MPS support "preliminary" |
| Shared with other phases | Same engine as I1 (being built now) and probably I2: nothing extra to install | A second large model just for clothes | Small additions to A |
| Maintenance | diffusers `Flux2KleinPipeline` (since diffusers 0.37.0) | CatVTON's GitHub code repository now returns 404 (checked 2026-11-20); only the HF weights remain | |

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

- **Memory.** ~16 GB of bf16 weights on a 16 GB Mac will not fit at once. I1
  must load in stages (encode the instruction, free the text encoder, then load
  the transformer) or use a smaller/quantised variant. BFL's published figures
  disagree (~13 GB on the model card and blog, ~8 GB in the GitHub README).
  This is I1's problem to solve; I3 inherits whatever I1 measures.
- **Quality.** Zero-shot try-on quality of a general editor is unknown,
  especially for long and draped garments (saree, lehenga). If it is not good
  enough, try option C2 below before considering B.

### Later options (not built)

- **C1: automatic garment area.** If the engine changes too much (the face, the
  background), a garment mask can limit the edit, either through an engine that
  reads masks or by pasting the original back outside the mask. The workflow
  already accepts an optional mask (see below). Candidates to make one
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
  instruction and references. Person = image 1, garment photo = image 2,
  optional mask last with role `mask`. The instruction always asks to keep the
  face, identity, expression, hair, skin tone, body shape, hands and pose, and
  (by default) the background, lighting and framing. "What to change" is
  whatever the garment is / top only / bottom only / whole outfit.
- **Garment photo and description together:** the photo is the garment, and
  the words add details. The job's `inputs.workflow.garment_source` records
  `photo`, `text` or `photo_and_text`, plus `instruction_version`.
- `POST /api/v1/projects/{id}/outfit`: looks up the assets (same project,
  PNG/JPEG/WebP, uploaded references or earlier results), picks an installed
  `image.edit` engine, applies a preset, and creates the job. A mask is passed
  only to engines that declare the `mask` feature; otherwise the user is told
  to remove it. FLUX.2 klein should not declare `mask`.
- UI: `frontend/src/components/image/OutfitPanel.tsx`, mounted at the end of
  Image Studio.
- Tests: `backend/tests/test_outfit.py` with fake `image.edit` engines in
  `backend/tests/fake_editor.py`.
- The instruction wording is a first draft. It has not been tried on a real
  model. Change `INSTRUCTION_VERSION` when it changes.

## Licences for the owner to confirm

| Item | Licence | Source |
| --- | --- | --- |
| FLUX.2 klein 4B (recommended, from I1) | Apache-2.0 (LICENSE.md is the Apache 2.0 text; not gated) | huggingface.co/black-forest-labs/FLUX.2-klein-4B |
| Its text encoder (Qwen3-4B architecture, shipped in the same repo) | Covered by the repo's Apache-2.0; upstream Qwen3-4B is also Apache-2.0 | same repo; huggingface.co/Qwen/Qwen3-4B |
| FLUX.2 klein **9B** | FLUX non-commercial licence: **do not use** | huggingface.co/black-forest-labs/FLUX.2-klein-9B |
| Later, C1: SAM 2.1 / Grounding DINO / CLIPSeg | Apache-2.0 | huggingface.co/facebook/sam2.1-hiera-small, IDEA-Research/grounding-dino-tiny, CIDAS/clipseg-rd64-refined |
| Later, C2: klein try-on LoRA | Apache-2.0 (training data not checked) | huggingface.co/xocialize/tryon-FLUX.2-klein-4B-lora |
| Comparison only, B: CatVTON | CC BY-NC-SA 4.0 (non-commercial) | huggingface.co/zhengchong/CatVTON |

Using photos of real people raises consent and likeness questions whatever the
licence: the UI asks for photos only of people who agreed, and results are
labelled as AI-generated.

## Not verified

- Whether klein 4B runs on the M2 under PyTorch MPS within 16 GB, and how fast.
- Try-on quality of klein 4B for formal, casual, traditional Indian and long
  garments, and across poses.
- Whether klein follows "image 1 / image 2" references in instructions as
  expected (the wording assumes it does).
- MPS support for SAM 2.1 and Grounding DINO (only needed for C1).
- Training-data licences of every model above (ATR, LIP, VITON-HD, DressCode).
  This needs a legal reading, not an engineering one.
