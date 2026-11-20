"""Audio helpers: automatic engine and voice choice (A5)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.audio import language as lang
from rasva.services import recommend, system_check

router = APIRouter(tags=["audio"])


@router.get("/audio/recommendation")
def recommendation(language: Literal["hi", "en"], state: State, conn: DB) -> dict[str, Any]:
    result = recommend.recommend(
        conn, state.registry, language, system_check.facts().total_memory_bytes
    )
    if result is None:
        raise ApiError(404, "no_engine", "No installed voice engine speaks this language yet.")
    return result


class TextBody(BaseModel):
    text: str = Field(max_length=200_000)


@router.post("/audio/analyze")
def analyze(body: TextBody) -> dict[str, Any]:
    """Per-sentence language with confidence and mixed-language spans (A2-002/003)."""
    return lang.analyze(body.text)


@router.post("/audio/transliterate")
def transliterate(body: TextBody) -> dict[str, Any]:
    """Devanagari suggestions for Roman Hinglish sentences. Nothing is applied (A2-004)."""
    return {"sentences": lang.transliteration_plan(body.text)}
