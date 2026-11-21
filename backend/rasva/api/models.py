"""Engine catalogue, install and removal (P0-013).

Install, software update and remove are queued jobs (task ``model.install`` /
``model.update`` / ``model.remove``), so they share the single-job queue,
progress stream, cancel and retry.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from rasva.api.deps import DB, AppState, State
from rasva.api.errors import ApiError
from rasva.engines.registry import RegisteredEngine, load_class
from rasva.jobs import repository as repo
from rasva.jobs.states import TERMINAL
from rasva.services import system_check
from rasva.services.installer import Installer, InstallError, ModelStore, check_source_url

router = APIRouter(tags=["models"])

NEEDS_CONFIRMATION = {"restricted", "unknown"}


class InstallRequest(BaseModel):
    # Required for restricted or unverified licences (PRD §2, §28).
    accept_license: bool = False


def _engine(state: AppState, engine_id: str) -> RegisteredEngine:
    engine = state.registry.get(engine_id)
    if engine is None:
        raise ApiError(404, "model_not_found", "That engine does not exist.")
    return engine


def _open_jobs(conn: sqlite3.Connection, engine_id: str) -> list[dict[str, Any]]:
    open_states = [s for s in repo.JobState if s not in TERMINAL]
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE engine_id = ? AND state IN ({','.join('?' * len(open_states))})"  # noqa: S608
        " ORDER BY created_at",
        [engine_id, *open_states],
    )
    return [repo.row_to_job(r) for r in rows]


def _describe(
    state: AppState, conn: sqlite3.Connection, engine: RegisteredEngine, facts: Any
) -> dict[str, Any]:
    d = engine.descriptor
    record = ModelStore(conn).get(d.id)
    if not d.requires_install:
        install_state = "built_in"
    else:
        install_state = record["install_state"] if record else "not_installed"
    model_job = next((j for j in _open_jobs(conn, d.id) if j["task"].startswith("model.")), None)
    installer = Installer(state.settings, conn, state.registry, state.storage)
    return {
        **d.model_dump(exclude={"artifacts", "requirements_lock"}),
        "download_bytes": sum(a.size_bytes for a in d.artifacts),
        "required_bytes": installer.required_bytes(engine) if d.requires_install else 0,
        "installed_bytes": record["installed_bytes"] if record else 0,
        # A paused (canceled or interrupted) download: installing again resumes it.
        "downloaded_bytes": (
            installer.downloaded_bytes(engine)
            if d.requires_install and install_state == "not_installed"
            else 0
        ),
        "install_state": install_state,
        "install_error": (record or {}).get("metadata", {}).get("error"),
        # The engine's software list changed since it was installed (ADR-0003).
        "software_update_available": installer.software_update_available(engine),
        "needs_license_confirmation": d.license_class in NEEDS_CONFIRMATION,
        "active_job": model_job,
        "compatibility": load_class(engine.import_path)().check_compatibility(facts).model_dump(),
    }


@router.get("/models")
def list_models(state: State, conn: DB) -> list[dict[str, Any]]:
    facts = system_check.facts()
    # Experimental engines (internal test tooling) are never shown to users.
    return [
        _describe(state, conn, e, facts)
        for e in state.registry.all()
        if not e.descriptor.experimental and e.descriptor.modality != "tool"
    ]


@router.get("/models/{engine_id}")
def get_model(engine_id: str, state: State, conn: DB) -> dict[str, Any]:
    return _describe(state, conn, _engine(state, engine_id), system_check.facts())


@router.post("/models/{engine_id}/install", status_code=202)
def install_model(
    engine_id: str, state: State, conn: DB, body: InstallRequest | None = None
) -> dict[str, Any]:
    body = body or InstallRequest()
    engine = _engine(state, engine_id)
    d = engine.descriptor
    if not d.requires_install:
        raise ApiError(409, "built_in", "This engine is built in and needs no installation.")

    for job in _open_jobs(conn, d.id):
        if job["task"] == "model.install":
            return job  # idempotent: already queued or running
        if job["task"] == "model.remove":
            raise ApiError(409, "engine_busy", "This engine is being removed. Try again after.")
        if job["task"] == "model.update":
            raise ApiError(409, "engine_busy", "This engine is being updated. Try again after.")
    if ModelStore(conn).is_installed(d.id):
        raise ApiError(409, "already_installed", "This engine is already installed.")

    compat = load_class(engine.import_path)().check_compatibility(system_check.facts())
    if not compat.compatible:
        raise ApiError(
            409, "incompatible", " ".join(compat.reasons) or "Not supported on this Mac."
        )
    if d.license_class in NEEDS_CONFIRMATION and not body.accept_license:
        raise ApiError(
            409,
            "license_confirmation_required",
            f"{d.display_name} uses the licence “{d.license}”, which may limit how you can use "
            "its results. Review it and confirm to install.",
        )

    try:
        for artifact in d.artifacts:
            check_source_url(artifact.url, state.settings.allow_loopback_http_downloads)
    except InstallError as exc:
        raise ApiError(409, exc.code, exc.user_message) from exc

    installer = Installer(state.settings, conn, state.registry, state.storage)
    try:
        installer.preflight(engine)
    except InstallError as exc:
        raise ApiError(507, exc.code, exc.user_message) from exc

    job = repo.create(
        conn,
        project_id=None,
        task="model.install",
        engine_id=d.id,
        inputs={
            "license_accepted": body.accept_license,
            "required_bytes": installer.required_bytes(engine),
        },
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job


@router.post("/models/{engine_id}/update", status_code=202)
def update_model(engine_id: str, state: State, conn: DB) -> dict[str, Any]:
    """Rebuild only the engine's software environment; model files are kept."""
    engine = _engine(state, engine_id)
    d = engine.descriptor
    if not d.requires_install:
        raise ApiError(409, "built_in", "This engine is built in and is updated with Rasva.")
    open_jobs = _open_jobs(conn, d.id)
    for job in open_jobs:
        if job["task"] == "model.update":
            return job  # idempotent: already queued or running
    if open_jobs:
        raise ApiError(
            409,
            "engine_in_use",
            "This engine has jobs waiting or running. Let them finish or cancel them first.",
        )
    if not ModelStore(conn).is_installed(d.id):
        raise ApiError(409, "not_installed", "This engine is not installed.")
    installer = Installer(state.settings, conn, state.registry, state.storage)
    if not installer.software_update_available(engine):
        raise ApiError(409, "up_to_date", "This engine's software is already up to date.")
    try:
        installer.update_preflight(engine)
    except InstallError as exc:
        raise ApiError(507, exc.code, exc.user_message) from exc
    job = repo.create(
        conn,
        project_id=None,
        task="model.update",
        engine_id=d.id,
        inputs={"required_bytes": installer.update_required_bytes(engine)},
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job


@router.delete("/models/{engine_id}/download")
def discard_download(engine_id: str, state: State, conn: DB) -> dict[str, Any]:
    """Delete a paused download of an engine that is not installed."""
    engine = _engine(state, engine_id)
    d = engine.descriptor
    if _open_jobs(conn, d.id):
        raise ApiError(409, "engine_in_use", "Pause or cancel the download first.")
    installer = Installer(state.settings, conn, state.registry, state.storage)
    try:
        freed = installer.discard_download(engine)
    except InstallError as exc:
        raise ApiError(409, exc.code, exc.user_message) from exc
    return {"freed_bytes": freed}


@router.delete("/models/{engine_id}", status_code=202)
def remove_model(engine_id: str, state: State, conn: DB) -> dict[str, Any]:
    engine = _engine(state, engine_id)
    d = engine.descriptor
    if not d.requires_install:
        raise ApiError(409, "built_in", "Built-in engines cannot be removed.")
    open_jobs = _open_jobs(conn, d.id)
    for job in open_jobs:
        if job["task"] == "model.remove":
            return job
    if open_jobs:
        raise ApiError(
            409,
            "engine_in_use",
            "This engine has jobs waiting or running. Let them finish or cancel them first.",
        )
    if ModelStore(conn).get(d.id) is None:
        raise ApiError(409, "not_installed", "This engine is not installed.")
    job = repo.create(conn, project_id=None, task="model.remove", engine_id=d.id, inputs={})
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job
