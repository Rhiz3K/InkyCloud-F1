## 1. Dependencies

- [x] 1.1 Add `boto3` to `pyproject.toml` dependencies

## 2. Configuration

- [x] 2.1 Add backup configuration fields to `app/config.py`:
  - `BACKUP_ENABLED: bool` (default: `false`)
  - `BACKUP_CRON: str` (default: `"0 3 * * *"`)
  - `BACKUP_RETENTION_DAYS: int` (default: `30`)
- [x] 2.2 Add S3 configuration fields to `app/config.py`:
  - `S3_ENDPOINT_URL: Optional[str]`
  - `S3_ACCESS_KEY_ID: Optional[str]`
  - `S3_SECRET_ACCESS_KEY: Optional[str]`
  - `S3_BUCKET_NAME: Optional[str]`
  - `S3_REGION: str` (default: `"auto"`)
- [x] 2.3 Add field validators for backup configuration (cron expression validation, non-negative retention days)

## 3. Backup Service

- [x] 3.1 Create `app/services/backup.py` with:
  - `BackupService` class or module-level functions
  - `perform_backup()` function that copies DB and uploads to S3
  - `cleanup_old_backups()` function for retention management
  - Proper error handling with logging and Sentry capture
- [x] 3.2 Implement database copy before upload (use `shutil.copy2()` to temp file)
- [x] 3.3 Implement S3 upload with boto3 client
- [x] 3.4 Implement retention cleanup (list objects, filter by date, delete old)
- [x] 3.5 Add console logging for all operations (INFO for success, ERROR for failures)

## 4. Scheduler Integration

- [x] 4.1 Update `app/services/scheduler.py` to conditionally register backup job
- [x] 4.2 Parse `BACKUP_CRON` expression for APScheduler CronTrigger
- [x] 4.3 Add backup job only when `BACKUP_ENABLED=true` and S3 credentials are configured

## 5. Documentation

- [x] 5.1 Update `.env.example` with all new backup/S3 variables
- [x] 5.2 Update `SELF-HOSTING.md` with backup configuration guide
- [x] 5.3 Add Cloudflare R2 setup example to documentation

## 6. Testing

- [x] 6.1 Add unit tests for backup service in `tests/test_backup.py`:
  - Test backup filename generation
  - Test retention day calculation
  - Test config validation
- [x] 6.2 Add integration test with mocked boto3 client (optional, requires moto or similar)
- [x] 6.3 Verify existing tests pass with new configuration fields

## 7. Validation

- [x] 7.1 Run `ruff check .` and `ruff format .`
- [x] 7.2 Run `pytest` to ensure all tests pass
- [ ] 7.3 Manual test with Cloudflare R2 or MinIO (document in PR)
