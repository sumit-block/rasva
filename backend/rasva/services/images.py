"""Image versions and image-engine options (PRD phase I4).

Versions: results of the same request are versions of one image. A new job joins
an existing group when

- it was made from a version (``version_of``: "Make a variation", "Try again with
  the same seed", or an edited description),
- it is a retry (``retry_of``), or
- it is an ``image.edit`` of the same reference photos (same asset ids and roles)
  in the same project.

Any other ``image.generate`` job starts a new group: a new description without
references is a new picture, not a version. The favourite is the version the
owner chose; with no favourite, the newest completed version is current.
Cleanup never offers the current version (see ``services/cleanup.py``).

Low-memory mode: the ``image.low_memory`` setting asks image engines that list
the ``low_memory`` feature to use less memory (for example CPU offload or a
smaller working size). It is added to the job's inputs after validation, like
``preset``, so it is recorded with the job; retries read the current setting, so
turning it on and retrying a job that ran out of memory helps. The worker runs
``validate_input`` again before ``run``, so an engine that lists the feature
must keep ``low_memory`` in what ``validate_input`` returns. Because workers stay
loaded between jobs, an engine whose low-memory setup happens at load time must
reload itself when the flag differs from how it was loaded.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from rasva.db import new_id, transaction, utcnow
from rasva.engines.contract import EngineDescriptor
from rasva.jobs import repository as repo
from rasva.services.settings_store import SettingsStore

IMAGE_PREFIX = "image."
LOW_MEMORY_FEATURE = "low_memory"
LOW_MEMORY_SETTING = "image.low_memory"
# Keys the API adds after validation; never sent back through validate_input.
ADDED_KEYS = ("low_memory",)


class VersionError(ValueError):
    pass


def is_image_task(task: str) -> bool:
    return task.startswith(IMAGE_PREFIX)


# ---- low-memory mode


def engine_extras(conn: sqlite3.Connection, descriptor: EngineDescriptor) -> dict[str, Any]:
    """Inputs added for image engines that support low-memory mode, when it is on."""
    if descriptor.modality != "image" or LOW_MEMORY_FEATURE not in descriptor.features:
        return {}
    return {"low_memory": True} if SettingsStore(conn).get(LOW_MEMORY_SETTING) else {}


# ---- grouping


def request_key(task: str, inputs: dict[str, Any]) -> str | None:
    refs = inputs.get("references") or []
    if task != "image.edit" or not isinstance(refs, list) or not refs:
        return None
    parts = sorted(f"{r.get('asset_id')}:{r.get('role', '')}" for r in refs if isinstance(r, dict))
    return hashlib.sha256("\n".join([task, *parts]).encode()).hexdigest()


def group_of(conn: sqlite3.Connection, job_id: str) -> str | None:
    row = conn.execute("SELECT group_id FROM image_versions WHERE job_id = ?", (job_id,)).fetchone()
    return row["group_id"] if row else None


def check_version_of(
    conn: sqlite3.Connection, project_id: str, task: str, version_of: str | None
) -> None:
    """Refuse ``version_of`` pointing at another project's or a non-image job."""
    if version_of is None:
        return
    if not is_image_task(task):
        raise VersionError("Only images have versions.")
    try:
        source = repo.get(conn, version_of)
    except repo.JobNotFound as exc:
        raise VersionError("That image no longer exists.") from exc
    if source["project_id"] != project_id or not is_image_task(source["task"]):
        raise VersionError("That image belongs to another project.")


def assign(
    conn: sqlite3.Connection, job: dict[str, Any], version_of: str | None = None
) -> str | None:
    """Record ``job`` as a version. Returns its group id (None for non-image jobs)."""
    if not is_image_task(job["task"]):
        return None
    now = utcnow()
    with transaction(conn):
        group_id = None
        for parent in (version_of, job.get("retry_of")):
            if parent and (group_id := group_of(conn, parent)):
                break
        key = None
        if group_id is None and (key := request_key(job["task"], job["inputs"])):
            row = conn.execute(
                "SELECT id FROM image_groups WHERE project_id = ? AND task = ? AND request_key = ?",
                (job["project_id"], job["task"], key),
            ).fetchone()
            group_id = row["id"] if row else None
        if group_id is None:
            group_id = new_id("imgg")
            conn.execute(
                "INSERT INTO image_groups(id, project_id, task, request_key, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (group_id, job["project_id"], job["task"], key, now, now),
            )
        conn.execute(
            "INSERT OR IGNORE INTO image_versions(job_id, group_id, created_at) VALUES (?, ?, ?)",
            (job["id"], group_id, job["created_at"]),
        )
        conn.execute("UPDATE image_groups SET updated_at = ? WHERE id = ?", (now, group_id))
    return group_id


def _usable(job: dict[str, Any]) -> bool:
    return job["state"] == "completed" and bool(job["outputs"])


def current_job_id(favourite: str | None, versions: list[dict[str, Any]]) -> str | None:
    """The favourite if it still has its image; otherwise the newest completed version."""
    by_id = {v["id"]: v for v in versions}
    if favourite and favourite in by_id and _usable(by_id[favourite]):
        return favourite
    done = [v for v in versions if _usable(v)]
    return max(done, key=lambda v: v["created_at"])["id"] if done else None


def _group(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    versions = [
        repo.row_to_job(r)
        for r in conn.execute(
            "SELECT j.* FROM image_versions v JOIN jobs j ON j.id = v.job_id"
            " WHERE v.group_id = ? ORDER BY j.created_at",
            (row["id"],),
        )
    ]
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "task": row["task"],
        "favourite_job_id": row["favourite_job_id"],
        "current_job_id": current_job_id(row["favourite_job_id"], versions),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "versions": versions,
    }


def get_group(conn: sqlite3.Connection, group_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM image_groups WHERE id = ?", (group_id,)).fetchone()
    if row is None:
        raise VersionError("That image no longer exists.")
    return _group(conn, row)


def list_groups(
    conn: sqlite3.Connection, project_id: str, limit: int = 100
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM image_groups WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
        (project_id, limit),
    ).fetchall()
    return [_group(conn, r) for r in rows]


def set_favourite(conn: sqlite3.Connection, group_id: str, job_id: str | None) -> dict[str, Any]:
    group = get_group(conn, group_id)
    if job_id is not None:
        version = next((v for v in group["versions"] if v["id"] == job_id), None)
        if version is None:
            raise VersionError("That version belongs to another image.")
        if not _usable(version):
            raise VersionError("Only a finished version with its picture can be the favourite.")
    conn.execute(
        "UPDATE image_groups SET favourite_job_id = ?, updated_at = ? WHERE id = ?",
        (job_id, utcnow(), group_id),
    )
    return get_group(conn, group_id)
