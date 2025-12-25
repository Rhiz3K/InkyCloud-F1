"""Tests for S3 backup service."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.services.backup import (
    BACKUP_FILENAME_PREFIX,
    generate_backup_filename,
    is_backup_configured,
)


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

            # Re-import to use mocked datetime
            from app.services.backup import generate_backup_filename

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
        """Test successful backup execution."""
        # Create a temporary database file
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
            tmp_db.write(b"test database content")
            tmp_db_path = tmp_db.name

        try:
            mock_s3_client = MagicMock()

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
        finally:
            os.unlink(tmp_db_path)


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
                    {"Key": "f1_backup_2025-01-01_03-00-00.db", "LastModified": old_date},
                    {"Key": "f1_backup_2025-03-10_03-00-00.db", "LastModified": recent_date},
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


class TestCronParsing:
    """Tests for cron expression parsing."""

    def test_parse_valid_cron(self):
        """Test parsing a valid cron expression."""
        from app.services.scheduler import _parse_cron_expression

        result = _parse_cron_expression("30 2 * * 1")

        assert result["minute"] == "30"
        assert result["hour"] == "2"
        assert result["day"] == "*"
        assert result["month"] == "*"
        assert result["day_of_week"] == "1"

    def test_parse_default_cron(self):
        """Test that default cron is used for invalid expressions."""
        from app.services.scheduler import _parse_cron_expression

        result = _parse_cron_expression("invalid")

        assert result["minute"] == "0"
        assert result["hour"] == "3"
        assert result["day"] == "*"
        assert result["month"] == "*"
        assert result["day_of_week"] == "*"
