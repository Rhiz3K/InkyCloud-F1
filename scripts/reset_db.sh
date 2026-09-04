#!/bin/bash
# Reset database script for F1 E-Ink Calendar
# Usage: reset-db [option] [--force]
#   all      - Delete entire database (default; stop the application first)
#   stats    - Delete only api_calls and request_stats
#   cache    - Delete only cache_meta and generated_images
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
    echo "  stats    - Delete only api_calls and request_stats"
    echo "  cache    - Delete cache_meta, generated_images and BMP files"
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

# Function to get record counts
get_counts() {
    echo "api_calls: $(run_sql "SELECT COUNT(*) FROM api_calls;" 2>/dev/null || echo 0)"
    echo "request_stats: $(run_sql "SELECT COUNT(*) FROM request_stats;" 2>/dev/null || echo 0)"
    echo "cache_meta: $(run_sql "SELECT COUNT(*) FROM cache_meta;" 2>/dev/null || echo 0)"
    echo "generated_images: $(run_sql "SELECT COUNT(*) FROM generated_images;" 2>/dev/null || echo 0)"
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
        echo "This will delete all records from api_calls and request_stats tables."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm

        run_sql "DELETE FROM api_calls; DELETE FROM request_stats; VACUUM;"

        echo ""
        echo -e "${GREEN}Statistics reset complete.${NC}"
        echo -e "${YELLOW}Record counts after reset:${NC}"
        get_counts
        ;;

    cache)
        check_db
        echo -e "${YELLOW}=== Reset Cache ===${NC}"
        echo "This will delete cache_meta, generated_images records and BMP files."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm

        run_sql "DELETE FROM cache_meta; DELETE FROM generated_images; VACUUM;"
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
