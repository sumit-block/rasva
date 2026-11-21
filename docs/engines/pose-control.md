# Pose control (phase I2: same person, new pose)

Status: **installed and run on the M2 (2026-11-21).** The owner accepted the
DWPose (Apache-2.0) and onnxruntime (MIT) licences on 2026-11-21. The two ONNX
files are pinned by SHA-256 and the environment is hash-locked (11 wheels, no
torch). Results on synthetic test people are under
[Measurements on the M2](#measurements-on-the-m2-2026-11-21); tests on real photos
of people are still to do. Klein runs on mflux 0.24.0 (Apple MLX, 4-bit, the
`flux2-klein-4b` engine).

## Recommendation

**Use FLUX.2 [klein] 4B, the I1 editing engine, with a `pose` reference image.
Make that pose image with a small ONNX pose finder (DWPose). If the stick
figure is not followed well, try the 92 MB Apache-2.0 "refcontrol" pose LoRA,
which mflux can load (at model load, not per job).**

- **No second large model.** The person photo and the pose go into Klein's
  multi-reference editing (mflux `Flux2KleinEdit.generate_image(image_paths=[...])`,
  images in order). The prompt says which image is which. The I2 code is
  `rasva.engines.pose.edit`: the Klein engine hands any `image.edit` job with a
  `pose` reference to it in `validate_input`, and it returns Klein's own inputs
  (full instruction, person then pose, per-reference sizes, output size from the
  pose's aspect ratio). The engine then runs it like any other edit.
- **Pose finder** (`dwpose` engine, task `image.pose`). It reads a pose photo
  and saves an OpenPose-style stick figure as an output asset, so the user can
  see it, reuse it and trace it back. Seven built-in figures, drawn by Rasva,
  cover the common poses without a photo.
- **The stick figure format works for both paths.** If Klein falls short, the
  fallback (SD 1.5 + ControlNet OpenPose) takes the same figure.

**This path depends on I1's memory measurement.** The bf16 files are about
16 GB, but I1 quantises them to 4-bit on the Mac at install (mflux's published
4-bit size is about 4.7 GB; the engine's own peak estimate is 8 GB, not yet
measured). Pose jobs send two references (three with a background), which adds
image tokens to every step. If I1 measures that Klein cannot run two or three
references at 768 px on the M2 at a usable speed, I2 falls back to the SD 1.5
path below.

## Options compared

| Option | Pose control | Identity | Licence | Extra download | Mac (MPS) | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **FLUX.2 Klein 4B multi-reference + pose image** | Prompted: "the pose of image 2". BFL's guide says FLUX.2 uses multi-reference editing *instead of* ControlNets. Klein is not named on that page | Strong. The person photo is a direct reference at up to 1024 px | Apache-2.0, commercial use allowed | None beyond I1 | mflux 0.24.0 on MLX, 4-bit (I1). Not measured | **Recommended** |
| + `xocialize/refcontrol-FLUX.2-klein-4B-pose-lora` | Trained for "pose from image 1 (OpenPose COCO-18 skeleton), reference from image 2" | Same | Apache-2.0 | 0.092 GB safetensors | Trained on klein-base-4B, while I1 uses the distilled 4B. mflux 0.24's `Flux2KleinEdit` takes `lora_paths`/`lora_scales` and bakes the LoRA in at load, and ships a FLUX.2 LoRA key mapping. **Not verified** that this LoRA's keys load | Optional add-on, if test plan §2 shows weak pose following |
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
| mflux 0.24 `mflux/models/openpose` (`OpenPoseBody`) | `lllyasviel/Annotators` `body_pose_model.pth` (pickle, loaded with `torch.load(weights_only=True)`), fetched with `hf_hub_download` at runtime | Same CMU OpenPose weights: **non-commercial research only** | MLX forward pass, torch to load, OpenCV for drawing; would need the file pinned and loaded locally (workers block the network) | Rejected: licence and pickle, although it would reuse Klein's environment |

## Identity settings (plain language)

| Setting | What it changes (each is visible to the model) |
| --- | --- |
| Keep the face closely | Person photo sent at 1024 px, pose at 512 px (references are resized to these long sides, up or down). The prompt stresses face, skin tone and hair |
| Balanced (default) | Person 1024 px, pose 768 px |
| Follow the pose closely | Person 768 px, pose 1024 px. The prompt stresses limbs, head angle and turn |

Klein 4B is distilled to 4 steps at guidance 1.0, so the usual "strength" dials
don't apply. The real levers are which images go in, their size and order, and
the prompt. (A LoRA weight per setting is not possible: mflux bakes the LoRA in
at load.) **Whether the three settings look different is
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
  - `edit.py`: turns pose jobs into the Klein engine's inputs.
  - `requirements.in`, which is not locked.
- `scripts/pin_engine.py dwpose` holds the pin spec: the two ONNX files only.
  The repo's `.pth` files are never listed.
- `scripts/make_pose_figures.py` writes the built-in figures to
  `frontend/public/poses/`, plus `poses.json`.
- Klein engine (`rasva/engines/flux2klein`): three small hooks marked "I2":
  pose jobs go to `edit.validate`; `_prepare_references` honours a reference's
  `max_side`; the output metadata gains `edit`, `identity` and `pose_source`.
  `"pose"` is in its features, which the UI looks for.
- I1's reference resolver (`rasva/services/references.py`) also accepts a
  pose-finder figure (an `output` asset with `metadata.kind == "pose"`) as the
  `pose` reference, and only as the pose, and marks it `pose_figure`.
- The job stores the built instruction as `prompt` and the user's own words as
  `user_prompt`, so Retry rebuilds the same instruction. Output metadata
  includes `source_asset_ids`, `roles`, `seed`, `model_revision`, `identity`,
  `pose_source` and `synthetic: true`. The prompt is never in metadata or logs.
- The pose finder is gated with licence class `unknown`. It shows as
  incompatible, with the exact next steps, until it is pinned and locked.

## Measurements on the M2 (2026-11-21)

M2, 16 GB, through the app (install job and `/jobs`). FLUX.2 Klein 4B 4-bit on
mflux 0.24.0, 4 steps. **The test people were synthetic:** FLUX.2 Klein made a
fictional woman (person photo) and a fictional man walking and waving (pose
photo) in the same project. No photos of real people were used; that test is
still the owner's.

| Measure | Result |
| --- | --- |
| Pose finder install | 351 MB download (2 ONNX files, SHA-256 checked) in 76 s; environment 9 s; 485 MB installed |
| Pose finder run | Load 1.0 s; read the pose in 1.4 s on CPU; 1 person, all 18 keypoints; peak RSS 0.70 GB |
| Pose finder accuracy, by eye | The figure matched the photo: raised right arm, bent left arm, stride. So the RGB channel order (`INPUT_ORDER`) is right for these weights |
| Pose change, extracted figure (balanced) | Same woman, face, hair and clothes; raised arm and walking stride followed. 205 s at 576 × 768 |
| Pose change, built-in "hands on hips" (balanced) | Same woman and clothes; hands at the hips but in front, elbows not out: pose only partly followed. 170 s at 512 × 768 |
| Same, "Follow the pose closely" | Hands on hips with elbows out; still recognisably the same woman. 155 s |
| Pose change, **original pose photo** (balanced) | **Failed:** right pose, but the face and head were the pose photo's man, wearing the woman's clothes, despite "do not copy the face". 157 s |
| MLX peak per job | 6.2 GB (576 × 768) to 8.4 GB (512 × 768 with the longer instruction); worker RSS 2.2 GB |

Decisions from this run:

- Pose changes **always send the stick figure**. The "send the pose photo
  instead" option was removed from Image Studio: Klein copies the person in
  any photo it is given. The engine still accepts `pose_source: "photo"` for
  experiments.
- The refcontrol LoRA is not needed for now: Klein followed both figures, and
  "Follow the pose closely" fixed the one loose result.
- The default identity setting stays "Balanced". One sample per setting is not
  enough to change it.

## Install and measure (steps 1–5 done 2026-11-21)

1. **Owner confirms licences** (list below). Done 2026-11-21; `dwpose` is now
   `permissive`.
2. Lock the pose finder (from `backend/rasva/engines/pose/`):
   `MACOSX_DEPLOYMENT_TARGET=14.0 uv pip compile requirements.in --generate-hashes --only-binary :all: --python-version 3.12 --python-platform aarch64-apple-darwin --no-header --no-annotate -o requirements.lock`.
   A dry run on 2026-11-20 resolved to 11 packages. Without the deployment
   target it fails, because onnxruntime 1.32 has macOS 14+ wheels only.
3. Pin the files: `python3 scripts/pin_engine.py dwpose`.
4. Install Klein (I1) and the pose finder from Models in the dev home. The Klein
   hooks are already on this branch.
5. Run [pose-i2-test-plan.md](pose-i2-test-plan.md), §1 first.
6. Check at once, before the full plan:
   - **DWPose channel order.** The code feeds RGB to both models, following
     the ControlNet annotator; rtmlib feeds BGR. Compare a known photo's
     figure against the photo; if limbs are wrong, set `INPUT_ORDER = "BGR"`.
   - **Thresholds.** Detector 0.3, keypoints 0.3.
   - **Core ML provider speed** against CPU.
7. If pose following is weak (test plan §2), try the LoRA:
   - try it first in a scratch environment:
     `Flux2KleinEdit(model_path=<q4>, lora_paths=[...], lora_scales=[1.0])`.
     Check that the keys map. The LoRA was trained on klein-base-4B, while I1
     runs the distilled model;
   - its own prompt convention is image 1 = skeleton, image 2 = person,
     trigger `refcontrol`. `edit.py` would need a LoRA instruction and order;
   - because mflux bakes LoRAs in at load, a LoRA-on worker is a separate load.
     Weigh that before shipping it.
8. Record the measurements in a section here, the way `sd15.md` does.

## Licences (accepted by the owner 2026-11-21)

| Component | Licence | Notes |
| --- | --- | --- |
| FLUX.2 [klein] 4B (`black-forest-labs/FLUX.2-klein-4B`) | Apache-2.0 per the model card | The owner confirms it with I1. The 9B variants are non-commercial; don't swap them in |
| DWPose ONNX weights and code (`yzd-v/DWPose`, IDEA-Research/DWPose) | Apache-2.0 | Training datasets (COCO-WholeBody, UBody and others) not checked |
| mflux 0.24.0 (runs Klein; owned by I1) | MIT | Nothing extra for I2 |
| onnxruntime 1.32.0 | MIT | |
| numpy, Pillow | BSD-style / HPND (MIT-CMU) | |
| refcontrol pose LoRA (optional) | Apache-2.0 | Community LoRA; author's training data not stated |
| Built-in pose figures | Rasva's own | Drawn from hand-written keypoints |
| Fallback only: `lllyasviel/control_v11p_sd15_openpose` | CreativeML OpenRAIL-M | Same terms as SD 1.5 |
| Fallback only: `h94/IP-Adapter` (Plus Face SD 1.5 + image encoder) | Apache-2.0 | The image encoder is OpenCLIP ViT-H; its licence is not checked |

## Not verified

- Pose changes on photos of real people, and with three references (background).
- How well the LoRA would do (not needed so far).
- Whether the refcontrol LoRA loads in mflux and works on the distilled model.
- DWPose thresholds on hard photos (several people, partial bodies). Channel
  order is checked: RGB gave a correct figure on the M2.
- Core ML execution-provider coverage for YOLOX and DWPose.
- The training-data licences behind DWPose and the LoRA.
- The SD 1.5 fallback's memory use with ControlNet and IP-Adapter added.
