# backup Specification

## Purpose
TBD - created by archiving change add-s3-database-backup. Update Purpose after archive.
## Requirements
### Requirement: Scheduled S3 Backup

The system SHALL support automatic scheduled backups of the SQLite database to S3-compatible storage when backup is enabled.

#### Scenario: Backup executes on schedule

- **WHEN** `BACKUP_ENABLED=true` and the cron schedule triggers
- **THEN** the system SHALL copy the database file and upload it to the configured S3 bucket
- **AND** log the backup result (success or failure) to console

#### Scenario: Backup disabled by default

- **WHEN** `BACKUP_ENABLED` is not set or set to `false`
- **THEN** the backup scheduler job SHALL NOT be registered
- **AND** no S3 operations SHALL occur

#### Scenario: Backup creates consistent snapshot

- **WHEN** a backup is triggered
- **THEN** the system SHALL copy the database file to a temporary location before upload
- **AND** delete the temporary file after upload completes (success or failure)

### Requirement: S3 Provider Compatibility

The backup service SHALL work with any S3-compatible storage provider (Cloudflare R2, AWS S3, MinIO, Backblaze B2).

#### Scenario: Cloudflare R2 configuration

- **WHEN** S3 endpoint is configured as Cloudflare R2 (`*.r2.cloudflarestorage.com`)
- **THEN** the backup SHALL use the configured endpoint, credentials, and bucket
- **AND** use region "auto" as default

#### Scenario: Custom S3 endpoint

- **WHEN** `S3_ENDPOINT_URL` is set to a custom endpoint (e.g., MinIO)
- **THEN** the backup SHALL use that endpoint for all S3 operations

### Requirement: Backup Retention Management

The system SHALL support automatic deletion of old backups based on a configurable retention period.

#### Scenario: Retention cleanup enabled

- **WHEN** `BACKUP_RETENTION_DAYS` is set to a positive integer
- **THEN** after each successful backup, the system SHALL delete backups older than the retention period

#### Scenario: Retention cleanup disabled

- **WHEN** `BACKUP_RETENTION_DAYS` is `0` or not set
- **THEN** the system SHALL NOT delete any existing backups

### Requirement: Backup Naming Convention

Backup files SHALL use a consistent naming format that enables chronological sorting.

#### Scenario: Backup filename format

- **WHEN** a backup is created
- **THEN** the filename SHALL follow the pattern `f1_backup_YYYY-MM-DD_HH-MM-SS.db`
- **AND** timestamps SHALL be in UTC

### Requirement: Backup Logging

All backup operations SHALL be logged to console (stdout/stderr) for container visibility.

#### Scenario: Successful backup logging

- **WHEN** a backup completes successfully
- **THEN** the system SHALL log at INFO level with backup filename and size

#### Scenario: Failed backup logging

- **WHEN** a backup fails (copy error, upload error, S3 error)
- **THEN** the system SHALL log at ERROR level with the exception details
- **AND** capture the exception to Sentry if configured

