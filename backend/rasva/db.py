"""SQLite access and forward-only, backed-up schema migrations.

Design notes:
- WAL mode so the UI can read while the orchestrator writes.
- Autocommit connections (``isolation_level=None``); multi-statement writes use
  the ``transaction`` context manager explicitly.
- Before any pending migration is applied to an existing database, a consistent
  copy is taken with the SQLite online-backup API (rollback path, PRD §21).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _available_migrations() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    pkg = resources.files("rasva.migrations")
    for entry in pkg.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append((int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Duplicate migration version numbers")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"] or 0)


def backup(conn: sqlite3.Connection, dest_dir: Path, label: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"rasva-{stamp}-{label}.sqlite3"
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def migrate(conn: sqlite3.Connection, backup_dir: Path) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    version = current_version(conn)
    pending = [m for m in _available_migrations() if m[0] > version]
    if not pending:
        return version

    has_user_tables = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name <> 'schema_migrations'"
    ).fetchone()["n"]
    if has_user_tables:
        path = backup(conn, backup_dir, f"pre-v{pending[0][0]:04d}")
        log.info("database backed up before migration", extra={"backup": str(path)})

    for ver, name, sql in pending:
        log.info("applying migration %s", name)
        conn.execute("BEGIN IMMEDIATE")
        try:
            # executescript would COMMIT our transaction; run statements individually.
            for stmt in _split_sql(sql):
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (ver, name, utcnow()),
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        version = ver
    return version


def _split_sql(sql: str) -> list[str]:
    # Migrations are plain DDL/DML without triggers or string literals containing ';'.
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)
