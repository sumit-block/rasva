from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from rasva.config import Settings
from rasva.db import connect
from rasva.engines.registry import EngineRegistry
from rasva.jobs.events import EventBus
from rasva.jobs.orchestrator import Orchestrator
from rasva.services.settings_store import SettingsStore
from rasva.services.storage import Preflight, Purpose, StorageService


@dataclass
class AppState:
    settings: Settings
    registry: EngineRegistry
    storage: StorageService
    bus: EventBus
    orchestrator: Orchestrator
    schema_version: int

    def preflight(self, conn: sqlite3.Connection, required: int, purpose: Purpose) -> Preflight:
        store = SettingsStore(conn)
        return self.storage.preflight(
            required,
            purpose,
            budget_bytes=store.get("storage.budget_bytes"),
            os_reserve_bytes=store.get("storage.os_reserve_bytes"),
        )


def get_state(request: Request) -> AppState:
    return request.app.state.rasva


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.rasva.settings.paths.db_file)
    try:
        yield conn
    finally:
        conn.close()


State = Annotated[AppState, Depends(get_state)]
DB = Annotated[sqlite3.Connection, Depends(get_db)]
