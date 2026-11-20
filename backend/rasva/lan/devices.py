"""Pairing sessions and paired-device authentication (ADR-0004)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from rasva.db import new_id, utcnow

CODE_TTL_S = 300
MAX_ATTEMPTS = 5
COOKIE_NAME = "rasva_device"
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetimes at 400 days
SEEN_WRITE_INTERVAL_S = 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class PairingSession:
    code: str
    expires_at: float
    attempts: int = 0
    used: bool = False

    @property
    def active(self) -> bool:
        return not self.used and self.attempts < MAX_ATTEMPTS and time.time() < self.expires_at


class Pairing:
    """At most one pairing code at a time, shown only on the Mac."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: PairingSession | None = None

    def start(self) -> PairingSession:
        with self._lock:
            self._session = PairingSession(
                code=f"{secrets.randbelow(1_000_000):06d}", expires_at=time.time() + CODE_TTL_S
            )
            return self._session

    def stop(self) -> None:
        with self._lock:
            self._session = None

    def current(self) -> PairingSession | None:
        with self._lock:
            s = self._session
            return s if s and s.active else None

    def redeem(self, code: str) -> bool:
        """Single use; five wrong guesses end the session (1-in-200,000 odds)."""
        with self._lock:
            s = self._session
            if s is None or not s.active:
                return False
            if hmac.compare_digest(s.code, code.strip()):
                s.used = True
                return True
            s.attempts += 1
            return False


class DeviceStore:
    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    @staticmethod
    def create(
        conn: sqlite3.Connection, name: str, user_agent: str, ip: str
    ) -> tuple[dict[str, Any], str]:
        token = secrets.token_urlsafe(32)
        device_id = new_id("dev")
        now = utcnow()
        conn.execute(
            "INSERT INTO devices(id, name, token_hash, user_agent, created_at, last_seen_at,"
            " last_ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                name.strip()[:100] or "Device",
                _hash(token),
                user_agent[:300],
                now,
                now,
                ip,
            ),
        )
        return DeviceStore.get(conn, device_id), token

    @staticmethod
    def get(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, name, user_agent, created_at, last_seen_at, last_ip FROM devices"
            " WHERE id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            raise KeyError(device_id)
        return dict(row)

    @staticmethod
    def list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT id, name, user_agent, created_at, last_seen_at, last_ip FROM devices"
            " ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]

    @staticmethod
    def revoke(conn: sqlite3.Connection, device_id: str) -> bool:
        return conn.execute("DELETE FROM devices WHERE id = ?", (device_id,)).rowcount == 1

    def authenticate(
        self, conn: sqlite3.Connection, token: str | None, ip: str
    ) -> dict[str, Any] | None:
        if not token or len(token) > 200:
            return None
        row = conn.execute(
            "SELECT id, name FROM devices WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
        if row is None:
            return None
        now = time.monotonic()
        if now - self._seen.get(row["id"], 0) > SEEN_WRITE_INTERVAL_S:
            self._seen[row["id"]] = now
            conn.execute(
                "UPDATE devices SET last_seen_at = ?, last_ip = ? WHERE id = ?",
                (utcnow(), ip, row["id"]),
            )
        return {"id": row["id"], "name": row["name"]}
