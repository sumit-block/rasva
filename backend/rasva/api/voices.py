"""Voice profiles API (PRD §11, phase A3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from rasva.api.deps import DB, Client, State
from rasva.api.errors import ApiError
from rasva.services import assets as asset_svc
from rasva.services import voices as svc
from rasva.services.media import find_ffmpeg

router = APIRouter(tags=["voices"])


class Consent(BaseModel):
    basis: Literal["self", "permission"]
    speaker_name: str = Field(default="", max_length=100)
    accepted: bool


class VoiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    language: Literal["hi", "en", "any"]
    reference_asset_id: str
    transcript: str = Field(max_length=2000)
    transcript_confirmed: bool
    consent: Consent


class VoiceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    try:
        return fn(*args, **kw)
    except svc.VoiceError as exc:
        raise ApiError(422, "invalid_voice", str(exc)) from exc
    except svc.VoiceNotFound as exc:
        raise ApiError(404, "voice_not_found", "That voice does not exist.") from exc


@router.get("/voices/consent-statement")
def statement() -> dict[str, str]:
    return {"statement": svc.CONSENT_STATEMENT}


@router.post("/voices/reference", status_code=201)
def upload_reference(state: State, conn: DB, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Store a reference recording and report its quality (nothing is enrolled yet)."""
    project_id = svc.reference_project(conn, state.settings.paths)
    asset = asset_svc.store_upload(
        conn, state.settings.paths, project_id=project_id, kind="reference",
        original_name=file.filename or "recording.wav", stream=file.file,
        max_bytes=min(state.settings.max_upload_bytes, 100 * 1024 * 1024),
        preflight=lambda n: state.preflight(conn, n, "upload"),
    )  # fmt: skip
    if not asset["media_type"].startswith("audio/"):
        conn.execute("DELETE FROM assets WHERE id = ?", (asset["id"],))
        asset_svc.resolve(state.settings.paths, asset["rel_path"]).unlink(missing_ok=True)
        raise ApiError(422, "not_audio", "Choose an audio recording.")
    try:
        quality = svc.analyze_asset(conn, state.settings.paths, asset["id"], find_ffmpeg())
    except svc.VoiceError as exc:
        discard_reference(asset["id"], state, conn)
        raise ApiError(422, "unreadable", str(exc)) from exc
    state.storage.invalidate()
    return {"asset": asset, "quality": quality}


@router.delete("/voices/reference/{asset_id}", status_code=204)
def discard_reference(asset_id: str, state: State, conn: DB) -> None:
    """Throw away a recording that hasn't become a voice (e.g. "record again")."""
    asset = asset_svc.get(conn, asset_id)
    if asset["project_id"] != svc.reference_project(conn, state.settings.paths):
        raise ApiError(422, "not_reference", "That isn't a voice recording.")
    if conn.execute(
        "SELECT 1 FROM voice_profiles WHERE reference_asset_id = ?", (asset_id,)
    ).fetchone():
        raise ApiError(409, "in_use", "Delete the voice to delete its recording.")
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    asset_svc.resolve(state.settings.paths, asset["rel_path"]).unlink(missing_ok=True)
    state.storage.invalidate()


@router.get("/voices")
def list_voices(conn: DB) -> list[dict[str, Any]]:
    return svc.list_profiles(conn)


@router.get("/voices/consent-log")
def consent_log(conn: DB) -> list[dict[str, Any]]:
    return svc.consent_log(conn)


@router.get("/voices/{profile_id}")
def get_voice(profile_id: str, conn: DB) -> dict[str, Any]:
    return _call(svc.get, conn, profile_id)


@router.post("/voices", status_code=201)
def create_voice(body: VoiceCreate, state: State, conn: DB, client: Client) -> dict[str, Any]:
    source = "this Mac" if client.get("kind") == "local" else (
        f"paired device “{(client.get('device') or {}).get('name', 'unknown')}”"
    )  # fmt: skip
    return _call(
        svc.create, conn, state.settings.paths, name=body.name, language=body.language,
        reference_asset_id=body.reference_asset_id, transcript=body.transcript,
        transcript_confirmed=body.transcript_confirmed, consent=body.consent.model_dump(),
        recorded_from=source, ffmpeg=find_ffmpeg(), description=body.description,
    )  # fmt: skip


@router.patch("/voices/{profile_id}")
def update_voice(profile_id: str, body: VoiceUpdate, conn: DB) -> dict[str, Any]:
    return _call(svc.rename, conn, profile_id, body.name, body.description)


@router.delete("/voices/{profile_id}", status_code=204)
def delete_voice(profile_id: str, state: State, conn: DB) -> None:
    _call(svc.delete, conn, state.settings.paths, profile_id)
    state.storage.invalidate()
