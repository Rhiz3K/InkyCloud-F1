# Copilot Instructions for F1 E-Ink Calendar

## Architecture Overview

This is a FastAPI service that generates **800x480 BMP images** (1-bit, B/W/R, B/W/R/Y, and Spectra 6 palettes) for E-Ink displays such as LaskaKit ESP32 devices. The service serves F1 race data from bundled static JSON refreshed from the Jolpica API, converts times from UTC to the requested IANA timezone (default `Europe/Prague`), and renders calendar and teams images server-side with Pillow.

**Key components:**
- `app/main.py` - FastAPI app, middleware, and lifespan; HTTP handlers live in `app/routes/`
- `app/config.py` - Configuration management from environment variables
- `app/models.py` - Pydantic data models
- `app/state.py` - Application state management
- `app/services/renderer.py` - Pixel-perfect 1-bit BMP rendering engine
- `app/services/spectra6_renderer.py` - Spectra6 multi-color E-Ink renderer
- `app/services/f1_service.py` - Jolpica API client with timezone conversion
- `app/services/teams_service.py` - Teams & drivers data management
- `app/services/standings_service.py` - Championship standings data
- `app/services/weather_service.py` - Weather forecast integration
- `app/services/database.py` - SQLite operations for data persistence
- `app/services/scheduler.py` - APScheduler background jobs
- `app/services/backup.py` - S3 database backup automation
- `app/services/i18n.py` - Translation loader with caching
- `app/services/analytics.py` - Fire-and-forget Umami tracking
- `app/services/version_service.py` - Version management
- `translations/*.json` - i18n strings for the 13 locales listed in `LANGUAGE_CODES` (`app/config.py`)

## Critical Patterns

### 1-Bit Rendering (Must Follow Exactly)

The default renderer produces 1-bit mode images (`Image.new("1", ...)`); never use "L" or "RGB" modes for its output. Color renderers (`bwr`, `bwry`, `spectra6`) map RGB canvases onto fixed palettes through `app/utils/bmp.py` and must keep the same layout.

```python
# ✓ Correct
image = Image.new("1", (800, 480), 1)  # 1 = white background

# ✗ Wrong
image = Image.new("RGB", (800, 480), (255, 255, 255))
```

When drawing, use `fill=0` for black and `fill=1` for white. The renderer uses pixel-precise layout constants in `self.layout` dict - **never hardcode coordinates**.

### Timezone Handling

ALL race times are stored in UTC in the Jolpica data and MUST be converted to the caller's validated IANA timezone (`tz` query parameter, default `config.DEFAULT_TIMEZONE`). See `F1Service._convert_race_times()` and `app/utils/race_times.py` for the canonical pattern:

```python
dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
dt_local = dt_utc.astimezone(self.target_tz)
```

Display format: `dt_local.strftime("%a %H:%M")` (e.g., "Sun 17:00"). Validate timezones with `app.utils.timezones.is_valid_timezone`.

### Translation Keys

Always use `translator.get(key, fallback)` pattern. Session names use prefix `session_` (e.g., `session_race`, `session_qualifying`). See [translations/en.json](../translations/en.json) for all keys.

### Error Handling

BMP endpoints (`/calendar.bmp`, `/teams.bmp`) never fail with a JSON error once input is validated: rendering problems return an error BMP via `renderer.render_error()` with `Cache-Control: no-store`, logged and sent to Sentry/GlitchTip. Invalid input (unknown timezone, `race_key` without `year`) raises `HTTPException` before rendering, and JSON API routes raise `HTTPException` normally.

### Async Patterns

- HTTP calls use `httpx.AsyncClient` via `get_shared_http_client` (never `requests`)
- Background work uses `create_supervised_task(...)` from `app/utils/async_tasks.py` so failures are logged, never bare `asyncio.create_task`
- CPU-bound rendering runs through `run_render(...)`; construct the renderer inside the callable because font caches are per thread
- FastAPI endpoints are `async def` with proper context managers

## Development Commands

```bash
# Setup environment
uv sync --locked --group dev

# Local dev (auto-reload)
uv run uvicorn app.main:app --reload

# Run with debug logging
DEBUG=true uv run python -m app.main

# Test suite (must pass before PR)
uv run pytest

# Global 95% statement + branch gate, plus 100% changed-line ratchet
uv run pytest tests/ -m "not benchmark" --cov=app --cov-branch --cov-report=term-missing --cov-report=xml
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=100

# Lint & format (CI enforced)
uv run ruff check .
uv run ruff format .
```

## Testing Requirements

1. All renderer changes MUST include tests in `tests/test_renderer.py`
2. Tests verify exact BMP properties: 800x480, mode="1", format="BMP"
3. Use `mock_race_data` fixture pattern for consistent test data
4. Test both Czech (`cs`) and English (`en`) translations

Example test structure:
```python
def test_new_feature(mock_race_data):
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)
    
    img = Image.open(BytesIO(bmp_data))
    assert img.mode == "1"
    assert img.size == (800, 480)
```

## Configuration Philosophy

All config comes from environment variables via `app/config.py`. Never hardcode:
- API URLs (use `config.JOLPICA_API_URL`)
- Display dimensions (use `config.DISPLAY_WIDTH/HEIGHT`)
- Monitoring DSNs (use `config.SENTRY_DSN`)

Feature flags like `UMAMI_ENABLED` control optional services - code must handle disabled features gracefully.

## Adding Translations

1. Add key to `translations/en.json` (source of truth)
2. Add the same key to every other `translations/*.json` file
3. Use in code: `translator.get("your_key", "Fallback Text")`

**Adding a New Language:**
1. Create `translations/<code>.json` with all keys from `en.json`
2. Add the code to `LANGUAGE_CODES` in `app/config.py` and to `LANGUAGE_LABELS` / `OG_LOCALES` in `app/web/templates.py`
3. Test the rendering:
   ```bash
   curl "http://localhost:8000/calendar.bmp?lang=<code>" > test.bmp
   ```
4. Add test coverage in `tests/test_renderer.py`
5. Update README.md and CONTRIBUTING.md with the new language

## Session Types

The service supports all F1 weekend session types. Translation keys MUST use `session_` prefix:
- `session_fp1`, `session_fp2`, `session_fp3` - Free Practice sessions
- `session_qualifying` - Traditional qualifying
- `session_sprint` - Sprint race
- `session_race` - Main Grand Prix

Future-proof for Sprint Qualifying when F1 adds it - follow the same `session_*` pattern.

## Docker & Deployment

Production runs in Docker with Python 3.14-slim. The Dockerfile installs system deps for Pillow (`libjpeg-dev`, `zlib1g-dev`). The service is stateful and runs as one replica because SQLite, the scheduler, and in-memory coordination are single-instance.

`GET /health` is liveness. Container orchestration must use `GET /health/ready`, which checks SQLite, the `/app/data` mount/write probe, and freshness of the latest core calendar generation. Optional weather or secondary-variant failures return HTTP 200 with status `degraded`; cold start, stale core output, or dependency failures return 503.

**Caching Best Practices:**
- `/calendar.bmp` sets `Cache-Control: public, max-age=3600` (1 hour)
- BMP endpoints return strong ETags and honor `If-None-Match` with an empty 304 response
- Race data rarely changes, so 1-hour HTTP cache is appropriate
- For Redis/external caching: Cache F1Service responses with race ID as key
- Translation cache lives in-memory (`_translations_cache` in `i18n.py`)
- Never cache error responses - always render fresh

## Integration with ESP32

The `/calendar.bmp` endpoint returns standard BMP files fetchable by ESP32 HTTPClient. Query param `?lang=cs` switches language. See [README.md](../README.md) for ESP32 integration examples.

## Track Map Rendering

Track maps are real circuit outlines. Source artwork lives in `artwork/tracks/`, and the display-specific BMPs under `app/assets/tracks_*` are produced by `python -m scripts.manage` (see `BMP_PROCESSING.md`). At render time `app/services/renderer_assets.py` picks the per-display asset, crops, and fits it into the left column; keep preprocessed heights within the runtime box so no resampling is needed.

## Common Gotchas

- Font loading falls back to default if DejaVuSans missing - test in Docker, not just locally
- Session schedule order matters: always `sort(key=lambda x: x["datetime"])`
- Missing circuit data is valid (Jolpica sometimes omits fields) - use `.get()` with defaults
- BMP format is little-endian, 1-bit depth - don't manually construct headers
- Layout constants in `renderer.py` are pixel-perfect for 800x480 - changing one may require adjusting neighbors

## API Endpoints

The service provides multiple endpoints for different use cases:

**Image Endpoints (BMP):**
- `GET /calendar.bmp` - F1 calendar with next race (supports `?lang=`, `?tz=`, `?year=`, `?round=`)
- `GET /teams.bmp` - Teams & drivers grid (supports `?lang=`, `?year=`)

**Web UI:**
- `GET /` - Landing page with screen type selection
- `GET /configure/{screen}` - Interactive preview (calendar/teams/standings)

**JSON API:**
- `GET /api/races/{year}` - All races for a season
- `GET /api/race/{year}/{round}` - Specific race details
- `GET /api/teams/{year}` - Teams and drivers for a season
- `GET /api/standings/leader` - Current championship leader
- `GET /api/stats` - Request statistics

**Health & Monitoring:**
- `GET /health` - Process liveness
- `GET /health/ready` - Dependency and generation readiness
- `GET /api` - API documentation

## Project Structure

```
InkyCloud-F1/
├── app/
│   ├── main.py              # FastAPI app & endpoints
│   ├── config.py            # Environment-based config
│   ├── models.py            # Pydantic models
│   ├── state.py             # Application state
│   ├── services/
│   │   ├── renderer.py          # 1-bit BMP rendering
│   │   ├── spectra6_renderer.py # Multi-color rendering
│   │   ├── f1_service.py        # F1 data API client
│   │   ├── teams_service.py     # Teams/drivers logic
│   │   ├── standings_service.py # Championship standings
│   │   ├── weather_service.py   # Weather forecasts
│   │   ├── database.py          # SQLite persistence
│   │   ├── scheduler.py         # Background jobs
│   │   ├── backup.py            # S3 backups
│   │   ├── analytics.py         # Umami tracking
│   │   ├── i18n.py              # Translations
│   │   └── version_service.py   # Version management
│   ├── templates/           # Jinja2 HTML templates
│   └── assets/              # Static assets (fonts, images)
├── tests/                   # Test suite (pytest)
├── translations/            # i18n JSON files (13 locales, see LANGUAGE_CODES)
├── scripts/                 # Data preprocessing utilities
└── .github/
    ├── copilot-instructions.md  # This file
    ├── copilot-setup-steps.yaml # Environment setup automation
    └── workflows/               # CI/CD pipelines
```
