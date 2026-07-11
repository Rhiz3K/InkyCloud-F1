"""Tests for S3 backup service."""

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.backup import (
    BACKUP_FILENAME_PREFIX,
    generate_backup_filename,
    is_backup_configured,
)
from scripts import backup_cli


class TestBackupFilename:
    """Tests for backup filename generation."""

    def test_generate_backup_filename_format(self):
        """Test that backup filename matches expected format."""
        filename = generate_backup_filename()

        assert filename.startswith(BACKUP_FILENAME_PREFIX)
        assert filename.endswith(".db")
        # Format: f1_backup_YYYY-MM-DD_HH-MM-SS.db
        assert len(filename) == len("f1_backup_2025-01-15_03-00-00.db")

    def test_generate_backup_filename_uses_utc(self):
        """Test that backup filename uses UTC timestamp."""
        with patch("app.services.backup.datetime") as mock_dt:
            mock_now = datetime(2025, 3, 15, 14, 30, 45, tzinfo=timezone.utc)
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            filename = generate_backup_filename()
            assert "2025-03-15" in filename
            assert "14-30-45" in filename


class TestBackupConfiguration:
    """Tests for backup configuration validation."""

    def test_is_backup_configured_disabled_by_default(self):
        """Test that backup is disabled when BACKUP_ENABLED=false."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = False
            mock_config.S3_ENDPOINT_URL = "https://example.com"
            mock_config.S3_ACCESS_KEY_ID = "test"
            mock_config.S3_SECRET_ACCESS_KEY = "test"
            mock_config.S3_BUCKET_NAME = "test"

            assert is_backup_configured() is False

    def test_is_backup_configured_missing_endpoint(self):
        """Test that backup is not configured without S3 endpoint."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = True
            mock_config.S3_ENDPOINT_URL = None
            mock_config.S3_ACCESS_KEY_ID = "test"
            mock_config.S3_SECRET_ACCESS_KEY = "test"
            mock_config.S3_BUCKET_NAME = "test"

            assert is_backup_configured() is False

    def test_is_backup_configured_missing_credentials(self):
        """Test that backup is not configured without credentials."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = True
            mock_config.S3_ENDPOINT_URL = "https://example.com"
            mock_config.S3_ACCESS_KEY_ID = None
            mock_config.S3_SECRET_ACCESS_KEY = "test"
            mock_config.S3_BUCKET_NAME = "test"

            assert is_backup_configured() is False

    def test_is_backup_configured_missing_bucket(self):
        """Test that backup is not configured without bucket name."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = True
            mock_config.S3_ENDPOINT_URL = "https://example.com"
            mock_config.S3_ACCESS_KEY_ID = "test"
            mock_config.S3_SECRET_ACCESS_KEY = "test"
            mock_config.S3_BUCKET_NAME = None

            assert is_backup_configured() is False

    def test_is_backup_configured_all_set(self):
        """Test that backup is configured when all settings are present."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = True
            mock_config.S3_ENDPOINT_URL = "https://example.com"
            mock_config.S3_ACCESS_KEY_ID = "test-key"
            mock_config.S3_SECRET_ACCESS_KEY = "test-secret"
            mock_config.S3_BUCKET_NAME = "test-bucket"

            assert is_backup_configured() is True


def test_manual_backup_refuses_to_run_when_disabled(capsys):
    with (
        patch("app.config.config.BACKUP_ENABLED", False),
        patch("app.services.backup.perform_backup_with_details") as perform_backup,
    ):
        result = backup_cli.cmd_now()

    assert result == 1
    assert "Backup is disabled" in capsys.readouterr().out
    perform_backup.assert_not_called()


class TestPerformBackup:
    """Tests for backup execution."""

    def test_perform_backup_skips_when_not_configured(self):
        """Test that backup is skipped when not configured."""
        with patch("app.services.backup.is_backup_configured", return_value=False):
            from app.services.backup import perform_backup

            result = perform_backup()
            assert result is False

    def test_perform_backup_skips_when_db_not_found(self):
        """Test that backup is skipped when database file doesn't exist."""
        with (
            patch("app.services.backup.is_backup_configured", return_value=True),
            patch("app.services.backup.config") as mock_config,
        ):
            mock_config.DATABASE_PATH = "/nonexistent/path/f1.db"

            from app.services.backup import perform_backup

            result = perform_backup()
            assert result is False

    def test_perform_backup_success(self):
        """Test successful backup execution with a consistent SQLite snapshot."""
        # Create a temporary SQLite database file with WAL enabled.
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            tmp_db_path = tmp_db.name

        uploaded_copy = f"{tmp_db_path}.uploaded"

        conn = sqlite3.connect(tmp_db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE races (id INTEGER PRIMARY KEY, name TEXT)")
            conn.execute("INSERT INTO races (name) VALUES (?)", ("Australian Grand Prix",))
            conn.commit()
        finally:
            conn.close()

        try:
            mock_s3_client = MagicMock()
            mock_s3_client.upload_file.side_effect = lambda src, *_args: shutil.copy2(
                src, uploaded_copy
            )

            with (
                patch("app.services.backup.is_backup_configured", return_value=True),
                patch("app.services.backup._get_s3_client", return_value=mock_s3_client),
                patch("app.services.backup.config") as mock_config,
                patch("app.services.backup.cleanup_old_backups"),
            ):
                mock_config.DATABASE_PATH = tmp_db_path
                mock_config.S3_BUCKET_NAME = "test-bucket"
                mock_config.BACKUP_RETENTION_DAYS = 30

                from app.services.backup import perform_backup

                result = perform_backup()

                assert result is True
                mock_s3_client.upload_file.assert_called_once()
                call_args = mock_s3_client.upload_file.call_args
                assert call_args[0][1] == "test-bucket"  # bucket name
                assert call_args[0][2].startswith(BACKUP_FILENAME_PREFIX)  # key

            snapshot = sqlite3.connect(uploaded_copy)
            try:
                rows = snapshot.execute("SELECT name FROM races").fetchall()
            finally:
                snapshot.close()

            assert rows == [("Australian Grand Prix",)]
        finally:
            os.unlink(tmp_db_path)
            if os.path.exists(uploaded_copy):
                os.unlink(uploaded_copy)

    @staticmethod
    def test_perform_backup_stops_when_client_creation_fails(tmp_path):
        from app.services.backup import perform_backup

        database_path = tmp_path / "f1.db"
        database_path.touch()
        with (
            patch("app.services.backup.is_backup_configured", return_value=True),
            patch("app.services.backup._get_s3_client", return_value=None),
            patch("app.services.backup.config.DATABASE_PATH", database_path),
        ):
            assert perform_backup() is False

    @staticmethod
    def test_perform_backup_reports_upload_failure(tmp_path):
        from app.services.backup import perform_backup

        database_path = tmp_path / "f1.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE test (value TEXT)")
        connection.commit()
        connection.close()
        client = MagicMock()
        client.upload_file.side_effect = RuntimeError("upload failed")
        capture_exception = MagicMock()
        with (
            patch("app.services.backup.is_backup_configured", return_value=True),
            patch("app.services.backup._get_s3_client", return_value=client),
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup.sentry_sdk.capture_exception", capture_exception),
        ):
            mock_config.DATABASE_PATH = database_path
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.BACKUP_RETENTION_DAYS = 0

            assert perform_backup() is False

        capture_exception.assert_called_once()

    @staticmethod
    def test_perform_backup_tolerates_tempfile_removal_failure(tmp_path):
        from app.services.backup import perform_backup

        database_path = tmp_path / "f1.db"
        database_path.touch()
        temp_path = tmp_path / "snapshot.db"
        temp_path.touch()
        temp_fd = os.open(temp_path, os.O_RDWR)
        client = MagicMock()
        with (
            patch("app.services.backup.is_backup_configured", return_value=True),
            patch("app.services.backup._get_s3_client", return_value=client),
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup.tempfile.mkstemp", return_value=(temp_fd, str(temp_path))),
            patch("app.services.backup._create_sqlite_snapshot"),
            patch("app.services.backup.os.remove", side_effect=OSError("busy")),
        ):
            mock_config.DATABASE_PATH = database_path
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.BACKUP_RETENTION_DAYS = 0

            assert perform_backup() is True


class TestCleanupOldBackups:
    """Tests for backup retention cleanup."""

    def test_cleanup_disabled_when_retention_zero(self):
        """Test that cleanup is skipped when retention is 0."""
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_RETENTION_DAYS = 0

            from app.services.backup import cleanup_old_backups

            result = cleanup_old_backups()
            assert result == 0

    def test_cleanup_deletes_old_backups(self):
        """Test that old backups are deleted based on retention period."""
        mock_s3_client = MagicMock()

        # Mock list_objects_v2 pagination
        old_date = datetime.now(timezone.utc) - timedelta(days=60)
        recent_date = datetime.now(timezone.utc) - timedelta(days=5)

        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "Contents": [
                    {
                        "Key": "f1_backup_2025-01-01_03-00-00.db",
                        "LastModified": old_date,
                    },
                    {
                        "Key": "f1_backup_2025-03-10_03-00-00.db",
                        "LastModified": recent_date,
                    },
                ]
            }
        ]
        mock_s3_client.get_paginator.return_value = mock_paginator

        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_RETENTION_DAYS = 30
            mock_config.S3_BUCKET_NAME = "test-bucket"

            from app.services.backup import cleanup_old_backups

            result = cleanup_old_backups(mock_s3_client)

            # Should delete only the old backup
            assert result == 1
            mock_s3_client.delete_objects.assert_called_once()
            delete_call = mock_s3_client.delete_objects.call_args
            assert delete_call[1]["Bucket"] == "test-bucket"
            assert len(delete_call[1]["Delete"]["Objects"]) == 1
            assert (
                delete_call[1]["Delete"]["Objects"][0]["Key"] == "f1_backup_2025-01-01_03-00-00.db"
            )

    @staticmethod
    def test_cleanup_returns_zero_without_configured_client():
        from app.services.backup import cleanup_old_backups

        with (
            patch("app.services.backup.config.BACKUP_RETENTION_DAYS", 30),
            patch("app.services.backup._get_s3_client", return_value=None),
        ):
            assert cleanup_old_backups() == 0

    @staticmethod
    def test_cleanup_handles_bucket_without_backups():
        from app.services.backup import cleanup_old_backups

        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{}]
        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_RETENTION_DAYS = 30
            mock_config.S3_BUCKET_NAME = "bucket"

            assert cleanup_old_backups(client) == 0

        client.delete_objects.assert_not_called()


class TestBackupDiagnostics:
    """Exercise backup configuration, diagnostics, and failure reporting."""

    @staticmethod
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("plain", "plain"),
            (123, "123"),
        ],
    )
    def test_resolve_secret_plain_values(value, expected):
        from app.services.backup import _resolve_secret

        assert _resolve_secret(value) == expected

    @staticmethod
    def test_resolve_secret_secret_value():
        from app.services.backup import _resolve_secret

        secret = MagicMock()
        secret.get_secret_value.return_value = "resolved"

        assert _resolve_secret(secret) == "resolved"

    @staticmethod
    @pytest.mark.parametrize(
        ("endpoint", "access_key", "secret_key", "bucket"),
        [
            (None, "access", "secret", "bucket"),
            ("https://s3.example", None, "secret", "bucket"),
            ("https://s3.example", "access", "secret", None),
        ],
    )
    def test_get_s3_client_rejects_incomplete_configuration(
        endpoint, access_key, secret_key, bucket
    ):
        from app.services.backup import _get_s3_client

        with patch("app.services.backup.config") as mock_config:
            mock_config.S3_ENDPOINT_URL = endpoint
            mock_config.S3_ACCESS_KEY_ID = access_key
            mock_config.S3_SECRET_ACCESS_KEY = secret_key
            mock_config.S3_BUCKET_NAME = bucket

            assert _get_s3_client() is None

    @staticmethod
    def test_get_s3_client_handles_missing_boto3():
        from app.services.backup import _get_s3_client

        with (
            patch("app.services.backup.config") as mock_config,
            patch.dict(sys.modules, {"boto3": None}),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            assert _get_s3_client() is None

    @staticmethod
    def test_get_s3_client_builds_configured_client():
        from app.services.backup import _get_s3_client

        client = object()
        boto3 = SimpleNamespace(client=MagicMock(return_value=client))
        with (
            patch("app.services.backup.config") as mock_config,
            patch.dict(sys.modules, {"boto3": boto3}),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.S3_REGION = "eu-test-1"

            assert _get_s3_client() is client

        boto3.client.assert_called_once_with(
            "s3",
            endpoint_url="https://s3.example",
            aws_access_key_id="access",
            aws_secret_access_key="secret",
            region_name="eu-test-1",
        )

    @staticmethod
    def test_get_backup_config_info_masks_credentials():
        from app.services.backup import get_backup_config_info

        with patch("app.services.backup.config") as mock_config:
            mock_config.BACKUP_ENABLED = True
            mock_config.S3_ENDPOINT_URL = None
            mock_config.S3_BUCKET_NAME = None
            mock_config.S3_REGION = "auto"
            mock_config.BACKUP_CRON = "0 3 * * *"
            mock_config.BACKUP_RETENTION_DAYS = 14
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"

            info = get_backup_config_info()

        assert info == {
            "enabled": True,
            "endpoint": "(not configured)",
            "bucket": "(not configured)",
            "region": "auto",
            "schedule": "0 3 * * *",
            "retention_days": 14,
            "credentials_configured": True,
        }

    @staticmethod
    @pytest.mark.parametrize(
        ("endpoint", "access_key", "secret_key", "bucket", "expected_error"),
        [
            (None, "access", "secret", "bucket", "S3_ENDPOINT_URL not configured"),
            (
                "https://s3.example",
                None,
                "secret",
                "bucket",
                "S3 credentials not configured",
            ),
            (
                "https://s3.example",
                "access",
                "secret",
                None,
                "S3_BUCKET_NAME not configured",
            ),
        ],
    )
    def test_s3_connection_rejects_incomplete_configuration(
        endpoint, access_key, secret_key, bucket, expected_error
    ):
        from app.services.backup import test_s3_connection

        with patch("app.services.backup.config") as mock_config:
            mock_config.S3_ENDPOINT_URL = endpoint
            mock_config.S3_ACCESS_KEY_ID = access_key
            mock_config.S3_SECRET_ACCESS_KEY = secret_key
            mock_config.S3_BUCKET_NAME = bucket

            result = test_s3_connection()

        assert result["success"] is False
        assert result["error"] == expected_error

    @staticmethod
    def test_s3_connection_reports_missing_client():
        from app.services.backup import test_s3_connection

        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=None),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            result = test_s3_connection()

        assert result["error"] == "Failed to create S3 client"

    @staticmethod
    def test_s3_connection_checks_read_and_write_access():
        from app.services.backup import test_s3_connection

        client = MagicMock()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=client),
            patch("time.time", side_effect=[10.0, 10.25, 10.5]),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            result = test_s3_connection()

        assert result == {
            "success": True,
            "credentials_valid": True,
            "bucket_accessible": True,
            "write_permission": True,
            "latency_ms": 500.0,
            "error": None,
        }
        client.put_object.assert_called_once_with(
            Bucket="bucket", Key=".connection_test_10", Body=b"connection test"
        )
        client.delete_object.assert_called_once_with(Bucket="bucket", Key=".connection_test_10")

    @staticmethod
    @pytest.mark.parametrize(
        ("code", "expected_valid", "expected_error"),
        [
            ("404", True, "Bucket 'bucket' not found"),
            ("403", False, "Access denied - check credentials and bucket permissions"),
            ("500", False, "S3 error: failure"),
        ],
    )
    def test_s3_connection_classifies_head_bucket_errors(code, expected_valid, expected_error):
        from app.services.backup import test_s3_connection

        class ClientError(Exception):
            def __init__(self):
                super().__init__("failure")
                self.response = {"Error": {"Code": code}}

        client = MagicMock()
        client.exceptions.ClientError = ClientError
        client.head_bucket.side_effect = ClientError()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=client),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            result = test_s3_connection()

        assert result["credentials_valid"] is expected_valid
        assert result["error"] == expected_error

    @staticmethod
    def test_s3_connection_reports_write_failure():
        from app.services.backup import test_s3_connection

        client = MagicMock()
        client.put_object.side_effect = RuntimeError("read only")
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=client),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            result = test_s3_connection()

        assert result["credentials_valid"] is True
        assert result["bucket_accessible"] is True
        assert result["error"] == "Write permission test failed: read only"

    @staticmethod
    def test_s3_connection_reports_unexpected_failure():
        from app.services.backup import test_s3_connection

        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=MagicMock()),
            patch("time.time", side_effect=RuntimeError("clock failed")),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"

            result = test_s3_connection()

        assert result["error"] == "clock failed"


class TestBackupReporting:
    """Validate bucket summaries and detailed backup responses."""

    @staticmethod
    def test_bucket_stats_reports_unavailable_client():
        from app.services.backup import get_bucket_stats

        with patch("app.services.backup._get_s3_client", return_value=None):
            result = get_bucket_stats()

        assert result["error"] == "S3 client not available"
        assert result["backup_count"] == 0

    @staticmethod
    def test_bucket_stats_summarizes_sorted_backups():
        from app.services.backup import get_bucket_stats

        older = datetime(2025, 1, 1, tzinfo=timezone.utc)
        newer = datetime(2025, 2, 1, tzinfo=timezone.utc)
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "f1_backup_2025-02-01.db", "Size": 20, "LastModified": newer},
                    {"Key": "f1_backup_2025-01-01.db", "Size": 10, "LastModified": older},
                ]
            }
        ]
        with (
            patch("app.services.backup._get_s3_client", return_value=client),
            patch("app.services.backup.config.S3_BUCKET_NAME", "bucket"),
        ):
            result = get_bucket_stats()

        assert result["backup_count"] == 2
        assert result["total_size_bytes"] == 30
        assert result["oldest_backup"] == "2025-01-01"
        assert result["newest_backup"] == "2025-02-01"
        assert [item["size"] for item in result["backups"]] == [10, 20]
        assert result["error"] is None

    @staticmethod
    def test_bucket_stats_handles_empty_and_failed_listing():
        from app.services.backup import get_bucket_stats

        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{}]
        with patch("app.services.backup._get_s3_client", return_value=client):
            assert get_bucket_stats()["backups"] == []

        client.get_paginator.side_effect = RuntimeError("listing failed")
        with patch("app.services.backup._get_s3_client", return_value=client):
            assert get_bucket_stats()["error"] == "listing failed"

    @staticmethod
    @pytest.mark.parametrize(
        ("endpoint", "access_key", "secret_key", "bucket", "database_path", "client", "error"),
        [
            (
                None,
                "access",
                "secret",
                "bucket",
                "/missing",
                object(),
                "S3_ENDPOINT_URL not configured",
            ),
            (
                "https://s3.example",
                None,
                "secret",
                "bucket",
                "/missing",
                object(),
                "S3 credentials not configured",
            ),
            (
                "https://s3.example",
                "access",
                "secret",
                None,
                "/missing",
                object(),
                "S3_BUCKET_NAME not configured",
            ),
            (
                "https://s3.example",
                "access",
                "secret",
                "bucket",
                "/missing",
                object(),
                "Database file not found: /missing",
            ),
        ],
    )
    def test_detailed_backup_validates_configuration(
        endpoint, access_key, secret_key, bucket, database_path, client, error
    ):
        from app.services.backup import perform_backup_with_details

        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=client),
        ):
            mock_config.S3_ENDPOINT_URL = endpoint
            mock_config.S3_ACCESS_KEY_ID = access_key
            mock_config.S3_SECRET_ACCESS_KEY = secret_key
            mock_config.S3_BUCKET_NAME = bucket
            mock_config.DATABASE_PATH = database_path

            result = perform_backup_with_details()

        assert result["success"] is False
        assert result["error"] == error

    @staticmethod
    def test_detailed_backup_reports_unavailable_client(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        database_path.touch()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=None),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path

            result = perform_backup_with_details()

        assert result["error"] == "Failed to create S3 client"

    @staticmethod
    def test_detailed_backup_uploads_snapshot_and_reports_cleanup(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE test (value TEXT)")
        connection.commit()
        connection.close()
        client = MagicMock()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=client),
            patch("app.services.backup.generate_backup_filename", return_value="backup.db"),
            patch("app.services.backup.cleanup_old_backups", return_value=3),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path
            mock_config.BACKUP_RETENTION_DAYS = 7

            result = perform_backup_with_details()

        assert result["success"] is True
        assert result["filename"] == "backup.db"
        assert result["size_bytes"] > 0
        assert result["deleted_count"] == 3
        client.upload_file.assert_called_once()

    @staticmethod
    def test_detailed_backup_skips_retention_when_disabled(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        connection = sqlite3.connect(database_path)
        connection.execute("CREATE TABLE test (value TEXT)")
        connection.commit()
        connection.close()
        cleanup = MagicMock()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=MagicMock()),
            patch("app.services.backup.cleanup_old_backups", cleanup),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path
            mock_config.BACKUP_RETENTION_DAYS = 0

            result = perform_backup_with_details()

        assert result["success"] is True
        assert result["deleted_count"] == 0
        cleanup.assert_not_called()

    @staticmethod
    def test_detailed_backup_reports_snapshot_failure(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        database_path.touch()
        capture_exception = MagicMock()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=MagicMock()),
            patch(
                "app.services.backup._create_sqlite_snapshot",
                side_effect=RuntimeError("snapshot failed"),
            ),
            patch("app.services.backup.sentry_sdk.capture_exception", capture_exception),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path
            mock_config.BACKUP_RETENTION_DAYS = 0

            result = perform_backup_with_details()

        assert result["error"] == "snapshot failed"
        capture_exception.assert_called_once()

    @staticmethod
    def test_detailed_backup_handles_missing_tempfile_after_failure(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        database_path.touch()
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=MagicMock()),
            patch("app.services.backup.tempfile.mkstemp", side_effect=OSError("disk full")),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path
            mock_config.BACKUP_RETENTION_DAYS = 0

            result = perform_backup_with_details()

        assert result["error"] == "disk full"

    @staticmethod
    def test_detailed_backup_tolerates_tempfile_removal_failure(tmp_path):
        from app.services.backup import perform_backup_with_details

        database_path = tmp_path / "f1.db"
        database_path.touch()
        temp_path = tmp_path / "snapshot.db"
        temp_path.touch()
        temp_fd = os.open(temp_path, os.O_RDWR)
        with (
            patch("app.services.backup.config") as mock_config,
            patch("app.services.backup._get_s3_client", return_value=MagicMock()),
            patch("app.services.backup.tempfile.mkstemp", return_value=(temp_fd, str(temp_path))),
            patch("app.services.backup._create_sqlite_snapshot"),
            patch("app.services.backup.os.remove", side_effect=OSError("busy")),
        ):
            mock_config.S3_ENDPOINT_URL = "https://s3.example"
            mock_config.S3_ACCESS_KEY_ID = "access"
            mock_config.S3_SECRET_ACCESS_KEY = "secret"
            mock_config.S3_BUCKET_NAME = "bucket"
            mock_config.DATABASE_PATH = database_path
            mock_config.BACKUP_RETENTION_DAYS = 0

            result = perform_backup_with_details()

        assert result["success"] is True


def test_cleanup_uses_configured_client_and_reports_listing_failure():
    from app.services.backup import cleanup_old_backups

    client = MagicMock()
    client.get_paginator.side_effect = RuntimeError("listing failed")
    capture_exception = MagicMock()
    with (
        patch("app.services.backup.config") as mock_config,
        patch("app.services.backup._get_s3_client", return_value=client),
        patch("app.services.backup.sentry_sdk.capture_exception", capture_exception),
    ):
        mock_config.BACKUP_RETENTION_DAYS = 30
        mock_config.S3_BUCKET_NAME = "bucket"

        assert cleanup_old_backups() == 0

    capture_exception.assert_called_once()


class TestCronParsing:
    """Tests for cron expression parsing."""

    @staticmethod
    def test_parse_valid_cron():
        """Test parsing a valid cron expression."""
        from app.services.scheduler import _parse_cron_expression

        result = _parse_cron_expression("30 2 * * 1")

        assert result["minute"] == "30"
        assert result["hour"] == "2"
        assert result["day"] == "*"
        assert result["month"] == "*"
        assert result["day_of_week"] == "mon"

    @staticmethod
    def test_parse_standard_cron_sunday():
        """Map standard cron Sunday zero to APScheduler's explicit name."""
        from app.services.scheduler import _parse_cron_expression

        result = _parse_cron_expression("0 3 * * 0")

        assert result["day_of_week"] == "sun"

    @staticmethod
    def test_parse_invalid_cron_raises():
        """Invalid expressions must be rejected before APScheduler startup."""
        from app.services.scheduler import _parse_cron_expression

        with pytest.raises(ValueError, match="five fields"):
            _parse_cron_expression("invalid")

        with pytest.raises(ValueError):
            _parse_cron_expression("60 3 * * *")
