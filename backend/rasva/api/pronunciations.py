"""Pronunciation dictionary API (PRD §10.3, A2-005, A2-006, A2-009)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.audio.pronunciation import applicable, apply, apply_by_sentence
from rasva.engines.registry import load_class
from rasva.jobs import repository as repo
from rasva.services import pronunciations as svc
from rasva.services.installer import ModelStore

router = APIRouter(tags=["pronunciations"])


class EntryCreate(BaseModel):
    written_forms: list[str]
    spoken: str
    language: Literal["hi", "en", "any"]
    project_id: str | None = None
    phonetic_hint: str = ""
    whole_word: bool = True
    case_sensitive: bool = False
    approved: bool = False
    examples: list[str] = []
    notes: str = ""


class EntryUpdate(BaseModel):
    written_forms: list[str] | None = None
    spoken: str | None = None
    language: Literal["hi", "en", "any"] | None = None
    phonetic_hint: str | None = None
    whole_word: bool | None = None
    case_sensitive: bool | None = None
    approved: bool | None = None
    examples: list[str] | None = None
    notes: str | None = None


class CheckRequest(BaseModel):
    text: str = Field(max_length=20_000)
    language: Literal["hi", "en"]
    project_id: str | None = None


class PreviewRequest(BaseModel):
    engine_id: str
    voice_id: str
    example: int | None = None  # index into the entry's examples; None = the word alone


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    try:
        return fn(*args, **kw)
    except svc.PronunciationError as exc:
        raise ApiError(422, "invalid_pronunciation", str(exc)) from exc
    except svc.EntryNotFound as exc:
        raise ApiError(
            404, "pronunciation_not_found", "That pronunciation does not exist."
        ) from exc


@router.get("/pronunciations")
def list_entries(
    conn: DB, project_id: str | None = None, q: str | None = None
) -> list[dict[str, Any]]:
    return svc.list_entries(conn, project_id, q)


@router.get("/pronunciations/suggestions")
def suggestions(conn: DB, project_id: str | None = None) -> list[dict[str, str]]:
    return svc.suggestions(conn, project_id)


@router.post("/pronunciations/check")
def check(body: CheckRequest, conn: DB) -> dict[str, Any]:
    return svc.check(conn, body.project_id, body.text, body.language)


@router.post("/pronunciations", status_code=201)
def create(body: EntryCreate, conn: DB) -> dict[str, Any]:
    return _call(svc.create, conn, body.model_dump())


@router.patch("/pronunciations/{entry_id}")
def update(entry_id: str, body: EntryUpdate, conn: DB) -> dict[str, Any]:
    return _call(svc.update, conn, entry_id, body.model_dump(exclude_none=True))


@router.delete("/pronunciations/{entry_id}", status_code=204)
def delete(entry_id: str, conn: DB) -> None:
    _call(svc.delete, conn, entry_id)


@router.post("/pronunciations/{entry_id}/preview", status_code=201)
def preview(entry_id: str, body: PreviewRequest, state: State, conn: DB) -> dict[str, Any]:
    """Hear an entry (approved or not) in a voice before approving it (A2-006)."""
    entry = _call(svc.get, conn, entry_id)
    engine = state.registry.get(body.engine_id)
    if engine is None or "audio.narrate" not in engine.descriptor.tasks:
        raise ApiError(422, "engine_unavailable", "Choose a voice engine.")
    if engine.descriptor.requires_install and not ModelStore(conn).is_installed(body.engine_id):
        raise ApiError(422, "engine_not_installed", "Install the voice engine first.")
    language = svc.voice_language(engine.descriptor, body.voice_id)
    if language is None:
        raise ApiError(422, "invalid_voice", "Choose one of the engine's voices.")
    per_sentence = svc.speaks_per_sentence(engine.descriptor)
    if not per_sentence and entry["language"] not in ("any", language):
        raise ApiError(
            422, "language_mismatch", "This pronunciation is for the other language's voices."
        )
    if body.example is not None:
        if not 0 <= body.example < len(entry["examples"]):
            raise ApiError(422, "invalid_example", "That example does not exist.")
        original = entry["examples"][body.example]
    else:
        original = entry["written_forms"][0]
    # Apply this entry plus the approved dictionary, exactly as a real narration would.
    others = [
        svc.to_entry(e)
        for e in svc.list_entries(conn, entry["project_id"])
        if e["approved"] and e["id"] != entry_id
    ]
    chosen = [*others, svc.to_entry(entry)]
    if per_sentence:
        # Engines that speak both choose entries by each sentence's language.
        text, applied = apply_by_sentence(original, chosen)
        if entry["language"] != "any" and all(a.entry_id != entry_id for a in applied):
            raise ApiError(
                422,
                "language_mismatch",
                "This pronunciation is for "
                + ("English" if entry["language"] == "en" else "Hindi")
                + " sentences, and this text is written in the other script.",
            )
    else:
        text, applied = apply(original, applicable(chosen, language))
    try:
        inputs = load_class(engine.import_path)().validate_input(
            "audio.narrate", {"text": text, "voice_id": body.voice_id, "preview": True}
        )
    except Exception as exc:  # EngineError carries a user message
        raise ApiError(422, "invalid_input", getattr(exc, "user_message", str(exc))) from exc
    inputs.update({
        "original_text": original,
        "pronunciations": [a.__dict__ for a in applied],
        "pronunciation_entry_id": entry_id,
    })  # fmt: skip
    job = repo.create(
        conn,
        project_id=svc.preview_project(conn, state.settings.paths),
        task="audio.narrate",
        engine_id=body.engine_id,
        inputs=inputs,
    )
    state.bus.publish({"type": "job", "job": job})
    state.orchestrator.notify()
    return job
