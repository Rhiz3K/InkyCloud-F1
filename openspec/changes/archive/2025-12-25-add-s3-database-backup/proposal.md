# Change: Add S3-compatible database backup

## Why

The SQLite database contains valuable operational data (generated image metadata, cache timestamps, request statistics, and API call logs). Currently there is no automated backup mechanism, creating risk of data loss in case of container restarts, host failures, or accidental deletion. Self-hosted instances and the public instance at f1.inkycloud.click need a reliable, automated backup solution.

## What Changes

- **New backup service** (`app/services/backup.py`) that copies the SQLite database to S3-compatible storage
- **Scheduled backups** integrated with existing APScheduler infrastructure
- **S3 configuration** via environment variables (endpoint, credentials, bucket, region)
- **Backup lifecycle management** with configurable retention period
- **Console logging** for backup operations (success/failure with timestamps)
- **New dependency**: `boto3` for S3 operations

## Impact

- Affected specs: 
  - `configuration` (new environment variables)
  - `backup` (new capability)
- Affected code:
  - `app/config.py` - Add backup and S3 configuration fields
  - `app/services/backup.py` - New backup service (create)
  - `app/services/scheduler.py` - Add backup job
  - `pyproject.toml` - Add boto3 dependency
  - `.env.example` - Document new variables
  - `SELF-HOSTING.md` - Document backup configuration
- No breaking changes - backup is disabled by default
- External dependency: boto3 library (~17MB installed)

## References

- GitHub Issue: #48
