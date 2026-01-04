# Scripts - Data Lifecycle Utilities

**21 preprocessing scripts** for F1 data ingestion, asset processing, and validation.

## Script Categories

| Category | Scripts | Purpose |
|----------|---------|---------|
| **Data Ingestion** | `update_seasons.py`, `update_historical.py` | Jolpica API → JSON |
| **Asset Processing** | `preprocess_tracks.py`, `preprocess_flags.py` | PNG → 1-bit BMP |
| **Scraping** | `scrape_circuits.py`, `scrape_wiki_teams.py` | Wikipedia/external data |
| **Downloads** | `download_flags.py`, `download_driver_photos.py`, `download_team_logos.py` | Fetch remote assets |
| **Validation** | `verify_layout.py`, `measure_alignment.py`, `measure_text.py` | Pixel-perfect checks |
| **Testing** | `benchmark_renderer.py`, `generate_test_image.py`, `generate_stress_test_image.py` | Performance/visual tests |
| **Utilities** | `fetch_real_data.py`, `find_longest_values.py`, `debug_crop.py` | Debug helpers |
| **Ops** | `reset_db.sh`, `backup_cli.py` | Database management |

## Critical Scripts (GitHub Actions)

These run automatically via `.github/workflows/update-f1-data.yml`:

```bash
# Weekly (Monday 06:00 UTC) - updates race results
python scripts/update_historical.py

# January yearly - updates season calendar  
python scripts/update_seasons.py --years 2026,2027
```

**Output**: Commits updated JSON to `app/assets/seasons/` and `app/assets/circuits_data.json`.

## Asset Processing Pipeline

```
Source PNG (any size)
    ↓ preprocess_tracks.py / preprocess_flags.py
1-bit BMP (max 490x280 tracks, 40x27 flags)
    ↓ committed to repo
app/assets/*_processed/
```

**Key constants**:
- `MAX_WIDTH = 490`, `MAX_HEIGHT = 280` (tracks)
- `THRESHOLD = 200` (grayscale → 1-bit cutoff)

## Running Scripts

```bash
# From project root
python scripts/update_seasons.py --years 2025,2026
python scripts/preprocess_tracks.py
python scripts/benchmark_renderer.py --iterations 100

# Reset local database
./scripts/reset_db.sh
```

## Adding New Scripts

1. Use `#!/usr/bin/env python3` shebang
2. Add docstring with `Usage:` section
3. Use `httpx.AsyncClient` for HTTP (not `requests`)
4. Output to `app/assets/` directories
5. Handle missing data gracefully (Jolpica API gaps)

## Common Gotchas

- **Path setup**: Scripts add parent to `sys.path` for imports
- **API rate limits**: Jolpica has no auth but be respectful
- **1-bit threshold**: `THRESHOLD = 200` works for most F1 circuit maps
- **Async main**: Use `asyncio.run(main())` pattern
