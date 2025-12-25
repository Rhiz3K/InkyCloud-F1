#!/bin/bash
# Reset database script for F1 E-Ink Calendar
# Usage: reset-db [option]
#   all      - Delete entire database (default)
#   stats    - Delete only api_calls and request_stats
#   cache    - Delete only cache_meta and generated_images
#   info     - Show current record counts (no changes)

DB_PATH="${DATABASE_PATH:-/app/data/f1.db}"
IMAGES_PATH="${IMAGES_PATH:-/app/data/images}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Check if database exists
check_db() {
    if [ ! -f "$DB_PATH" ]; then
        echo -e "${YELLOW}Database not found at $DB_PATH${NC}"
        exit 0
    fi
}

# Function to get record counts
get_counts() {
    echo "api_calls: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM api_calls;" 2>/dev/null || echo 0)"
    echo "request_stats: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM request_stats;" 2>/dev/null || echo 0)"
    echo "cache_meta: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM cache_meta;" 2>/dev/null || echo 0)"
    echo "generated_images: $(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM generated_images;" 2>/dev/null || echo 0)"
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

case "${1:-all}" in
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
        
        sqlite3 "$DB_PATH" "DELETE FROM api_calls; DELETE FROM request_stats; VACUUM;"
        
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
        
        sqlite3 "$DB_PATH" "DELETE FROM cache_meta; DELETE FROM generated_images; VACUUM;"
        rm -rf "${IMAGES_PATH}"/*.bmp 2>/dev/null
        
        echo ""
        echo -e "${GREEN}Cache reset complete.${NC}"
        echo -e "${YELLOW}Record counts after reset:${NC}"
        get_counts
        ;;
        
    all)
        check_db
        echo -e "${RED}=== Delete Entire Database ===${NC}"
        echo "This will DELETE the entire database file and all BMP images."
        echo ""
        echo -e "${YELLOW}Current record counts:${NC}"
        get_counts
        echo ""
        confirm
        
        rm -f "$DB_PATH"
        rm -rf "${IMAGES_PATH}"/*.bmp 2>/dev/null
        
        echo ""
        echo -e "${GREEN}Database deleted. Will be recreated on next request.${NC}"
        ;;
        
    *)
        echo "Usage: reset-db [all|stats|cache|info]"
        echo ""
        echo "Options:"
        echo "  all    - Delete entire database file (default)"
        echo "  stats  - Delete only api_calls and request_stats"
        echo "  cache  - Delete cache_meta, generated_images and BMP files"
        echo "  info   - Show current record counts (no changes)"
        exit 1
        ;;
esac
