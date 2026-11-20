"""Consented voice profiles (PRD §11, phase A3).

Rules:
- No profile without consent: either the owner's own voice, or a named
  speaker's permission. The consent statement is stored with the profile and
  logged (without audio or text) so the history survives deletion.
- References are uploads in a hidden system project, protected from cleanup.
- Deleting a profile deletes its recording immediately (PRD §11 step 7).
- Only engines that declare the "voice_cloning" feature receive a profile; they
  get the reference file path, its transcript and its language.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from rasva.audio import reference
from rasva.config import Paths
from rasva.db import dumps, loads, new_id, transaction, utcnow
from rasva.engines.contract import EngineDescriptor
from rasva.jobs.states import TERMINAL
from rasva.services import assets as asset_svc
from rasva.services import projects as project_svc

REFERENCE_PROJECT = "Voice references"
CLONING = "voice_cloning"
CONSENT_STATEMENT = (
    "I confirm this is my own voice, or that I have the speaker's permission to create "
    "and use a synthetic copy of it. I will not use it to deceive, impersonate or defraud anyone."
)


class VoiceError(ValueError):
    pass


class VoiceNotFound(Exception):
    pass


def reference_project(conn: sqlite3.Connection, paths: Paths) -> str:
    row = conn.execute(
        "SELECT id FROM projects WHERE kind = 'system' AND name = ?", (REFERENCE_PROJECT,)
    ).fetchone()
    if row:
        return row["id"]
    return project_svc.create(conn, paths, name=REFERENCE_PROJECT, module="audio", kind="system")[
        "id"
    ]


def analyze_asset(
    conn: sqlite3.Connection, paths: Paths, asset_id: str, ffmpeg: str | None
) -> dict[str, Any]:
    asset = asset_svc.get(conn, asset_id)
    if not asset["media_type"].startswith("audio/"):
        raise VoiceError("Choose an audio recording.")
    path = asset_svc.resolve(paths, asset["rel_path"])
    try:
        samples, rate = reference.load_mono(str(path), ffmpeg)
    except ValueError as exc:
        raise VoiceError(str(exc)) from exc
    return reference.analyze(samples, rate)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["quality"] = loads(d["quality"], {})
    d["consent"] = loads(d["consent"], {})
    d["transcript_confirmed"] = bool(d["transcript_confirmed"])
    return d


def get(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise VoiceNotFound(profile_id)
    return _row(row)


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [_row(r) for r in conn.execute("SELECT * FROM voice_profiles ORDER BY created_at DESC")]


def create(
    conn: sqlite3.Connection,
    paths: Paths,
    *,
    name: str,
    language: str,
    reference_asset_id: str,
    transcript: str,
    transcript_confirmed: bool,
    consent: dict[str, Any],
    recorded_from: str,
    ffmpeg: str | None,
    description: str = "",
) -> dict[str, Any]:
    basis = consent.get("basis")
    speaker = str(consent.get("speaker_name") or "").strip()
    if basis not in ("self", "permission"):
        raise VoiceError("Confirm whose voice this is.")
    if not consent.get("accepted"):
        raise VoiceError("Accept the consent statement to create a voice.")
    if basis == "permission" and not speaker:
        raise VoiceError("Enter the name of the person who gave permission.")
    if language not in ("hi", "en", "any"):
        raise VoiceError("Choose the voice's language.")
    if not transcript_confirmed:
        raise VoiceError("Confirm that the transcript matches the recording word for word.")
    asset = asset_svc.get(conn, reference_asset_id)
    if asset["project_id"] != reference_project(conn, paths) or asset["kind"] != "reference":
        raise VoiceError("Use a recording added for voices.")
    if conn.execute(
        "SELECT 1 FROM voice_profiles WHERE reference_asset_id = ?", (reference_asset_id,)
    ).fetchone():
        raise VoiceError("That recording already belongs to a voice.")

    quality = analyze_asset(conn, paths, reference_asset_id, ffmpeg)
    if quality["level"] == "error":
        raise VoiceError(
            " ".join(i["message"] for i in quality["issues"] if i["severity"] == "error")
        )
    transcript = " ".join(transcript.split())
    issues = reference.transcript_check(transcript, quality.get("speech_s", 0))
    if any(i["severity"] == "error" for i in issues):
        raise VoiceError(issues[0]["message"])
    quality["transcript_issues"] = issues

    now = utcnow()
    record = {
        "basis": basis, "speaker_name": speaker or "Owner (self)",
        "statement": CONSENT_STATEMENT, "recorded_at": now, "recorded_from": recorded_from,
    }  # fmt: skip
    profile_id = new_id("voc")
    with transaction(conn):
        conn.execute(
            "INSERT INTO voice_profiles(id, name, description, language, reference_asset_id,"
            " transcript, transcript_confirmed, quality, consent, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (profile_id, name.strip()[:100], description[:500], language, reference_asset_id,
             transcript, dumps(quality), dumps(record), now, now),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO consent_log(profile_id, profile_name, event, basis, speaker_name, at)"
            " VALUES (?, ?, 'granted', ?, ?, ?)",
            (profile_id, name.strip()[:100], basis, record["speaker_name"], now),
        )
    return get(conn, profile_id)


def rename(
    conn: sqlite3.Connection, profile_id: str, name: str, description: str | None
) -> dict[str, Any]:
    get(conn, profile_id)
    conn.execute(
        "UPDATE voice_profiles SET name = ?, description = COALESCE(?, description),"
        " updated_at = ? WHERE id = ?",
        (name.strip()[:100], description, utcnow(), profile_id),
    )
    return get(conn, profile_id)


def _open_jobs_using(conn: sqlite3.Connection, profile_id: str) -> int:
    terminal = {s.value for s in TERMINAL}
    rows = conn.execute(
        "SELECT state FROM jobs WHERE json_extract(inputs, '$.voice_profile_id') = ?",
        (profile_id,),
    ).fetchall()
    return sum(1 for r in rows if r["state"] not in terminal)


def delete(conn: sqlite3.Connection, paths: Paths, profile_id: str) -> None:
    profile = get(conn, profile_id)
    if _open_jobs_using(conn, profile_id):
        raise VoiceError(
            "This voice is being used by jobs that are still running. Stop them first."
        )
    asset = asset_svc.get(conn, profile["reference_asset_id"])
    path = asset_svc.resolve(paths, asset["rel_path"])
    with transaction(conn):
        conn.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
        conn.execute("DELETE FROM assets WHERE id = ?", (asset["id"],))
        conn.execute(
            "INSERT INTO consent_log(profile_id, profile_name, event, basis, speaker_name, at)"
            " VALUES (?, ?, 'deleted', ?, ?, ?)",
            (profile_id, profile["name"], profile["consent"].get("basis", ""),
             profile["consent"].get("speaker_name", ""), utcnow()),
        )  # fmt: skip
    path.unlink(missing_ok=True)


def consent_log(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM consent_log ORDER BY id DESC")]


def resolve_profile(
    conn: sqlite3.Connection, paths: Paths, descriptor: EngineDescriptor, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Turn inputs.voice_profile_id into the reference an engine needs."""
    profile_id = inputs.get("voice_profile_id")
    if not profile_id:
        return inputs
    if CLONING not in descriptor.features:
        raise VoiceError(f"{descriptor.display_name} can't use your own voices.")
    try:
        profile = get(conn, profile_id)
    except VoiceNotFound as exc:
        raise VoiceError("That voice was deleted. Choose another voice.") from exc
    asset = asset_svc.get(conn, profile["reference_asset_id"])
    path = asset_svc.resolve(paths, asset["rel_path"])
    if not Path(path).is_file():
        raise VoiceError("This voice's recording is missing. Record it again.")
    out = {k: v for k, v in inputs.items() if k != "voice_id"}
    out["reference"] = {
        "path": str(path), "transcript": profile["transcript"], "language": profile["language"],
    }  # fmt: skip
    return out
