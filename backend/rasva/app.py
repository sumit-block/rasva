"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from rasva import __version__
from rasva.api import (
    assets,
    chapters,
    devices,
    errors,
    experiments,
    jobs,
    models,
    projects,
    pronunciations,
    settings,
    system,
)
from rasva.api import audio as audio_api
from rasva.api import storage as storage_api
from rasva.api.deps import AppState
from rasva.config import Settings
from rasva.db import connect, migrate
from rasva.engines.registry import EngineRegistry
from rasva.jobs.events import EventBus
from rasva.jobs.orchestrator import Orchestrator
from rasva.lan.server import LanError, LanManager
from rasva.logging_setup import setup_logging
from rasva.security import LocalOnlyGuard
from rasva.services.settings_store import SettingsStore
from rasva.services.storage import StorageService

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


def create_app(
    cfg: Settings | None = None,
    *,
    trust_test_client: bool = False,
    extra_engines: tuple[str, ...] = (),
) -> FastAPI:
    cfg = cfg or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        paths = cfg.paths
        paths.ensure()
        setup_logging(paths.logs)
        conn = connect(paths.db_file)
        try:
            version = migrate(conn, paths.db_backups)
        finally:
            conn.close()
        registry = EngineRegistry(cfg.enable_test_engines, extra=extra_engines)
        storage = StorageService(paths)
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        orchestrator = Orchestrator(cfg, lambda: connect(paths.db_file), registry, storage, bus)
        lan = LanManager(cfg, app)
        app.state.rasva = AppState(cfg, registry, storage, bus, orchestrator, version, lan)
        await orchestrator.start()
        conn = connect(paths.db_file)
        try:
            lan_enabled = SettingsStore(conn).get("lan.enabled")
        finally:
            conn.close()
        if lan_enabled:
            try:
                await lan.start()
            except LanError as exc:
                log.warning("home Wi-Fi access could not start: %s", exc)
        log.info("Rasva Studio %s ready", __version__, extra={"home": str(paths.home)})
        try:
            yield
        finally:
            await lan.stop()
            await orchestrator.stop()

    app = FastAPI(
        title="Rasva Studio",
        version=__version__,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    errors.install(app)

    api = APIRouter(prefix=API_PREFIX)
    routers = (system, projects, assets, jobs, models, settings, experiments, pronunciations)
    for module in (*routers, chapters, devices, storage_api, audio_api):
        api.include_router(module.router)
    app.include_router(api)

    @app.api_route(f"{API_PREFIX}/{{rest:path}}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "not_found", "message": "Unknown API endpoint."}}, status_code=404
        )

    if cfg.frontend_dist:
        _mount_frontend(app, cfg.frontend_dist)

    app.add_middleware(LocalOnlyGuard, trust_test_client=trust_test_client)
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the locally built SPA. No CDN or remote assets (PRD §5.2, §21.5)."""
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
