"""Backup listing, local backups, data-file embedding and restore (#185 F4)."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import backup
from app.services.database import DATABASE_SCHEMA_VERSION, Database
from scripts import backup_cli


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """An isolated data directory with an initialized application database."""
    from app.services import circuit_data

    db_path = tmp_path / "f1.db"
    # Modules imported after a config reload hold another Config object; share one.
    monkeypatch.setattr(circuit_data, "config", backup.config)
    monkeypatch.setattr(backup.config, "DATABASE_PATH", str(db_path))
    monkeypatch.setattr(backup.config, "BACKUP_LOCAL_DIR", "")
    monkeypatch.setattr(backup.config, "BACKUP_INCLUDE_DATA_FILES", True)
    monkeypatch.setattr(backup.config, "BACKUP_RETENTION_DAYS", 30)
    monkeypatch.setattr(backup.config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(backup.config, "S3_ENDPOINT_URL", None)
    _initialize(db_path)
    return tmp_path


def _initialize(db_path: Path) -> None:
    async def run() -> None:
        with Database.schema_state_lock:
            Database.initialized_paths.discard(str(db_path))
        database = Database(str(db_path))
        try:
            await database.ping()
        finally:
            await database.close()

    asyncio.run(run())


def _set_marker(db_path: Path, value: str) -> None:
    with closing(sqlite3.connect(db_path)) as db:
        db.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value, updated_at) VALUES ('marker', ?, 'x')",
            (value,),
        )
        db.commit()


def _marker(db_path: Path) -> str | None:
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute("SELECT value FROM cache_meta WHERE key = 'marker'").fetchone()
    return row[0] if row else None


def _tables(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _circuits(data_dir: Path) -> Path:
    return data_dir / "circuits_jolpica.json"


def _local_dir(data_dir: Path) -> Path:
    return data_dir / "backups"


def _no_sidecars(path: Path) -> bool:
    return not any(Path(f"{path}{suffix}").exists() for suffix in ("-wal", "-shm"))


# --- snapshots ---------------------------------------------------------------------------


def test_local_backup_embeds_the_circuits_file_and_restore_round_trips(data_dir):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "before")
    _circuits(data_dir).write_text('{"monza": "backed-up"}')

    created = backup.perform_local_backup()

    assert created["success"] is True
    assert created["data_files"] == ["circuits_jolpica.json"]
    archive = _local_dir(data_dir) / created["filename"]
    assert backup.check_integrity(archive)
    assert _no_sidecars(archive)
    with closing(sqlite3.connect(archive)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)

    _set_marker(db_path, "after")
    _circuits(data_dir).write_text('{"monza": "current"}')

    restored = backup.restore_backup("latest")

    assert restored["success"] is True, restored
    assert restored["name"] == created["filename"]
    assert restored["source"] == "local"
    assert restored["data_files_restored"] == ["circuits_jolpica.json"]
    assert restored["schema_version_backup"] == restored["schema_version_after"]
    assert _marker(db_path) == "before"
    assert _circuits(data_dir).read_text() == '{"monza": "backed-up"}'
    assert backup.DATA_FILES_TABLE not in _tables(db_path)
    assert _no_sidecars(db_path)
    assert not list(data_dir.glob(".f1.db.restore.*"))

    # The safety snapshot keeps the overwritten state, including its circuits file.
    snapshot = _local_dir(data_dir) / restored["safety_snapshot"]
    assert restored["safety_snapshot"].startswith(backup.SAFETY_SNAPSHOT_PREFIX)
    assert _marker(snapshot) == "after"
    with closing(sqlite3.connect(snapshot)) as db:
        stored = dict(db.execute(f"SELECT name, content FROM {backup.DATA_FILES_TABLE}"))
    assert stored == {"circuits_jolpica.json": b'{"monza": "current"}'}

    # Restoring the safety snapshot by name undoes the restore.
    again = backup.restore_backup(restored["safety_snapshot"], source="local")
    assert again["success"] is True
    assert _marker(db_path) == "after"
    assert _circuits(data_dir).read_text() == '{"monza": "current"}'


def test_data_files_can_be_left_out_of_backups(data_dir, monkeypatch):
    monkeypatch.setattr(backup.config, "BACKUP_INCLUDE_DATA_FILES", False)
    _circuits(data_dir).write_text('{"old": true}')
    created = backup.perform_local_backup()
    _circuits(data_dir).write_text('{"new": true}')

    assert created["data_files"] == []
    assert backup.DATA_FILES_TABLE not in _tables(_local_dir(data_dir) / created["filename"])

    restored = backup.restore_backup(created["filename"])

    assert restored["success"] is True
    assert restored["data_files_restored"] == []
    assert _circuits(data_dir).read_text() == '{"new": true}'


def test_missing_circuits_file_is_simply_not_embedded(data_dir):
    created = backup.perform_local_backup()

    assert created["success"] is True
    assert created["data_files"] == []


def test_unknown_embedded_files_are_ignored_on_restore(data_dir):
    created = backup.perform_local_backup()
    archive = _local_dir(data_dir) / created["filename"]
    with closing(sqlite3.connect(archive)) as db:
        db.execute(f"INSERT INTO {backup.DATA_FILES_TABLE} VALUES ('../../etc/evil', x'00')")
        db.commit()

    restored = backup.restore_backup(created["filename"])

    assert restored["success"] is True
    assert restored["data_files_restored"] == []
    assert not (data_dir.parent / "etc").exists()


def test_local_backup_errors_and_retention(data_dir, monkeypatch):
    local = _local_dir(data_dir)
    local.mkdir()
    old = datetime.now(timezone.utc) - timedelta(days=40)
    expired = local / old.strftime(backup.BACKUP_FILENAME_FORMAT)
    kept_snapshot = local / old.strftime(backup.SAFETY_SNAPSHOT_FORMAT)
    expired.write_bytes(b"old")
    kept_snapshot.write_bytes(b"old")

    result = backup.perform_local_backup()

    assert result["deleted_count"] == 1
    assert not expired.exists()
    assert kept_snapshot.exists()

    with patch("app.services.backup.create_backup_snapshot", side_effect=OSError("disk full")):
        failed = backup.perform_local_backup()
    assert failed == {**failed, "success": False, "error": "Local backup failed (OSError)"}
    assert not list(local.glob(".*.tmp"))

    monkeypatch.setattr(backup.config, "DATABASE_PATH", str(data_dir / "missing.db"))
    assert backup.perform_local_backup()["error"] == "Database file not found"


# --- listing -----------------------------------------------------------------------------


def test_list_reports_local_backups_with_unknown_or_verified_integrity(data_dir):
    good = backup.perform_local_backup()["filename"]
    local = _local_dir(data_dir)
    (local / "f1_backup_2020-01-01_00-00-00.db").write_bytes(b"not a database")
    (local / "README.txt").write_text("ignored")
    (local / "f1_backup_2021-01-01_00-00-00.db").mkdir()

    listing = backup.list_backups()
    assert [entry["name"] for entry in listing["backups"]] == [
        "f1_backup_2020-01-01_00-00-00.db",
        good,
    ]
    assert {entry["integrity"] for entry in listing["backups"]} == {"unknown"}
    assert listing["s3_available"] is False
    assert listing["errors"] == []

    verified = {
        entry["name"]: entry["integrity"] for entry in backup.list_backups(verify=True)["backups"]
    }
    assert verified == {"f1_backup_2020-01-01_00-00-00.db": "failed", good: "ok"}


def test_list_without_local_directory_is_empty(data_dir):
    assert backup.list_backups() == {"backups": [], "s3_available": False, "errors": []}


class FakeS3:
    """Minimal boto3 stand-in serving files from a local directory."""

    def __init__(self, files: dict[str, Path], *, fail_list: bool = False):
        self.files = files
        self.fail_list = fail_list
        self.uploaded: list[str] = []

    def get_paginator(self, _name):
        if self.fail_list:
            raise RuntimeError("https://secret-endpoint.example bucket=private")
        stamp = datetime(2026, 11, 1, 3, tzinfo=timezone.utc)
        contents = [
            {"Key": key, "Size": path.stat().st_size, "LastModified": stamp + timedelta(days=i)}
            for i, (key, path) in enumerate(self.files.items())
        ]
        contents.append({"Key": "f1_backup_unexpected.txt", "Size": 1, "LastModified": stamp})
        return SimpleNamespace(paginate=lambda **_kwargs: [{"Contents": contents}])

    def download_file(self, _bucket, key, target):
        shutil.copyfile(self.files[key], target)

    def upload_file(self, source, _bucket, key):
        self.uploaded.append(key)


def _s3_archive(data_dir: Path, marker: str, name: str) -> Path:
    _set_marker(data_dir / "f1.db", marker)
    archive = data_dir / "remote" / name
    archive.parent.mkdir(exist_ok=True)
    backup.create_backup_snapshot(data_dir / "f1.db", archive)
    return archive


def test_list_includes_s3_backups_and_verifies_them(data_dir, monkeypatch):
    name = "f1_backup_2026-11-01_03-00-00.db"
    client = FakeS3({name: _s3_archive(data_dir, "remote", name)})
    monkeypatch.setattr(backup, "_get_s3_client", lambda: client)

    listing = backup.list_backups(verify=True)

    assert listing["s3_available"] is True
    assert listing["backups"] == [
        {
            "name": name,
            "source": "s3",
            "created_utc": "2026-11-01T03:00:00+00:00",
            "size_bytes": (data_dir / "remote" / name).stat().st_size,
            "integrity": "ok",
            "safety_snapshot": False,
        }
    ]


def test_list_errors_name_only_the_failing_step(data_dir, monkeypatch):
    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({}, fail_list=True))
    listing = backup.list_backups()
    assert listing["errors"] == ["S3 listing failed (RuntimeError)"]
    assert "secret-endpoint" not in json.dumps(listing)

    name = "f1_backup_2026-11-01_03-00-00.db"
    client = FakeS3({name: data_dir / "missing-file.db"})
    client.get_paginator = lambda _name: SimpleNamespace(
        paginate=lambda **_kwargs: [
            {"Contents": [{"Key": name, "Size": 1, "LastModified": datetime.now(timezone.utc)}]}
        ]
    )
    monkeypatch.setattr(backup, "_get_s3_client", lambda: client)
    listing = backup.list_backups(verify=True)
    assert listing["backups"][0]["integrity"] == "failed"
    assert listing["errors"] == [f"Verifying {name} failed (FileNotFoundError)"]


def test_minimal_data_mode_keeps_external_backups_off(data_dir, monkeypatch):
    monkeypatch.setattr(backup.config, "MINIMAL_DATA_MODE", True)
    monkeypatch.setattr(backup.config, "S3_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setattr(backup.config, "S3_BUCKET_NAME", "bucket")

    assert backup.list_backups()["s3_available"] is False
    assert backup.restore_backup("latest", source="s3")["error"] == "Backup not found"
    # Local backups never leave the machine and stay available.
    assert backup.perform_local_backup()["success"] is True


@pytest.mark.parametrize("foreign", ["empty", "foreign", "wrong_columns"])
def test_verify_and_restore_reject_non_application_archives(data_dir, foreign, capsys, monkeypatch):
    path = _local_dir(data_dir) / "f1_backup_2026-02-04_05-06-07.db"
    path.parent.mkdir()
    path.touch()
    if foreign != "empty":
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE cache_meta(key, value, updated_at)")
            if foreign == "wrong_columns":
                db.execute("CREATE TABLE generated_images(other)")
    assert not backup.check_integrity(path)
    for as_json in (False, True):
        assert backup_cli.cmd_list(verify=True, as_json=as_json) == 1
        assert "failed" in capsys.readouterr().out
    assert not backup.restore_backup(path.name, force=True)["database_changed"]
    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({path.name: path}))
    assert all(row["integrity"] == "failed" for row in backup.list_backups(verify=True)["backups"])


def test_empty_version_zero_application_archive_is_restorable(data_dir):
    name = backup.perform_local_backup()["filename"]
    with closing(sqlite3.connect(_local_dir(data_dir) / name)) as db:
        db.execute("PRAGMA user_version=0")
        db.execute("DELETE FROM cache_meta")
        db.commit()
    assert backup.check_integrity(_local_dir(data_dir) / name)
    assert backup.restore_backup(name)["success"]


def test_safety_snapshot_restores_absent_circuit_file(data_dir):
    circuit = _circuits(data_dir)
    circuit.write_bytes(b"archived")
    name = backup.perform_local_backup()["filename"]
    circuit.unlink()
    result = backup.restore_backup(name)
    assert circuit.read_bytes() == b"archived"
    assert backup.restore_backup(result["safety_snapshot"])["success"]
    assert not circuit.exists()


@pytest.mark.parametrize("hard_links", [True, False])
def test_local_backups_publish_distinct_completed_files(data_dir, monkeypatch, hard_links):
    if not hard_links:
        monkeypatch.setattr(backup.os, "link", MagicMock(side_effect=OSError(errno.EPERM, "link")))
        # Mounts with fixed permissions can also reject a redundant chmod.
        monkeypatch.setattr(
            backup.os, "fchmod", MagicMock(side_effect=OSError(errno.ENOTSUP, "mode"))
        )
    stamp = datetime.now(timezone.utc).strftime(backup.BACKUP_FILENAME_FORMAT)
    monkeypatch.setattr(backup, "generate_backup_filename", lambda: stamp)
    barrier = Barrier(2)
    source = backup.create_backup_snapshot

    def stage(db, path):
        files = source(db, path)
        _set_marker(Path(path), str(path))
        barrier.wait(timeout=4)
        return files

    monkeypatch.setattr(backup, "create_backup_snapshot", stage)
    with ThreadPoolExecutor(2) as pool:
        results = [
            future.result(timeout=8)
            for future in [pool.submit(backup.perform_local_backup) for _ in range(2)]
        ]
    archives = [_local_dir(data_dir) / row["filename"] for row in results]
    assert all(row["success"] for row in results)
    assert len(set(archives)) == len({_marker(path) for path in archives}) == 2
    # The suffix is a sequence number. Invalid calendar dates are ignored by latest.
    local = _local_dir(data_dir)
    for suffix in ("_2", "_10"):
        shutil.copyfile(archives[0], local / (stamp[:-3] + suffix + ".db"))
    (local / "f1_backup_2026-02-31_03-00-00.db").touch()
    assert backup._resolve_restore_source("latest", "local", None) == (
        stamp[:-3] + "_10.db",
        "local",
    )


@pytest.mark.parametrize(("mask", "expected"), [(0o000, 0o644), (0o022, 0o644), (0o077, 0o600)])
@pytest.mark.parametrize("private_snapshot", [False, True])
def test_local_backup_permissions_follow_umask(
    data_dir, monkeypatch, mask, expected, private_snapshot
):
    snapshot = backup.create_backup_snapshot

    def private(db, staged):
        embedded = snapshot(db, staged)
        staged.chmod(0o600)
        return embedded

    if private_snapshot:
        monkeypatch.setattr(backup, "create_backup_snapshot", private)
    previous = os.umask(mask)
    try:
        created = backup.perform_local_backup()
    finally:
        os.umask(previous)
    assert created["success"]
    assert stat.S_IMODE((_local_dir(data_dir) / created["filename"]).stat().st_mode) == expected


@pytest.mark.parametrize("link_errno", [errno.EIO, errno.ENOTSUP])
def test_failed_local_publication_cleans_its_reservation(data_dir, monkeypatch, link_errno):
    monkeypatch.setattr(backup.os, "link", MagicMock(side_effect=OSError(link_errno, "link")))
    replace = MagicMock(side_effect=OSError(errno.EIO, "replace"))
    monkeypatch.setattr(backup.os, "replace", replace)
    assert not backup.perform_local_backup()["success"]
    assert replace.call_count == int(link_errno == errno.ENOTSUP)
    assert not list(_local_dir(data_dir).iterdir())


def test_restore_excludes_service_and_second_restore_until_cleanup(data_dir, monkeypatch):
    from app import main

    name = backup.perform_local_backup()["filename"]
    entered, release = Event(), Event()
    original = backup._save_safety_snapshot

    def paused(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        entered.set()
        assert release.wait(6)
        return snapshot

    monkeypatch.setattr(backup, "_save_safety_snapshot", paused)
    monkeypatch.setattr(main, "config", backup.config)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(backup.restore_backup, name)
        try:
            assert entered.wait(4)
            assert not backup.restore_backup(name, force=True)["success"]

            async def startup():
                async with main.lifespan(main.app):
                    pytest.fail("started during restore")

            with pytest.raises(BlockingIOError):
                asyncio.run(startup())
        finally:
            release.set()
        assert first.result()["success"]


def test_service_lock_survives_force_and_is_released_on_process_exit(data_dir):
    from app.services.storage_lock import storage_lock

    path = data_dir / "f1.db"
    name = backup.perform_local_backup()["filename"]
    with storage_lock(path), storage_lock(path):
        assert not backup.restore_backup(name, force=True)["database_changed"]
    script = """
import os
import sys
from pathlib import Path
from app.services.storage_lock import storage_lock
with storage_lock(Path(sys.argv[1]), exclusive=True):
    os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    assert backup.restore_backup(name)["success"]


def test_storage_lock_opens_read_only_and_names_an_inaccessible_lock(tmp_path, monkeypatch):
    from app.services.storage_lock import storage_lock

    db = tmp_path / "f1.db"
    path = tmp_path / "f1.db.lock"
    opened = MagicMock(wraps=os.open)
    monkeypatch.setattr(os, "open", opened)
    with storage_lock(db, exclusive=True):
        opened.assert_called_once_with(path, os.O_RDONLY | os.O_CREAT, 0o644)
    path.chmod(0o444)
    with storage_lock(db):
        assert path.is_file()
    opened.side_effect = PermissionError("permission denied")
    with pytest.raises(PermissionError, match=str(path)):
        with storage_lock(db):
            pytest.fail("acquired an inaccessible lock")


def test_lock_io_failure_and_exhausted_publication_do_not_replace_data(data_dir, monkeypatch):
    name = backup.perform_local_backup()["filename"]
    monkeypatch.setattr(backup, "_MAX_SNAPSHOT_SUFFIX", 0)
    monkeypatch.setattr(backup, "generate_backup_filename", lambda: name)
    assert not backup.perform_local_backup()["success"]
    with patch.object(backup, "storage_lock", side_effect=OSError):
        assert not backup.restore_backup(name)["database_changed"]


# --- restore safety ----------------------------------------------------------------------


def test_restore_refuses_a_corrupt_backup_without_touching_anything(data_dir):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "live")
    local = _local_dir(data_dir)
    local.mkdir()
    corrupt = local / "f1_backup_2026-01-01_00-00-00.db"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)

    result = backup.restore_backup(corrupt.name)

    assert result["success"] is False
    assert result["error"] == "Integrity check failed; nothing was restored"
    assert _marker(db_path) == "live"
    assert result["safety_snapshot"] is None
    assert sorted(path.name for path in local.iterdir()) == [corrupt.name]
    assert not list(data_dir.glob(".f1.db.restore.*"))


@pytest.mark.parametrize(
    ("name", "source", "error"),
    [
        ("../f1.db", "auto", "Unknown backup name"),
        ("f1_backup_2026-01-01_00-00-00.db/../x", "auto", "Unknown backup name"),
        ("latest", "ftp", "Unknown source"),
        ("f1_backup_2026-01-01_00-00-00.db", "local", "Backup not found"),
        ("latest", "local", "Backup not found"),
        ("latest", "s3", "Backup not found"),
    ],
)
def test_restore_rejects_unknown_names_sources_and_missing_backups(data_dir, name, source, error):
    assert backup.restore_backup(name, source=source)["error"].startswith(error)


def test_restore_refuses_while_the_database_is_in_use(data_dir):
    db_path = data_dir / "f1.db"
    backup.perform_local_backup()
    wal = Path(f"{db_path}-wal")
    wal.write_bytes(b"")

    refused = backup.restore_backup("latest")
    assert refused["success"] is False
    assert "Stop the service first" in refused["error"]

    wal.unlink()
    with closing(sqlite3.connect(db_path, timeout=0)) as holder:
        holder.execute("PRAGMA journal_mode=DELETE")
        holder.execute("BEGIN EXCLUSIVE")
        assert "in use" in backup.restore_backup("latest")["error"]
        assert "in use" in backup.restore_backup("latest", force=True)["error"]
        holder.rollback()


def test_restore_with_force_ignores_stale_sidecars(data_dir):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "backup")
    backup.perform_local_backup()
    _set_marker(db_path, "live")
    Path(f"{db_path}-shm").write_bytes(b"")

    result = backup.restore_backup("latest", force=True)

    assert result["success"] is True, result
    assert _marker(db_path) == "backup"


_GARBAGE = b"this is not an SQLite database" * 64


def test_restore_without_force_reports_a_corrupt_live_database_cleanly(data_dir):
    db_path = data_dir / "f1.db"
    backup.perform_local_backup()
    db_path.write_bytes(_GARBAGE)

    refused = backup.restore_backup("latest")

    assert refused["success"] is False
    assert "not a readable SQLite database" in refused["error"]
    assert "--force" in refused["error"]
    assert refused["database_changed"] is False
    assert refused["safety_snapshot"] is None
    assert db_path.read_bytes() == _GARBAGE


def test_restore_with_force_replaces_a_corrupt_live_database_and_keeps_a_raw_copy(data_dir, capsys):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "backup")
    _circuits(data_dir).write_text('{"monza": "backup"}')
    backup.perform_local_backup()
    db_path.write_bytes(_GARBAGE)
    Path(f"{db_path}-wal").write_bytes(b"stale wal")
    _circuits(data_dir).write_text('{"monza": "live"}')

    result = backup.restore_backup("latest", force=True)

    assert result["success"] is True, result
    assert result["database_changed"] is True
    assert result["safety_snapshot_raw"] is True
    assert _marker(db_path) == "backup"
    assert _circuits(data_dir).read_text() == '{"monza": "backup"}'
    assert backup.check_integrity(db_path)
    snapshot = _local_dir(data_dir) / result["safety_snapshot"]
    assert snapshot.read_bytes() == _GARBAGE
    assert Path(f"{snapshot}-wal").read_bytes() == b"stale wal"
    assert not Path(f"{snapshot}-shm").exists()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert Path(f"{snapshot}-wal").stat().st_mode & 0o777 == 0o600
    assert result["safety_snapshot"] in [b["name"] for b in backup.local_backups()]

    with patch("app.services.backup.restore_backup", return_value=result):
        assert backup_cli.cmd_restore("latest", assume_yes=True) == 0
    assert f"raw copy of the unreadable database as {result['safety_snapshot']}" in (
        capsys.readouterr().out
    )


def test_healthy_restore_keeps_an_sqlite_safety_snapshot(data_dir):
    result = backup.restore_backup(backup.perform_local_backup()["filename"], source="local")

    assert result["success"] is True, result
    assert result["safety_snapshot_raw"] is False
    assert backup.check_integrity(_local_dir(data_dir) / result["safety_snapshot"])


def test_unopenable_database_path_is_not_reported_as_corrupt(tmp_path):
    # A path SQLite cannot open (here a directory) is an I/O problem, not a corrupt file.
    assert backup._is_unreadable_database(tmp_path) is False
    assert backup._is_unreadable_database(tmp_path / "missing.db") is False


def test_late_failure_after_a_raw_safety_copy_says_to_copy_it_back(data_dir, capsys):
    failed = {
        "success": False,
        "error": "Restore failed (OSError)",
        "database_changed": True,
        "safety_snapshot": "f1_pre_restore_2026-11-23_10-00-00.db",
        "safety_snapshot_raw": True,
    }
    with patch("app.services.backup.restore_backup", return_value=failed):
        assert backup_cli.cmd_restore("latest", assume_yes=True) == 1
    output = capsys.readouterr().out
    assert "raw copy" in output
    assert "backup restore f1_pre_restore" not in output


def test_restore_refuses_a_backup_from_a_newer_schema_unless_forced(data_dir):
    created = backup.perform_local_backup()["filename"]
    with closing(sqlite3.connect(_local_dir(data_dir) / created)) as db:
        db.execute("PRAGMA user_version = 99")

    refused = backup.restore_backup(created)
    assert refused["success"] is False
    assert "newer than this version's" in refused["error"]

    forced = backup.restore_backup(created, force=True)
    assert forced["success"] is True
    assert forced["schema_version_backup"] == 99


def test_restore_migrates_a_backup_from_an_older_schema(data_dir):
    """A pre-v8 backup (no weekend_results, older columns) is upgraded by the normal migrations."""
    local = _local_dir(data_dir)
    local.mkdir()
    old = local / "f1_backup_2025-06-01_03-00-00.db"
    with closing(sqlite3.connect(old)) as db:
        db.executescript(
            """
            CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL);
            INSERT INTO cache_meta VALUES ('marker', 'from-2025', 'x');
            CREATE TABLE api_calls (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                                    endpoint TEXT NOT NULL);
            CREATE TABLE weather_cache (id INTEGER PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL,
                temperature_c REAL NOT NULL, weather_code INTEGER NOT NULL,
                precipitation_probability INTEGER NOT NULL, fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL);
            PRAGMA user_version = 5;
            """
        )

    result = backup.restore_backup(old.name)

    assert result["success"] is True, result
    assert result["schema_version_backup"] == 5
    assert result["schema_version_after"] == DATABASE_SCHEMA_VERSION
    db_path = data_dir / "f1.db"
    assert _marker(db_path) == "from-2025"
    assert {"weekend_results", "weather_api_requests", "generated_images"} <= _tables(db_path)
    with closing(sqlite3.connect(db_path)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(weather_cache)")}
    assert "precipitation_display_override" in columns


def test_restore_into_an_empty_volume_needs_no_safety_snapshot(data_dir, monkeypatch):
    backup.perform_local_backup()
    fresh = data_dir / "fresh" / "f1.db"
    monkeypatch.setattr(backup.config, "BACKUP_LOCAL_DIR", str(_local_dir(data_dir)))
    monkeypatch.setattr(backup.config, "DATABASE_PATH", str(fresh))

    result = backup.restore_backup("latest")

    assert result["success"] is True
    assert result["safety_snapshot"] is None
    assert fresh.is_file()


def test_restore_from_s3_downloads_the_newest_backup(data_dir, monkeypatch):
    older = "f1_backup_2026-11-01_03-00-00.db"
    newer = "f1_backup_2026-11-02_03-00-00.db"
    client = FakeS3(
        {older: _s3_archive(data_dir, "older", older), newer: _s3_archive(data_dir, "newer", newer)}
    )
    _set_marker(data_dir / "f1.db", "live")
    monkeypatch.setattr(backup, "_get_s3_client", lambda: client)

    result = backup.restore_backup("latest")

    assert (result["success"], result["name"], result["source"]) == (True, newer, "s3")
    assert _marker(data_dir / "f1.db") == "newer"

    named = backup.restore_backup(older)
    assert (named["name"], named["source"]) == (older, "s3")
    assert _marker(data_dir / "f1.db") == "older"


def test_restore_reports_s3_and_io_failures_by_type_only(data_dir, monkeypatch):
    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({}, fail_list=True))
    assert backup.restore_backup("latest")["error"] == "S3 listing failed (RuntimeError)"

    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({}))
    missing = backup.restore_backup("f1_backup_2026-11-01_03-00-00.db", source="s3")
    assert missing["error"] == "Could not read the backup (KeyError)"

    monkeypatch.setattr(backup, "_get_s3_client", lambda: None)
    backup.perform_local_backup()
    with patch("app.services.backup.create_backup_snapshot", side_effect=OSError("disk full")):
        failed = backup.restore_backup("latest")
    assert failed["error"] == "Restore failed (OSError)"


def test_failed_migration_of_the_staged_copy_leaves_the_live_data_untouched(data_dir, capsys):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "backed-up")
    _circuits(data_dir).write_text('{"monza": "backed-up"}')
    backup.perform_local_backup()
    _set_marker(db_path, "live")
    _circuits(data_dir).write_text('{"monza": "live"}')

    with patch(
        "app.services.backup._migrate_database", side_effect=sqlite3.OperationalError("boom")
    ):
        failed = backup.restore_backup("latest")

    assert failed["success"] is False
    assert failed["database_changed"] is False
    assert failed["safety_snapshot"] is None
    assert _marker(db_path) == "live"
    assert _circuits(data_dir).read_text() == '{"monza": "live"}'
    assert not list(_local_dir(data_dir).glob(f"{backup.SAFETY_SNAPSHOT_PREFIX}*"))

    with patch("app.services.backup.restore_backup", return_value=failed):
        assert backup_cli.cmd_restore("latest", assume_yes=True) == 1
    assert "The current database was not changed." in capsys.readouterr().out


def test_failure_after_the_swap_names_the_snapshot_that_undoes_it(data_dir, capsys):
    db_path = data_dir / "f1.db"
    _set_marker(db_path, "backed-up")
    _circuits(data_dir).write_text('{"monza": "backed-up"}')
    backup.perform_local_backup()
    _set_marker(db_path, "live")
    _circuits(data_dir).write_text('{"monza": "live"}')

    with patch("app.services.backup.atomic_write_bytes_sync", side_effect=OSError("disk full")):
        failed = backup.restore_backup("latest")

    assert failed["success"] is False
    assert failed["error"] == "Restore failed (OSError)"
    assert failed["database_changed"] is True
    snapshot = failed["safety_snapshot"]
    assert snapshot and (_local_dir(data_dir) / snapshot).is_file()

    with patch("app.services.backup.restore_backup", return_value=failed):
        assert backup_cli.cmd_restore("latest", assume_yes=True) == 1
    output = capsys.readouterr().out
    assert "was not changed" not in output
    assert f"backup restore {snapshot}" in output

    # The named snapshot brings back the database and the data file from before the restore.
    undone = backup.restore_backup(snapshot, source="local")
    assert undone["success"] is True, undone
    assert _marker(db_path) == "live"
    assert _circuits(data_dir).read_text() == '{"monza": "live"}'

    no_previous = {**failed, "safety_snapshot": None}
    with patch("app.services.backup.restore_backup", return_value=no_previous):
        assert backup_cli.cmd_restore("latest", assume_yes=True) == 1
    assert "no previous database to save" in capsys.readouterr().out


def test_restores_within_one_second_never_overwrite_a_safety_snapshot(data_dir):
    db_path = data_dir / "f1.db"
    backup.perform_local_backup()
    frozen = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    names = []
    with patch("app.services.backup.datetime", _Frozen):
        for marker in ("first", "second", "third"):
            _set_marker(db_path, marker)
            restored = backup.restore_backup("latest", source="local")
            assert restored["success"] is True, restored
            names.append(restored["safety_snapshot"])

    stem = frozen.strftime(backup.SAFETY_SNAPSHOT_FORMAT).removesuffix(".db")
    assert names == [f"{stem}.db", f"{stem}_1.db", f"{stem}_2.db"]
    assert [_marker(_local_dir(data_dir) / name) for name in names] == ["first", "second", "third"]
    listed = [b["name"] for b in backup.local_backups() if b["safety_snapshot"]]
    assert listed == names
    # Suffixed snapshots stay restorable by name and keep the parsed timestamp.
    assert backup._parse_backup_name(names[1]) == frozen
    assert backup.restore_backup(names[1], source="local")["success"] is True
    assert _marker(db_path) == "second"


def test_reserving_a_snapshot_name_gives_up_when_every_suffix_is_taken(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_MAX_SNAPSHOT_SUFFIX", 1)
    frozen = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backup,
        "datetime",
        type("_Frozen", (datetime,), {"now": classmethod(lambda c, tz=None: frozen)}),
    )
    first = backup._reserve_safety_snapshot(tmp_path)
    second = backup._reserve_safety_snapshot(tmp_path)
    assert first != second
    with pytest.raises(FileExistsError):
        backup._reserve_safety_snapshot(tmp_path)


# --- outcome recording -------------------------------------------------------------------


def _meta(db_path: Path, key: str) -> str | None:
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def test_external_backup_records_success_and_failure_and_embeds_data_files(data_dir, monkeypatch):
    monkeypatch.setattr(backup.config, "S3_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setattr(backup.config, "S3_ACCESS_KEY_ID", "access")
    monkeypatch.setattr(backup.config, "S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(backup.config, "S3_BUCKET_NAME", "bucket")
    monkeypatch.setattr(backup.config, "BACKUP_RETENTION_DAYS", 30)
    _circuits(data_dir).write_text("{}")
    client = FakeS3({})
    monkeypatch.setattr(backup, "_get_s3_client", lambda: client)
    monkeypatch.setattr(backup, "cleanup_old_backups", lambda _client=None: 0)

    result = backup.perform_backup_with_details()

    assert result["success"] is True
    assert result["data_files"] == ["circuits_jolpica.json"]
    assert _meta(data_dir / "f1.db", backup.BACKUP_SUCCESS_META_NAME) is not None

    failing = MagicMock()
    failing.upload_file.side_effect = RuntimeError("network")
    monkeypatch.setattr(backup, "_get_s3_client", lambda: failing)
    with patch("app.services.backup.sentry_sdk.capture_exception"):
        assert backup.perform_backup_with_details()["success"] is False
    assert _meta(data_dir / "f1.db", backup.BACKUP_FAILURE_META_NAME) is not None


def test_scheduled_backup_records_its_outcome(data_dir, monkeypatch):
    monkeypatch.setattr(backup.config, "S3_BUCKET_NAME", "bucket")
    monkeypatch.setattr(backup, "is_backup_configured", lambda: True)
    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({}))
    monkeypatch.setattr(backup, "cleanup_old_backups", lambda _client=None: 0)

    assert backup.perform_backup() is True
    assert _meta(data_dir / "f1.db", backup.BACKUP_SUCCESS_META_NAME) is not None

    failing = MagicMock()
    failing.upload_file.side_effect = RuntimeError("network")
    monkeypatch.setattr(backup, "_get_s3_client", lambda: failing)
    with patch("app.services.backup.sentry_sdk.capture_exception"):
        assert backup.perform_backup() is False
    assert _meta(data_dir / "f1.db", backup.BACKUP_FAILURE_META_NAME) is not None


def test_recording_never_creates_a_missing_database(tmp_path, monkeypatch):
    missing = tmp_path / "missing.db"
    monkeypatch.setattr(backup.config, "DATABASE_PATH", str(missing))

    backup._record_backup_event(backup.BACKUP_SUCCESS_META_NAME)

    assert not missing.exists()


# --- CLI ---------------------------------------------------------------------------------


def test_cli_list_now_and_restore(data_dir, capsys, monkeypatch):
    assert backup_cli.main(["list"]) == 0
    assert "No backups found." in capsys.readouterr().out

    assert backup_cli.main(["now", "--local"]) == 0
    assert "Local backup: f1_backup_" in capsys.readouterr().out

    assert backup_cli.main(["list", "--verify"]) == 0
    assert "integrity=ok" in capsys.readouterr().out
    assert backup_cli.main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["backups"][0]["source"] == "local"

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert backup_cli.main(["restore", "latest"]) == 1
    assert "Aborted." in capsys.readouterr().out

    assert backup_cli.main(["restore", "latest", "--yes"]) == 0
    output = capsys.readouterr().out
    assert "Previous database saved as f1_pre_restore_" in output

    assert backup_cli.main(["restore", "f1_backup_1999-01-01_00-00-00.db", "--yes"]) == 1
    assert "Backup not found" in capsys.readouterr().out
    assert backup_cli.main([]) == 1


def test_cli_local_backup_failure(data_dir, capsys, monkeypatch):
    monkeypatch.setattr(backup.config, "DATABASE_PATH", str(data_dir / "missing.db"))

    assert backup_cli.cmd_now(local=True) == 1
    assert "Database file not found" in capsys.readouterr().out


def test_cli_list_warns_about_errors(data_dir, capsys, monkeypatch):
    monkeypatch.setattr(backup, "_get_s3_client", lambda: FakeS3({}, fail_list=True))

    assert backup_cli.cmd_list() == 1
    assert "Warning: S3 listing failed (RuntimeError)" in capsys.readouterr().out


def test_self_hosting_one_off_containers_use_the_pinned_release():
    """A bare `docker compose run` would select f1-eink-cal:local, not the running release."""
    guide = (Path(__file__).resolve().parents[1] / "SELF-HOSTING.md").read_text(encoding="utf-8")
    run_lines = [line.strip() for line in guide.splitlines() if "docker compose run" in line]

    assert run_lines
    assert all(
        line.startswith("F1_IMAGE=ghcr.io/rhiz3k/inkycloud-f1:$F1_VERSION docker compose run")
        for line in run_lines
    )
