# Project Context

## Purpose

F1 E-Ink Calendar is a web service that generates **800x480 1-bit BMP images** for E-Ink displays (LaskaKit ESP32 devices). It displays the next F1 race with:
- Race name, location, and country flag
- Full session schedule (FP1, FP2, FP3, Qualifying, Sprint, Race) with times
- Circuit map, length, laps, and first GP year
- Previous year's podium results for the circuit

The public instance runs at [f1.inkycloud.click](https://f1.inkycloud.click) and is designed to work with [zivyobraz.eu](https://zivyobraz.eu) for ESP32 E-Ink display management.

## Tech Stack

- **Runtime**: Python 3.11+ (3.12 recommended)
- **Web Framework**: FastAPI with uvicorn
- **Image Rendering**: Pillow (PIL) for 1-bit BMP generation
- **HTTP Client**: httpx (async)
- **Database**: SQLite with aiosqlite
- **Templating**: Jinja2
- **Configuration**: pydantic-settings with environment variables
- **Scheduling**: APScheduler for background tasks
- **Error Tracking**: Sentry SDK
- **Analytics**: Umami (optional)
- **Linting/Formatting**: Ruff
- **Testing**: pytest with pytest-asyncio
- **Containerization**: Docker
- **Deployment**: Coolify on Hetzner

## Project Conventions

### Code Style

- **Line length**: 100 characters max (ruff enforces)
- **Exception**: `app/main.py` allows longer lines for inline HTML templates
- **Import order**: Standard library → Third-party → Local (ruff enforces E/F/I rules)
- **Naming**:
  - Functions/variables: `snake_case` (e.g., `render_calendar`, `race_data`)
  - Classes: `PascalCase` (e.g., `Renderer`, `F1Service`)
  - Constants: `UPPER_SNAKE_CASE` (e.g., `DISPLAY_WIDTH`, `CIRCUITS_DATA`)
  - Private members: `_leading_underscore` (e.g., `_load_font`, `_bmp_cache`)
- **Type hints**: Required for function signatures; use Pydantic models for API data
- **HttpUrl handling**: Always cast `HttpUrl` to `str()` when passing to httpx

### Architecture Patterns

- **Service layer**: Business logic in `app/services/` (renderer, f1_service, i18n, analytics, database, scheduler)
- **Configuration**: All settings from environment variables via `app/config.py` using pydantic-settings
- **Async-first**: All HTTP operations use `async def` and `httpx.AsyncClient`
- **Graceful degradation**: Feature flags (e.g., `UMAMI_ENABLED`) handled gracefully when disabled
- **Error handling**: Endpoints NEVER raise exceptions - always return a rendered error BMP
- **Translations**: Use `translator.get(key, fallback)` pattern; source of truth is `translations/en.json`

### Testing Strategy

- **Framework**: pytest with pytest-asyncio
- **Coverage**: All renderer changes require tests verifying:
  1. Image mode is "1" (1-bit)
  2. Image size is (800, 480)
  3. Output format is BMP
- **Fixtures**: `mock_race_data`, `mock_historical_data` in `tests/conftest.py`
- **Environment**: Tests use temporary directories for DATABASE_PATH and IMAGES_PATH
- **Languages**: Test both Czech (`cs`) and English (`en`) translations
- **Commands**:
  ```bash
  pytest                           # Run all tests
  pytest tests/test_renderer.py -v # Single test file
  pytest --cov=app tests/          # With coverage
  ```

### Git Workflow

- **Branching**: Feature branches from main (`feature/your-feature-name`)
- **Commit convention**: Conventional commits
  - `feat:` New feature
  - `fix:` Bug fix
  - `docs:` Documentation changes
  - `style:` Code style changes
  - `refactor:` Code refactoring
  - `test:` Adding or updating tests
  - `chore:` Maintenance tasks
- **CI/CD**: GitHub Actions on push/PR to main (`.github/workflows/ci.yml`)
- **Checks**: `ruff check .`, `ruff format . --check`, `pytest`

## Domain Context

### E-Ink Display Constraints

- **Resolution**: 800x480 pixels (LaskaKit 7.5" displays)
- **Color depth**: 1-bit only (black and white)
- **Image format**: BMP (little-endian, 1-bit depth)
- Drawing colors: `fill=0` = black, `fill=1` = white
- **Layout**: Pixel-perfect coordinates in `renderer.py` `self.layout` dict

### F1 Data

- **Source**: Jolpica F1 API (`https://api.jolpi.ca/ergast/f1/`)
- **Race times**: Stored in UTC, converted to user timezone (default: Europe/Prague)
- **Sessions**: FP1, FP2, FP3, Qualifying, Sprint Qualifying, Sprint, Race
- **Session order**: Always `sort(key=lambda x: x["datetime"])`
- **Circuit data**: May be incomplete from API - use `.get()` with defaults

### Timezone Handling

All race times from API are in UTC and must be converted:
```python
dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
dt_prague = dt_utc.astimezone(self.prague_tz)
display_time = dt_prague.strftime("%a %H:%M")  # e.g., "Sun 17:00"
```
Prague observes DST - always use `pytz` for proper handling.

## Important Constraints

1. **1-bit BMP output**: All images MUST use `Image.new("1", (800, 480), 1)` - never RGB or grayscale
2. **No hardcoded pixels**: Use `self.layout` dict in renderer.py for all coordinates
3. **Never hardcode config**: URLs, dimensions, DSNs, timeouts come from environment
4. **Endpoints never raise**: Always catch exceptions and return `render_error()` BMP
5. **Font fallback**: TitilliumWeb may be missing - renderer falls back to default font
6. **No breaking changes**: Maintain backward compatibility with ESP32 clients

## External Dependencies

### APIs

- **Jolpica F1 API**: `https://api.jolpi.ca/ergast/f1/` - Race schedules, results, circuit data
- **Umami Analytics**: Optional analytics tracking (configurable via `UMAMI_ENABLED`)
- **Sentry/GlitchTip**: Error tracking (configurable via `SENTRY_DSN`)

### Services

- **zivyobraz.eu**: Primary integration target for ESP32 E-Ink display management
- **Coolify**: Deployment platform for public instance
- **Hetzner**: Hosting infrastructure

### Assets

- **Fonts**: TitilliumWeb (Bold, Regular) in `app/assets/fonts/`
- **Flags**: Country flags as PNG/BMP in `app/assets/flags/` and `flags_processed/`
- **Tracks**: Circuit maps as PNG/BMP in `app/assets/tracks/` and `tracks_processed/`
- **Season data**: JSON files in `app/assets/seasons/` (2025.json, 2026.json)
