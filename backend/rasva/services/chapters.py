"""Long-form chapters (PRD §12, phase A4).

A chapter's script is split into segments. Every generation of a segment is a
*take* backed by an ordinary queued job, so failures, restarts and cancellation
affect only that segment (NFR-04). Takes are never overwritten:

- A segment's *current* take is what the chapter export uses.
- The first finished take becomes current automatically. Later takes wait for
  review ("needs_review") until the user chooses new or keeps old.
- Editing the script re-segments it; segments whose text is unchanged keep
  their takes, so only edited sentences need generating again.

Statuses are derived from the takes' job states when the chapter is read, so
there is no second source of truth to drift from the job queue.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from rasva.audio.language import classify
from rasva.audio.text import group, segment
from rasva.db import dumps, loads, new_id, transaction, utcnow
from rasva.engines.contract import EngineError
from rasva.engines.registry import EngineRegistry, RegisteredEngine, load_class
from rasva.jobs import repository as repo
from rasva.jobs.states import ACTIVE, JobState
from rasva.services import projects as project_svc
from rasva.services import pronunciations as pronunciation_svc
from rasva.services.installer import ModelStore
from rasva.services.presets import PresetError, apply_preset

MAX_SCRIPT_CHARS = 200_000  # roughly two hours of narration
SETTING_KEYS = ("engine_id", "voice_id", "speed", "use_dictionary", "preset")
GENERATE_SCOPES = ("needed", "failed", "all")
OPEN_STATES = {JobState.QUEUED.value, JobState.DRAFT.value, *(s.value for s in ACTIVE)}
BAD_STATES = {JobState.FAILED.value, JobState.INTERRUPTED.value}


class ChapterError(ValueError):
    pass


class ChapterNotFound(Exception):
    pass


# ------------------------------------------------------------------ helpers


def _chapter_row(conn: sqlite3.Connection, chapter_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    if row is None:
        raise ChapterNotFound(chapter_id)
    d = dict(row)
    d["settings"] = loads(d["settings"], {})
    d["mix"] = loads(d["mix"], {})
    return d


def _engine(conn: sqlite3.Connection, registry: EngineRegistry, engine_id: Any) -> RegisteredEngine:
    engine = registry.get(str(engine_id or ""))
    if engine is None or "audio.narrate" not in engine.descriptor.tasks:
        raise ChapterError("Choose a voice engine.")
    if engine.descriptor.requires_install and not ModelStore(conn).is_installed(
        engine.descriptor.id
    ):
        raise ChapterError(f"{engine.descriptor.display_name} is not installed.")
    return engine


def normalize_settings(
    conn: sqlite3.Connection, registry: EngineRegistry, settings: dict[str, Any]
) -> dict[str, Any]:
    engine = _engine(conn, registry, settings.get("engine_id"))
    preset = settings.get("preset") or None
    try:
        raw = apply_preset(
            engine.descriptor,
            {"text": "Test.", "voice_id": settings.get("voice_id"),
             "speed": settings.get("speed", 1.0), "preset": preset},
        )  # fmt: skip
        checked = load_class(engine.import_path)().validate_input("audio.narrate", raw)
    except PresetError as exc:
        raise ChapterError(str(exc)) from exc
    except EngineError as exc:
        raise ChapterError(exc.user_message) from exc
    return {
        "engine_id": engine.descriptor.id,
        "voice_id": checked["voice_id"],
        "speed": checked.get("speed", 1.0),
        "use_dictionary": bool(settings.get("use_dictionary", True)),
        "preset": preset,
    }


def _same_settings(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(a.get(k) == b.get(k) for k in SETTING_KEYS)


# ------------------------------------------------------------------ create / edit


def create(
    conn: sqlite3.Connection,
    registry: EngineRegistry,
    *,
    project_id: str,
    title: str,
    script: str,
    settings: dict[str, Any],
) -> str:
    project_svc.get(conn, project_id)
    clean = normalize_settings(conn, registry, settings)
    if len(script) > MAX_SCRIPT_CHARS:
        raise ChapterError(f"Chapters can hold up to {MAX_SCRIPT_CHARS:,} characters.")
    chapter_id = new_id("chp")
    now = utcnow()
    position = conn.execute(
        "SELECT COALESCE(MAX(position) + 1, 0) AS n FROM chapters WHERE project_id = ?",
        (project_id,),
    ).fetchone()["n"]
    with transaction(conn):
        conn.execute(
            "INSERT INTO chapters(id, project_id, title, script, settings, position, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chapter_id, project_id, title.strip(), script, dumps(clean), position, now, now),
        )
        _resegment(conn, chapter_id, script)
    return chapter_id


def update(
    conn: sqlite3.Connection,
    registry: EngineRegistry,
    chapter_id: str,
    *,
    title: str | None = None,
    script: str | None = None,
    settings: dict[str, Any] | None = None,
    mix: dict[str, Any] | None = None,
) -> None:
    chapter = _chapter_row(conn, chapter_id)
    sets: list[str] = []
    params: list[Any] = []
    if title is not None:
        sets.append("title = ?")
        params.append(title.strip())
    if settings is not None:
        sets.append("settings = ?")
        params.append(
            dumps(normalize_settings(conn, registry, {**chapter["settings"], **settings}))
        )
    if mix is not None:
        sets.append("mix = ?")
        params.append(dumps({**chapter["mix"], **_validate_mix(mix)}))
    if script is not None:
        if len(script) > MAX_SCRIPT_CHARS:
            raise ChapterError(f"Chapters can hold up to {MAX_SCRIPT_CHARS:,} characters.")
        sets.append("script = ?")
        params.append(script)
    with transaction(conn):
        if sets:
            sets.append("updated_at = ?")
            params.extend([utcnow(), chapter_id])
            # Column names are fixed literals above, never user input.
            conn.execute(f"UPDATE chapters SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
        if script is not None and script != chapter["script"]:
            _resegment(conn, chapter_id, script)


def _validate_mix(mix: dict[str, Any]) -> dict[str, Any]:
    out = {}
    if "duck_db" in mix:
        value = float(mix["duck_db"])
        if not -30 <= value <= 0:
            raise ChapterError("Music lowering must be between 0 and -30 dB.")
        out["duck_db"] = value
    return out


def _resegment(conn: sqlite3.Connection, chapter_id: str, script: str) -> None:
    """Re-split the script, keeping segments (and their takes) whose text is unchanged.

    Must run inside the caller's transaction.
    """
    fresh = group(segment(script)) if script.strip() else []
    old = conn.execute(
        "SELECT id, text FROM segments WHERE chapter_id = ? ORDER BY position", (chapter_id,)
    ).fetchall()
    by_text: dict[str, list[str]] = defaultdict(list)
    for row in old:
        by_text[row["text"]].append(row["id"])

    plan: list[tuple[str | None, Any]] = []
    for seg in fresh:
        ids = by_text.get(seg.text)
        plan.append((ids.pop(0) if ids else None, seg))
    keep = {sid for sid, _ in plan if sid}
    for row in old:
        if row["id"] not in keep:
            conn.execute("DELETE FROM segments WHERE id = ?", (row["id"],))
    # Move kept rows out of the way of the UNIQUE(chapter_id, position) constraint.
    conn.execute("UPDATE segments SET position = -1 - position WHERE chapter_id = ?", (chapter_id,))
    now = utcnow()
    for position, (sid, seg) in enumerate(plan):
        if sid:
            conn.execute(
                "UPDATE segments SET position = ?, paragraph = ?, pause_after_s = ? WHERE id = ?",
                (position, seg.paragraph, seg.pause_after_s, sid),
            )
        else:
            conn.execute(
                "INSERT INTO segments(id, chapter_id, position, paragraph, text, pause_after_s,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id("seg"), chapter_id, position, seg.paragraph, seg.text,
                 seg.pause_after_s, now),
            )  # fmt: skip


def delete(conn: sqlite3.Connection, chapter_id: str) -> None:
    _chapter_row(conn, chapter_id)
    if open_job_ids(conn, chapter_id):
        raise ChapterError("Stop this chapter's generation before deleting it.")
    # Takes and segments cascade. Generated files stay in the project's outputs.
    conn.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))


# ------------------------------------------------------------------ read


def _segments_with_takes(conn: sqlite3.Connection, chapter_id: str) -> list[dict[str, Any]]:
    segments = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM segments WHERE chapter_id = ? ORDER BY position", (chapter_id,)
        )
    ]
    takes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute(
        "SELECT t.*, j.state AS job_state, j.outputs AS job_outputs, j.metrics AS job_metrics,"
        " j.error_message AS job_error, j.progress AS job_progress"
        " FROM takes t JOIN segments s ON s.id = t.segment_id JOIN jobs j ON j.id = t.job_id"
        " WHERE s.chapter_id = ? ORDER BY t.created_at, t.rowid",
        (chapter_id,),
    ):
        outputs = loads(r["job_outputs"], []) or []
        metrics = loads(r["job_metrics"], {}) or {}
        takes[r["segment_id"]].append({
            "id": r["id"],
            "job_id": r["job_id"],
            "state": r["job_state"],
            "asset_id": outputs[0] if outputs else None,
            "duration_s": metrics.get("audio_s"),
            "error": r["job_error"],
            "progress": r["job_progress"],
            "settings": loads(r["settings"], {}),
            "text": r["text"],
            "reviewed": bool(r["reviewed"]),
            "created_at": r["created_at"],
        })  # fmt: skip
    for s in segments:
        s["takes"] = takes.get(s["id"], [])
    return segments


def _derive(conn: sqlite3.Connection, chapter: dict[str, Any], seg: dict[str, Any]) -> str:
    takes = seg["takes"]
    by_id = {t["id"]: t for t in takes}
    current = by_id.get(seg["current_take_id"]) if seg["current_take_id"] else None
    if current is not None and current["state"] != "completed":
        current = None
    if current is None:
        done = [t for t in takes if t["state"] == "completed"]
        if done:
            # First finished take is used automatically (persisted so it is stable).
            current = done[-1]
            conn.execute(
                "UPDATE segments SET current_take_id = ? WHERE id = ?", (current["id"], seg["id"])
            )
            conn.execute("UPDATE takes SET reviewed = 1 WHERE id = ?", (current["id"],))
            current["reviewed"] = True
            seg["current_take_id"] = current["id"]
    seg["current"] = current
    latest = takes[-1] if takes else None
    if latest is None:
        return "pending"
    if latest["state"] in OPEN_STATES:
        return "generating"
    if latest["state"] in BAD_STATES:
        return "failed"
    if current is None:
        return "pending"
    if latest["state"] == "completed" and latest["id"] != current["id"] and not latest["reviewed"]:
        return "needs_review"
    if (
        not _same_settings(current["settings"], chapter["settings"])
        or current["text"] != seg["text"]
    ):
        return "outdated"
    return "ready"


def detail(conn: sqlite3.Connection, chapter_id: str) -> dict[str, Any]:
    chapter = _chapter_row(conn, chapter_id)
    segments = _segments_with_takes(conn, chapter_id)
    counts: dict[str, int] = defaultdict(int)
    duration = 0.0
    for seg in segments:
        seg["status"] = _derive(conn, chapter, seg)
        seg["language"], seg["language_confidence"] = classify(seg["text"])
        counts[seg["status"]] += 1
        if seg["current"] and seg["current"]["duration_s"]:
            duration += float(seg["current"]["duration_s"]) + seg["pause_after_s"]
    tracks = [
        {**dict(r), "loop": bool(r["loop"]), "duck": bool(r["duck"])}
        for r in conn.execute(
            "SELECT t.*, a.original_name, a.media_type FROM mix_tracks t"
            " JOIN assets a ON a.id = t.asset_id WHERE t.chapter_id = ? ORDER BY t.created_at",
            (chapter_id,),
        )
    ]
    exports = [
        repo.row_to_job(r)
        for r in conn.execute(
            "SELECT * FROM jobs WHERE task = 'audio.assemble' AND json_extract(inputs,"
            " '$.chapter_id') = ? ORDER BY created_at DESC LIMIT 20",
            (chapter_id,),
        )
    ]
    return {
        **chapter,
        "segments": segments,
        "summary": {
            "segments": len(segments),
            "counts": dict(counts),
            "duration_s": round(duration, 1),
            "exportable": len(segments) > 0 and all(s["current"] is not None for s in segments),
        },
        "tracks": tracks,
        "exports": exports,
    }


def list_chapters(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.id, c.title, c.position, c.updated_at,"
        " (SELECT COUNT(*) FROM segments s WHERE s.chapter_id = c.id) AS segments"
        " FROM chapters c WHERE c.project_id = ? ORDER BY c.position",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ generate / review


def _queue_take(
    conn: sqlite3.Connection,
    registry: EngineRegistry,
    chapter: dict[str, Any],
    seg: dict[str, Any],
) -> str:
    settings = chapter["settings"]
    engine = _engine(conn, registry, settings["engine_id"])
    raw = {
        "text": seg["text"],
        "voice_id": settings["voice_id"],
        "speed": settings["speed"],
        "use_dictionary": settings["use_dictionary"],
        "preview": False,
        "preset": settings.get("preset"),
    }
    raw = apply_preset(engine.descriptor, raw)
    raw, extras = pronunciation_svc.prepare_narration(
        conn, engine.descriptor, chapter["project_id"], raw
    )
    try:
        inputs = load_class(engine.import_path)().validate_input("audio.narrate", raw)
    except EngineError as exc:
        raise ChapterError(f"Sentence {seg['position'] + 1}: {exc.user_message}") from exc
    inputs.update(extras)
    inputs.update({"chapter_id": chapter["id"], "segment_id": seg["id"]})
    if settings.get("preset"):
        inputs["preset"] = settings["preset"]
    job_id = repo.insert(
        conn,
        project_id=chapter["project_id"],
        task="audio.narrate",
        engine_id=engine.descriptor.id,
        inputs=inputs,
    )
    conn.execute(
        "INSERT INTO takes(id, segment_id, job_id, settings, text, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_id("tak"), seg["id"], job_id, dumps(settings), seg["text"], utcnow()),
    )
    return job_id


def generate(
    conn: sqlite3.Connection, registry: EngineRegistry, chapter_id: str, scope: str
) -> list[str]:
    if scope not in GENERATE_SCOPES:
        raise ChapterError(f"Scope must be one of {GENERATE_SCOPES}.")
    info = detail(conn, chapter_id)
    wanted = {
        "needed": {"pending", "failed", "outdated"},
        "failed": {"failed"},
        "all": {"pending", "failed", "outdated", "ready", "needs_review"},
    }[scope]
    targets = [s for s in info["segments"] if s["status"] in wanted]
    job_ids: list[str] = []
    with transaction(conn):
        for seg in targets:
            job_ids.append(_queue_take(conn, registry, info, seg))
    return job_ids


def regenerate_segment(conn: sqlite3.Connection, registry: EngineRegistry, segment_id: str) -> str:
    row = conn.execute("SELECT chapter_id FROM segments WHERE id = ?", (segment_id,)).fetchone()
    if row is None:
        raise ChapterNotFound(segment_id)
    info = detail(conn, row["chapter_id"])
    seg = next(s for s in info["segments"] if s["id"] == segment_id)
    if seg["status"] == "generating":
        raise ChapterError("This sentence is already being generated.")
    with transaction(conn):
        return _queue_take(conn, registry, info, seg)


def choose_take(conn: sqlite3.Connection, segment_id: str, take_id: str) -> str:
    """Use a take for the chapter (review: "use new" or "keep old")."""
    take = conn.execute(
        "SELECT t.id, j.state FROM takes t JOIN jobs j ON j.id = t.job_id"
        " WHERE t.id = ? AND t.segment_id = ?",
        (take_id, segment_id),
    ).fetchone()
    if take is None:
        raise ChapterNotFound(take_id)
    if take["state"] != "completed":
        raise ChapterError("Only finished takes can be used.")
    row = conn.execute("SELECT chapter_id FROM segments WHERE id = ?", (segment_id,)).fetchone()
    with transaction(conn):
        conn.execute("UPDATE segments SET current_take_id = ? WHERE id = ?", (take_id, segment_id))
        # Choosing settles the review for every finished take of this segment.
        conn.execute(
            "UPDATE takes SET reviewed = 1 WHERE segment_id = ? AND job_id IN"
            " (SELECT id FROM jobs WHERE state = 'completed')",
            (segment_id,),
        )
    return row["chapter_id"]


def open_job_ids(conn: sqlite3.Connection, chapter_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT j.id, j.state FROM takes t JOIN segments s ON s.id = t.segment_id"
        " JOIN jobs j ON j.id = t.job_id WHERE s.chapter_id = ?",
        (chapter_id,),
    ).fetchall()
    return [r["id"] for r in rows if r["state"] in OPEN_STATES]
