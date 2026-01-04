#!/usr/bin/env python3
"""
Backup CLI tool for F1 E-Ink Calendar.

Usage:
    backup info    - Show backup configuration
    backup test    - Test S3 connection
    backup now     - Perform backup immediately
"""

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

    if not info["enabled"]:
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


def cmd_now():
    """Perform backup immediately."""
    from app.services.backup import perform_backup_with_details

    print("Starting manual backup...")
    print()

    result = perform_backup_with_details()

    if result["success"]:
        print(f"  [OK] Database copied: {format_size(result['size_bytes'])}")
        print(f"  [OK] Uploaded: {result['filename']}")
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


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("Commands:")
        print("  info    Show backup configuration")
        print("  test    Test S3 connection and permissions")
        print("  now     Perform backup immediately")
        return 1

    command = sys.argv[1].lower()

    if command == "info":
        cmd_info()
        return 0
    elif command == "test":
        return cmd_test()
    elif command == "now":
        return cmd_now()
    else:
        print(f"Unknown command: {command}")
        print("Available commands: info, test, now")
        return 1


if __name__ == "__main__":
    sys.exit(main())
