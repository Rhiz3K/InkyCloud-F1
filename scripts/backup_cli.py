#!/usr/bin/env python3
"""
Backup CLI tool for F1 E-Ink Calendar.

Usage:
    backup info                      - Show backup configuration
    backup test                      - Test S3 connection
    backup now [--local]             - Perform backup immediately (S3, or the local directory)
    backup list [--verify] [--json]  - List local and S3 backups
    backup restore <name|latest> [--source auto|local|s3] [--yes] [--force]
                                     - Restore a backup; stop the service first
"""

import argparse
import json
import sys


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def cmd_info():
    """Show backup configuration."""
    from app.services.backup import get_backup_config_info

    info = get_backup_config_info()

    print("S3 Backup Configuration")
    print("=" * 40)
    print(f"  Enabled:      {info['enabled']}")
    print(f"  Endpoint:     {info['endpoint']}")
    print(f"  Bucket:       {info['bucket']}")
    print(f"  Region:       {info['region']}")
    print(f"  Schedule:     {info['schedule']}")
    print(f"  Retention:    {info['retention_days']} days")
    print(f"  Credentials:  {'configured' if info['credentials_configured'] else 'NOT configured'}")
    print()

    if info["minimal_data_mode"]:
        print("Note: Backup is disabled because MINIMAL_DATA_MODE=true.")
    elif not info["enabled"]:
        print("Note: Backup is disabled. Set BACKUP_ENABLED=true to enable.")
    elif not info["credentials_configured"]:
        print("Warning: S3 credentials are not configured.")
        print("Set S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY.")


def cmd_test():
    """Test S3 connection."""
    from app.services.backup import get_bucket_stats, test_s3_connection

    print("Testing S3 connection...")
    print()

    result = test_s3_connection()

    if result["credentials_valid"]:
        print("  [OK] Credentials valid")
    else:
        print("  [FAIL] Credentials invalid")

    if result["bucket_accessible"]:
        print("  [OK] Bucket accessible")
    elif result["credentials_valid"]:
        print("  [FAIL] Bucket not accessible")

    if result["write_permission"]:
        print("  [OK] Write permission confirmed")
    elif result["bucket_accessible"]:
        print("  [FAIL] Write permission denied")

    print()

    if result["success"]:
        print(f"Connection latency: {result['latency_ms']} ms")
        print()

        # Get bucket statistics
        stats = get_bucket_stats()
        if stats["error"]:
            print(f"Warning: Could not get bucket stats: {stats['error']}")
        else:
            print("Bucket statistics:")
            print(f"  Existing backups: {stats['backup_count']}")
            print(f"  Total size:       {format_size(stats['total_size_bytes'])}")
            if stats["oldest_backup"]:
                print(f"  Oldest backup:    {stats['oldest_backup']}")
            if stats["newest_backup"]:
                print(f"  Newest backup:    {stats['newest_backup']}")

        print()
        print("Connection test PASSED")
        return 0
    else:
        print(f"Error: {result['error']}")
        print()
        print("Connection test FAILED")
        return 1


def cmd_now(local: bool = False):
    """Perform backup immediately."""
    from app.config import config
    from app.services.backup import perform_backup_with_details, perform_local_backup

    if local:
        result = perform_local_backup()
        if not result["success"]:
            print(f"  [FAIL] {result['error']}")
            return 1
        print(f"  [OK] Local backup: {result['filename']} ({format_size(result['size_bytes'])})")
        print(f"  [OK] Data files: {', '.join(result['data_files']) or 'none'}")
        if result["deleted_count"]:
            print(f"  [OK] Cleanup: {result['deleted_count']} old local backup(s) deleted")
        return 0

    if not config.BACKUP_ENABLED:
        print("Backup is disabled. Set BACKUP_ENABLED=true before running a manual backup.")
        return 1

    print("Starting manual backup...")
    print()

    result = perform_backup_with_details()

    if result["success"]:
        print(f"  [OK] Database copied: {format_size(result['size_bytes'])}")
        print(f"  [OK] Uploaded: {result['filename']}")
        print(f"  [OK] Data files: {', '.join(result['data_files']) or 'none'}")
        if result["deleted_count"] > 0:
            print(f"  [OK] Cleanup: {result['deleted_count']} old backup(s) deleted")
        else:
            print("  [OK] Cleanup: no old backups to delete")
        print()
        print("Backup completed successfully.")
        return 0
    else:
        print(f"  [FAIL] {result['error']}")
        print()
        print("Backup FAILED.")
        return 1


def cmd_list(verify: bool = False, as_json: bool = False):
    """List local and S3 backups with their integrity state."""
    from app.services.backup import list_backups

    result = list_backups(verify=verify)
    if as_json:
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] else 0
    if not result["backups"]:
        print("No backups found.")
    for backup in result["backups"]:
        kind = " (safety snapshot)" if backup["safety_snapshot"] else ""
        print(
            f"  {backup['name']}  {backup['source']:5}  {backup['created_utc']}  "
            f"{format_size(backup['size_bytes']):>9}  integrity={backup['integrity']}{kind}"
        )
    if not result["s3_available"]:
        print("S3 backups not listed: S3 is not configured or MINIMAL_DATA_MODE=true.")
    for error in result["errors"]:
        print(f"Warning: {error}")
    return 1 if result["errors"] else 0


def cmd_restore(name: str, source: str = "auto", assume_yes: bool = False, force: bool = False):
    """Restore a backup after confirmation; the service must be stopped."""
    from app.services.backup import restore_backup

    if not assume_yes:
        reply = input(f"Restore {name} over the current database? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    result = restore_backup(name, source=source, force=force)
    if not result["success"]:
        print(f"  [FAIL] {result['error']}")
        print()
        print("Restore FAILED. The current database was not changed.")
        return 1
    print(f"  [OK] Integrity check passed: {result['name']} ({result['source']})")
    if result["safety_snapshot"]:
        print(f"  [OK] Previous database saved as {result['safety_snapshot']}")
    print(f"  [OK] Data files restored: {', '.join(result['data_files_restored']) or 'none'}")
    print(
        f"  [OK] Schema {result['schema_version_backup']} migrated to "
        f"{result['schema_version_after']}"
    )
    print()
    print("Restore completed. Start the service again.")
    return 0


def build_parser():
    """Build the ``backup`` command parser."""
    parser = argparse.ArgumentParser(prog="backup", description="F1 E-Ink Calendar backups")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", help="show backup configuration")
    commands.add_parser("test", help="test S3 connection and permissions")
    now = commands.add_parser("now", help="perform a backup immediately")
    now.add_argument("--local", action="store_true", help="write into BACKUP_LOCAL_DIR")
    listing = commands.add_parser("list", help="list local and S3 backups")
    listing.add_argument("--verify", action="store_true", help="run PRAGMA integrity_check")
    listing.add_argument("--json", action="store_true", help="print JSON")
    restore = commands.add_parser("restore", help="restore a backup; stop the service first")
    restore.add_argument("name", help="backup name from 'backup list', or 'latest'")
    restore.add_argument("--source", choices=("auto", "local", "s3"), default="auto")
    restore.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    restore.add_argument(
        "--force",
        action="store_true",
        help="ignore stale -wal/-shm files or a backup from a newer schema",
    )
    return parser


def main(argv=None):
    """Main entry point."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 1
    args = build_parser().parse_args([argv[0].lower(), *argv[1:]])
    if args.command == "info":
        cmd_info()
        return 0
    if args.command == "test":
        return cmd_test()
    if args.command == "now":
        return cmd_now(local=args.local)
    if args.command == "list":
        return cmd_list(verify=args.verify, as_json=args.json)
    return cmd_restore(args.name, source=args.source, assume_yes=args.yes, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
