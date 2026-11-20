# ADR-0008: Image exports: PNG on the Mac with the standard library, JPEG in the browser

- Status: Proposed (needs owner review)
- Date: 2026-11-20
- PRD refs: §19 (I4), §34.2 ("Before/after, versions and PNG/JPEG exports work")

## Context

Image Studio needs PNG and JPEG downloads, with the option to remove generation
details. Engines save bare PNGs, and the details (engine, model revision, seed,
`synthetic: true`) are stored with the asset, not in the file. The app's own
Python environment has no image library. Pillow is only installed in engine
environments, which the API process does not import.

## Options

| Option | For | Against |
| --- | --- | --- |
| Add pinned Pillow to the app environment | One encoder for every client. Server-side tests | Adds a native dependency and its security updates to the always-running process, only to encode JPEG. Encoding uses the API process's CPU and memory while an engine may be running |
| Encode JPEG in an engine worker | Pillow is already there | A worker is single-purpose and may hold a 9 GB model. An export would queue behind generation or evict the engine |
| **Encode JPEG in the browser (canvas)** | No new dependency. Works on the Mac and paired phones. Costs the Mac nothing. Canvas output has no metadata | The browser's encoder decides the bytes, so files are not identical across browsers. No server-side test of JPEG bytes |

## Decision

- **PNG export is server-side, standard library only**
  (`backend/rasva/services/image_export.py`). It reads the PNG chunks and always
  removes text, EXIF and time chunks (`tEXt`, `zTXt`, `iTXt`, `eXIf`, `tIME`).
  With details on (the default), it adds `tEXt` chunks for `Software`, `Source`
  (engine and model revision) and `Comment` ("AI-generated image (synthetic) …
  Seed N"). The pixels are not changed. With details off, the file has no text
  at all. Endpoint: `GET /api/v1/assets/{id}/export?details=true|false`.
- **JPEG is encoded in the browser** with `canvas.toBlob("image/jpeg", quality)`.
  Quality runs from 50 to 100 and defaults to 90. Transparency is flattened onto
  white. With details on, a JPEG comment (COM) segment with the same label
  (from `GET /api/v1/assets/{id}/provenance`) is inserted after the start
  marker. With details off, the canvas output already has no metadata.
- **The description (prompt) is never written into an exported file.** It is
  private and stays in Rasva's database.

## Consequences

- Turning details off removes the "AI-generated" label from the file. The UI
  says so next to the checkbox. Whether to allow that is the owner's call under
  the engine licences. The default keeps the label.
- JPEG bytes are not reproducible across browsers. The PNG remains the exact
  original.
- If server-side JPEG becomes necessary (for example batch export), revisit
  with a hash-pinned Pillow in the app lockfile.
