"""Long-form chapters, segment takes, mix tracks and export (PRD §12-13, A4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from rasva.api.deps import DB, State
from rasva.api.errors import ApiError
from rasva.db import new_id, utcnow
from rasva.engines.contract import EngineError
from rasva.engines.registry import load_class
from rasva.jobs import repository as repo
from rasva.services import assets as asset_svc
from rasva.services import chapters as svc
from rasva.services.media import find_ffmpeg

router = APIRouter(tags=["chapters"])

# Simple-mode presets describe mixing only; they never generate music (PRD §13).
MUSIC_PRESETS = {
    "quiet": {"gain_db": -24.0, "duck": True, "fade_in_s": 2.0, "fade_out_s": 3.0, "loop": True},
    "balanced": {"gain_db": -18.0, "duck": True, "fade_in_s": 2.0, "fade_out_s": 3.0, "loop": True},
    "present": {"gain_db": -12.0, "duck": True, "fade_in_s": 1.5, "fade_out_s": 2.0, "loop": True},
}
EFFECT_DEFAULTS = {
    "gain_db": -6.0,
    "duck": False,
    "fade_in_s": 0.0,
    "fade_out_s": 0.0,
    "loop": False,
}


class Settings(BaseModel):
    engine_id: str | None = None
    voice_id: str | None = None
    speed: float | None = None
    use_dictionary: bool | None = None
    # Quality preset id; empty string clears it (custom settings).
    preset: str | None = None
    # One of your own voices (engines with voice cloning); empty string clears it.
    voice_profile_id: str | None = None


class ChapterCreate(BaseModel):
    title: str = Field(default="Chapter 1", min_length=1, max_length=200)
    script: str = ""
    settings: Settings


class ChapterUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    script: str | None = None
    settings: Settings | None = None
    mix: dict[str, Any] | None = None


class Generate(BaseModel):
    scope: Literal["needed", "failed", "all"] = "needed"


class TrackCreate(BaseModel):
    kind: Literal["music", "effect"]
    asset_id: str
    preset: Literal["quiet", "balanced", "present"] | None = None
    anchor_segment: int | None = Field(default=None, ge=0)
    offset_s: float = Field(default=0, ge=0, le=36_000)
    gain_db: float | None = Field(default=None, ge=-60, le=6)
    fade_in_s: float | None = Field(default=None, ge=0, le=60)
    fade_out_s: float | None = Field(default=None, ge=0, le=60)
    loop: bool | None = None
    duck: bool | None = None


class TrackUpdate(BaseModel):
    anchor_segment: int | None = Field(default=None, ge=0)
    offset_s: float | None = Field(default=None, ge=0, le=36_000)
    gain_db: float | None = Field(default=None, ge=-60, le=6)
    fade_in_s: float | None = Field(default=None, ge=0, le=60)
    fade_out_s: float | None = Field(default=None, ge=0, le=60)
    loop: bool | None = None
    duck: bool | None = None


class Export(BaseModel):
    formats: list[Literal["wav", "mp3"]] = ["wav"]
    stems: bool = False
    include_music: bool = True


def _call(fn: Any, *args: Any, **kw: Any) -> Any:
    try:
        return fn(*args, **kw)
    except svc.ChapterError as exc:
        raise ApiError(422, "invalid_chapter", str(exc)) from exc
    except svc.ChapterNotFound as exc:
        raise ApiError(404, "chapter_not_found", "That chapter does not exist.") from exc


def _detail(conn: DB, chapter_id: str) -> dict[str, Any]:
    data = _call(svc.detail, conn, chapter_id)
    data["mp3_available"] = find_ffmpeg() is not None
    data["music_presets"] = list(MUSIC_PRESETS)
    return data


def _publish(state: State, conn: DB, job_ids: list[str]) -> None:
    for job_id in job_ids:
        state.bus.publish({"type": "job", "job": repo.get(conn, job_id)})
    if job_ids:
        state.orchestrator.notify()


@router.get("/projects/{project_id}/chapters")
def list_chapters(project_id: str, conn: DB) -> list[dict[str, Any]]:
    return svc.list_chapters(conn, project_id)


@router.post("/projects/{project_id}/chapters", status_code=201)
def create(project_id: str, body: ChapterCreate, state: State, conn: DB) -> dict[str, Any]:
    chapter_id = _call(
        svc.create, conn, state.registry, project_id=project_id, title=body.title,
        script=body.script, settings=body.settings.model_dump(exclude_none=True),
        paths=state.settings.paths,
    )  # fmt: skip
    return _detail(conn, chapter_id)


@router.get("/chapters/{chapter_id}")
def get(chapter_id: str, conn: DB) -> dict[str, Any]:
    return _detail(conn, chapter_id)


@router.patch("/chapters/{chapter_id}")
def update(chapter_id: str, body: ChapterUpdate, state: State, conn: DB) -> dict[str, Any]:
    _call(
        svc.update, conn, state.registry, chapter_id, title=body.title, script=body.script,
        settings=body.settings.model_dump(exclude_none=True) if body.settings else None,
        mix=body.mix, paths=state.settings.paths,
    )  # fmt: skip
    return _detail(conn, chapter_id)


@router.delete("/chapters/{chapter_id}", status_code=204)
def delete(chapter_id: str, conn: DB) -> None:
    _call(svc.delete, conn, chapter_id)


@router.post("/chapters/{chapter_id}/generate")
def generate(chapter_id: str, body: Generate, state: State, conn: DB) -> dict[str, Any]:
    job_ids = _call(
        svc.generate, conn, state.registry, chapter_id, body.scope, state.settings.paths
    )
    _publish(state, conn, job_ids)
    return _detail(conn, chapter_id)


@router.post("/chapters/{chapter_id}/cancel")
def cancel(chapter_id: str, state: State, conn: DB) -> dict[str, Any]:
    _call(svc.detail, conn, chapter_id)  # 404 if missing
    for job_id in svc.open_job_ids(conn, chapter_id):
        job = repo.request_cancel(conn, job_id)
        state.orchestrator.cancel(job_id)
        state.bus.publish({"type": "job", "job": job})
    return _detail(conn, chapter_id)


@router.post("/segments/{segment_id}/regenerate")
def regenerate(segment_id: str, state: State, conn: DB) -> dict[str, Any]:
    job_id = _call(svc.regenerate_segment, conn, state.registry, segment_id, state.settings.paths)
    _publish(state, conn, [job_id])
    chapter_id = conn.execute(
        "SELECT chapter_id FROM segments WHERE id = ?", (segment_id,)
    ).fetchone()["chapter_id"]
    return _detail(conn, chapter_id)


@router.post("/segments/{segment_id}/takes/{take_id}/use")
def use_take(segment_id: str, take_id: str, conn: DB) -> dict[str, Any]:
    chapter_id = _call(svc.choose_take, conn, segment_id, take_id)
    return _detail(conn, chapter_id)


# ------------------------------------------------------------------ mix tracks


@router.post("/chapters/{chapter_id}/tracks", status_code=201)
def add_track(chapter_id: str, body: TrackCreate, conn: DB) -> dict[str, Any]:
    chapter = _call(svc.detail, conn, chapter_id)
    asset = asset_svc.get(conn, body.asset_id)
    if asset["project_id"] != chapter["project_id"] or not asset["media_type"].startswith("audio/"):
        raise ApiError(422, "invalid_asset", "Choose an audio file from this project.")
    defaults = MUSIC_PRESETS[body.preset or "balanced"] if body.kind == "music" else EFFECT_DEFAULTS
    values = {
        **defaults,
        **body.model_dump(exclude_none=True, exclude={"kind", "asset_id", "preset"}),
    }
    if body.anchor_segment is not None and body.anchor_segment >= len(chapter["segments"]):
        raise ApiError(422, "invalid_anchor", "That sentence number does not exist.")
    track_id = new_id("trk")
    conn.execute(
        "INSERT INTO mix_tracks(id, chapter_id, kind, asset_id, anchor_segment, offset_s,"
        " gain_db, fade_in_s, fade_out_s, loop, duck, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            track_id, chapter_id, body.kind, body.asset_id, values.get("anchor_segment"),
            values.get("offset_s", 0), values["gain_db"], values["fade_in_s"], values["fade_out_s"],
            int(values["loop"]), int(values["duck"]), utcnow(),
        ),
    )  # fmt: skip
    return _detail(conn, chapter_id)


def _track(conn: DB, track_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM mix_tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        raise ApiError(404, "track_not_found", "That track does not exist.")
    return dict(row)


@router.patch("/tracks/{track_id}")
def update_track(track_id: str, body: TrackUpdate, conn: DB) -> dict[str, Any]:
    track = _track(conn, track_id)
    fields = body.model_dump(exclude_unset=True)
    sets, params = [], []
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        params.append(int(value) if isinstance(value, bool) else value)
    if sets:
        params.append(track_id)
        # Column names come from the TrackUpdate model's fixed fields.
        conn.execute(f"UPDATE mix_tracks SET {', '.join(sets)} WHERE id = ?", params)  # noqa: S608
    return _detail(conn, track["chapter_id"])


@router.delete("/tracks/{track_id}")
def delete_track(track_id: str, conn: DB) -> dict[str, Any]:
    track = _track(conn, track_id)
    conn.execute("DELETE FROM mix_tracks WHERE id = ?", (track_id,))
    return _detail(conn, track["chapter_id"])


# ------------------------------------------------------------------ export


@router.post("/chapters/{chapter_id}/export", status_code=201)
def export(chapter_id: str, body: Export, state: State, conn: DB) -> dict[str, Any]:
    chapter = _detail(conn, chapter_id)
    missing = [s["position"] + 1 for s in chapter["segments"] if s["current"] is None]
    if not chapter["segments"]:
        raise ApiError(409, "nothing_to_export", "Write and generate the chapter first.")
    if missing:
        listed = ", ".join(map(str, missing[:10])) + ("…" if len(missing) > 10 else "")
        raise ApiError(
            409, "incomplete", f"{len(missing)} sentences have no audio yet (#{listed})."
        )
    paths = state.settings.paths
    segments = []
    for s in chapter["segments"]:
        asset = asset_svc.get(conn, s["current"]["asset_id"])
        segments.append({
            "path": str(asset_svc.resolve(paths, asset["rel_path"])),
            "pause_after_s": s["pause_after_s"],
        })  # fmt: skip
    tracks = []
    if body.include_music:
        for t in chapter["tracks"]:
            asset = asset_svc.get(conn, t["asset_id"])
            tracks.append({
                "kind": t["kind"], "path": str(asset_svc.resolve(paths, asset["rel_path"])),
                "name": asset["original_name"], "anchor_segment": t["anchor_segment"],
                "offset_s": t["offset_s"], "gain_db": t["gain_db"], "fade_in_s": t["fade_in_s"],
                "fade_out_s": t["fade_out_s"], "loop": t["loop"], "duck": t["duck"],
            })  # fmt: skip
    mixer = state.registry.get("audio-mixer")
    assert mixer is not None
    try:
        inputs = load_class(mixer.import_path)().validate_input("audio.assemble", {
            "chapter_id": chapter_id, "title": chapter["title"], "segments": segments,
            "tracks": tracks, "duck_db": chapter["mix"].get("duck_db", -12),
            "formats": body.formats, "stems": body.stems, "ffmpeg": find_ffmpeg(),
        })  # fmt: skip
    except EngineError as exc:
        raise ApiError(422, exc.code, exc.user_message) from exc
    job = repo.create(
        conn, project_id=chapter["project_id"], task="audio.assemble", engine_id="audio-mixer",
        inputs=inputs,
    )  # fmt: skip
    _publish(state, conn, [job["id"]])
    return job
