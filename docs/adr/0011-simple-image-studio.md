# ADR-0011: Simple Image Studio, Qwen-Image only

- Status: Accepted (owner decision, 2026-11-21)
- Supersedes the Image Studio UI parts of I1–I4 (ADR-0006, ADR-0007); engines and APIs remain

## Context

Image Studio had grown to modes, a model picker, presets, steps, role boxes for
reference photos, a pose studio, an outfit panel, versions, before/after
comparison, export options and a low-memory toggle. The owner chose Qwen-Image
2.1 Turbo (ADR-0010) as the only image model and asked for "simple UI … only one
input + upload image or paste. I will write prompt, attach image, it will
generate result image".

## Decisions

1. **Uninstall the other image models** (owner's choice): Stable Diffusion 1.5,
   FLUX.2 Klein and the DWPose pose finder. 25 GB freed; projects and generated
   images kept. Their engine code stays in the repository, so they can be
   reinstalled from Models.
2. **Image Studio is one form:** a text box, Attach photo (or paste with ⌘V
   anywhere on the page, the Paste button, or drag and drop), and Generate.
   - No photos: `image.generate` (Qwen's default 1024 × 1024, 8 steps).
   - With photos (up to 4): `image.edit`. Photos keep the order attached; the
     output takes the first photo's shape at about 1 MP.
   - The model is the installed image model; there is no picker. Results show as
     job rows with progress, cancel, retry and download.
3. **Removed from the UI** (git history keeps them): the reference role boxes,
   pose studio and built-in pose figures, outfit panel, versions, before/after
   comparison, PNG/JPEG export options and the low-memory toggle. The backend
   endpoints and tests for those features stay (outfit, image groups, export,
   settings), so a future UI could use them again.
4. **One project:** the most recent image project is used, or "Images" is created
   on the first attach or generate.

## Consequences

- "Improve my instruction" (ADR-0010) is not in the simple form. The engine
  still supports it through the API.
- Pose change needs FLUX.2 Klein and the pose finder, which are uninstalled.
- Each Qwen picture takes several minutes on the 16 GB M2 (docs/engines/qwen21turbo.md).
