from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.api.models import InstallRequest, install_model, remove_model
from rasva.engines.contract import EngineError
from rasva.engines.registry import load_class
from rasva.jobs import repository as repo
from rasva.jobs.orchestrator import MODEL_TASKS
from rasva.jobs.states import JobState
from rasva.services import projects as project_svc
from rasva.services import pronunciations as pronunciation_svc
from rasva.services import voices as voice_svc
from rasva.services.installer import ModelStore
from rasva.services.presets import PresetError, apply_preset

router = APIRouter(tags=["jobs"])

HEARTBEAT_S = 15.0
INTERNAL_TASKS = frozenset({"audio.assemble"})
RETRYABLE = {JobState.FAILED, JobState.CANCELED, JobState.INTERRUPTED}


class JobCreate(BaseModel):
    project_id: str
    task: str = Field(min_length=1, max_length=100)
    engine_id: str | None = None
    inputs: dict[str, Any] = {}


def _validate(
    state: State, conn: sqlite3.Connection, engine_id: str, task: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    engine = state.registry.get(engine_id)
    if engine is None:
        raise ApiError(422, "engine_unavailable", "That engine is not available.")
    if engine.descriptor.requires_install and not ModelStore(conn).is_installed(engine_id):
        raise ApiError(
            422,
            "engine_not_installed",
            f"{engine.descriptor.display_name} is not installed. Install it from Models first.",
        )
    if task not in engine.descriptor.tasks:
        raise ApiError(
            422, "unsupported_task", f"{engine.descriptor.display_name} cannot do this task."
        )
    try:
        # validate_input is required to be pure and lightweight (see contract).
        return load_class(engine.import_path)().validate_input(task, inputs)
    except EngineError as exc:
        raise ApiError(422, exc.code, exc.user_message) from exc


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, state: State, conn: DB) -> dict[str, Any]:
    if body.task in MODEL_TASKS:
        raise ApiError(422, "unsupported_task", "Install or remove engines from Models.")
    if body.task in INTERNAL_TASKS:
        # Built only by the chapter export endpoint, from the project's own assets.
        raise ApiError(422, "unsupported_task", "Export chapters from Audio Studio.")
    project_svc.get(conn, body.project_id)
    engine_id = body.engine_id
    if engine_id is None:
        store = ModelStore(conn)
        candidates = [
            e
            for e in state.registry.for_task(body.task)
            if not e.descriptor.requires_install or store.is_installed(e.descriptor.id)
        ]
        if not candidates:
            raise ApiError(422, "no_engine", "No installed engine can do this yet.")
        engine_id = candidates[0].descriptor.id
    raw, extras = dict(body.inputs), {}
    engine = state.registry.get(engine_id)
    if engine is not None and raw.get("preset"):
        try:
            extras["preset"] = raw["preset"]
            raw = apply_preset(engine.descriptor, raw)
        except PresetError as exc:
            raise ApiError(422, "invalid_preset", str(exc)) from exc
    if engine is not None and raw.get("voice_profile_id"):
        try:
            extras["voice_profile_id"] = raw["voice_profile_id"]
            raw = voice_svc.resolve_profile(conn, state.settings.paths, engine.descriptor, raw)
        except voice_svc.VoiceError as exc:
            raise ApiError(422, "invalid_voice", str(exc)) from exc
    if body.task == "audio.narrate" and engine is not None:
        # Approved pronunciations change only what the engine reads (PRD §10.2).
        raw, narration_extras = pronunciation_svc.prepare_narration(
            conn, engine.descriptor, body.project_id, raw
        )
        extras.update(narration_extras)
    inputs = {**_validate(state, conn, engine_id, body.task, raw), **extras}
    job = repo.create(
        conn, project_id=body.project_id, task=body.task, engine_id=engine_id, inputs=inputs
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job


@router.get("/jobs")
def list_jobs(
    conn: DB,
    project_id: str | None = None,
    state: Annotated[list[JobState] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict[str, Any]]:
    return repo.list_jobs(
        conn, project_id=project_id, states=[s.value for s in state] if state else None, limit=limit
    )


@router.get("/jobs/stream")
async def stream(request: Request, state: State) -> StreamingResponse:
    """Server-Sent Events: one `job` event per state/progress change."""
    queue = state.bus.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            state.bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str, conn: DB) -> dict[str, Any]:
    job = repo.get(conn, job_id)
    job["events"] = repo.events(conn, job_id)
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, state: State, conn: DB) -> dict[str, Any]:
    job = repo.request_cancel(conn, job_id)
    state.orchestrator.cancel(job_id)
    state.bus.publish({"type": "job", "job": job})
    return job


@router.post("/jobs/{job_id}/retry", status_code=201)
def retry_job(job_id: str, state: State, conn: DB) -> dict[str, Any]:
    old = repo.get(conn, job_id)
    if JobState(old["state"]) not in RETRYABLE:
        raise ApiError(
            409, "not_retryable", "Only failed, canceled or interrupted jobs can be retried."
        )
    if old["task"] == "model.install":
        accepted = bool(old["inputs"].get("license_accepted"))
        return install_model(old["engine_id"], state, conn, InstallRequest(accept_license=accepted))
    if old["task"] == "model.remove":
        return remove_model(old["engine_id"], state, conn)
    # Retry exactly what was generated before, including its pronunciation choices.
    keep = (*pronunciation_svc.EXTRA_KEYS, "preset", "chapter_id", "segment_id", "voice_profile_id")
    extras = {k: v for k, v in old["inputs"].items() if k in keep}
    raw = {k: v for k, v in old["inputs"].items() if k not in keep and k != "reference"}
    if old["inputs"].get("voice_profile_id"):
        # Re-resolve: the voice may have been deleted since.
        engine = state.registry.get(old["engine_id"])
        try:
            raw = voice_svc.resolve_profile(
                conn, state.settings.paths, engine.descriptor,
                {**raw, "voice_profile_id": old["inputs"]["voice_profile_id"]},
            )  # fmt: skip
        except (voice_svc.VoiceError, AttributeError) as exc:
            raise ApiError(422, "invalid_voice", str(exc)) from exc
    inputs = {**_validate(state, conn, old["engine_id"], old["task"], raw), **extras}
    job = repo.create(
        conn,
        project_id=old["project_id"],
        task=old["task"],
        engine_id=old["engine_id"],
        inputs=inputs,
        retry_of=job_id,
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job
