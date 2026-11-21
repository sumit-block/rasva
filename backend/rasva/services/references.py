"""Reference photos for image editing (PRD §16, I1–I3).

A reference is an image asset uploaded to an image project with a role. Jobs name
references as ``[{asset_id, role}]``; :func:`resolve_inputs` checks each one and
adds the file path the engine reads. It always derives the path from the asset id,
so a retry re-checks references that may have been deleted since.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rasva.config import Paths
from rasva.services import assets as asset_svc
from rasva.services import projects as project_svc

ROLES = ("person", "garment", "pose", "background")
# Each reference adds image tokens to every step; more than this does not fit a
# 16 GB Mac comfortably (PRD §24).
MAX_REFERENCES = 4


class PhotoReferenceError(ValueError):
    """User-facing problem with the chosen reference photos."""


def check_role(role: str | None) -> str | None:
    if role in (None, ""):
        return None
    if role not in ROLES:
        raise PhotoReferenceError(f"Unknown photo role {role!r}. Use one of: {', '.join(ROLES)}.")
    return role


def check_upload(
    conn: sqlite3.Connection, project_id: str, kind: str, role: str | None
) -> str | None:
    """Validate a role given with an upload; only image-project references have one."""
    role = check_role(role)
    if role is None:
        return None
    if kind != "reference":
        raise PhotoReferenceError("Only reference photos have a role.")
    if project_svc.get(conn, project_id)["module"] != "image":
        raise PhotoReferenceError("Photo roles are for image projects.")
    return role


def resolve_inputs(
    conn: sqlite3.Connection, paths: Paths, project_id: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Turn ``inputs.references`` into ``[{asset_id, role, path}]`` an engine can read
    (plus ``pose_figure: true`` for pose-finder figures, I2)."""
    refs = inputs.get("references")
    if refs is None:
        return inputs
    if not isinstance(refs, list) or not refs:
        raise PhotoReferenceError("Choose at least one photo.")
    if len(refs) > MAX_REFERENCES:
        raise PhotoReferenceError(f"Use at most {MAX_REFERENCES} photos at once.")
    out, seen = [], set()
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("asset_id"):
            raise PhotoReferenceError("Each photo must be one of this project's uploads.")
        asset_id = str(ref["asset_id"])
        if asset_id in seen:
            raise PhotoReferenceError("The same photo was chosen twice.")
        seen.add(asset_id)
        try:
            asset = asset_svc.get(conn, asset_id)
        except asset_svc.AssetNotFound as exc:
            raise PhotoReferenceError("A chosen photo was deleted. Choose it again.") from exc
        # I2: a stick figure made by the pose finder is an output asset; it can be
        # used again as a pose reference.
        figure = asset["kind"] == "output" and asset["metadata"].get("kind") == "pose"
        if asset["project_id"] != project_id or not (asset["kind"] == "reference" or figure):
            raise PhotoReferenceError("Each photo must be one of this project's uploads.")
        if not str(asset["media_type"]).startswith("image/"):
            raise PhotoReferenceError(f"{asset['original_name']} is not a photo.")
        role = check_role(ref.get("role")) or asset["metadata"].get("role") or "person"
        if figure and role != "pose":
            raise PhotoReferenceError("A pose figure can only be used as the pose.")
        path = asset_svc.resolve(paths, asset["rel_path"])
        if not Path(path).is_file():
            name = asset["original_name"]
            raise PhotoReferenceError(f"{name} is missing from disk. Upload it again.")
        entry = {"asset_id": asset_id, "role": role, "path": str(path)}
        if figure:
            entry["pose_figure"] = True
        out.append(entry)
    return {**inputs, "references": out}
