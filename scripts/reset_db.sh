#!/bin/bash
# Reset database script for F1 E-Ink Calendar
# Usage: reset-db [option] [--force]
#   all      - Delete entire database (default; stop the application first)
#   stats    - Delete usage statistics (api_calls, request_stats, api_call_totals, perf_metrics,
#              perf_buckets, stats_batches)
#   cache    - Delete only cache_meta, generated_images and weekend_results
#   info     - Show current record counts (no changes)
#   --force  - Skip the running-instance guard for "all"
set -euo pipefail

DB_PATH="${DATABASE_PATH:-/app/data/f1.db}"
IMAGES_PATH="${IMAGES_PATH:-/app/data/images}"
# Match the application's busy timeout so maintenance does not fail on a brief write lock.
SQLITE_TIMEOUT_MS=30000

if [ -z "$DB_PATH" ] || [ -z "$IMAGES_PATH" ] || [ "$DB_PATH" = "/" ] || [ "$IMAGES_PATH" = "/" ]; then
    echo "Refusing to run with an empty or root database/images path." >&2
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: reset-db [all|stats|cache|info] [--force]"
    echo ""
    echo "Options:"
    echo "  all      - Delete entire database file (default; stop the application first)"
    echo "  stats    - Delete usage statistics (api_calls, request_stats, api_call_totals, perf_metrics,"
    echo "             perf_buckets, stats_batches)"
    echo "  cache    - Delete cache_meta, generated_images, weekend_results and BMP files"
    echo "  info     - Show current record counts (no changes)"
    echo "  --force  - Skip the running-instance guard for \"all\""
}

SCOPE=""
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force)
            FORCE=1
            ;;
        all|stats|cache|info)
            if [ -n "$SCOPE" ]; then
                usage
                exit 1
            fi
            SCOPE="$arg"
            ;;
        *)
            usage
            exit 1
            ;;
    esac
done
SCOPE="${SCOPE:-all}"

# Run one SQL statement with the application's busy timeout; failures abort the script.
run_sql() {
    sqlite3 -cmd ".timeout ${SQLITE_TIMEOUT_MS}" "$DB_PATH" "$1"
}

# Check if database exists
check_db() {
    if [ ! -f "$DB_PATH" ]; then
        echo -e "${YELLOW}Database not found at $DB_PATH${NC}"
        exit 0
    fi
}

# Deleting the file under a running instance leaves the app writing to an unlinked inode and,
# because the schema is only created once per process, every later query fails until restart.
refuse_if_in_use() {
    if [ "$FORCE" = "1" ]; then
        return 0
    fi
    if [ -e "$DB_PATH-wal" ] || [ -e "$DB_PATH-shm" ]; then
        echo -e "${RED}Database appears to be in use (WAL/SHM sidecar present).${NC}" >&2
        echo "Stop the application first, or re-run with --force if it is not running." >&2
        exit 1
    fi
}

# Statistics tables: the legacy per-request tables plus the aggregate totals, performance
# buckets and batch ledger that the default AGGREGATE_STATS_ONLY=true profile actually writes
# (the same set the application's own usage-data cleanup deletes).
STATS_TABLES=(api_calls request_stats api_call_totals perf_metrics perf_buckets stats_batches)
# weekend_results holds re-fetchable podiums for weekend_mode=auto (a restart clears memory).
CACHE_TABLES=(cache_meta generated_images weekend_results)

# Succeeds only when a successful lookup finds the table. A failed lookup (e.g. SQLITE_BUSY after
# the timeout) aborts instead of being mistaken for an absent table, which would skip it silently.
# The explicit exit is required: callers use this in an `if`, where `set -e` does not apply.
table_exists() {
    local count
    if ! count="$(run_sql "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$1';")"; then
        echo -e "${RED}Could not check table $1; nothing was reset.${NC}" >&2
        exit 1
    fi
    [ "$count" = "1" ]
}

# Function to get record counts
get_counts() {
    local table
    for table in "${STATS_TABLES[@]}" "${CACHE_TABLES[@]}"; do
        echo "$table: $(run_sql "SELECT COUNT(*) FROM $table;" 2>/dev/null || echo 0)"
    done
}

# Confirmation prompt
confirm() {
    read -p "Are you sure you want to continue? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
}

delete_bmp_files() {
    find "$IMAGES_PATH" -maxdepth 1 -type f -name '*.bmp' -delete 2>/dev/null || true
}

case "$SCOPE" in
    info)
        check_db
        echo -e "${CYAN}=== Database Info ===${NC}"
        echo "Database path: $DB_PATH"
        echo "Database size: $(du -h "$DB_PATH" 2>/dev/null | cut -f1 || echo "unknown")"
        echo ""
        echo -e "${CYAN}Record counts:${NC}"
        get_counts
        ;;

    stats)
        check_db
        echo -e "${YELLOW}=== Reset Statistics ===${NC}"
        echo "This will delete all records from: ${STATS_TABLES[*]}."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm

        # Older databases may predate some tables; delete only from the ones that exist.
        stats_sql=""
        for table in "${STATS_TABLES[@]}"; do
            if table_exists "$table"; then
                stats_sql+="DELETE FROM $table; "
            fi
        done
        run_sql "${stats_sql}VACUUM;"

        echo ""
        echo -e "${GREEN}Statistics reset complete.${NC}"
        echo -e "${YELLOW}Record counts after reset:${NC}"
        get_counts
        ;;

    cache)
        check_db
        echo -e "${YELLOW}=== Reset Cache ===${NC}"
        echo "This will delete ${CACHE_TABLES[*]} records and BMP files."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm

        cache_sql=""
        for table in "${CACHE_TABLES[@]}"; do
            if table_exists "$table"; then
                cache_sql+="DELETE FROM $table; "
            fi
        done
        run_sql "${cache_sql}VACUUM;"
        delete_bmp_files

        echo ""
        echo -e "${GREEN}Cache reset complete.${NC}"
        echo -e "${YELLOW}Record counts after reset:${NC}"
        get_counts
        ;;

    all)
        check_db
        refuse_if_in_use
        echo -e "${RED}=== Delete Entire Database ===${NC}"
        echo "This will DELETE the entire database file and all BMP images."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm

        rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
        delete_bmp_files

        echo ""
        echo -e "${GREEN}Database deleted. Start the application to recreate the schema.${NC}"
        ;;
esac
