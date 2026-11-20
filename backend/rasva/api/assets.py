from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.services import assets as svc
from rasva.services import references as reference_svc

router = APIRouter(tags=["assets"])


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, conn: DB) -> dict[str, Any]:
    return svc.get(conn, asset_id)


@router.get("/assets/{asset_id}/content")
def asset_content(asset_id: str, state: State, conn: DB, download: bool = False) -> FileResponse:
    asset = svc.get(conn, asset_id)
    path = svc.resolve(state.settings.paths, asset["rel_path"])
    if not path.is_file():
        raise ApiError(410, "file_missing", "This file is no longer on disk.")
    return FileResponse(
        path,
        media_type=asset["media_type"],
        filename=asset["original_name"],
        content_disposition_type="attachment" if download else "inline",
    )


class AssetUpdate(BaseModel):
    role: str | None = None


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, body: AssetUpdate, conn: DB) -> dict[str, Any]:
    """Change a reference photo's role (person, garment, pose, background)."""
    asset = svc.get(conn, asset_id)
    try:
        role = reference_svc.check_upload(conn, asset["project_id"], asset["kind"], body.role)
    except reference_svc.PhotoReferenceError as exc:
        raise ApiError(422, "invalid_reference", str(exc)) from exc
    if role is None:
        raise ApiError(422, "invalid_reference", "Choose a role for this photo.")
    return svc.set_metadata(conn, asset_id, role=role)
