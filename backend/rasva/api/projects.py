from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.services import assets as asset_svc
from rasva.services import projects as svc

router = APIRouter(tags=["projects"])

MAX_SETTINGS_BYTES = 256 * 1024  # scripts up to A1's limit, with room to spare


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    module: Literal["audio", "image"]
    description: str = Field(default="", max_length=2000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None
    # Merged into existing settings (e.g. the autosaved script draft).
    settings: dict[str, Any] | None = None


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, state: State, conn: DB) -> dict[str, Any]:
    return svc.create(conn, state.settings.paths, **body.model_dump())


@router.get("/projects")
def list_projects(
    conn: DB,
    module: Literal["audio", "image"] | None = None,
    status: Literal["active", "archived"] | None = "active",
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    return svc.list_projects(conn, module=module, status=status, query=q, limit=limit)


@router.get("/projects/{project_id}")
def get_project(project_id: str, conn: DB) -> dict[str, Any]:
    return svc.get(conn, project_id)


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, conn: DB) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if "settings" in fields:
        if len(json.dumps(fields["settings"])) > MAX_SETTINGS_BYTES:
            raise ApiError(413, "too_large", "Project settings are too large to save.")
        fields["settings"] = {**svc.get(conn, project_id)["settings"], **fields["settings"]}
    return svc.update(conn, project_id, **fields)


@router.post("/projects/{project_id}/assets", status_code=201)
def upload_asset(
    project_id: str,
    state: State,
    conn: DB,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["reference", "import"], Form()] = "reference",
) -> dict[str, Any]:
    svc.get(conn, project_id)  # 404 if missing
    asset = asset_svc.store_upload(
        conn,
        state.settings.paths,
        project_id=project_id,
        kind=kind,
        original_name=file.filename or "upload",
        stream=file.file,
        max_bytes=state.settings.max_upload_bytes,
        preflight=lambda n: state.preflight(conn, n, "upload"),
    )
    svc.touch(conn, project_id)
    state.storage.invalidate()
    return asset


@router.get("/projects/{project_id}/assets")
def list_assets(
    project_id: str,
    conn: DB,
    kind: Literal["reference", "import", "output"] | None = None,
) -> list[dict[str, Any]]:
    svc.get(conn, project_id)
    return asset_svc.list_assets(conn, project_id, kind)
