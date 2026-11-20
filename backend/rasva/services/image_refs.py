"""Reference photos for image editing jobs (shared I1-I4 contract, PRD §19).

A job's raw inputs carry ``references: [{asset_id, role}]``. Before the engine's
``validate_input`` runs, the jobs API swaps each entry for
``{asset_id, role, path}`` so the engine (in its worker) can open the file.
``asset_id`` stays in the stored inputs, so Retry can resolve the files again
and outputs can record which assets they came from.

Phase I2 note: I1 (reference-guided editing) owns this contract and is being
built in another branch. This module is the I2 branch's stand-in so pose jobs
work end to end; on merge keep I1's resolver and check it also accepts
generated pose figures (``output`` assets with ``metadata.kind == "pose"``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rasva.config import Paths
from rasva.services import assets as asset_svc

ROLES = ("person", "garment", "pose", "background")
MAX_REFERENCES = 4


class ReferenceError(ValueError):
    """A reference problem with a message safe to show users."""


def role_for_upload(role: str | None) -> dict[str, Any]:
    """Asset metadata for an uploaded reference photo with an optional role."""
    if role in (None, ""):
        return {}
    if role not in ROLES:
        raise ReferenceError(f"Unknown photo role '{role}'. Use one of: {', '.join(ROLES)}.")
    return {"role": role}


def resolve(
    conn: sqlite3.Connection, paths: Paths, project_id: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Return inputs with ``references`` resolved to local files."""
    refs = inputs.get("references")
    if refs in (None, []):
        return inputs
    if not isinstance(refs, list) or len(refs) > MAX_REFERENCES:
        raise ReferenceError(f"Use between 1 and {MAX_REFERENCES} reference photos.")
    resolved = []
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("asset_id"), str):
            raise ReferenceError("Each reference needs a photo.")
        role = ref.get("role")
        if role not in ROLES:
            raise ReferenceError(f"Unknown photo role '{role}'.")
        try:
            asset = asset_svc.get(conn, ref["asset_id"])
        except asset_svc.AssetNotFound as exc:
            raise ReferenceError("A reference photo was deleted. Choose it again.") from exc
        if asset["project_id"] != project_id:
            raise ReferenceError("Reference photos must belong to this project.")
        if not str(asset["media_type"]).startswith("image/"):
            raise ReferenceError("Reference files must be pictures (PNG, JPEG or WebP).")
        path = asset_svc.resolve(paths, asset["rel_path"])
        if not Path(path).is_file():
            raise ReferenceError("A reference photo is missing from disk. Upload it again.")
        resolved.append({"asset_id": asset["id"], "role": role, "path": str(path)})
    return {**inputs, "references": resolved}


def unresolve(inputs: dict[str, Any]) -> dict[str, Any]:
    """Stored inputs -> raw inputs for Retry (drop paths, keep asset ids and roles)."""
    refs = inputs.get("references")
    if not isinstance(refs, list):
        return inputs
    raw = [{"asset_id": r.get("asset_id"), "role": r.get("role")} for r in refs if isinstance(r, dict)]
    return {**inputs, "references": raw}
