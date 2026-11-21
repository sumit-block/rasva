# I3 clothing replacement: test plan

Status: **not run yet.** Run this on the 16 GB M2 once an editing engine is
installed (see [outfit.md](outfit.md)). Fill in the result columns only from
real runs. Leave a cell empty rather than estimate it.

## Before you start

- **Consent.** Use only photos of people who have agreed in writing to this
  test, or photos of yourself. Do not use photos of children. Keep the test
  photos in one Rasva project (for example "I3 tests") and delete them when
  the review is done unless the people agreed to longer storage.
- **Garment photos.** Use your own photos or product photos you are allowed to
  use. Mix flat-lay shots, garments on a hanger and garments worn by a model.
- **Fixed settings.** Use the same seed (for example 1234), the Balanced preset
  and "Keep the background" on for every case unless the case says otherwise.
  Write down the engine, the model revision (from the result's details) and the
  instruction version (`workflow.instruction_version` in the job's inputs).
- **Memory.** Close other heavy apps. Note whether audio engines were loaded.
- Nothing should leave the Mac. Run at least one case with Wi-Fi off.

## Photo set

Aim for at least 3 different people (with consent), so results don't depend on
one face or body type.

| ID | Person photo | Pose |
| --- | --- | --- |
| P1 | Standing, facing the camera, full body | Front |
| P2 | Standing, turned about 45°, full body | Three-quarter |
| P3 | Sitting on a chair | Seated |
| P4 | Arms crossed or one hand on the hip | Arms over the body |
| P5 | Walking or mid-step, outdoors | Movement |
| P6 | Waist-up portrait | Half body |

| ID | Garment | Kind |
| --- | --- | --- |
| G1 | Navy two-piece suit with a white shirt | Formal |
| G2 | Plain white shirt with a collar | Formal |
| G3 | Printed T-shirt (logo or text on it) | Casual |
| G4 | Blue jeans | Casual, bottom only |
| G5 | Hoodie | Casual |
| G6 | Silk saree with a contrasting border | Traditional Indian, long |
| G7 | Kurta with churidar | Traditional Indian |
| G8 | Lehenga choli | Traditional Indian, long |
| G9 | Sherwani | Traditional Indian, long, formal |
| G10 | Ankle-length maxi dress | Long |
| G11 | Long overcoat over other clothes | Long, layered |

## Cases

Run each case on every engine being compared (see "Comparison"). "Text" means
the garment is described in words, with no garment photo.

| Case | Person | Garment | Source | What to change | Checks |
| --- | --- | --- | --- | --- | --- |
| C1 | P1 | G1 | Photo | Whole outfit | Formal fit, lapels, tie, shirt collar |
| C2 | P2 | G2 | Photo | Top only | Trousers unchanged, collar and buttons |
| C3 | P6 | G2 | Photo | Top only | Half-body photo, sleeves at the crop |
| C4 | P1 | G3 | Photo | Top only | Print and text on the T-shirt copied or garbled? |
| C5 | P3 | G4 | Photo | Bottom only | Seated legs, top unchanged |
| C6 | P4 | G5 | Photo | Top only | Arms over the garment, hands intact |
| C7 | P1 | G6 | Photo | Whole outfit | Saree drape and pallu, border kept |
| C8 | P2 | G6 | Photo | Whole outfit | Drape at an angle |
| C9 | P5 | G7 | Photo | Whole outfit | Movement, churidar folds |
| C10 | P1 | G8 | Photo | Whole outfit | Long flared skirt, dupatta |
| C11 | P2 | G9 | Photo | Whole outfit | Long formal coat, buttons |
| C12 | P3 | G10 | Photo | Whole outfit | Long garment while seated |
| C13 | P1 | G11 | Photo | Top only | Layering over the existing clothes |
| C14 | P1 | "A maroon silk saree with a gold zari border" | Text | Whole outfit | Text fallback, traditional |
| C15 | P2 | "A light blue linen shirt with the sleeves rolled up" | Text | Top only | Text fallback, casual |
| C16 | P3 | "A black formal suit with a white shirt and a thin black tie" | Text | Whole outfit | Text fallback, seated, formal |
| C17 | P1 | G6 + "in deep green" | Photo and text | Whole outfit | Does the text change the colour without losing the border? |
| C18 | P5 | G3 | Photo | Top only, background **not** kept | Does the scene change when allowed to? |
| C19 | P1 | G1 | Photo | Whole outfit, same seed twice | Reproducible: identical result? |
| C20 | P1 | G7 | Photo | Whole outfit, with Wi-Fi off | Works offline |

## What to record for each result

Score 1 to 5 (5 = can't tell it was edited), or yes/no where marked.

| Measure | How to judge |
| --- | --- |
| Identity kept | Same face and person. Compare side by side with the original; ask someone who knows the person if possible |
| Body and pose kept | Same body shape, limbs, hands and posture. Note any extra or missing fingers |
| Garment matches | Colour, pattern, fabric, neckline, sleeves, length and border match the garment photo (or the description) |
| Fit and drape | Folds, hem and how the fabric falls look physically plausible |
| Rest unchanged | Other clothing (for top or bottom only), background, lighting and framing |
| Artefacts | Seams, smears, text garbling, skin where there should be cloth (yes/no and note) |
| Time | `generation_s` from the job's metrics, plus model load time on the first run |
| Peak memory | `gpu_peak_bytes` from the metrics, and Activity Monitor's memory pressure (green/yellow/red) |
| Failed | Did the job fail? Copy the plain message shown |

Results table (one row per case and engine):

| Case | Engine | Identity | Body/pose | Garment | Fit | Rest unchanged | Artefacts | Time (s) | Peak memory | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | | | | | | | | | | |

## Comparison: general editing vs a dedicated try-on model

The recommended engine is general reference editing (see [outfit.md](outfit.md)).
Here "general editing" means FLUX.2 klein 4B on mflux (4-bit), the I1 engine. A
dedicated try-on model is compared only if the owner decides its licence is
acceptable for this test (most are non-commercial).

- Run C1–C13 with both engines on the same photos and seeds.
- For the try-on model, note whether it needed a garment-area mask and how that
  mask was made (drawn by hand or from a segmentation model).
- A dedicated model is worth adding only if it is clearly better on identity,
  garment match or fit in at least half the cases, fits in memory alongside the
  app, and its licence is accepted.

## Pass bar for the I3 gate (proposal for the owner)

- No job crashes the app or the worker, and failures show a plain message.
- Identity kept scores 4 or more in at least 80% of cases.
- Garment matches scores 3 or more in at least 70% of photo cases, including at
  least 3 of the 5 long or traditional Indian garments (C7–C12).
- The text fallback (C14–C16) gives a usable result in at least 2 of 3 cases.
- C19 is reproducible and C20 runs offline.
- Peak memory stays out of the red on the 16 GB M2.

These thresholds are a starting point. The owner sets the final bar.
