"""S3-compatible backup service for SQLite database."""

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sentry_sdk

from app.config import config
from app.services.circuit_data import runtime_circuits_data_path
from app.utils.atomic_io import atomic_write_bytes_sync
from app.utils.sqlite_readonly import read_only_uri

logger = logging.getLogger(__name__)

# Backup filename format: f1_backup_YYYY-MM-DD_HH-MM-SS.db
BACKUP_FILENAME_PREFIX = "f1_backup_"
BACKUP_FILENAME_FORMAT = f"{BACKUP_FILENAME_PREFIX}%Y-%m-%d_%H-%M-%S.db"
# Copy of the live database taken right before a restore overwrites it.
SAFETY_SNAPSHOT_PREFIX = "f1_pre_restore_"
SAFETY_SNAPSHOT_FORMAT = f"{SAFETY_SNAPSHOT_PREFIX}%Y-%m-%d_%H-%M-%S.db"
_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
_BACKUP_NAME_PATTERN = re.compile(
    rf"^(?P<prefix>{BACKUP_FILENAME_PREFIX}|{SAFETY_SNAPSHOT_PREFIX})"
    r"(?P<stamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.db$"
)
# Mutable files kept next to the database travel inside the snapshot in this table, so one
# object stays atomic, retention keeps working by prefix and older backups simply lack it.
DATA_FILES_TABLE = "inkycloud_backup_files"
BACKUP_SUCCESS_META_KEY = "last_backup_success"
BACKUP_FAILURE_META_KEY = "last_backup_failure"
INTEGRITY_UNKNOWN = "unknown"
INTEGRITY_OK = "ok"
INTEGRITY_FAILED = "failed"


def _resolve_secret(value: Any) -> str | None:
    """Return a plain-text secret value from either SecretStr or str input."""
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    if isinstance(value, str):
        return value
    return str(value)


def _create_sqlite_snapshot(db_path: Path, temp_path: str) -> None:
    """Create a consistent SQLite snapshot using SQLite's online backup API."""
    src = sqlite3.connect(read_only_uri(db_path), uri=True)
    dst = sqlite3.connect(temp_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def data_file_paths() -> dict[str, Path]:
    """Return the mutable data files the application keeps beside the database."""
    path = runtime_circuits_data_path()
    return {path.name: path}


def _embed_data_files(snapshot_path: str | Path, *, include: bool | None = None) -> list[str]:
    """Copy the mutable data files into a snapshot; return the names that were stored."""
    if not (config.BACKUP_INCLUDE_DATA_FILES if include is None else include):
        return []
    files = {name: path for name, path in data_file_paths().items() if path.is_file()}
    with closing(sqlite3.connect(snapshot_path)) as db:
        db.execute(
            f"CREATE TABLE IF NOT EXISTS {DATA_FILES_TABLE} "
            "(name TEXT PRIMARY KEY, content BLOB NOT NULL)"
        )
        db.executemany(
            f"INSERT OR REPLACE INTO {DATA_FILES_TABLE} (name, content) VALUES (?, ?)",
            [(name, path.read_bytes()) for name, path in files.items()],
        )
        db.commit()
    return sorted(files)


def create_backup_snapshot(
    db_path: Path, temp_path: str | Path, *, include_data_files: bool | None = None
) -> list[str]:
    """Write a consistent database snapshot plus embedded data files; return their names.

    The snapshot is switched to rollback-journal mode so reading it later (integrity checks,
    a manual copy into place) never leaves ``-wal``/``-shm`` files beside it.
    """
    _create_sqlite_snapshot(db_path, str(temp_path))
    with closing(sqlite3.connect(temp_path)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    return _embed_data_files(temp_path, include=include_data_files)


def _database_uri(path: Path, mode: str) -> str:
    """Return an SQLite URI that never creates a missing database file.

    ``immutable`` opens a backup file read-only without locks or ``-wal``/``-shm`` files, even
    when the snapshot still carries the WAL flag of the live database it was copied from.
    """
    if mode == "immutable":
        return f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    return f"{path.resolve().as_uri()}?mode={mode}"


def _record_backup_event(key: str) -> None:
    """Remember the latest backup outcome in ``cache_meta`` for ``ops status``; best effort."""
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with closing(
            sqlite3.connect(_database_uri(Path(config.DATABASE_PATH), "rw"), uri=True, timeout=5)
        ) as db:
            db.execute(
                """
                INSERT INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, stamp, stamp),
            )
            db.commit()
    except (OSError, sqlite3.Error) as exc:
        logger.debug("Could not record backup outcome: %s", exc)


def _get_s3_client():
    """
    Create and return a boto3 S3 client configured for the backup endpoint.

    Returns:
        boto3 S3 client or None if configuration is incomplete.
    """
    if config.MINIMAL_DATA_MODE:
        return None
    access_key_id = _resolve_secret(config.S3_ACCESS_KEY_ID)
    secret_access_key = _resolve_secret(config.S3_SECRET_ACCESS_KEY)

    if not config.S3_ENDPOINT_URL:
        logger.debug("S3_ENDPOINT_URL not configured, skipping S3 client creation")
        return None

    if not access_key_id or not secret_access_key:
        logger.warning("S3 credentials not configured, backup disabled")
        return None

    if not config.S3_BUCKET_NAME:
        logger.warning("S3_BUCKET_NAME not configured, backup disabled")
        return None

    # Lazy import boto3 to avoid startup overhead when backup is disabled
    try:
        import boto3
    except ImportError:
        logger.error("boto3 not installed, backup disabled")
        return None

    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT_URL,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name=config.S3_REGION,
    )


def is_backup_configured() -> bool:
    """
    Check if backup is properly configured and enabled.

    Returns:
        True if backup is enabled and all required S3 settings are present.
    """
    if config.MINIMAL_DATA_MODE or not config.BACKUP_ENABLED:
        return False

    required = [
        config.S3_ENDPOINT_URL,
        _resolve_secret(config.S3_ACCESS_KEY_ID),
        _resolve_secret(config.S3_SECRET_ACCESS_KEY),
        config.S3_BUCKET_NAME,
    ]
    return all(required)


def generate_backup_filename() -> str:
    """
    Generate a backup filename with current UTC timestamp.

    Returns:
        Filename in format: f1_backup_YYYY-MM-DD_HH-MM-SS.db
    """
    return datetime.now(timezone.utc).strftime(BACKUP_FILENAME_FORMAT)


def perform_backup() -> bool:
    """
    Perform a database backup to S3-compatible storage.

    This function:
    1. Copies the database file to a temporary location
    2. Uploads the copy to S3
    3. Cleans up the temporary file
    4. Optionally cleans up old backups based on retention policy

    Returns:
        True if backup was successful, False otherwise.
    """
    if not is_backup_configured():
        logger.debug("Backup not configured or disabled, skipping")
        return False

    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        logger.warning("Database file not found: %s", db_path)
        return False

    s3_client = _get_s3_client()
    if s3_client is None:
        return False

    temp_path = None
    try:
        # Create temporary copy of database to avoid locking issues
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)

        logger.info("Creating SQLite snapshot of %s", db_path)
        create_backup_snapshot(db_path, temp_path)

        # Generate backup filename and upload
        backup_filename = generate_backup_filename()
        file_size = os.path.getsize(temp_path)

        logger.info("Uploading backup to S3: %s (%s bytes)", backup_filename, file_size)
        s3_client.upload_file(
            temp_path,
            config.S3_BUCKET_NAME,
            backup_filename,
        )

        logger.info("Backup completed successfully: %s", backup_filename)
        _record_backup_event(BACKUP_SUCCESS_META_KEY)

        # Clean up old backups if retention is configured
        if config.BACKUP_RETENTION_DAYS > 0:
            cleanup_old_backups(s3_client)

        return True

    except Exception as e:
        logger.error("Backup failed: %s", e, exc_info=True)
        _record_backup_event(BACKUP_FAILURE_META_KEY)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(e)
        return False

    finally:
        # Always clean up temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                logger.warning("Failed to remove temporary file %s: %s", temp_path, e)


def cleanup_old_backups(s3_client=None) -> int:
    """
    Delete backups older than the configured retention period.

    Parameters:
        s3_client: Optional boto3 S3 client; if None, creates one via _get_s3_client().

    Returns:
        Number of backups deleted. Returns 0 if retention disabled or no client.
    """
    if config.BACKUP_RETENTION_DAYS <= 0:
        logger.debug("Backup retention disabled (BACKUP_RETENTION_DAYS=0)")
        return 0

    if s3_client is None:
        s3_client = _get_s3_client()
        if s3_client is None:
            return 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=config.BACKUP_RETENTION_DAYS)
    deleted_count = 0

    try:
        # List all objects in the bucket with our prefix
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=config.S3_BUCKET_NAME,
            Prefix=BACKUP_FILENAME_PREFIX,
        )

        objects_to_delete = []

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                last_modified = obj["LastModified"]

                # Check if object is older than retention period
                if last_modified < cutoff_date:
                    objects_to_delete.append({"Key": key})
                    logger.debug("Marking for deletion: %s (modified: %s)", key, last_modified)

        # Delete old backups in batches
        if objects_to_delete:
            # S3 delete_objects supports up to 1000 keys per request
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                response = s3_client.delete_objects(
                    Bucket=config.S3_BUCKET_NAME,
                    Delete={"Objects": batch},
                )
                # delete_objects reports per-key failures in a 200 response; count only the
                # keys the store confirms instead of the requested batch size.
                errors = (response or {}).get("Errors") or []
                for error in errors:
                    logger.warning(
                        "Failed to delete backup %s: %s",
                        error.get("Key"),
                        error.get("Code") or error.get("Message"),
                    )
                deleted = (response or {}).get("Deleted")
                deleted_count += len(deleted or [])

            logger.info(
                "Deleted %s old backup(s) (older than %s)", deleted_count, cutoff_date.date()
            )

    except Exception as e:
        logger.error("Failed to cleanup old backups: %s", e, exc_info=True)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(e)

    return deleted_count


def get_backup_config_info() -> dict[str, Any]:
    """
    Return non-sensitive backup configuration details.

    Returns:
        dict with enabled, endpoint, bucket, region, schedule, retention_days,
        and credentials_configured fields.
    """
    return {
        # MINIMAL_DATA_MODE disables backups regardless of BACKUP_ENABLED.
        "enabled": config.BACKUP_ENABLED and not config.MINIMAL_DATA_MODE,
        "minimal_data_mode": config.MINIMAL_DATA_MODE,
        "endpoint": config.S3_ENDPOINT_URL or "(not configured)",
        "bucket": config.S3_BUCKET_NAME or "(not configured)",
        "region": config.S3_REGION,
        "schedule": config.BACKUP_CRON,
        "retention_days": config.BACKUP_RETENTION_DAYS,
        "credentials_configured": bool(
            _resolve_secret(config.S3_ACCESS_KEY_ID)
            and _resolve_secret(config.S3_SECRET_ACCESS_KEY)
        ),
    }


def test_s3_connection() -> dict[str, Any]:
    """
    Test S3 connectivity, credentials, and write permissions.

    Returns:
        dict with success, credentials_valid, bucket_accessible, write_permission,
        latency_ms, and error fields.
    """
    import time

    result: dict[str, Any] = {
        "success": False,
        "credentials_valid": False,
        "bucket_accessible": False,
        "write_permission": False,
        "latency_ms": None,
        "error": None,
    }

    # Check configuration
    if not config.S3_ENDPOINT_URL:
        result["error"] = "S3_ENDPOINT_URL not configured"
        return result

    if not config.S3_ACCESS_KEY_ID or not config.S3_SECRET_ACCESS_KEY:
        result["error"] = "S3 credentials not configured"
        return result

    if not config.S3_BUCKET_NAME:
        result["error"] = "S3_BUCKET_NAME not configured"
        return result

    s3_client = _get_s3_client()
    if s3_client is None:
        result["error"] = "Failed to create S3 client"
        return result

    try:
        # Test 1: Credentials valid (list buckets or head bucket)
        start_time = time.time()
        try:
            s3_client.head_bucket(Bucket=config.S3_BUCKET_NAME)
            result["credentials_valid"] = True
            result["bucket_accessible"] = True
        except s3_client.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                result["credentials_valid"] = True
                result["error"] = f"Bucket '{config.S3_BUCKET_NAME}' not found"
                return result
            elif error_code in ("403", "AccessDenied"):
                result["error"] = "Access denied - check credentials and bucket permissions"
                return result
            else:
                result["error"] = f"S3 error: {e}"
                return result

        # Test 2: Write permission (upload and delete a test file)
        test_key = f".connection_test_{int(time.time())}"
        try:
            s3_client.put_object(
                Bucket=config.S3_BUCKET_NAME,
                Key=test_key,
                Body=b"connection test",
            )
            s3_client.delete_object(Bucket=config.S3_BUCKET_NAME, Key=test_key)
            result["write_permission"] = True
        except Exception as e:
            result["error"] = f"Write permission test failed: {e}"
            return result

        end_time = time.time()
        result["latency_ms"] = round((end_time - start_time) * 1000, 1)
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def get_bucket_stats() -> dict[str, Any]:
    """
    Retrieve backup statistics from the configured S3 bucket.

    Returns:
        dict with backup_count, total_size_bytes, oldest_backup, newest_backup,
        backups list, and error fields.
    """
    result: dict[str, Any] = {
        "backup_count": 0,
        "total_size_bytes": 0,
        "oldest_backup": None,
        "newest_backup": None,
        "backups": [],
        "error": None,
    }

    s3_client = _get_s3_client()
    if s3_client is None:
        result["error"] = "S3 client not available"
        return result

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=config.S3_BUCKET_NAME,
            Prefix=BACKUP_FILENAME_PREFIX,
        )

        backups = []
        for page in pages:
            for obj in page.get("Contents", []):
                backups.append(
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                )

        if backups:
            # Sort by last modified
            backups.sort(key=lambda x: x["last_modified"])

            result["backup_count"] = len(backups)
            result["total_size_bytes"] = sum(b["size"] for b in backups)
            result["oldest_backup"] = (
                backups[0]["key"].replace(BACKUP_FILENAME_PREFIX, "").replace(".db", "")
            )
            result["newest_backup"] = (
                backups[-1]["key"].replace(BACKUP_FILENAME_PREFIX, "").replace(".db", "")
            )
            result["backups"] = [
                {
                    "name": b["key"],
                    "size": b["size"],
                    "date": b["last_modified"].strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
                for b in backups
            ]

    except Exception as e:
        result["error"] = str(e)

    return result


def perform_backup_with_details() -> dict[str, Any]:
    """
    Perform a database backup to S3 and return operation details.

    Returns:
        dict with success, filename, size_bytes, deleted_count, and error fields.
    """
    result: dict[str, Any] = {
        "success": False,
        "filename": None,
        "size_bytes": None,
        "deleted_count": 0,
        "data_files": [],
        "error": None,
    }

    if not config.S3_ENDPOINT_URL:
        result["error"] = "S3_ENDPOINT_URL not configured"
        return result

    if not config.S3_ACCESS_KEY_ID or not config.S3_SECRET_ACCESS_KEY:
        result["error"] = "S3 credentials not configured"
        return result

    if not config.S3_BUCKET_NAME:
        result["error"] = "S3_BUCKET_NAME not configured"
        return result

    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        result["error"] = f"Database file not found: {db_path}"
        return result

    s3_client = _get_s3_client()
    if s3_client is None:
        result["error"] = "Failed to create S3 client"
        return result

    temp_path = None
    try:
        # Create a consistent SQLite snapshot of the live database
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)
        result["data_files"] = create_backup_snapshot(db_path, temp_path)

        # Generate filename and get size
        backup_filename = generate_backup_filename()
        file_size = os.path.getsize(temp_path)

        # Upload
        s3_client.upload_file(
            temp_path,
            config.S3_BUCKET_NAME,
            backup_filename,
        )

        result["success"] = True
        result["filename"] = backup_filename
        result["size_bytes"] = file_size
        _record_backup_event(BACKUP_SUCCESS_META_KEY)

        # Cleanup old backups
        if config.BACKUP_RETENTION_DAYS > 0:
            result["deleted_count"] = cleanup_old_backups(s3_client)

    except Exception as e:
        result["error"] = str(e)
        _record_backup_event(BACKUP_FAILURE_META_KEY)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(e)

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return result


# =========================================================================
# Local backups, listing and restore
# =========================================================================


def check_integrity(path: Path) -> bool:
    """Return whether ``path`` is an SQLite database that passes ``PRAGMA integrity_check``."""
    try:
        with closing(sqlite3.connect(_database_uri(path, "immutable"), uri=True)) as db:
            rows = db.execute("PRAGMA integrity_check").fetchall()
    except OSError, sqlite3.Error:
        return False
    return rows == [("ok",)]


def _parse_backup_name(name: str) -> datetime | None:
    """Return the UTC timestamp encoded in a backup or safety-snapshot name."""
    match = _BACKUP_NAME_PATTERN.match(name)
    if match is None:
        return None
    return datetime.strptime(match["stamp"], _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def local_backups() -> list[dict[str, Any]]:
    """Return local backups and safety snapshots, oldest first."""
    directory = config.backup_local_dir
    if not directory.is_dir():
        return []
    backups = []
    for path in directory.iterdir():
        created = _parse_backup_name(path.name)
        if created is None or not path.is_file():
            continue
        backups.append(
            {
                "name": path.name,
                "source": "local",
                "created_utc": created.isoformat(),
                "size_bytes": path.stat().st_size,
                "integrity": INTEGRITY_UNKNOWN,
                "safety_snapshot": path.name.startswith(SAFETY_SNAPSHOT_PREFIX),
            }
        )
    return sorted(backups, key=lambda backup: backup["created_utc"])


def _s3_backups(s3_client) -> list[dict[str, Any]]:
    """Return the database backups in the configured bucket, oldest first."""
    paginator = s3_client.get_paginator("list_objects_v2")
    backups = []
    for page in paginator.paginate(Bucket=config.S3_BUCKET_NAME, Prefix=BACKUP_FILENAME_PREFIX):
        for obj in page.get("Contents", []):
            if _parse_backup_name(obj["Key"]) is None:
                continue
            backups.append(
                {
                    "name": obj["Key"],
                    "source": "s3",
                    "created_utc": obj["LastModified"].astimezone(timezone.utc).isoformat(),
                    "size_bytes": obj["Size"],
                    "integrity": INTEGRITY_UNKNOWN,
                    "safety_snapshot": False,
                }
            )
    return sorted(backups, key=lambda backup: backup["created_utc"])


def _download_s3_backup(s3_client, name: str, target: Path) -> None:
    """Download one bucket object to ``target``."""
    s3_client.download_file(config.S3_BUCKET_NAME, name, str(target))


def _verify_backup(backup: dict[str, Any], s3_client) -> str:
    """Run the integrity check on a listed backup (downloading a remote one to a temp file)."""
    if backup["source"] == "local":
        path = config.backup_local_dir / backup["name"]
        return INTEGRITY_OK if check_integrity(path) else INTEGRITY_FAILED
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / backup["name"]
        _download_s3_backup(s3_client, backup["name"], target)
        return INTEGRITY_OK if check_integrity(target) else INTEGRITY_FAILED


def list_backups(*, verify: bool = False) -> dict[str, Any]:
    """List local backups and, unless MINIMAL_DATA_MODE disables it, the S3 backups.

    Integrity is ``unknown`` until ``verify`` runs ``PRAGMA integrity_check`` on each file.
    Errors name only the failing step and the exception type, never endpoints or keys.
    """
    result: dict[str, Any] = {"backups": [], "s3_available": False, "errors": []}
    backups = local_backups()
    s3_client = _get_s3_client()
    if s3_client is not None:
        result["s3_available"] = True
        try:
            backups.extend(_s3_backups(s3_client))
        except Exception as exc:
            result["errors"].append(f"S3 listing failed ({type(exc).__name__})")
    if verify:
        for backup in backups:
            try:
                backup["integrity"] = _verify_backup(backup, s3_client)
            except Exception as exc:
                backup["integrity"] = INTEGRITY_FAILED
                result["errors"].append(f"Verifying {backup['name']} failed ({type(exc).__name__})")
    result["backups"] = sorted(backups, key=lambda backup: backup["created_utc"])
    return result


def perform_local_backup() -> dict[str, Any]:
    """Write a snapshot (with embedded data files) into the local backup directory.

    Local backups never leave the machine, so they are available in MINIMAL_DATA_MODE too.
    Regular local backups older than BACKUP_RETENTION_DAYS are removed; safety snapshots stay.
    """
    result: dict[str, Any] = {
        "success": False,
        "filename": None,
        "size_bytes": None,
        "data_files": [],
        "deleted_count": 0,
        "error": None,
    }
    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        result["error"] = "Database file not found"
        return result
    directory = config.backup_local_dir
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        filename = generate_backup_filename()
        temp_path = directory / f".{filename}.tmp"
        try:
            result["data_files"] = create_backup_snapshot(db_path, temp_path)
            os.replace(temp_path, directory / filename)
        finally:
            temp_path.unlink(missing_ok=True)
    except (OSError, sqlite3.Error) as exc:
        result["error"] = f"Local backup failed ({type(exc).__name__})"
        return result
    result.update(success=True, filename=filename, size_bytes=(directory / filename).stat().st_size)
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.BACKUP_RETENTION_DAYS)
    for backup in local_backups():
        if backup["safety_snapshot"] or datetime.fromisoformat(backup["created_utc"]) >= cutoff:
            continue
        (directory / backup["name"]).unlink(missing_ok=True)
        result["deleted_count"] += 1
    return result


def _database_in_use(db_path: Path) -> bool:
    """Return whether another process appears to hold the database open or locked."""
    if any(Path(f"{db_path}{suffix}").exists() for suffix in ("-wal", "-shm")):
        return True
    if not db_path.exists():
        return False
    try:
        with closing(sqlite3.connect(_database_uri(db_path, "rw"), uri=True, timeout=0)) as db:
            db.execute("BEGIN EXCLUSIVE")
            db.rollback()
    except sqlite3.OperationalError:
        return True
    return False


def _schema_version(path: Path) -> int:
    """Return ``PRAGMA user_version`` of an SQLite file."""
    with closing(sqlite3.connect(_database_uri(path, "immutable"), uri=True)) as db:
        return int(db.execute("PRAGMA user_version").fetchone()[0])


def _extract_data_files(path: Path) -> dict[str, bytes]:
    """Remove the embedded data files from a working copy and return the known ones."""
    known = set(data_file_paths())
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (DATA_FILES_TABLE,)
        ).fetchone()
        if not exists:
            return {}
        rows = db.execute(f"SELECT name, content FROM {DATA_FILES_TABLE}").fetchall()
        db.execute(f"DROP TABLE {DATA_FILES_TABLE}")
        db.commit()
    return {name: bytes(content) for name, content in rows if name in known}


def _resolve_restore_source(name: str, source: str, s3_client) -> tuple[str, str] | None:
    """Return ``(name, source)`` for a named or ``latest`` backup, or None if it is unknown."""
    if source == "auto":
        if name != "latest" and (config.backup_local_dir / name).is_file():
            return name, "local"
        source = "s3" if s3_client is not None else "local"
    if source == "s3" and s3_client is None:
        return None
    if name != "latest":
        if source == "local":
            return (name, "local") if (config.backup_local_dir / name).is_file() else None
        return name, "s3"
    candidates = (
        [backup for backup in local_backups() if not backup["safety_snapshot"]]
        if source == "local"
        else _s3_backups(s3_client)
    )
    return (candidates[-1]["name"], source) if candidates else None


async def _migrate_database(db_path: str) -> None:
    """Run the normal schema creation and migrations on a restored database."""
    from app.services.database import Database

    with Database.schema_state_lock:
        Database.initialized_paths.discard(db_path)
    database = Database(db_path)
    try:
        await database.ping()
    finally:
        await database.close()


def restore_backup(name: str, *, source: str = "auto", force: bool = False) -> dict[str, Any]:
    """Restore a backup over the configured database; run with the service stopped.

    The backup is copied to a working file, checked with ``PRAGMA integrity_check`` and its
    schema version compared before anything changes. The live database is then saved as a
    safety snapshot, replaced through SQLite's backup API, its embedded data files are written
    back atomically, and the normal migrations bring older schemas up to date.
    """
    from app.services.database import DATABASE_SCHEMA_VERSION

    result: dict[str, Any] = {
        "success": False,
        "name": None,
        "source": None,
        "safety_snapshot": None,
        "data_files_restored": [],
        "schema_version_backup": None,
        "schema_version_after": None,
        "error": None,
    }
    if name != "latest" and _parse_backup_name(name) is None:
        result["error"] = "Unknown backup name; use a name from 'backup list' or 'latest'"
        return result
    if source not in ("auto", "local", "s3"):
        result["error"] = "Unknown source; use auto, local or s3"
        return result

    db_path = Path(config.DATABASE_PATH)
    if _database_in_use(db_path) and not force:
        result["error"] = (
            "The database is in use. Stop the service first (see SELF-HOSTING.md), or pass "
            "--force if a crash left stale -wal/-shm files behind"
        )
        return result

    s3_client = _get_s3_client() if source != "local" else None
    try:
        resolved = _resolve_restore_source(name, source, s3_client)
    except Exception as exc:
        result["error"] = f"S3 listing failed ({type(exc).__name__})"
        return result
    if resolved is None:
        result["error"] = "Backup not found"
        return result
    result["name"], result["source"] = resolved

    db_path.parent.mkdir(parents=True, exist_ok=True)
    working = db_path.with_name(f".{db_path.name}.restore.{os.getpid()}.tmp")
    try:
        try:
            if result["source"] == "local":
                shutil.copyfile(config.backup_local_dir / result["name"], working)
            else:
                _download_s3_backup(s3_client, result["name"], working)
        except Exception as exc:
            result["error"] = f"Could not read the backup ({type(exc).__name__})"
            return result

        if not check_integrity(working):
            result["error"] = "Integrity check failed; nothing was restored"
            return result
        result["schema_version_backup"] = _schema_version(working)
        if result["schema_version_backup"] > DATABASE_SCHEMA_VERSION and not force:
            result["error"] = (
                f"The backup uses schema {result['schema_version_backup']}, newer than this "
                f"version's {DATABASE_SCHEMA_VERSION}; upgrade first or pass --force"
            )
            return result
        data_files = _extract_data_files(working)

        if db_path.exists():
            directory = config.backup_local_dir
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            snapshot = directory / datetime.now(timezone.utc).strftime(SAFETY_SNAPSHOT_FORMAT)
            create_backup_snapshot(db_path, snapshot, include_data_files=True)
            result["safety_snapshot"] = snapshot.name

        with (
            closing(sqlite3.connect(working)) as src,
            closing(sqlite3.connect(db_path, timeout=5)) as dst,
        ):
            src.backup(dst)

        targets = data_file_paths()
        for file_name, content in sorted(data_files.items()):
            atomic_write_bytes_sync(targets[file_name], content)
            result["data_files_restored"].append(file_name)

        asyncio.run(_migrate_database(str(db_path)))
        result["schema_version_after"] = _schema_version(db_path)
    except (OSError, sqlite3.Error) as exc:
        result["error"] = f"Restore failed ({type(exc).__name__})"
        return result
    finally:
        for leftover in (working, *(Path(f"{working}{suffix}") for suffix in ("-wal", "-shm"))):
            with suppress(OSError):
                leftover.unlink(missing_ok=True)
    result["success"] = True
    return result
