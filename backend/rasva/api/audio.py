"""Audio helpers: automatic engine and voice choice (A5)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
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
