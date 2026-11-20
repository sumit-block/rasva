"""Experiments API (PRD §20, A1-010). Advanced mode only in the UI."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.experiments import service as svc
from rasva.jobs import repository as repo
from rasva.services import system_check

router = APIRouter(tags=["experiments"])


class Candidate(BaseModel):
    engine_id: str
    settings: dict[str, Any] = {}


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=2000)
    task: Literal["audio.narrate"] = "audio.narrate"
    benchmark: str | None = None
    texts: list[str] | None = None
    candidates: list[Candidate]


class Scores(BaseModel):
    scores: dict[str, int | None]


class Usability(BaseModel):
    candidate_key: str
    score: int | None = Field(default=None, ge=1, le=5)


class Decision(BaseModel):
    decision: str = Field(min_length=1, max_length=4000)


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    try:
        return fn(*args, **kw)
    except svc.ExperimentError as exc:
        raise ApiError(422, "invalid_experiment", str(exc)) from exc
    except svc.ExperimentNotFound as exc:
        raise ApiError(404, "experiment_not_found", "That experiment does not exist.") from exc


@router.get("/experiments/benchmarks")
def benchmarks() -> list[dict[str, Any]]:
    return svc.list_benchmarks()


@router.get("/experiments")
def list_experiments(conn: DB) -> list[dict[str, Any]]:
    return svc.list_experiments(conn)


@router.post("/experiments", status_code=201)
def create(body: ExperimentCreate, state: State, conn: DB) -> dict[str, Any]:
    created = _call(
        svc.create,
        conn,
        state.settings.paths,
        state.registry,
        name=body.name,
        objective=body.objective,
        task=body.task,
        benchmark=body.benchmark,
        texts=body.texts,
        candidates=[c.model_dump() for c in body.candidates],
    )
    for job_id in created["job_ids"]:
        state.bus.publish({"type": "job", "job": repo.get(conn, job_id)})
    state.orchestrator.notify()
    return _detail(created["id"], state, conn)


def _detail(experiment_id: str, state: State, conn: DB) -> dict[str, Any]:
    total_memory = system_check.facts().total_memory_bytes
    return _call(svc.get, conn, state.registry, experiment_id, total_memory)


@router.get("/experiments/{experiment_id}")
def get(experiment_id: str, state: State, conn: DB) -> dict[str, Any]:
    return _detail(experiment_id, state, conn)


@router.put("/experiments/{experiment_id}/runs/{run_id}/scores")
def set_scores(
    experiment_id: str, run_id: str, body: Scores, state: State, conn: DB
) -> dict[str, Any]:
    _call(svc.set_scores, conn, experiment_id, run_id, body.scores)
    return _detail(experiment_id, state, conn)


@router.put("/experiments/{experiment_id}/usability")
def set_usability(experiment_id: str, body: Usability, state: State, conn: DB) -> dict[str, Any]:
    _call(svc.set_usability, conn, experiment_id, body.candidate_key, body.score)
    return _detail(experiment_id, state, conn)


@router.post("/experiments/{experiment_id}/decision")
def decide(experiment_id: str, body: Decision, state: State, conn: DB) -> dict[str, Any]:
    _call(svc.decide, conn, experiment_id, body.decision)
    return _detail(experiment_id, state, conn)


@router.post("/experiments/{experiment_id}/cancel")
def cancel(experiment_id: str, state: State, conn: DB) -> dict[str, Any]:
    for job_id in _call(svc.open_job_ids, conn, experiment_id):
        job = repo.request_cancel(conn, job_id)
        state.orchestrator.cancel(job_id)
        state.bus.publish({"type": "job", "job": job})
    return _detail(experiment_id, state, conn)
