"""Durable job persistence. All state changes go through ``transition``."""

from __future__ import annotations

import sqlite3
from typing import Any

from rasva.db import dumps, loads, new_id, transaction, utcnow
from rasva.jobs.states import ACTIVE, TERMINAL, InvalidTransition, JobState, check_transition


class JobNotFound(Exception):
    pass


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "task": row["task"],
        "engine_id": row["engine_id"],
        "state": row["state"],
        "inputs": loads(row["inputs"], {}),
        "outputs": loads(row["outputs"], []),
        "progress": row["progress"],
        "stage_detail": row["stage_detail"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "metrics": loads(row["metrics"], {}),
        "cancel_requested": bool(row["cancel_requested"]),
        "retry_of": row["retry_of"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _event(conn: sqlite3.Connection, job_id: str, state: str, detail: str | None) -> None:
    conn.execute(
        "INSERT INTO job_events(job_id, state, detail, at) VALUES (?, ?, ?, ?)",
        (job_id, state, detail, utcnow()),
    )


def create(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    task: str,
    engine_id: str,
    inputs: dict[str, Any],
    retry_of: str | None = None,
    queue: bool = True,
) -> dict[str, Any]:
    job_id = new_id("job")
    now = utcnow()
    state = JobState.QUEUED if queue else JobState.DRAFT
    with transaction(conn):
        conn.execute(
            "INSERT INTO jobs(id, project_id, task, engine_id, state, inputs, retry_of,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, project_id, task, engine_id, state, dumps(inputs), retry_of, now, now),
        )
        _event(conn, job_id, state, None)
    return get(conn, job_id)


def get(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobNotFound(job_id)
    return row_to_job(row)


def list_jobs(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    states: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jobs"
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if states:
        clauses.append(f"state IN ({','.join('?' * len(states))})")
        params.extend(states)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [row_to_job(r) for r in conn.execute(sql, params)]


def events(conn: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT state, detail, at FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
    )
    return [dict(r) for r in rows]


def next_queued(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE state = ? ORDER BY created_at, rowid LIMIT 1",
        (JobState.QUEUED,),
    ).fetchone()
    return row_to_job(row) if row else None


def transition(
    conn: sqlite3.Connection,
    job_id: str,
    target: JobState,
    *,
    detail: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Move a job to ``target``, validating the edge and guarding against races.

    The UPDATE is conditional on the state we validated, so two writers can never
    both apply conflicting transitions.
    """
    allowed = {
        "progress",
        "stage_detail",
        "error_code",
        "error_message",
        "outputs",
        "metrics",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown job fields: {sorted(unknown)}")

    with transaction(conn):
        row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        current = JobState(row["state"])
        check_transition(current, target)

        now = utcnow()
        sets = ["state = ?", "updated_at = ?"]
        params: list[Any] = [target, now]
        if current == JobState.QUEUED and target in ACTIVE:
            sets.append("started_at = ?")
            params.append(now)
        if target in TERMINAL:
            sets.append("finished_at = ?")
            params.append(now)
        if target == JobState.PROCESSING and "progress" not in fields:
            fields["progress"] = 0.0
        if target == JobState.COMPLETED:
            fields.setdefault("progress", 1.0)
        for key, value in fields.items():
            sets.append(f"{key} = ?")
            params.append(dumps(value) if key in {"outputs", "metrics"} else value)
        params.extend([job_id, current])
        cur = conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ? AND state = ?",  # noqa: S608
            params,
        )
        if cur.rowcount != 1:
            raise InvalidTransition(current, target)
        _event(conn, job_id, target, detail)
    return get(conn, job_id)


def update_progress(
    conn: sqlite3.Connection, job_id: str, progress: float | None, detail: str | None
) -> dict[str, Any]:
    if progress is not None:
        progress = min(max(progress, 0.0), 1.0)
    conn.execute(
        "UPDATE jobs SET progress = COALESCE(?, progress), stage_detail = ?, updated_at = ?"
        " WHERE id = ?",
        (progress, detail, utcnow(), job_id),
    )
    return get(conn, job_id)


def request_cancel(conn: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    """Cancel immediately if not started; otherwise flag for the orchestrator."""
    job = get(conn, job_id)
    state = JobState(job["state"])
    if state in TERMINAL:
        return job
    if state in (JobState.DRAFT, JobState.QUEUED):
        try:
            return transition(conn, job_id, JobState.CANCELED, detail="Canceled before start")
        except InvalidTransition:
            # Raced with the orchestrator picking it up; fall through to flagging.
            pass
    conn.execute(
        "UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (utcnow(), job_id)
    )
    return get(conn, job_id)


def recover_after_restart(conn: sqlite3.Connection) -> list[str]:
    """Mark jobs that were mid-flight when the app stopped as Interrupted.

    Queued jobs stay queued and run normally. Completed outputs of other jobs are
    untouched (NFR-04, NFR-06).
    """
    active = [s.value for s in ACTIVE]
    rows = conn.execute(
        f"SELECT id FROM jobs WHERE state IN ({','.join('?' * len(active))})",  # noqa: S608
        active,
    ).fetchall()
    recovered: list[str] = []
    for r in rows:
        transition(
            conn,
            r["id"],
            JobState.INTERRUPTED,
            detail="Rasva stopped while this job was running",
            error_code="interrupted",
            error_message=(
                "Rasva Studio closed or your Mac restarted while this was running. "
                "Your other work is safe. You can retry this job."
            ),
        )
        recovered.append(r["id"])
    return recovered
