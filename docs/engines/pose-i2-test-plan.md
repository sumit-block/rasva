# I2 test plan: same person, new pose

Status: **not run yet.** Run this once the I2 engines are installed on the M2
(see [pose-control.md](pose-control.md) for what to install). Nothing in this
file is a result; fill in the tables as you go.

The goal of PRD phase I2 is that a person in a reference photo can be shown in a
different pose and still be recognisably the same person. These checks cover
the PRD's I2 cases: front and three-quarter views, seated and standing,
portrait and full-body framing, and a range of body shapes, ages, skin tones
and clothing silhouettes.

## Before you start

1. Install the I1 editing engine (FLUX.2 Klein 4B) and the pose extractor from
   Models. Confirm both licences first.
2. Use the dev home, not the real one, if you are testing a branch:
   `RASVA_HOME=<worktree>/.rasva-dev RASVA_PORT=8783 RASVA_LAN_PORT=8784`.
3. Close other heavy apps. Note free memory (Activity Monitor → Memory) before
   the first job.
4. Use photos you have the right to use: your own, family members who agreed,
   or openly licensed photos with the licence noted. Never test with photos of
   strangers or public figures. Keep test photos out of the repository.

## Test photos (the "person" set)

Pick at least 8 person photos so the set covers, between them:

| Need | Covered by photo # |
| --- | --- |
| Front view, full body |  |
| Three-quarter view |  |
| Head and shoulders only (portrait) |  |
| Seated |  |
| Light, medium and dark skin tones (at least one each) |  |
| A child or teenager (with a guardian's consent) and an older adult |  |
| Larger and smaller body shapes |  |
| Loose clothing (sari, kurta, dress, coat) and fitted clothing |  |
| Glasses, head covering, or long hair |  |
| A busy background and a plain one |  |

## Target poses

Use the seven built-in figures (`frontend/public/poses/`; note "seated" is drawn from the side, so also use a front-view seated pose photo) plus two pose photos
of your own:

- standing-front, three-quarter, hands-on-hips, waving, walking, seated, portrait
- one pose photo of a different person (tests extraction from a real photo)
- one pose photo with a partly hidden body (a desk or a crop at the waist)

## 1. Pose extraction (pose photo → stick figure)

For each pose photo:

| # | Photo | Figure looks right? | Joints missed | Framing reported | Time (s) | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | Full body, standing |  |  |  |  |  |
| E2 | Seated |  |  |  |  |  |
| E3 | Three-quarter |  |  |  |  |  |
| E4 | Waist-up crop |  |  |  |  |  |
| E5 | No person (a landscape) | expect "no person found" error, no file |  |  |  |  |
| E6 | Two people | expect the larger person only, and a note |  |  |  |  |

Pass: E1–E4 figures match the photo by eye; E5 fails with a plain message and
saves nothing; E6 picks one person and says so.

## 2. Pose change (person + pose → new image)

Run each person photo against at least four target poses (36+ images). For
every image record:

| Column | How to judge |
| --- | --- |
| Pose followed | 2 = matches the figure, 1 = roughly, 0 = ignored |
| Same person | 2 = clearly the same person to someone who knows them, 1 = similar, 0 = a different person |
| Face intact | 2 = no distortion, 1 = minor, 0 = broken |
| Hands and limbs | 2 = correct count and shape, 1 = minor, 0 = broken |
| Clothing kept | 2 = same clothes, 1 = similar, 0 = changed |
| Skin tone kept | yes / no (any lightening or darkening is a failure, note it) |
| Time (s), seed | from the job details |

| Person # | Target pose | Identity setting | Pose | Same person | Face | Hands | Clothing | Skin tone | Time | Seed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |  |

Cases that must each appear at least twice:

- front → three-quarter, and three-quarter → front
- standing → seated, and seated → standing
- portrait photo → full-body pose (the model must invent the body: note
  whether clothing is plausible) and full-body photo → portrait pose
- each skin-tone group, each age group, loose and fitted clothing

## 3. Identity settings

For two person photos and two poses, run all three identity settings
(Keep the face closely / Balanced / Follow the pose closely) with the **same
seed**. Pass: the order is visible (closer identity on the first, closer pose
on the last), and no setting breaks the face.

## 3b. Stick figure or pose photo

For three person/pose pairs, run once with the pose sent as the stick figure
and once as the original pose photo (same seed), without the pose LoRA, and
again with it if installed. Record which follows the pose better and whether
the photo version copies anything (face, clothes) from the pose photo's person.
This decides the default `pose_source` and whether the LoRA is needed.

## 4. Repeatability, cancel, failure

| Check | Pass when |
| --- | --- |
| Same inputs and seed twice | byte-identical PNG, or visually identical (note which) |
| Cancel during generation | stops within one step; no file saved; job shows Canceled |
| Retry a failed or canceled job | same seed and same reference photos are reused |
| Delete the person photo, then Retry | plain "photo was deleted" message, no crash |
| Pose figure reused in a second job | works without extracting again |
| Network off (Wi-Fi off) | extraction and generation both work |
| Logs | grep engine logs and app log for the prompt text: nothing found |

## 5. Memory and time on the M2

| Measure | Pose extractor | Pose change (Klein 4B) |
| --- | --- | --- |
| Model load, cold (s) |  |  |
| Time per job, median and range (s) |  |  |
| Peak GPU memory (`gpu_peak_bytes`) |  |  |
| Worker RSS peak |  |  |
| Installed size |  |  |
| Audio still works after (Kokoro cold start) |  |  |

## 6. Gate

I2 passes when, across the photo set:

- pose followed ≥ 1 on at least 80 % of images and = 2 on at least half;
- same person = 2 on at least 70 % of images, and no skin-tone shift;
- no group (skin tone, age, body shape, clothing) does clearly worse than the
  others. If one does, record it as a known limit in the Image Studio help
  text before release;
- every failure case in §1 and §4 behaves as described.

Write the results into `docs/engines/pose-control.md` (Measurements section)
with the date, versions and model revisions, the way `sd15.md` does.
