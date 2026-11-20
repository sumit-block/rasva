"""Storage cleanup (PRD §23, §27, §34.3: "safe cleanup never silently deletes user originals").

Cleanup works on *groups* of reclaimable items. The client only names groups;
the plan is recomputed here at execution time, so a stale screen can never
delete something that has since become current.

Never offered for deletion: uploads (references and imports), the take each
segment currently uses, a take waiting for review, the latest export of a
chapter, experiment recordings, anything belonging to a job that is still
running, installed models (removed from Models), and engine caches.

Images (phase I4): reference photos are uploads, so never offered. Of each
image's versions, the favourite (or, with none chosen, the newest finished
version) is never offered, nor is any image used as the source of another job,
nor an image with no version record. Other versions are offered once they are a
day old, off by default. Files of failed or interrupted image jobs count as
temporary files.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rasva.config import Paths
from rasva.jobs.states import TERMINAL
from rasva.services import assets as asset_svc
from rasva.services import images as image_svc

DAY = 86_400
PREVIEW_MIN_AGE_S = DAY  # keep today's previews: you may still be comparing them
TEMP_MIN_AGE_S = 3_600

GROUPS: dict[str, dict[str, Any]] = {
    "previews": {
        "label": "Old previews",
        "description": "Short previews and pronunciation tests more than a day old.",
        "default": True,
    },
    "orphaned_narration": {
        "label": "Audio for removed sentences",
        "description": "Sentences you deleted or rewrote. Chapters no longer use this audio.",
        "default": True,
    },
    "temp": {
        "label": "Temporary files",
        "description": "Leftovers from finished or interrupted jobs and uploads.",
        "default": True,
    },
    "older_image_versions": {
        "label": "Older image versions",
        "description": (
            "Versions of an image other than your favourite (or the newest, if you haven't "
            "chosen one). You won't be able to compare with them again."
        ),
        "default": False,
    },
    "superseded_takes": {
        "label": "Replaced sentence versions",
        "description": (
            "Older versions you chose not to use. You won't be able to switch back to them."
        ),
        "default": False,
    },
    "old_exports": {
        "label": "Older exports",
        "description": "Earlier exports of a chapter. The latest export of every chapter is kept.",
        "default": False,
    },
    "paused_downloads": {
        "label": "Paused downloads",
        "description": "Partly downloaded engines. Installing again will start from scratch.",
        "default": False,
    },
    "untracked": {
        "label": "Unrecognised files",
        "description": (
            "Files in Rasva's project folders with no record, for example after a crash."
        ),
        "default": False,
    },
}


@dataclass
class Item:
    bytes: int
    path: Path | None = None
    asset_id: str | None = None
    take_id: str | None = None


@dataclass
class Plan:
    groups: dict[str, list[Item]] = field(default_factory=lambda: defaultdict(list))
    missing_files: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "groups": [
                {
                    "id": gid,
                    **meta,
                    "count": len(self.groups.get(gid, [])),
                    "bytes": sum(i.bytes for i in self.groups.get(gid, [])),
                }
                for gid, meta in GROUPS.items()
            ],
            "missing_files": len(self.missing_files),
        }


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size if path.exists() else 0


def _asset_item(paths: Paths, row: sqlite3.Row, take_id: str | None = None) -> Item:
    return Item(
        bytes=row["size_bytes"],
        path=asset_svc.resolve(paths, row["rel_path"]),
        asset_id=row["id"],
        take_id=take_id,
    )


def _open_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    terminal = {s.value for s in TERMINAL}
    return [
        r for r in conn.execute("SELECT id, task, state FROM jobs") if r["state"] not in terminal
    ]


def plan(conn: sqlite3.Connection, paths: Paths, now: float | None = None) -> Plan:
    now = now or time.time()
    result = Plan()
    open_jobs = _open_jobs(conn)
    open_ids = {r["id"] for r in open_jobs}

    outputs = conn.execute(
        "SELECT a.*, j.task, j.inputs, j.state, j.created_at AS job_created"
        " FROM assets a JOIN jobs j ON j.id = a.job_id WHERE a.kind = 'output' AND a.protected = 0"
    ).fetchall()
    take_by_job = {
        r["job_id"]: r
        for r in conn.execute(
            "SELECT t.id, t.job_id, t.reviewed, s.current_take_id FROM takes t"
            " JOIN segments s ON s.id = t.segment_id"
        )
    }
    latest_export: dict[str, str] = {}
    for r in conn.execute(
        "SELECT id, inputs FROM jobs WHERE task = 'audio.assemble' AND state = 'completed'"
        " ORDER BY created_at"
    ):
        chapter_id = json.loads(r["inputs"]).get("chapter_id")
        if chapter_id:
            latest_export[chapter_id] = r["id"]
    existing_chapters = {r["id"] for r in conn.execute("SELECT id FROM chapters")}
    keep_images = _image_assets_to_keep(conn)

    for row in outputs:
        if row["job_id"] in open_ids:
            continue
        if image_svc.is_image_task(row["task"]):
            if row["id"] in keep_images[0]:
                continue
            if row["state"] != "completed":
                result.groups["temp"].append(_asset_item(paths, row))
            elif row["job_id"] in keep_images[1] and now - _epoch(row["created_at"]) >= DAY:
                result.groups["older_image_versions"].append(_asset_item(paths, row))
            continue
        if row["state"] != "completed":
            continue
        inputs = json.loads(row["inputs"] or "{}")
        created = row["created_at"]
        age = now - _epoch(created)
        if row["task"] == "audio.narrate" and inputs.get("preview"):
            if age >= PREVIEW_MIN_AGE_S:
                result.groups["previews"].append(_asset_item(paths, row))
        elif row["task"] == "audio.narrate" and inputs.get("chapter_id"):
            take = take_by_job.get(row["job_id"])
            if take is None:
                result.groups["orphaned_narration"].append(_asset_item(paths, row))
            elif (
                take["current_take_id"] is not None
                and take["current_take_id"] != take["id"]
                and take["reviewed"]
            ):
                result.groups["superseded_takes"].append(_asset_item(paths, row, take["id"]))
        elif row["task"] == "audio.assemble":
            chapter_id = inputs.get("chapter_id")
            if chapter_id in existing_chapters and latest_export.get(chapter_id) != row["job_id"]:
                result.groups["old_exports"].append(_asset_item(paths, row))

    # Temporary files: staging of finished jobs, stale partial uploads, other temp.
    for d in paths.staging.glob("*") if paths.staging.exists() else []:
        if d.name not in open_ids:
            result.groups["temp"].append(Item(bytes=_size(d), path=d))
    for f in paths.uploads_tmp.glob("*") if paths.uploads_tmp.exists() else []:
        if now - f.stat().st_mtime >= TEMP_MIN_AGE_S:
            result.groups["temp"].append(Item(bytes=_size(f), path=f))
    if not any(r["task"] == "model.install" for r in open_jobs) and paths.downloads.exists():
        for f in paths.downloads.glob("*.part"):
            result.groups["paused_downloads"].append(Item(bytes=_size(f), path=f))

    # Files on disk with no record, and records with no file.
    known = {r["rel_path"] for r in conn.execute("SELECT rel_path FROM assets")}
    for r in conn.execute("SELECT id, rel_path FROM assets"):
        if not asset_svc.resolve(paths, r["rel_path"]).is_file():
            result.missing_files.append(r["id"])
    if paths.projects.exists():
        root = paths.projects.resolve()
        for f in paths.projects.glob("*/*/*"):
            if (
                f.is_file()
                and str(f.resolve().relative_to(root)) not in known
                and now - f.stat().st_mtime >= TEMP_MIN_AGE_S  # not a write in progress
            ):
                result.groups["untracked"].append(Item(bytes=_size(f), path=f))
    return result


def _image_assets_to_keep(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """(asset ids never offered, image job ids that are offerable older versions).

    Kept: images used as a source by any job (inputs.references or the output's
    source_asset_ids). Offerable: completed versions that are not their group's
    current version. Image jobs without a version record are never offerable.
    """
    sources: set[str] = set()
    for r in conn.execute("SELECT inputs FROM jobs WHERE task LIKE 'image.%'"):
        for ref in json.loads(r["inputs"] or "{}").get("references") or []:
            if isinstance(ref, dict) and ref.get("asset_id"):
                sources.add(str(ref["asset_id"]))
    for r in conn.execute("SELECT metadata FROM assets WHERE kind = 'output'"):
        meta = json.loads(r["metadata"] or "{}")
        sources.update(str(a) for a in meta.get("source_asset_ids") or [])

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    favourites: dict[str, str | None] = {}
    for r in conn.execute(
        "SELECT v.group_id, g.favourite_job_id, j.id, j.state, j.outputs, j.created_at"
        " FROM image_versions v JOIN image_groups g ON g.id = v.group_id"
        " JOIN jobs j ON j.id = v.job_id"
    ):
        favourites[r["group_id"]] = r["favourite_job_id"]
        groups[r["group_id"]].append(
            {"id": r["id"], "state": r["state"], "outputs": json.loads(r["outputs"] or "[]"),
             "created_at": r["created_at"]}
        )  # fmt: skip
    offerable: set[str] = set()
    for gid, versions in groups.items():
        current = image_svc.current_job_id(favourites[gid], versions)
        if current is None:
            continue  # nothing finished yet: keep everything
        offerable.update(v["id"] for v in versions if v["id"] != current)
    return sources, offerable


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def execute(
    conn: sqlite3.Connection, paths: Paths, group_ids: list[str], now: float | None = None
) -> dict[str, Any]:
    unknown = set(group_ids) - set(GROUPS)
    if unknown:
        raise ValueError(f"Unknown cleanup groups: {sorted(unknown)}")
    current = plan(conn, paths, now)  # recomputed: never trust a stale client list
    items = [(gid, item) for gid in group_ids for item in current.groups.get(gid, [])]
    asset_ids = [i.asset_id for _, i in items if i.asset_id]
    take_ids = [i.take_id for _, i in items if i.take_id]

    conn.execute("BEGIN IMMEDIATE")
    try:
        for take_id in take_ids:
            conn.execute("DELETE FROM takes WHERE id = ?", (take_id,))
        if asset_ids:
            gone = set(asset_ids)
            job_ids = {
                r["job_id"]
                for a in asset_ids
                for r in conn.execute("SELECT job_id FROM assets WHERE id = ?", (a,))
                if r["job_id"]
            }
            for job_id in job_ids:
                row = conn.execute("SELECT outputs FROM jobs WHERE id = ?", (job_id,)).fetchone()
                kept = [a for a in json.loads(row["outputs"] or "[]") if a not in gone]
                conn.execute("UPDATE jobs SET outputs = ? WHERE id = ?", (json.dumps(kept), job_id))
            for asset_id in asset_ids:
                conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    # Files go only after the records are gone; a failure here leaves an
    # untracked file, which the next scan reports, never a broken record.
    freed: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    for gid, item in items:
        if item.path is None:
            continue
        try:
            if item.path.is_dir():
                shutil.rmtree(item.path)
            else:
                item.path.unlink(missing_ok=True)
        except OSError:
            continue
        freed[gid] += item.bytes
        counts[gid] += 1
    return {"freed_bytes": sum(freed.values()), "freed": dict(freed), "deleted": dict(counts)}
