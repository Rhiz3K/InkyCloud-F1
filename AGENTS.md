<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

# F1 E-Ink Calendar - Agent Knowledge Base

**Generated:** 2026-01-01 | **Commit:** 1425a33 | **Branch:** main

FastAPI service generating **800x480 1-bit BMP images** for E-Ink displays (LaskaKit ESP32).

## Architecture (Non-Standard)

| Pattern | Reality | Impact |
|---------|---------|--------|
| **Monolithic core** | `main.py` (1549 lines), `renderer.py` (1601 lines) | No routers/ - all in one file |
| **Data-as-Code** | F1 data in `app/assets/seasons/*.json` | SQLite for metadata only |
| **Script-heavy** | `/scripts/` has 21 preprocessing utilities | Data lifecycle outside app/ |
| **Self-updating** | GitHub Actions commits race data back to repo | Versioned data history |

## Code Map (Key Symbols)

| Symbol | Location | Role |
|--------|----------|------|
| `Renderer` | services/renderer.py | 36 methods - full BMP engine |
| `F1Service` | services/f1_service.py | Jolpica API + timezone conversion |
| `lifespan` | main.py | Startup/shutdown + scheduler init |
| `get_calendar_bmp` | main.py | Main endpoint, handles caching |
| `CIRCUITS_DATA` | renderer.py | Circuit metadata from JSON |

## Quick Reference Commands

### Testing
```bash
# Run all tests
pytest

# Run single test file with verbose output
pytest tests/test_renderer.py -v

# Run single test function
pytest tests/test_renderer.py::test_render_calendar -v

# Run with coverage report
pytest --cov=app tests/

# Run tests matching a pattern
pytest -k "test_render" -v
```

### Linting & Formatting
```bash
# Check for lint errors (CI enforced)
ruff check .

# Auto-fix lint errors
ruff check . --fix

# Format code
ruff format .

# Check formatting without changes
ruff format . --check
```

### Development Server
```bash
# Start with auto-reload
uvicorn app.main:app --reload

# Start with debug logging
DEBUG=true python -m app.main

# Specify host/port
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker
```bash
# Build test image
docker build -t f1-eink-cal:test .

# Run container
docker run -p 8000:8000 f1-eink-cal:test
```

## Code Style Guidelines

### Python Version & Line Length
- **Python**: 3.11+ required (3.12 recommended)
- **Line length**: 100 characters max (ruff enforces)
- **Exception**: `app/main.py` allows longer lines for inline HTML templates

### Import Order (ruff enforces E/F/I rules)
```python
# 1. Standard library
import asyncio
import logging
from datetime import datetime
from pathlib import Path

# 2. Third-party packages
import httpx
import pytz
from fastapi import FastAPI, Request
from PIL import Image, ImageDraw

# 3. Local imports
from app.config import config
from app.services.renderer import Renderer
```

### Naming Conventions
- **Functions/variables**: `snake_case` (e.g., `render_calendar`, `race_data`)
- **Classes**: `PascalCase` (e.g., `Renderer`, `F1Service`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `DISPLAY_WIDTH`, `CIRCUITS_DATA`)
- **Private members**: `_leading_underscore` (e.g., `_load_font`, `_bmp_cache`)

### Type Hints
- Use type hints for function signatures
- Use Pydantic models for API data (`app/models.py`)
- Convert `HttpUrl` to `str()` when passing to httpx:
```python
# Correct
async with httpx.AsyncClient() as client:
    response = await client.get(str(config.JOLPICA_API_URL))

# Wrong - will fail
response = await client.get(config.JOLPICA_API_URL)
```

### Async Patterns
- **Always** use `async def` for HTTP operations
- Use `httpx.AsyncClient` (never `requests`)
- Fire-and-forget tasks: `asyncio.create_task(_send_analytics(...))`
- Use proper context managers for async clients

### Error Handling
**Endpoints NEVER raise exceptions** - always return a rendered error BMP:
```python
try:
    race_data = await f1_service.get_next_race()
    bmp_data = renderer.render_calendar(race_data)
except Exception as e:
    logger.error(f"Error rendering calendar: {e}", exc_info=True)
    sentry_sdk.capture_exception(e)
    return StreamingResponse(
        BytesIO(renderer.render_error(str(e))),
        media_type="image/bmp"
    )
```

### Configuration
- All settings from environment variables via `app/config.py`
- **Never hardcode**: URLs, dimensions, DSNs, timeouts
- Access config: `from app.config import config`
- Feature flags (e.g., `UMAMI_ENABLED`) must be handled gracefully when disabled

## Critical Domain Rules

### 1-Bit E-Ink Rendering (MUST Follow)
All images **MUST** use 1-bit mode for E-Ink compatibility:
```python
# Correct - 1-bit mode, white background
image = Image.new("1", (800, 480), 1)

# Wrong - will not display correctly on E-Ink
image = Image.new("RGB", (800, 480), (255, 255, 255))
image = Image.new("L", (800, 480), 255)
```

Drawing colors:
- `fill=0` = black
- `fill=1` = white

### Layout Constants
- **Never hardcode pixel coordinates** - use `self.layout` dict in `renderer.py`
- Layout constants are pixel-perfect for 800x480 - changing one may require adjusting neighbors
- Test visual changes with actual BMP output

### Timezone Handling
All race times stored in UTC (Jolpica API) must be converted:
```python
# Parse UTC time
dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

# Convert to Prague timezone
dt_prague = dt_utc.astimezone(self.prague_tz)

# Display format
display_time = dt_prague.strftime("%a %H:%M")  # e.g., "Sun 17:00"
```

### Translations
- Use `translator.get(key, fallback)` pattern
- Session names use `session_` prefix: `session_race`, `session_qualifying`, `session_fp1`
- Source of truth: `translations/en.json`
- Always add keys to both `en.json` and `cs.json`

## Testing Requirements

### All renderer changes require tests verifying:
1. Image mode is "1" (1-bit)
2. Image size is (800, 480)
3. Output format is BMP

### Test Structure
```python
def test_new_feature(mock_race_data):
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)
    
    img = Image.open(BytesIO(bmp_data))
    assert img.mode == "1"
    assert img.size == (800, 480)
```

### Test Fixtures
- `mock_race_data` - Standard race data fixture
- `mock_historical_data` - Historical results fixture
- Test both Czech (`cs`) and English (`en`) translations

### Test Environment
- Tests use temporary directories for DATABASE_PATH and IMAGES_PATH
- Set up in `tests/conftest.py` before any app imports
- Cleanup is automatic via `atexit`

## Project Structure

```
app/
├── main.py              # FastAPI app, endpoints, lifespan
├── config.py            # Pydantic settings from env vars
├── models.py            # Pydantic models for API data
├── services/
│   ├── renderer.py      # 1-bit BMP rendering engine
│   ├── f1_service.py    # Jolpica API client
│   ├── i18n.py          # Translation loader
│   ├── analytics.py     # Umami tracking
│   ├── database.py      # SQLite operations
│   └── scheduler.py     # APScheduler background tasks
├── templates/           # Jinja2 HTML templates
└── assets/              # Static files (fonts, flags, tracks)

tests/
├── conftest.py          # Pytest fixtures & env setup
├── test_renderer.py     # Renderer tests (critical)
├── test_main.py         # Endpoint tests
└── test_config.py       # Configuration tests

translations/
├── en.json              # English (source of truth)
└── cs.json              # Czech
```

## Anti-Patterns (NEVER Do)

| Forbidden | Correct Alternative |
|-----------|---------------------|
| `Image.new("RGB", ...)` | `Image.new("1", (800, 480), 1)` |
| `requests.get()` | `httpx.AsyncClient` |
| Hardcoded coordinates | Use `self.layout` dict |
| `raise` in endpoints | Return `renderer.render_error()` |
| `as any`, `@ts-ignore` | Fix the type properly |
| Bare `except:` | Specify exception class |

## Common Gotchas

1. **Font loading**: Falls back to default if TitilliumWeb missing - test in Docker
2. **Session order**: Always `sort(key=lambda x: x["datetime"])`
3. **Missing circuit data**: Use `.get()` with defaults (Jolpica sometimes omits fields)
4. **BMP format**: Little-endian, 1-bit depth - don't manually construct headers
5. **Pydantic URLs**: Cast to `str()` before passing to httpx
6. **Timezone**: Prague observes DST - use `pytz` for proper handling
7. **Persistence**: Check `.persistence_marker` exists in Docker volume

## CI/CD Notes

- GitHub Actions runs on push/PR to main
- Workflow: `.github/workflows/ci.yml`
- Must pass: `ruff check .`, `ruff format . --check`, `pytest`
- Season data updates: `.github/workflows/update-f1-data.yml`

## Adding New Features

### New Translation Key
1. Add to `translations/en.json`
2. Add to `translations/cs.json`
3. Use: `translator.get("your_key", "Fallback")`

### New Language
1. Create `translations/{lang}.json` with all keys
2. Update validation in `app/main.py`: `if lang not in ["cs", "en", "de"]:`
3. Add test case following `test_render_calendar_czech` pattern

### New Endpoint
1. Add async handler in `app/main.py`
2. Wrap in try/except, return `render_error()` on failure
3. Log errors with `exc_info=True`
4. Add Sentry capture: `sentry_sdk.capture_exception(e)`

## Reference Documentation

See `.github/copilot-instructions.md` for:
- Architecture deep dive
- Track map rendering details
- ESP32 integration examples
- Caching best practices
