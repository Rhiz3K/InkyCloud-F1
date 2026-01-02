## Context

The F1 E-Ink Calendar service uses SQLite for storing operational metadata:
- Generated image paths and timestamps
- Cache metadata
- Request statistics (legacy hourly snapshots)
- API call logs with detailed metrics

The database uses WAL mode for concurrent access. The public instance runs on Hetzner via Coolify, and self-hosted instances may run in various container environments.

**Stakeholders**: Self-hosters, maintainers of the public instance

**Constraints**:
- Must work with Cloudflare R2 (primary target) and other S3-compatible providers
- Must not interfere with normal database operations
- Must be disabled by default (opt-in feature)
- Container environments may have limited disk space for local copies

## Goals / Non-Goals

**Goals**:
- Automatic scheduled backups to S3-compatible storage
- Configurable backup schedule via cron expression
- Automatic cleanup of old backups (retention policy)
- Clear console logging for backup operations
- Zero-downtime backup (copy file first to avoid locks)

**Non-Goals**:
- Point-in-time recovery (WAL shipping) - out of scope
- Backup verification/integrity checks - out of scope for v1
- Restore functionality - manual restore via S3 console
- Backup encryption beyond S3 server-side encryption
- Multi-region replication (handled by S3 provider)

## Decisions

### Decision 1: Use boto3 for S3 operations

**Choice**: Use `boto3` library directly

**Alternatives considered**:
1. `aioboto3` - Async wrapper, adds complexity for a simple use case
2. `s3fs` - High-level abstraction, overkill for single file uploads
3. Custom HTTP with `httpx` - Requires reimplementing S3 auth (SigV4)

**Rationale**: boto3 is the de facto standard for S3 in Python, well-documented, and supports all S3-compatible providers. The backup operation runs in background scheduler and doesn't need to be async.

### Decision 2: Copy database before upload

**Choice**: Copy the database file to a temporary location before uploading

**Rationale**: SQLite with WAL mode may have active writers. Copying the file ensures a consistent snapshot without holding database locks during potentially slow S3 uploads. Use `shutil.copy2()` to preserve metadata.

### Decision 3: Filename format with ISO timestamp

**Choice**: `f1_backup_{YYYY-MM-DD}_{HH-MM-SS}.db`

**Rationale**: ISO-based format sorts chronologically in S3 console/CLI. Underscore separators avoid URL encoding issues. UTC timestamps ensure consistency across timezones.

### Decision 4: Integrate with existing APScheduler

**Choice**: Add backup job to existing scheduler in `app/services/scheduler.py`

**Rationale**: Project already uses APScheduler for hourly image generation and API call flushing. Reusing the same scheduler pattern keeps the codebase consistent and avoids multiple scheduler instances.

### Decision 5: Environment variable naming

**Choice**: Prefix backup vars with `BACKUP_`, S3 vars with `S3_`

**Variables**:
```
BACKUP_ENABLED=false         # Master toggle
BACKUP_CRON="0 3 * * *"      # Cron expression (default: daily at 3 AM UTC)
BACKUP_RETENTION_DAYS=30     # Auto-delete after N days (0=disabled)

S3_ENDPOINT_URL=             # Required when enabled
S3_ACCESS_KEY_ID=            # Required when enabled
S3_SECRET_ACCESS_KEY=        # Required when enabled
S3_BUCKET_NAME=              # Required when enabled
S3_REGION=auto               # Default "auto" for R2
```

**Rationale**: Clear namespacing, self-documenting names, sensible defaults.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| S3 credentials in environment variables | Standard practice; recommend using secrets management in production |
| Backup fails silently | Log errors at ERROR level, consider Sentry capture for failures |
| Temporary file disk usage | Cleanup temp file in finally block; file is small (~100KB typical) |
| Retention cleanup deletes needed backups | Default 30 days is generous; users can set higher or disable (0) |
| boto3 adds ~17MB to image size | Acceptable trade-off for functionality; lazy import to avoid startup overhead |

## Migration Plan

1. **No migration needed** - Feature is opt-in via `BACKUP_ENABLED=true`
2. **Rollback**: Set `BACKUP_ENABLED=false` or remove S3 environment variables
3. **Existing databases**: No schema changes required

## Open Questions

1. Should backup status be exposed via `/health` endpoint? (Issue mentions as optional)
   - **Recommendation**: Defer to v2; keep v1 simple with just logging
