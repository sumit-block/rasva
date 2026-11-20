"""Storage cleanup API (PRD §23). Owner-only: runs on the Mac, never from a paired device."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from rasva.api.deps import DB, LocalOnly, State
from rasva.api.errors import ApiError
from rasva.services import cleanup

router = APIRouter(tags=["storage"], dependencies=[LocalOnly])


class CleanupRequest(BaseModel):
    groups: list[str]


@router.get("/storage/cleanup")
def preview(state: State, conn: DB) -> dict[str, Any]:
    return cleanup.plan(conn, state.settings.paths).summary()


@router.post("/storage/cleanup")
def run(body: CleanupRequest, state: State, conn: DB) -> dict[str, Any]:
    if not body.groups:
        raise ApiError(422, "nothing_selected", "Choose what to clean up.")
    try:
        result = cleanup.execute(conn, state.settings.paths, body.groups)
    except ValueError as exc:
        raise ApiError(422, "invalid_cleanup", str(exc)) from exc
    state.storage.invalidate()
    return {**result, **cleanup.plan(conn, state.settings.paths).summary()}
