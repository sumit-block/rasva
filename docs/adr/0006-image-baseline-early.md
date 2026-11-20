# ADR-0006: Start the image baseline (I0) before the audio release gate

- Status: Accepted (owner decisions)
- Date: 2026-11-20
- PRD refs: §15 (A5 release gate), §18, §19 (I0), §28, §31, §35.1

## Context

The PRD says image phases begin only after the A5 audio release gate. On
2026-11-20 the audio studio is built, but A5 still needs an offline test with
the internet switched off, usability tests and the release checklist. A1-012,
A2 (IndicF5) and the A3 listening tests are also open. The owner chose to
start images now anyway.

## Decisions

1. **Start I0 now**, in parallel with the remaining audio work. The A5 gate
   still applies to the audio release; I1 and later image phases still need
   their own decisions.
2. **Runtime: diffusers on PyTorch** in its own Python 3.12 environment,
   built from a hash-pinned lockfile (ADR-0003). This is the same pattern as
   Kokoro and Chatterbox, and later image adapters (ControlNet, IP-Adapter)
   build on diffusers. Apple's Core ML version was not chosen: it isn't on
   PyPI, needs separately converted models, and doesn't carry over to I1–I3.
3. **Model: Stable Diffusion 1.5**, fp16 safetensors from
   `stable-diffusion-v1-5/stable-diffusion-v1-5` at a pinned commit. The
   original `runwayml` repository is no longer available.
4. **No safety checker.** It is not downloaded, and the pipeline runs with
   `safety_checker=None`. This saves about 0.6 GB and avoids false blocks on
   harmless images. The engine's licence notes and the Image Studio say that
   images are not screened.
5. **Licence:** CreativeML OpenRAIL-M, classed `restricted` in Rasva. The
   owner reviewed and accepted it, and the app asks for confirmation again at
   install.

## Consequences

- Audio release work (A5) and image work now compete for the same Mac and
  review time.
- With no safety checker, responsibility for content sits with the user under
  the licence's use restrictions. Revisit this before any shared or packaged
  release.
- The experiment service is still narration-only. I0 measurements come from
  repeated jobs, and image experiments with PRD §20.2 weights are later work.
