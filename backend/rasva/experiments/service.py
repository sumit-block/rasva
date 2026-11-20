"""Experiment lifecycle: create (fixed cases x candidates -> queued jobs), read with
live job state and scoring, record reviews, decide, cancel.

Every run is an ordinary queued job, so experiments get the single-job scheduling,
crash isolation, metrics and recovery of the rest of the app (PRD §20, A1-010).
"""

from __future__ import annotations

import json
import sqlite3
from importlib import resources
from typing import Any

from rasva.config import Paths
from rasva.db import dumps, loads, new_id, transaction, utcnow
from rasva.engines.contract import EngineError
from rasva.engines.registry import EngineRegistry, RegisteredEngine, load_class
from rasva.experiments.scoring import (
    AUDIO_REVIEW_DIMENSIONS,
    AUDIO_WEIGHTS,
    CandidateView,
    RunView,
    summarize,
)
from rasva.jobs import repository as repo
from rasva.jobs.states import TERMINAL
from rasva.services import projects as project_svc
from rasva.services.installer import ModelStore

MAX_RUNS = 600
MAX_CANDIDATES = 8
MAX_CUSTOM_CASES = 100


class ExperimentError(ValueError):
    pass


class ExperimentNotFound(Exception):
    pass


# ------------------------------------------------------------------ benchmarks


def list_benchmarks() -> list[dict[str, Any]]:
    out = []
    for entry in resources.files("rasva.experiments.benchmarks").iterdir():
        if entry.name.endswith(".json"):
            data = json.loads(entry.read_text(encoding="utf-8"))
            categories: dict[str, int] = {}
            for c in data["cases"]:
                categories[c["category"]] = categories.get(c["category"], 0) + 1
            out.append({
                "id": data["id"], "name": data["name"], "description": data["description"],
                "task": data["task"], "cases": len(data["cases"]), "categories": categories,
            })  # fmt: skip
    return sorted(out, key=lambda b: b["id"])


def load_benchmark(benchmark_id: str) -> dict[str, Any]:
    for entry in resources.files("rasva.experiments.benchmarks").iterdir():
        if entry.name == f"{benchmark_id}.json":
            return json.loads(entry.read_text(encoding="utf-8"))
    raise ExperimentError(f"Unknown benchmark {benchmark_id!r}.")


# ------------------------------------------------------------------ helpers


def candidate_key(engine_id: str, settings: dict[str, Any]) -> str:
    parts = ",".join(f"{k}={settings[k]}" for k in sorted(settings))
    return f"{engine_id}:{parts}" if parts else engine_id


def _usable(conn: sqlite3.Connection, engine: RegisteredEngine) -> bool:
    return not engine.descriptor.requires_install or ModelStore(conn).is_installed(
        engine.descriptor.id
    )


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["candidates"] = loads(d["candidates"], [])
    d["usability"] = loads(d["usability"], {})
    return d


def _get_row(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    if row is None:
        raise ExperimentNotFound(experiment_id)
    return _row(row)


# ------------------------------------------------------------------ create


def create(
    conn: sqlite3.Connection,
    paths: Paths,
    registry: EngineRegistry,
    *,
    name: str,
    objective: str,
    task: str,
    candidates: list[dict[str, Any]],
    benchmark: str | None = None,
    texts: list[str] | None = None,
) -> dict[str, Any]:
    if task != "audio.narrate":
        raise ExperimentError("Only narration experiments are supported so far.")
    if bool(benchmark) == bool(texts):
        raise ExperimentError("Choose a benchmark or enter your own sentences (not both).")
    if benchmark:
        bench = load_benchmark(benchmark)
        if bench["task"] != task:
            raise ExperimentError("That benchmark is for a different task.")
        cases = bench["cases"]
    else:
        lines = [t.strip() for t in texts or [] if t.strip()]
        if not lines:
            raise ExperimentError("Enter at least one sentence.")
        if len(lines) > MAX_CUSTOM_CASES:
            raise ExperimentError(f"Use at most {MAX_CUSTOM_CASES} sentences.")
        cases = [
            {"id": f"c{i:03d}", "category": "custom", "text": t, "focus": ""}
            for i, t in enumerate(lines, 1)
        ]
    if not candidates:
        raise ExperimentError("Choose at least one engine and voice to compare.")
    if len(candidates) > MAX_CANDIDATES:
        raise ExperimentError(f"Compare at most {MAX_CANDIDATES} candidates at once.")
    if len(cases) * len(candidates) > MAX_RUNS:
        raise ExperimentError(f"That would be more than {MAX_RUNS} generations. Use fewer.")

    resolved: list[dict[str, Any]] = []
    for c in candidates:
        engine = registry.get(str(c.get("engine_id", "")))
        if engine is None or task not in engine.descriptor.tasks:
            raise ExperimentError(f"Engine {c.get('engine_id')!r} cannot do this task.")
        if not _usable(conn, engine):
            raise ExperimentError(f"{engine.descriptor.display_name} is not installed.")
        settings = {k: v for k, v in (c.get("settings") or {}).items() if k != "preview"}
        key = candidate_key(engine.descriptor.id, settings)
        if any(r["key"] == key for r in resolved):
            raise ExperimentError("The same engine and settings were chosen twice.")
        resolved.append({"key": key, "engine": engine, "settings": settings})

    experiment_id = new_id("exp")
    now = utcnow()
    job_ids: list[str] = []
    with transaction(conn):
        project = project_svc.create(
            conn, paths, name=f"Experiment: {name}", module="audio", kind="experiment"
        )
        conn.execute(
            "INSERT INTO experiments(id, project_id, name, objective, task, benchmark,"
            " candidates, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id, project["id"], name.strip(), objective.strip(), task, benchmark,
                dumps([{"key": r["key"], "engine_id": r["engine"].descriptor.id,
                        "settings": r["settings"]} for r in resolved]),
                now, now,
            ),
        )  # fmt: skip
        for case in cases:
            for r in resolved:
                engine: RegisteredEngine = r["engine"]
                d = engine.descriptor
                skip_reason = None
                job_id = None
                try:
                    inputs = load_class(engine.import_path)().validate_input(
                        task, {**r["settings"], "text": case["text"], "preview": False}
                    )
                except EngineError as exc:
                    skip_reason = exc.user_message
                else:
                    job_id = repo.insert(
                        conn, project_id=project["id"], task=task, engine_id=d.id, inputs=inputs
                    )
                    job_ids.append(job_id)
                model = ModelStore(conn).get(d.id)
                conn.execute(
                    "INSERT INTO experiment_runs(id, experiment_id, case_id, category,"
                    " candidate_key, engine_id, engine_version, model_revision, job_id,"
                    " skip_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("run"), experiment_id, case["id"], case["category"], r["key"],
                        d.id, d.version, model["revision"] if model else None, job_id,
                        skip_reason, now,
                    ),
                )  # fmt: skip
    return {"id": experiment_id, "job_ids": job_ids}


# ------------------------------------------------------------------ read


def list_experiments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT e.*,"
        " (SELECT COUNT(*) FROM experiment_runs r WHERE r.experiment_id = e.id) AS runs,"
        " (SELECT COUNT(*) FROM experiment_runs r JOIN jobs j ON j.id = r.job_id"
        "   WHERE r.experiment_id = e.id AND j.state = 'completed') AS completed,"
        " (SELECT COUNT(DISTINCT s.run_id) FROM experiment_scores s"
        "   JOIN experiment_runs r ON r.id = s.run_id WHERE r.experiment_id = e.id) AS rated"
        " FROM experiments e WHERE e.status <> 'archived' ORDER BY e.created_at DESC"
    ).fetchall()
    return [_row(r) for r in rows]


def get(
    conn: sqlite3.Connection, registry: EngineRegistry, experiment_id: str, total_memory: int
) -> dict[str, Any]:
    exp = _get_row(conn, experiment_id)
    cases = (
        {c["id"]: c for c in load_benchmark(exp["benchmark"])["cases"]}
        if exp["benchmark"]
        else None
    )
    rows = conn.execute(
        "SELECT r.*, j.state AS job_state, j.metrics AS job_metrics, j.outputs AS job_outputs,"
        " j.error_message AS job_error, j.inputs AS job_inputs, j.progress AS job_progress"
        " FROM experiment_runs r LEFT JOIN jobs j ON j.id = r.job_id"
        " WHERE r.experiment_id = ? ORDER BY r.case_id, r.candidate_key",
        (experiment_id,),
    ).fetchall()
    scores: dict[str, dict[str, int]] = {}
    for s in conn.execute(
        "SELECT s.run_id, s.dimension, s.score FROM experiment_scores s"
        " JOIN experiment_runs r ON r.id = s.run_id WHERE r.experiment_id = ?",
        (experiment_id,),
    ):
        scores.setdefault(s["run_id"], {})[s["dimension"]] = s["score"]

    runs = []
    for r in rows:
        inputs = loads(r["job_inputs"], {}) or {}
        case = (cases or {}).get(r["case_id"])
        runs.append({
            "id": r["id"],
            "case_id": r["case_id"],
            "category": r["category"],
            "text": case["text"] if case else inputs.get("text"),
            "focus": case["focus"] if case else "",
            "candidate_key": r["candidate_key"],
            "engine_id": r["engine_id"],
            "engine_version": r["engine_version"],
            "model_revision": r["model_revision"],
            "job_id": r["job_id"],
            "status": "skipped" if r["skip_reason"] else r["job_state"],
            "skip_reason": r["skip_reason"],
            "error": r["job_error"],
            "progress": r["job_progress"],
            "metrics": loads(r["job_metrics"], {}) or {},
            "output_asset_id": (loads(r["job_outputs"], []) or [None])[0],
            "scores": scores.get(r["id"], {}),
        })  # fmt: skip

    store = ModelStore(conn)
    views = []
    for c in exp["candidates"]:
        engine = registry.get(c["engine_id"])
        model = store.get(c["engine_id"])
        views.append(
            CandidateView(
                key=c["key"],
                engine_id=c["engine_id"],
                license_class=engine.descriptor.license_class if engine else "unknown",
                installed_bytes=(model or {}).get("installed_bytes", 0),
                runs=[
                    RunView(r["status"] or "queued", r["metrics"], r["scores"])
                    for r in runs
                    if r["candidate_key"] == c["key"]
                ],  # fmt: skip
                usability=exp["usability"].get(c["key"]),
            )
        )
    summary = [s.as_dict() for s in summarize(views, total_memory)]
    return {
        **exp,
        "review_dimensions": list(AUDIO_REVIEW_DIMENSIONS),
        "weights": AUDIO_WEIGHTS,
        "runs": runs,
        "summary": summary,
    }


# ------------------------------------------------------------------ review / decide


def set_scores(
    conn: sqlite3.Connection, experiment_id: str, run_id: str, scores: dict[str, int | None]
) -> None:
    run = conn.execute(
        "SELECT r.id, r.skip_reason, j.state FROM experiment_runs r"
        " LEFT JOIN jobs j ON j.id = r.job_id WHERE r.id = ? AND r.experiment_id = ?",
        (run_id, experiment_id),
    ).fetchone()
    if run is None:
        raise ExperimentNotFound(run_id)
    if run["state"] != "completed":
        raise ExperimentError("Only finished results can be rated.")
    unknown = set(scores) - set(AUDIO_REVIEW_DIMENSIONS)
    if unknown:
        raise ExperimentError(f"Unknown review dimension(s): {sorted(unknown)}")
    now = utcnow()
    with transaction(conn):
        for dim, score in scores.items():
            if score is None:
                conn.execute(
                    "DELETE FROM experiment_scores WHERE run_id = ? AND dimension = ?",
                    (run_id, dim),
                )
                continue
            if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
                raise ExperimentError("Scores are whole numbers from 1 to 5.")
            conn.execute(
                "INSERT INTO experiment_scores(run_id, dimension, score, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(run_id, dimension) DO UPDATE SET"
                " score = excluded.score, updated_at = excluded.updated_at",
                (run_id, dim, score, now),
            )
        conn.execute("UPDATE experiments SET updated_at = ? WHERE id = ?", (now, experiment_id))


def set_usability(
    conn: sqlite3.Connection, experiment_id: str, key: str, score: int | None
) -> None:
    exp = _get_row(conn, experiment_id)
    if key not in {c["key"] for c in exp["candidates"]}:
        raise ExperimentError("Unknown candidate.")
    if score is not None and not (isinstance(score, int) and 1 <= score <= 5):
        raise ExperimentError("Scores are whole numbers from 1 to 5.")
    usability = dict(exp["usability"])
    if score is None:
        usability.pop(key, None)
    else:
        usability[key] = score
    conn.execute(
        "UPDATE experiments SET usability = ?, updated_at = ? WHERE id = ?",
        (dumps(usability), utcnow(), experiment_id),
    )


def decide(conn: sqlite3.Connection, experiment_id: str, decision: str) -> None:
    _get_row(conn, experiment_id)
    conn.execute(
        "UPDATE experiments SET decision = ?, status = 'decided', updated_at = ? WHERE id = ?",
        (decision.strip(), utcnow(), experiment_id),
    )


def open_job_ids(conn: sqlite3.Connection, experiment_id: str) -> list[str]:
    _get_row(conn, experiment_id)
    rows = conn.execute(
        "SELECT j.id, j.state FROM experiment_runs r JOIN jobs j ON j.id = r.job_id"
        " WHERE r.experiment_id = ?",
        (experiment_id,),
    ).fetchall()
    terminal = {s.value for s in TERMINAL}
    return [r["id"] for r in rows if r["state"] not in terminal]
