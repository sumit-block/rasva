from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from rasva import __version__
from rasva.api.deps import DB, Client, State
from rasva.services import memory as memory_svc
from rasva.services import system_check
from rasva.services.settings_store import SettingsStore
from rasva.services.storage import PLANNING_BANDS_GB

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health(state: State) -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "schema_version": state.schema_version}


def _storage(state: State, conn: DB, force: bool = False) -> dict[str, Any]:
    store = SettingsStore(conn)
    u = state.storage.usage(
        store.get("storage.budget_bytes"), store.get("storage.os_reserve_bytes"), force=force
    )
    return {
        "budget_bytes": u.budget_bytes,
        "used_bytes": u.used_bytes,
        "remaining_bytes": max(u.budget_bytes - u.used_bytes, 0),
        "ratio": round(u.ratio, 4),
        "level": u.level,
        "free_disk_bytes": u.free_disk_bytes,
        "total_disk_bytes": u.total_disk_bytes,
        "os_reserve_bytes": u.os_reserve_bytes,
        "categories": u.categories,
        "planning_bands_gb": PLANNING_BANDS_GB,
    }


@router.get("/storage")
def storage(state: State, conn: DB, refresh: bool = False) -> dict[str, Any]:
    return _storage(state, conn, force=refresh)


@router.get("/check")
def check(state: State, conn: DB) -> dict[str, Any]:
    s = _storage(state, conn)
    needed = s["budget_bytes"] - s["used_bytes"] + s["os_reserve_bytes"]
    return system_check.run_checks(s["free_disk_bytes"], max(needed, 0))


def _memory() -> dict[str, Any]:
    """System memory as psutil reports it (PRD §24). ``available`` on macOS is free
    plus inactive memory; it is not Activity Monitor's memory-pressure figure."""
    r = memory_svc.read()
    return {
        "total_bytes": r.total_bytes,
        "available_bytes": r.available_bytes,
        "level": memory_svc.level(r),
    }


@router.get("/status")
def status(state: State, conn: DB, client: Client) -> dict[str, Any]:
    """Compact header status: Mac readiness, active job, storage remaining (PRD §7.3)."""
    s = _storage(state, conn)
    return {
        "orchestrator": state.orchestrator.status(),
        "memory": _memory(),
        "storage": {k: s[k] for k in ("used_bytes", "budget_bytes", "remaining_bytes", "level")},
        "settings": SettingsStore(conn).all(),
        "client": {"kind": client.get("kind"), "device": client.get("device")},
    }
