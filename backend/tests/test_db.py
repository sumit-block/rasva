import sqlite3

from rasva.db import backup, connect, current_version, migrate


def test_migrate_fresh_and_idempotent(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    v1 = migrate(conn, tmp_path / "backups")
    assert v1 >= 1
    assert migrate(conn, tmp_path / "backups") == v1
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "assets", "models", "jobs", "job_events", "settings"} <= tables
    # Fresh install: nothing to back up.
    assert not (tmp_path / "backups").exists() or not any((tmp_path / "backups").iterdir())


def test_backup_is_consistent_copy(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn, tmp_path / "backups")
    conn.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES ('ui.mode', '\"advanced\"', 'now')"
    )
    path = backup(conn, tmp_path / "backups", "test")
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT value FROM settings").fetchone()[0] == '"advanced"'
    assert current_version(connect(path)) == current_version(conn)


def test_foreign_keys_enforced(tmp_path):
    conn = connect(tmp_path / "db.sqlite3")
    migrate(conn, tmp_path / "backups")
    try:
        conn.execute(
            "INSERT INTO jobs(id, project_id, task, engine_id, state, created_at, updated_at)"
            " VALUES ('j', 'missing', 't', 'e', 'queued', 'x', 'x')"
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError("foreign key not enforced")
