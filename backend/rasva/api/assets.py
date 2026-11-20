from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import FileResponse

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.services import assets as svc

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
