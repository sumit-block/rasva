"""Typed key/value application settings persisted in SQLite."""

from __future__ import annotations

import sqlite3
from typing import Any

from rasva.config import DEFAULT_BUDGET_BYTES, DEFAULT_OS_RESERVE_BYTES, GB
from rasva.db import dumps, loads, utcnow
from rasva.services.memory import DEFAULT_SAFETY_MARGIN_BYTES

DEFAULTS: dict[str, Any] = {
    "storage.budget_bytes": DEFAULT_BUDGET_BYTES,
    "storage.os_reserve_bytes": DEFAULT_OS_RESERVE_BYTES,
    "ui.mode": "simple",
    "onboarding.completed": False,
    "onboarding.step": 0,  # resume point for an interrupted first-run setup
    "lan.enabled": False,
    # Memory left over after an engine's measured peak before a cold start (PRD §24).
    "memory.safety_margin_bytes": DEFAULT_SAFETY_MARGIN_BYTES,
    # When a cold start is short of memory: "start" anyway (may be slow while
    # macOS compresses or swaps) or "wait" until enough is free (PRD §24).
    "memory.when_short": "start",
    # Ask image engines that support it to use less memory (services/images.py).
    "image.low_memory": False,
    # The model the owner chose in Image Studio, per mode; "" until chosen.
    "image.engine.generate": "",
    "image.engine.edit": "",
}

# Validators keep obviously unsafe values out (e.g. a 0-byte OS reserve).
_LIMITS: dict[str, tuple[int, int]] = {
    "storage.budget_bytes": (5 * GB, 2000 * GB),
    "storage.os_reserve_bytes": (5 * GB, 500 * GB),
    "onboarding.step": (0, 20),
    "memory.safety_margin_bytes": (0, 8 * GB),
}
_ENUMS: dict[str, set[Any]] = {
    "ui.mode": {"simple", "advanced"},
    "memory.when_short": {"start", "wait"},
}
# Not settable through the generic API: LAN needs pairing + TLS (phase A5).
READ_ONLY = {"lan.enabled"}


class SettingError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise SettingError(f"Unknown setting {key!r}")
    if key in READ_ONLY:
        raise SettingError(f"{key!r} cannot be changed here")
    default = DEFAULTS[key]
    free_text = isinstance(default, str) and key not in _ENUMS
    if free_text and (not isinstance(value, str) or len(value) > 64):
        raise SettingError(f"{key!r} must be text of at most 64 characters")
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingError(f"{key!r} must be true or false")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingError(f"{key!r} must be a whole number")
        lo, hi = _LIMITS.get(key, (0, 2**62))
        if not lo <= value <= hi:
            raise SettingError(f"{key!r} must be between {lo} and {hi}")
    if key in _ENUMS and value not in _ENUMS[key]:
        raise SettingError(f"{key!r} must be one of {sorted(_ENUMS[key])}")
    return value


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> Any:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return loads(row["value"]) if row else DEFAULTS[key]

    def all(self) -> dict[str, Any]:
        values = dict(DEFAULTS)
        for row in self._conn.execute("SELECT key, value FROM settings"):
            if row["key"] in values:
                values[row["key"]] = loads(row["value"])
        return values

    def set(self, key: str, value: Any, *, internal: bool = False) -> None:
        if not internal:
            value = validate(key, value)
        self._conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " value = excluded.value, updated_at = excluded.updated_at",
            (key, dumps(value), utcnow()),
        )
