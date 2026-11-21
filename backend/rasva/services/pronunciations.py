"""Pronunciation dictionary storage and narration input preparation (PRD §10)."""

from __future__ import annotations

import sqlite3
from typing import Any

from rasva.audio.pronunciation import Applied, Entry, applicable, apply, apply_by_sentence
from rasva.config import Paths
from rasva.db import dumps, loads, new_id, utcnow
from rasva.engines.contract import EngineDescriptor
from rasva.services import projects as project_svc

# PRD §10.3 initial priority names, with IAST as a phonetic hint.
PRIORITY_NAMES: list[tuple[str, str]] = [
    ("कृष्ण", "Kṛṣṇa"),
    ("अर्जुन", "Arjuna"),
    ("कर्ण", "Karṇa"),
    ("भीष्म", "Bhīṣma"),
    ("युधिष्ठिर", "Yudhiṣṭhira"),
    ("दुर्योधन", "Duryodhana"),
    ("द्रोणाचार्य", "Droṇācārya"),
    ("धृतराष्ट्र", "Dhṛtarāṣṭra"),
    ("अश्वत्थामा", "Aśvatthāmā"),
    ("कुंती", "Kuntī"),
]

PREVIEW_PROJECT = "Pronunciation previews"
# Engines declaring this feature read each sentence in its own language (by
# script, or the user's override) whatever the voice; others read everything in
# the voice's language.
PER_SENTENCE = "language_per_sentence"
SPOKEN_LANGUAGES = ("en", "hi")
EXTRA_KEYS = ("original_text", "pronunciations")


class PronunciationError(ValueError):
    pass


class EntryNotFound(Exception):
    pass


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["written_forms"] = loads(d["written_forms"], [])
    d["examples"] = loads(d["examples"], [])
    for k in ("whole_word", "case_sensitive", "approved"):
        d[k] = bool(d[k])
    return d


def to_entry(d: dict[str, Any]) -> Entry:
    return Entry(
        id=d["id"],
        written_forms=tuple(d["written_forms"]),
        spoken=d["spoken"],
        language=d["language"],
        whole_word=d["whole_word"],
        case_sensitive=d["case_sensitive"],
        project_scoped=d["project_id"] is not None,
    )


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if "written_forms" in out:
        forms = [f.strip() for f in out["written_forms"] if isinstance(f, str) and f.strip()]
        forms = list(dict.fromkeys(forms))
        if not 1 <= len(forms) <= 10:
            raise PronunciationError("Add between 1 and 10 written forms.")
        if any(len(f) > 100 or "\n" in f for f in forms):
            raise PronunciationError("Each written form must be one line, up to 100 characters.")
        out["written_forms"] = forms
    if "spoken" in out:
        spoken = (out["spoken"] or "").strip()
        if not 1 <= len(spoken) <= 200 or "\n" in spoken:
            raise PronunciationError("“Say it as” must be one line, up to 200 characters.")
        out["spoken"] = spoken
    if "language" in out and out["language"] not in ("hi", "en", "any"):
        raise PronunciationError("Language must be Hindi voices, English voices or all voices.")
    if "examples" in out:
        examples = [e.strip() for e in out["examples"] if isinstance(e, str) and e.strip()]
        if len(examples) > 10 or any(len(e) > 300 for e in examples):
            raise PronunciationError("Use up to 10 example sentences of 300 characters each.")
        out["examples"] = examples
    if "phonetic_hint" in out and len(out["phonetic_hint"] or "") > 200:
        raise PronunciationError("The phonetic hint is too long.")
    if "notes" in out and len(out["notes"] or "") > 2000:
        raise PronunciationError("Notes are too long.")
    return out


# ------------------------------------------------------------------ CRUD


def get(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM pronunciation_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise EntryNotFound(entry_id)
    return _row(row)


def list_entries(
    conn: sqlite3.Connection, project_id: str | None = None, query: str | None = None
) -> list[dict[str, Any]]:
    """Global entries plus, if given, that project's entries."""
    rows = conn.execute(
        "SELECT * FROM pronunciation_entries WHERE project_id IS NULL OR project_id = ?"
        " ORDER BY updated_at DESC",
        (project_id,),
    ).fetchall()
    entries = [_row(r) for r in rows]
    if query:
        q = query.casefold()
        entries = [
            e
            for e in entries
            if any(q in f.casefold() for f in e["written_forms"]) or q in e["spoken"].casefold()
        ]
    return entries


def create(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    data = _validate(data)
    for key in ("written_forms", "spoken", "language"):
        if key not in data:
            raise PronunciationError(f"Missing {key.replace('_', ' ')}.")
    if data.get("project_id"):
        project_svc.get(conn, data["project_id"])
    entry_id = new_id("pron")
    now = utcnow()
    conn.execute(
        "INSERT INTO pronunciation_entries(id, project_id, written_forms, spoken, phonetic_hint,"
        " language, whole_word, case_sensitive, approved, examples, notes, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry_id, data.get("project_id"), dumps(data["written_forms"]), data["spoken"],
            data.get("phonetic_hint", ""), data["language"], int(data.get("whole_word", True)),
            int(data.get("case_sensitive", False)), int(data.get("approved", False)),
            dumps(data.get("examples", [])), data.get("notes", ""), now, now,
        ),
    )  # fmt: skip
    return get(conn, entry_id)


def update(conn: sqlite3.Connection, entry_id: str, data: dict[str, Any]) -> dict[str, Any]:
    get(conn, entry_id)
    data = _validate({k: v for k, v in data.items() if v is not None})
    allowed = {
        "written_forms", "spoken", "phonetic_hint", "language", "whole_word",
        "case_sensitive", "approved", "examples", "notes",
    }  # fmt: skip
    sets, params = [], []
    for key, value in data.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        if key in ("written_forms", "examples"):
            params.append(dumps(value))
        elif key in ("whole_word", "case_sensitive", "approved"):
            params.append(int(bool(value)))
        else:
            params.append(value)
    if sets:
        sets.append("updated_at = ?")
        params.extend([utcnow(), entry_id])
        # Column names come from the fixed `allowed` set, never from input.
        conn.execute(
            f"UPDATE pronunciation_entries SET {', '.join(sets)} WHERE id = ?",  # noqa: S608
            params,
        )
    return get(conn, entry_id)


def delete(conn: sqlite3.Connection, entry_id: str) -> None:
    get(conn, entry_id)
    conn.execute("DELETE FROM pronunciation_entries WHERE id = ?", (entry_id,))


def suggestions(conn: sqlite3.Connection, project_id: str | None) -> list[dict[str, str]]:
    covered = {f for e in list_entries(conn, project_id) for f in e["written_forms"]}
    return [{"written": w, "hint": h} for w, h in PRIORITY_NAMES if w not in covered]


# ------------------------------------------------------------------ application


def voice_language(descriptor: EngineDescriptor, voice_id: Any) -> str | None:
    voice = next((v for v in descriptor.voices if v.id == voice_id), None)
    return voice.language if voice else None


def speaks_per_sentence(descriptor: EngineDescriptor) -> bool:
    return PER_SENTENCE in descriptor.features


def _apply(
    entries: list[Entry], text: str, language: str | None, override: str | None
) -> tuple[str, list[Applied]]:
    if language is None:  # per sentence
        return apply_by_sentence(text, entries, override)
    return apply(text, applicable(entries, language))


def check(
    conn: sqlite3.Connection,
    project_id: str | None,
    text: str,
    language: str | None,
    extra_entry_ids: tuple[str, ...] = (),
    override: str | None = None,
) -> dict[str, Any]:
    """Show what the dictionary will change, and which unapproved entries match.

    ``language`` None chooses entries per sentence (engines that speak both),
    with ``override`` as the user's choice for the whole text.
    """
    all_entries = list_entries(conn, project_id)
    approved = [to_entry(e) for e in all_entries if e["approved"] or e["id"] in extra_entry_ids]
    normalized, applied = _apply(approved, text, language, override)
    pending = [to_entry(e) for e in all_entries if not e["approved"]]
    _, unapproved = _apply(pending, text, language, override)
    return {
        "normalized": normalized,
        "applied": [a.__dict__ for a in applied],
        "unapproved_matches": [a.__dict__ for a in unapproved],
    }


def prepare_narration(
    conn: sqlite3.Connection,
    descriptor: EngineDescriptor,
    project_id: str | None,
    inputs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply approved pronunciations to narration text.

    Returns (inputs for the engine, extras to store with the job). The user's
    original text is kept in extras["original_text"]; the engine only ever sees
    the transformed text. Every substitution records the language it was chosen
    for. Retries reuse the stored engine text (and ``language``), so they repeat
    the same choices even if the dictionary changed since.
    """
    text = inputs.get("text")
    use = inputs.get("use_dictionary", True)
    engine_inputs = {k: v for k, v in inputs.items() if k not in (*EXTRA_KEYS, "use_dictionary")}
    if not use or not isinstance(text, str):
        return engine_inputs, {}
    if speaks_per_sentence(descriptor):
        # Each sentence's language, not the voice's: a voice cloned from a Hindi
        # recording still reads English sentences in English.
        language = None
        override = inputs.get("language") if inputs.get("language") in SPOKEN_LANGUAGES else None
    else:
        override = None
        language = voice_language(descriptor, inputs.get("voice_id"))
        if language is None and isinstance(inputs.get("reference"), dict):
            language = inputs["reference"].get("language")
        if language is None:
            return engine_inputs, {}
    result = check(conn, project_id, text, language, override=override)
    if not result["applied"]:
        return engine_inputs, {}
    engine_inputs["text"] = result["normalized"]
    return engine_inputs, {"original_text": text, "pronunciations": result["applied"]}


def preview_project(conn: sqlite3.Connection, paths: Paths) -> str:
    row = conn.execute(
        "SELECT id FROM projects WHERE kind = 'system' AND name = ?", (PREVIEW_PROJECT,)
    ).fetchone()
    if row:
        return row["id"]
    return project_svc.create(conn, paths, name=PREVIEW_PROJECT, module="audio", kind="system")[
        "id"
    ]
