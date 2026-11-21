"""Image versions, favourites, "make another version" and PNG export (PRD phase I4).

Paired devices may use all of these (they are normal studio use); cleanup and
storage settings stay Mac-only elsewhere.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.api.jobs import _resolve_references, _validate
from rasva.jobs import repository as repo
from rasva.jobs.states import TERMINAL, JobState
from rasva.services import assets as asset_svc
from rasva.services import image_export
from rasva.services import images as svc
from rasva.services import projects as project_svc
from rasva.services.presets import PresetError, apply_preset

router = APIRouter(tags=["images"])

MAX_PROMPT = 1_000


class FavouriteBody(BaseModel):
    favourite_job_id: str | None


class AgainBody(BaseModel):
    # "new": a variation with a fresh seed; "same": try again with the same seed.
    seed: Literal["new", "same"] = "new"
    # Optional edits; anything left out is copied from the version.
    prompt: str | None = Field(default=None, max_length=MAX_PROMPT)
    negative_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)


def _version_error(exc: svc.VersionError) -> ApiError:
    return ApiError(422, "invalid_version", str(exc))


@router.get("/projects/{project_id}/image-groups")
def list_groups(project_id: str, conn: DB) -> list[dict[str, Any]]:
    project_svc.get(conn, project_id)
    return svc.list_groups(conn, project_id)


@router.get("/image-groups/{group_id}")
def get_group(group_id: str, conn: DB) -> dict[str, Any]:
    try:
        return svc.get_group(conn, group_id)
    except svc.VersionError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc


@router.patch("/image-groups/{group_id}")
def update_group(group_id: str, body: FavouriteBody, conn: DB) -> dict[str, Any]:
    try:
        return svc.set_favourite(conn, group_id, body.favourite_job_id)
    except svc.VersionError as exc:
        raise _version_error(exc) from exc


# I3 outfit: which garment source was used (jobs.retry_job keeps it too).
KEPT_KEYS = ("workflow",)


@router.post("/jobs/{job_id}/again", status_code=201)
def make_version(job_id: str, body: AgainBody, state: State, conn: DB) -> dict[str, Any]:
    """A new version of an image: same request, new or same seed, optionally edited text."""
    old = repo.get(conn, job_id)
    if not svc.is_image_task(old["task"]):
        raise ApiError(422, "unsupported_task", "Only images can be made again.")
    if JobState(old["state"]) not in TERMINAL:
        raise ApiError(409, "still_running", "Wait for this image to finish first.")
    engine = state.registry.get(old["engine_id"])
    if engine is None:
        raise ApiError(422, "engine_unavailable", "That engine is not available.")
    # Records the engine does not validate travel with the version, as on retry.
    kept = {k: old["inputs"][k] for k in KEPT_KEYS if k in old["inputs"]}
    raw = {k: v for k, v in old["inputs"].items() if k not in svc.ADDED_KEYS and k not in KEPT_KEYS}
    if body.seed == "new":
        raw.pop("seed", None)  # validate_input picks and stores a fresh one
    if body.prompt is not None:
        raw["prompt"] = body.prompt
    if body.negative_prompt is not None:
        raw["negative_prompt"] = body.negative_prompt
    extras: dict[str, Any] = dict(kept)
    if raw.get("preset"):
        try:
            extras["preset"] = raw["preset"]
            raw = apply_preset(engine.descriptor, raw)
        except PresetError as exc:
            raise ApiError(422, "invalid_preset", str(exc)) from exc
    # Same reference checks as POST /jobs and retry: photos may have been deleted since.
    raw = _resolve_references(state, conn, old["project_id"], raw)
    extras.update(svc.engine_extras(conn, engine.descriptor))
    inputs = {**_validate(state, conn, old["engine_id"], old["task"], raw), **extras}
    job = repo.create(
        conn, project_id=old["project_id"], task=old["task"], engine_id=old["engine_id"],
        inputs=inputs,
    )  # fmt: skip
    svc.assign(conn, job, version_of=job_id)
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job


@router.get("/assets/{asset_id}/provenance")
def provenance(asset_id: str, state: State, conn: DB) -> dict[str, Any]:
    asset = asset_svc.get(conn, asset_id)
    return {"details": image_export.provenance(asset, _engine_name(state, asset))}


@router.get("/assets/{asset_id}/export")
def export_png(asset_id: str, state: State, conn: DB, details: bool = True) -> Response:
    """PNG download. ``details=false`` strips every text and metadata chunk."""
    asset = asset_svc.get(conn, asset_id)
    if asset["media_type"] != "image/png":
        raise ApiError(422, "not_png", "Only PNG images can be exported here.")
    path = asset_svc.resolve(state.settings.paths, asset["rel_path"])
    if not path.is_file():
        raise ApiError(410, "file_missing", "This file is no longer on disk.")
    label = image_export.provenance(asset, _engine_name(state, asset)) if details else None
    try:
        data = image_export.export_png(path.read_bytes(), label)
    except image_export.ExportError as exc:
        raise ApiError(422, "bad_image", str(exc)) from exc
    name = image_export.export_name(asset, "png")
    return Response(
        data,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


def _engine_name(state: Any, asset: dict[str, Any]) -> str | None:
    engine_id = (asset.get("metadata") or {}).get("engine")
    engine = state.registry.get(engine_id) if engine_id else None
    return engine.descriptor.display_name if engine else None
