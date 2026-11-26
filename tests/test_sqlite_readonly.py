"""Tests for lock-free read-only SQLite access and its fallback to a locked read."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.utils.sqlite_readonly import database_state, read_consistently, read_only_uri


def _make_db(path: Path, rows: int = 1) -> Path:
    """Create a WAL database with ``rows`` rows and no open connection (no ``-wal`` file)."""
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t (v INTEGER)")
        db.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
        db.commit()
    assert not Path(f"{path}-wal").exists()
    return path


def _write_like_the_service(path: Path) -> None:
    """Open, write and close the database as another process would; this checkpoints it."""
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE added (v INTEGER)")
        db.executemany("INSERT INTO added VALUES (?)", [(i,) for i in range(500)])
        db.commit()


def _count_tables(uri: str) -> int:
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]


def test_uri_is_immutable_only_without_a_wal_file(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    assert read_only_uri(db).endswith("?mode=ro&immutable=1")
    assert read_only_uri(db, allow_immutable=False).endswith("?mode=ro")

    Path(f"{db}-wal").write_bytes(b"")
    assert read_only_uri(db).endswith("?mode=ro")


def test_database_state_changes_when_a_writer_checkpoints(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    before = database_state(db)
    assert database_state(db) == before

    _write_like_the_service(db)

    assert database_state(db) != before


def test_unchanged_database_is_read_once_without_locks(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    uris: list[str] = []

    def read(uri: str) -> int:
        uris.append(uri)
        return _count_tables(uri)

    assert read_consistently(db, read) == 1
    assert len(uris) == 1 and uris[0].endswith("&immutable=1")
    assert not Path(f"{db}-wal").exists() and not Path(f"{db}-shm").exists()


def test_open_database_is_read_once_with_locks(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    uris: list[str] = []
    with closing(sqlite3.connect(db)) as service:
        service.execute("SELECT 1 FROM t").fetchall()  # the service holds the -wal file open
        assert read_consistently(db, lambda uri: uris.append(uri) or _count_tables(uri)) == 1
    assert len(uris) == 1 and not uris[0].endswith("&immutable=1")


def test_writer_starting_during_a_lock_free_read_triggers_a_locked_reread(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    uris: list[str] = []

    def read(uri: str) -> int:
        uris.append(uri)
        if len(uris) == 1:
            # The service starts while the backup reads: the immutable read cannot see this.
            _write_like_the_service(db)
            return -1
        return _count_tables(uri)

    assert read_consistently(db, read) == 2
    assert uris[0].endswith("&immutable=1")
    assert uris[1].endswith("?mode=ro")


def test_failed_lock_free_read_is_retried_only_when_the_database_changed(tmp_path):
    db = _make_db(tmp_path / "f1.db")
    calls: list[str] = []

    def torn(uri: str) -> int:
        calls.append(uri)
        if len(calls) == 1:
            _write_like_the_service(db)
            raise sqlite3.DatabaseError("database disk image is malformed")
        return _count_tables(uri)

    assert read_consistently(db, torn) == 2
    assert len(calls) == 2

    # The locked re-read above leaves -wal/-shm behind; a fresh database is read lock-free again.
    fresh = _make_db(tmp_path / "fresh.db")
    seen: list[str] = []

    def broken(uri: str) -> int:
        seen.append(uri)
        raise sqlite3.DatabaseError("file is not a database")

    with pytest.raises(sqlite3.DatabaseError):
        read_consistently(fresh, broken)
    assert len(seen) == 1 and seen[0].endswith("&immutable=1")


def test_backup_snapshot_includes_writes_made_during_a_lock_free_copy(tmp_path, monkeypatch):
    from app.services import backup
    from app.utils import sqlite_readonly

    db = _make_db(tmp_path / "f1.db", rows=3)
    snapshot = tmp_path / "snap.db"
    real_state = sqlite_readonly.database_state
    calls = {"n": 0}

    def state_with_a_concurrent_writer(path: Path):
        calls["n"] += 1
        if calls["n"] == 2:  # right after the first (immutable) copy finished
            _write_like_the_service(db)
        return real_state(path)

    monkeypatch.setattr(sqlite_readonly, "database_state", state_with_a_concurrent_writer)

    backup._create_sqlite_snapshot(db, str(snapshot))

    with closing(sqlite3.connect(snapshot)) as copy:
        assert copy.execute("SELECT COUNT(*) FROM added").fetchone()[0] == 500
        assert copy.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_ops_status_reads_rows_again_after_a_concurrent_write(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from app.services import ops_status
    from app.utils import sqlite_readonly

    db = tmp_path / "f1.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.commit()
    key = ops_status._META_KEYS[0]
    real_state = sqlite_readonly.database_state
    calls = {"n": 0}

    def state_with_a_concurrent_writer(path: Path):
        calls["n"] += 1
        if calls["n"] == 2:
            with closing(sqlite3.connect(db)) as writer:
                writer.execute("INSERT INTO cache_meta VALUES (?, 'v', 'now')", (key,))
                writer.execute("CREATE TABLE padding (v BLOB)")
                writer.execute("INSERT INTO padding VALUES (?)", (os.urandom(8192),))
                writer.commit()
        return real_state(path)

    monkeypatch.setattr(sqlite_readonly, "database_state", state_with_a_concurrent_writer)

    rows = ops_status._read_database(db, datetime.now(timezone.utc))

    assert rows is not None and rows["meta"] == {key: "v"}
