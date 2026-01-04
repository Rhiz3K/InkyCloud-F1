"""S3-compatible backup service for SQLite database."""

import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sentry_sdk

from app.config import config

logger = logging.getLogger(__name__)

# Backup filename format: f1_backup_YYYY-MM-DD_HH-MM-SS.db
BACKUP_FILENAME_PREFIX = "f1_backup_"
BACKUP_FILENAME_FORMAT = f"{BACKUP_FILENAME_PREFIX}%Y-%m-%d_%H-%M-%S.db"


def _get_s3_client():
    """
    Create and return a boto3 S3 client configured for the backup endpoint.

    Returns:
        boto3 S3 client or None if configuration is incomplete.
    """
    if not config.S3_ENDPOINT_URL:
        logger.debug("S3_ENDPOINT_URL not configured, skipping S3 client creation")
        return None

    if not config.S3_ACCESS_KEY_ID or not config.S3_SECRET_ACCESS_KEY:
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
        aws_access_key_id=config.S3_ACCESS_KEY_ID,
        aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
        region_name=config.S3_REGION,
    )


def is_backup_configured() -> bool:
    """
    Check if backup is properly configured and enabled.

    Returns:
        True if backup is enabled and all required S3 settings are present.
    """
    if not config.BACKUP_ENABLED:
        return False

    required = [
        config.S3_ENDPOINT_URL,
        config.S3_ACCESS_KEY_ID,
        config.S3_SECRET_ACCESS_KEY,
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
        logger.warning(f"Database file not found: {db_path}")
        return False

    s3_client = _get_s3_client()
    if s3_client is None:
        return False

    temp_path = None
    try:
        # Create temporary copy of database to avoid locking issues
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)

        logger.info(f"Creating backup copy of {db_path}")
        shutil.copy2(db_path, temp_path)

        # Generate backup filename and upload
        backup_filename = generate_backup_filename()
        file_size = os.path.getsize(temp_path)

        logger.info(f"Uploading backup to S3: {backup_filename} ({file_size} bytes)")
        s3_client.upload_file(
            temp_path,
            config.S3_BUCKET_NAME,
            backup_filename,
        )

        logger.info(f"Backup completed successfully: {backup_filename}")

        # Clean up old backups if retention is configured
        if config.BACKUP_RETENTION_DAYS > 0:
            cleanup_old_backups(s3_client)

        return True

    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return False

    finally:
        # Always clean up temporary file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError as e:
                logger.warning(f"Failed to remove temporary file {temp_path}: {e}")


def cleanup_old_backups(s3_client=None) -> int:
    """
    Delete backups older than the retention period.

    Args:
        s3_client: Optional boto3 S3 client. If None, creates a new client.

    Returns:
        Number of backups deleted.
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
                    logger.debug(f"Marking for deletion: {key} (modified: {last_modified})")

        # Delete old backups in batches
        if objects_to_delete:
            # S3 delete_objects supports up to 1000 keys per request
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i : i + 1000]
                s3_client.delete_objects(
                    Bucket=config.S3_BUCKET_NAME,
                    Delete={"Objects": batch},
                )
                deleted_count += len(batch)

            logger.info(f"Deleted {deleted_count} old backup(s) (older than {cutoff_date.date()})")

    except Exception as e:
        logger.error(f"Failed to cleanup old backups: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)

    return deleted_count


def get_backup_config_info() -> dict:
    """
    Get backup configuration information (without sensitive data).

    Returns:
        Dictionary with configuration details.
    """
    return {
        "enabled": config.BACKUP_ENABLED,
        "endpoint": config.S3_ENDPOINT_URL or "(not configured)",
        "bucket": config.S3_BUCKET_NAME or "(not configured)",
        "region": config.S3_REGION,
        "schedule": config.BACKUP_CRON,
        "retention_days": config.BACKUP_RETENTION_DAYS,
        "credentials_configured": bool(config.S3_ACCESS_KEY_ID and config.S3_SECRET_ACCESS_KEY),
    }


def test_s3_connection() -> dict:
    """
    Test S3 connection and return detailed results.

    Returns:
        Dictionary with test results including:
        - success: bool
        - credentials_valid: bool
        - bucket_accessible: bool
        - write_permission: bool
        - latency_ms: float (if successful)
        - error: str (if failed)
    """
    import time

    result = {
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


def get_bucket_stats() -> dict:
    """
    Get statistics about backups in the S3 bucket.

    Returns:
        Dictionary with:
        - backup_count: int
        - total_size_bytes: int
        - oldest_backup: str or None
        - newest_backup: str or None
        - error: str or None
    """
    result = {
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


def perform_backup_with_details() -> dict:
    """
    Perform backup and return detailed results for CLI output.

    Returns:
        Dictionary with:
        - success: bool
        - filename: str or None
        - size_bytes: int or None
        - deleted_count: int
        - error: str or None
    """
    result = {
        "success": False,
        "filename": None,
        "size_bytes": None,
        "deleted_count": 0,
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
        # Create temporary copy of database
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        os.close(temp_fd)
        shutil.copy2(db_path, temp_path)

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

        # Cleanup old backups
        if config.BACKUP_RETENTION_DAYS > 0:
            result["deleted_count"] = cleanup_old_backups(s3_client)

    except Exception as e:
        result["error"] = str(e)
        sentry_sdk.capture_exception(e)

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return result
