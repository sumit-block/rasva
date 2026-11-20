"""Project persistence."""

from __future__ import annotations

import sqlite3
from typing import Any

from rasva.config import Paths
from rasva.db import dumps, loads, new_id, utcnow


class ProjectNotFound(Exception):
    pass


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["settings"] = loads(d["settings"], {})
    return d


def create(
    conn: sqlite3.Connection,
    paths: Paths,
    *,
    name: str,
    module: str,
    description: str = "",
    kind: str = "user",
) -> dict[str, Any]:
    project_id = new_id("prj")
    now = utcnow()
    conn.execute(
        "INSERT INTO projects(id, name, module, description, kind, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project_id, name.strip(), module, description, kind, now, now),
    )
    for sub in ("references", "imports", "outputs"):
        (paths.project_dir(project_id) / sub).mkdir(parents=True, exist_ok=True)
    return get(conn, project_id)


def get(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise ProjectNotFound(project_id)
    return _row(row)


def list_projects(
    conn: sqlite3.Connection,
    *,
    module: str | None = None,
    status: str | None = "active",
    query: str | None = None,
    limit: int = 100,
    kind: str | None = "user",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if module:
        clauses.append("module = ?")
        params.append(module)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if query:
        clauses.append("name LIKE ? ESCAPE '\\'")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    sql = "SELECT * FROM projects"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in conn.execute(sql, params)]


def update(conn: sqlite3.Connection, project_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"name", "description", "status", "settings"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        sets.append(f"{key} = ?")
        params.append(dumps(value) if key == "settings" else value)
    if sets:
        sets.append("updated_at = ?")
        params.extend([utcnow(), project_id])
        cur = conn.execute(
            # Column names come from the fixed `allowed` set, never from input.
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
        if cur.rowcount == 0:
            raise ProjectNotFound(project_id)
    return get(conn, project_id)


def touch(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (utcnow(), project_id))
