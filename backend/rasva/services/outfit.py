"""Phase I3 "Change outfit": build an ``image.edit`` request from a person photo
and either a garment photo or a description of the garment.

This module is pure (no database, no engine imports) so the wording and the
validation are easy to test. ``api/outfit.py`` looks the assets up and creates
the job. See docs/engines/outfit.md for why this uses general reference editing
rather than a dedicated try-on model.

Rules kept here, in one place:

- Reference order is fixed: the person is image 1 and the garment photo image 2.
  The editing engine (FLUX.2 klein on mflux) receives the photos in that order,
  and the instruction names them by it. No garment mask: klein works out the
  garment area itself (docs/engines/outfit.md).
- The instruction always asks the engine to keep face, identity, hair, skin
  tone, body shape, hands and pose. The wording is a first draft: it has not
  been tried on a real model yet (docs/engines/outfit-test-plan.md).
- A garment photo and a description may both be given. The photo is then the
  garment, and the description only adds details ("in maroon", "full sleeves").
  The job records which source was used.
- The user's description goes into the instruction but never into logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WORKFLOW = "outfit"
# Bump when the instruction wording changes, so results can be compared fairly.
INSTRUCTION_VERSION = 1
MAX_DESCRIPTION_CHARS = 300
MIN_DESCRIPTION_CHARS = 3
USABLE_KINDS = ("reference",)  # same rule as services/references.py
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

# Garment type -> how the instruction names it, and what else to leave alone.
GARMENT_TYPES: dict[str, tuple[str, str]] = {
    "auto": ("clothing", ""),
    "top": ("top (the upper-body garment)", "Leave the lower-body clothing as it is."),
    "bottom": ("lower-body garment", "Leave the upper-body clothing as it is."),
    "full": ("whole outfit", ""),
}
GARMENT_TYPE_LABELS = {
    "auto": "Whatever the garment is",
    "top": "Top only",
    "bottom": "Bottom only",
    "full": "Whole outfit (saree, dress, suit)",
}

KEEP_PERSON = (
    "Keep the person's face, identity, expression, hair, skin tone, body shape, "
    "hands and pose exactly as in image 1."
)
KEEP_SCENE = "Keep the background, lighting and framing of image 1 unchanged."
NO_EXTRAS = "Do not add text, logos or extra people."


class OutfitError(ValueError):
    """A plain-language problem with the request, safe to show to the user."""


@dataclass(frozen=True)
class OutfitPlan:
    """What the job gets: the engine inputs and a record of how they were made."""

    prompt: str
    references: list[dict[str, str]]  # [{asset_id, role}], in instruction order
    workflow: dict[str, Any]

    def inputs(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "references": [dict(r) for r in self.references]}


def clean_description(text: str | None) -> str:
    """Collapse whitespace and drop control characters; '' when nothing is left."""
    if not text:
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return " ".join(printable.split()).rstrip(" .")


def check_photo(asset: dict[str, Any] | None, project_id: str, what: str) -> dict[str, Any]:
    """The asset must exist, belong to this project and be a picture."""
    if asset is None or asset.get("project_id") != project_id:
        # Same message for "missing" and "another project's": don't confirm it exists.
        raise OutfitError(f"The {what} is not in this project. Add it here first.")
    if asset.get("kind") not in USABLE_KINDS or asset.get("media_type") not in IMAGE_TYPES:
        raise OutfitError(f"The {what} must be a PNG, JPEG or WebP photo added to this project.")
    return asset


def plan(
    *,
    project_id: str,
    person: dict[str, Any] | None,
    garment: dict[str, Any] | None = None,
    description: str | None = None,
    garment_type: str = "auto",
    keep_background: bool = True,
) -> OutfitPlan:
    """Check the choices and build the instruction and references.

    ``person`` and ``garment`` are asset rows (or None). Raise
    OutfitError with a message a non-technical user can act on.
    """
    if person is None:
        raise OutfitError("Choose a photo of the person first.")
    person = check_photo(person, project_id, "person photo")
    text = clean_description(description)
    if garment is None and not text:
        raise OutfitError("Add a photo of the garment, or describe it in words.")
    if text and len(text) < MIN_DESCRIPTION_CHARS:
        raise OutfitError("Describe the garment in a few more words.")
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise OutfitError(
            f"Keep the garment description under {MAX_DESCRIPTION_CHARS} characters. "
            "Colour, fabric, length and sleeves are what matter most."
        )
    if garment is not None:
        garment = check_photo(garment, project_id, "garment photo")
        if garment["id"] == person["id"]:
            raise OutfitError("Choose a different photo for the garment than for the person.")
    if garment_type not in GARMENT_TYPES:
        raise OutfitError("Choose what to change: the top, the bottom, or the whole outfit.")

    references = [{"asset_id": person["id"], "role": "person"}]
    if garment is not None:
        references.append({"asset_id": garment["id"], "role": "garment"})

    source = "photo_and_text" if garment is not None and text else "photo" if garment else "text"
    prompt = instruction(
        source=source, description=text, garment_type=garment_type, keep_background=keep_background
    )
    workflow = {
        "name": WORKFLOW,
        "instruction_version": INSTRUCTION_VERSION,
        "garment_source": source,
        "garment_type": garment_type,
        "keep_background": keep_background,
        "person_asset_id": person["id"],
        "garment_asset_id": garment["id"] if garment else None,
    }
    return OutfitPlan(prompt=prompt, references=references, workflow=workflow)


def instruction(*, source: str, description: str, garment_type: str, keep_background: bool) -> str:
    """The editing instruction. Image 1 is always the person, image 2 the garment photo."""
    thing, leave_rest = GARMENT_TYPES[garment_type]
    if source == "text":
        change = (
            f"Change the {thing} worn by the person in image 1 to: {description}. "
            "Fit it naturally to their body, with realistic folds and drape."
        )
    else:
        change = (
            f"Dress the person in image 1 in the {thing} shown in image 2. "
            "Copy its colour, pattern, fabric, neckline, sleeves and length from image 2, "
            "and fit it naturally to their body, with realistic folds and drape. "
            "Ignore the person, mannequin or background in image 2."
        )
        if source == "photo_and_text":
            change += f" Garment details: {description}."
    parts = [change, leave_rest, KEEP_PERSON, KEEP_SCENE if keep_background else "", NO_EXTRAS]
    return " ".join(p for p in parts if p)
