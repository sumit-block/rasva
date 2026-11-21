"""Phase I3 "Change outfit" (PRD I3): one endpoint that turns a person photo plus a
garment photo or description into an ``image.edit`` job.

The wording and checks live in ``services/outfit.py``. This module looks the
assets up, picks an editing engine, resolves the references the same way
``POST /jobs`` does, and creates the job with a ``workflow`` record (which
garment source was used). ``retry_job`` keeps that record.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.api.jobs import _resolve_references, _validate
from rasva.engines.registry import RegisteredEngine
from rasva.jobs import repository as repo
from rasva.services import assets as asset_svc
from rasva.services import outfit as svc
from rasva.services import projects as project_svc
from rasva.services.installer import ModelStore
from rasva.services.presets import PresetError, apply_preset

router = APIRouter(tags=["image"])

TASK = "image.edit"


class OutfitCreate(BaseModel):
    person_asset_id: str = Field(min_length=1, max_length=64)
    garment_asset_id: str | None = Field(default=None, max_length=64)
    # Generous here; the plain-language limit is checked in services/outfit.py.
    garment_description: str | None = Field(default=None, max_length=5_000)
    garment_type: str = "auto"
    keep_background: bool = True
    engine_id: str | None = None
    preset: str | None = None
    seed: int | None = None
    steps: int | None = None
    guidance: float | None = None


def _asset(conn: sqlite3.Connection, asset_id: str | None) -> dict[str, Any] | None:
    if not asset_id:
        return None
    try:
        return asset_svc.get(conn, asset_id)
    except asset_svc.AssetNotFound:
        return None


def _engine(state: State, conn: sqlite3.Connection, engine_id: str | None) -> RegisteredEngine:
    if engine_id is not None:
        engine = state.registry.get(engine_id)
        if engine is None or TASK not in engine.descriptor.tasks:
            raise ApiError(422, "engine_unavailable", "That engine cannot change outfits.")
        return engine
    store = ModelStore(conn)
    for engine in state.registry.for_task(TASK):
        if not engine.descriptor.requires_install or store.is_installed(engine.descriptor.id):
            return engine
    raise ApiError(
        422,
        "no_engine",
        "Changing outfits needs an image editing engine. Install one from Models first.",
    )


@router.post("/projects/{project_id}/outfit", status_code=201)
def change_outfit(project_id: str, body: OutfitCreate, state: State, conn: DB) -> dict[str, Any]:
    project_svc.get(conn, project_id)  # 404 if missing
    person = _asset(conn, body.person_asset_id)
    garment = _asset(conn, body.garment_asset_id)
    if body.garment_asset_id and garment is None:
        raise ApiError(422, "invalid_input", "The garment photo is not in this project.")
    try:
        plan = svc.plan(
            project_id=project_id, person=person, garment=garment,
            description=body.garment_description, garment_type=body.garment_type,
            keep_background=body.keep_background,
        )  # fmt: skip
    except svc.OutfitError as exc:
        raise ApiError(422, "invalid_input", str(exc)) from exc

    engine = _engine(state, conn, body.engine_id)
    raw: dict[str, Any] = plan.inputs()
    for key in ("seed", "steps", "guidance"):
        if getattr(body, key) is not None:
            raw[key] = getattr(body, key)
    extras: dict[str, Any] = {"workflow": plan.workflow}
    if body.preset:
        try:
            raw = apply_preset(engine.descriptor, {**raw, "preset": body.preset})
        except PresetError as exc:
            raise ApiError(422, "invalid_preset", str(exc)) from exc
        extras["preset"] = body.preset
    raw = _resolve_references(state, conn, project_id, raw)
    inputs = {**_validate(state, conn, engine.descriptor.id, TASK, raw), **extras}
    job = repo.create(
        conn, project_id=project_id, task=TASK, engine_id=engine.descriptor.id, inputs=inputs
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job
