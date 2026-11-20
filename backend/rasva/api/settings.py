from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rasva.api.deps import DB, Client, State
from rasva.api.errors import ApiError
from rasva.services.settings_store import SettingsStore, validate

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings(conn: DB) -> dict[str, Any]:
    return SettingsStore(conn).all()


@router.patch("/settings")
def update_settings(body: dict[str, Any], state: State, conn: DB, client: Client) -> dict[str, Any]:
    if client.get("kind") != "local" and any(k.startswith(("storage.", "lan.")) for k in body):
        raise ApiError(403, "mac_only", "Storage settings can only be changed on the Mac.")
    store = SettingsStore(conn)
    validated = {k: validate(k, v) for k, v in body.items()}  # all-or-nothing
    for key, value in validated.items():
        store.set(key, value, internal=True)
    state.storage.invalidate()
    return store.all()
