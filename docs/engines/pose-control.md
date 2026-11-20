# Pose control (phase I2: same person, new pose): DRAFT

Status: **draft, not installed, nothing measured.** Written 2026-11-20 from
model cards, repositories and package indexes, with no weights downloaded. The
code is built and tested with fake runtimes only. The owner must confirm the
licences below before anything is pinned, locked or installed.

## Recommendation

**Use FLUX.2 [klein] 4B, the I1 editing engine, with a `pose` reference image.
Make that pose image with a small ONNX pose finder (DWPose). If the stick
figure is not followed well, add the 92 MB Apache-2.0 "refcontrol" pose LoRA.**

- **No second large model.** The person photo and the pose go into Klein's
  multi-reference editing. The prompt says which image is which. The I2 code is
  `rasva.engines.pose.edit`, which the I1 engine calls for any `image.edit` job
  with a `pose` reference.
- **Pose finder** (`dwpose` engine, task `image.pose`). It reads a pose photo
  and saves an OpenPose-style stick figure as an output asset, so the user can
  see it, reuse it and trace it back. Seven built-in figures, drawn by Rasva,
  cover the common poses without a photo.
- **The stick figure format works for both paths.** If Klein falls short, the
  fallback (SD 1.5 + ControlNet OpenPose) takes the same figure.

**This path depends on I1's memory measurement.** Klein 4B's diffusers files
come to about 16 GB in bf16: transformer 7.75 GB plus text encoder 8.05 GB. That
is the whole memory of the M2. BFL's own figures disagree: about 13 GB VRAM on
the model card, about 8 GB in the GitHub README. One user reports running out of
memory on a 16 GB GPU even with CPU offload (flux2 issue #35). If I1 cannot run
Klein on the M2 at a usable speed, even with offload or a quantised text
encoder, I2 falls back to the SD 1.5 path below.

## Options compared

| Option | Pose control | Identity | Licence | Extra download | Mac (MPS) | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **FLUX.2 Klein 4B multi-reference + pose image** | Prompted: "the pose of image 2". BFL's guide says FLUX.2 uses multi-reference editing *instead of* ControlNets. Klein is not named on that page | Strong. The person photo is a direct reference at up to 1024 px | Apache-2.0, commercial use allowed | None beyond I1 | diffusers `Flux2KleinPipeline` (0.37.0+). The docs show an MPS badge. Not measured | **Recommended** |
| + `xocialize/refcontrol-FLUX.2-klein-4B-pose-lora` | Trained for "pose from image 1 (OpenPose COCO-18 skeleton), reference from image 2" | Same | Apache-2.0 | 0.092 GB safetensors | Trained on klein-base-4B. The card shows only an MLX/Swift run. **Loading in diffusers not verified** | Optional add-on, if test plan §2 shows weak pose following |
| SD 1.5 + `lllyasviel/control_v11p_sd15_openpose` + IP-Adapter Plus Face (`h94/IP-Adapter`) | ControlNet, strong and well proven | Weak to fair. 512 px output; a face embedding, not the photo | ControlNet: CreativeML OpenRAIL-M (already accepted for SD 1.5). IP-Adapter: Apache-2.0 | ControlNet fp16 0.72 GB, IP-Adapter 0.10 GB, image encoder 2.53 GB (all safetensors) | SD 1.5 runs on the M2 today, at 8.5 GB GPU memory reserved. With ControlNet it will be more. Not measured | **Fallback** if Klein does not fit or fails §2 |
| IP-Adapter FaceID / InstantID / PhotoMaker | Needs ControlNet anyway | Strong | FaceID and InsightFace models are non-commercial research only. InstantID and PhotoMaker are SDXL, with `.bin` (pickle) weights | 1–4 GB | — | Rejected: licence and pickle |
| FLUX.2-dev Fun ControlNet Union (alibaba-pai) | Pose + reference | Strong | FLUX dev **non-commercial** | 2 × 8.2 GB, for the 32B FLUX.2-dev | Needs VideoX-Fun code, not diffusers | Rejected: licence, size |
| DiffSynth `Template-KleinBase4B-ControlNet` | Depth shown; pose not verified | — | Apache-2.0 | 7.75 GB | DiffSynth-Studio, not diffusers; card says 40 GB GPU | Rejected: size, runtime |
| FLUX.1-dev ControlNet Union (InstantX, Shakker-Labs) | Pose | — | FLUX.1-dev **non-commercial** | 4.3–6.6 GB + 12B base | — | Rejected: licence, size |
| Qwen-Image-Edit-2509/2511 | Accepts keypoint maps as references | Strong | Apache-2.0 | ~58 GB | — | Rejected: far too big for 16 GB |

## Pose extraction compared

| Option | Weights | Licence | Runtime | Verdict |
| --- | --- | --- | --- | --- |
| **DWPose ONNX** (`yzd-v/DWPose`: `yolox_l.onnx` 216.7 MB + `dw-ll_ucoco_384.onnx` 134.4 MB) | ONNX, no pickle | Apache-2.0 (repo and code). Training-data terms not checked | `onnxruntime` 1.32.0 (MIT), numpy, Pillow. No torch, no OpenCV. CPU by default; Core ML optional via `RASVA_ONNX_COREML=1` | **Chosen.** Pinnable with the existing `pin_engine.py` |
| rtmlib (RTMW/RTMPose ONNX) | ONNX inside zips on download.openmmlab.com, mirrored on HF `Tau-J/RTMPose` | Apache-2.0 | onnxruntime, but requires **both** `opencv-python` and `opencv-contrib-python`. Defaults download from URLs | Good code, awkward lock. DWPose's pre- and post-processing is short enough to keep in-house instead |
| MediaPipe Pose Landmarker | `.task` (TFLite), 5.8–30.7 MB from a `latest` URL | Package Apache-2.0. **Model card licence not read** | `mediapipe` wheel; 33 landmarks that must be remapped to 18 | Smallest; kept as a fallback if DWPose's licence is not accepted |
| controlnet_aux OpenposeDetector (`lllyasviel/Annotators`) | `.pth` pickle only | Weights derive from CMU OpenPose: **non-commercial research only** | torch + many packages | Rejected |
| controlnet_aux DWposeDetector | — | — | Needs mmcv, mmpose and mmdet | Rejected |

## Identity settings (plain language)

| Setting | What it changes (each is visible to the model) |
| --- | --- |
| Keep the face closely | Person photo sent at 1024 px, pose at 512 px (references are resized to these long sides, up or down). The prompt stresses face, skin tone and hair. Pose-LoRA weight 0.7 |
| Balanced (default) | Person 1024 px, pose 768 px. LoRA 0.85 |
| Follow the pose closely | Person 768 px, pose 1024 px. The prompt stresses limbs, head angle and turn. LoRA 1.0 |

Klein 4B is distilled to 4 steps at guidance 1.0, so the usual "strength" dials
don't apply. The real levers are which images go in, their size and order, the
prompt, and the LoRA weight. **Whether the three settings look different is
unverified** (test plan §3). If they don't, show one setting only, so the UI
doesn't offer a choice that does nothing.

The pose can be sent as the **stick figure** (default) or as the **original
pose photo** (an advanced option). No primary source shows Klein following an
OpenPose skeleton without the LoRA. BFL's guide talks about pose photos. When
the photo is used, the prompt tells the model not to copy that person's face,
body or clothes.

## What the code does now

- `backend/rasva/engines/pose/` holds:
  - `skeleton.py`: COCO-18 OpenPose drawing and a stdlib PNG encoder.
  - `__init__.py`: the `dwpose` engine.
  - `edit.py`: the pose-change path for the I1 engine.
  - `requirements.in`, which is not locked.
- `scripts/pin_engine.py dwpose` holds the pin spec: the two ONNX files only.
  The repo's `.pth` files are never listed.
- `scripts/make_pose_figures.py` writes the built-in figures to
  `frontend/public/poses/`, plus `poses.json`.
- `rasva/services/image_refs.py` is a stand-in for I1's reference resolver.
  Uploads take `role`; jobs resolve `references` to `{asset_id, role, path}`,
  plus `pose_figure` for figures; Retry resolves them again.
- Output metadata includes `source_asset_ids`, `seed`, `model_revision`,
  `identity`, `pose_source`, `reference_roles` and `synthetic: true`. The prompt
  is never in metadata or logs.
- The pose finder is gated with licence class `unknown`. It shows as
  incompatible, with the exact next steps, until it is pinned and locked.

## For the main session: install and measure

1. **Owner confirms licences** (list below). Then reclassify `dwpose`'s
   `license_class` if wanted. It is `unknown` now, so the installer asks.
2. Lock the pose finder (from `backend/rasva/engines/pose/`):
   `MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile requirements.in --generate-hashes --only-binary :all: --python-version 3.12 --python-platform aarch64-apple-darwin --no-header --no-annotate -o requirements.lock`.
   A dry run on 2026-11-20 resolved to 11 packages. Without the deployment
   target it fails, because onnxruntime 1.32 has macOS 14+ wheels only.
3. Pin the files: `python3 scripts/pin_engine.py dwpose`.
4. In the I1 Klein engine, route pose jobs to `edit` (see the module docstring
   in `edit.py`). Add `"pose"` to its `features`.
5. Install both from Models in the dev home. Run
   [pose-i2-test-plan.md](pose-i2-test-plan.md), §1 first.
6. Check at once, before the full plan:
   - **DWPose channel order.** The code feeds RGB to both models, following
     the ControlNet annotator; rtmlib feeds BGR. Compare a known photo's
     figure against the photo; if limbs are wrong, set `INPUT_ORDER = "BGR"`.
   - **Thresholds.** Detector 0.3, keypoints 0.3.
   - **Core ML provider speed** against CPU.
7. If pose following is weak (test plan §2), try the LoRA:
   - add `xocialize/refcontrol-FLUX.2-klein-4B-pose-lora` to Klein's manifest;
   - load it with `pipe.load_lora_weights(..., adapter_name="refcontrol_pose")`;
   - pass `lora_available=True` / `lora_loaded=True`.
   Verify that it loads in diffusers first: the card uses BFL key names.
8. Record the measurements in a section here, the way `sd15.md` does.

## Licences to confirm

| Component | Licence | Notes |
| --- | --- | --- |
| FLUX.2 [klein] 4B (`black-forest-labs/FLUX.2-klein-4B`) | Apache-2.0 per the model card | The owner confirms it with I1. The 9B variants are non-commercial; don't swap them in |
| DWPose ONNX weights and code (`yzd-v/DWPose`, IDEA-Research/DWPose) | Apache-2.0 | Training datasets (COCO-WholeBody, UBody and others) not checked |
| onnxruntime 1.32.0 | MIT | |
| numpy, Pillow | BSD-style / HPND (MIT-CMU) | |
| refcontrol pose LoRA (optional) | Apache-2.0 | Community LoRA; author's training data not stated |
| Built-in pose figures | Rasva's own | Drawn from hand-written keypoints |
| Fallback only: `lllyasviel/control_v11p_sd15_openpose` | CreativeML OpenRAIL-M | Same terms as SD 1.5 |
| Fallback only: `h94/IP-Adapter` (Plus Face SD 1.5 + image encoder) | Apache-2.0 | The image encoder is OpenCLIP ViT-H; its licence is not checked |

## Not verified

- Klein's memory use and speed on the M2. Whether Klein runs at all with the
  other apps open.
- Whether Klein follows a stick figure without the LoRA, and how well with it.
- Whether the refcontrol LoRA loads in diffusers. The LoRA scale API is
  `set_adapters`.
- DWPose's channel order and thresholds against real weights. The decoding
  was written to follow DWPose's ONNX code and compared with rtmlib 0.0.16
  (grid decode, padding, normalisation and SimCC rescaling agree). It has only
  run on synthetic tensors. Channel order is the open question: the ControlNet
  annotator passes RGB, rtmlib passes BGR. `INPUT_ORDER` in `pose/__init__.py`
  is the one switch.
- Core ML execution-provider coverage for YOLOX and DWPose.
- The training-data licences behind DWPose and the LoRA.
- The SD 1.5 fallback's memory use with ControlNet and IP-Adapter added.
