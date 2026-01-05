# Services Layer - Agent Knowledge Base

**12 service modules** powering the F1 E-Ink rendering pipeline.

## Service Map

| Service | Lines | Role | Key Pattern |
|---------|-------|------|-------------|
| `renderer.py` | 1628 | 1-bit BMP generation | `self.layout` dict for coordinates |
| `database.py` | 878 | SQLite metadata/stats | Async `aiosqlite` with WAL mode |
| `f1_service.py` | 678 | Jolpica API + static JSON fallback | `_fetch_with_retry` exponential backoff |
| `scheduler.py` | 505 | Hourly pre-generation | APScheduler + cron triggers |
| `backup.py` | 469 | S3 database backups | boto3 async operations |
| `teams_service.py` | 448 | Team/driver data | Static JSON in `assets/seasons/` |
| `weather_service.py` | 244 | Race weather forecasts | TTL caching with `cachetools` |
| `analytics.py` | 230 | Umami tracking | Fire-and-forget `asyncio.create_task` |
| `standings_service.py` | 224 | Championship standings | API + static fallback |
| `i18n.py` | 65 | Translation loading | In-memory cache, path injection safe |
| `version_service.py` | ~50 | Git version info | Cached on startup |

## Critical Patterns

### Renderer (renderer.py)

```python
# ALWAYS use layout dict - NEVER hardcode coordinates
x = self.layout["schedule_x"]
y = self.layout["header_y"]

# ALWAYS 1-bit mode
image = Image.new("1", (800, 480), 1)  # 1=white
draw.text((x, y), text, font=font, fill=0)  # 0=black
```

**Key methods**:
- `render_calendar(race_data)` - Main race weekend view
- `render_standings(drivers, constructors)` - Championship table
- `render_teams(teams_data)` - Driver grid with photos
- `render_error(message)` - Error display for E-Ink

**Layout constants** in `self.layout` dict - pixel-perfect for 800x480.

### F1Service (f1_service.py)

```python
# Static-first pattern - graceful offline support
async def get_next_race(self, year: int) -> dict:
    # 1. Try static JSON in assets/seasons/
    # 2. Fallback to Jolpica API if missing
    # 3. Convert UTC times to target timezone
```

**Retry logic**: `_fetch_with_retry` handles 429 rate limits with exponential backoff.

### Database (database.py)

SQLite for metadata only - race data lives in JSON files.

```python
# WAL mode enabled for concurrent reads
await conn.execute("PRAGMA journal_mode=WAL;")
```

**Tables**: `generated_images`, `cache_meta`, `api_calls`, `request_stats`

### Analytics (analytics.py)

```python
# Fire-and-forget - never blocks request
_create_background_task(_send_to_umami(...))

# Task set prevents garbage collection
_background_tasks.add(task)
task.add_done_callback(lambda t: _background_tasks.discard(t))
```

### I18n (i18n.py)

```python
# Path injection prevention - hardcoded file paths
_TRANSLATION_FILES = {
    "en": _TRANSLATIONS_DIR / "en.json",
    "cs": _TRANSLATIONS_DIR / "cs.json",
}

# Usage pattern
translator = get_translator("en")
label = translator.get("session_race", "Race")
```

## Adding a New Service

1. Create `app/services/new_service.py`
2. Use `from app.config import config` for settings
3. Use `httpx.AsyncClient` for HTTP (never `requests`)
4. Implement retry logic for external APIs
5. Add to `__init__.py` if needed
6. Write tests in `tests/test_new_service.py`

## Dependency Graph

```
main.py
├── Renderer ← translator (i18n)
├── F1Service ← config, pytz
├── Database ← config
├── Scheduler ← F1Service, Renderer, Database
├── Analytics ← config (fire-and-forget)
└── WeatherService ← httpx, cachetools
```

## Anti-Patterns

| Forbidden | Alternative |
|-----------|-------------|
| `requests.get()` | `httpx.AsyncClient` |
| Hardcoded pixels | `self.layout["key"]` |
| `Image.new("RGB")` | `Image.new("1", ...)` |
| Blocking analytics | `asyncio.create_task()` |
| Direct translation keys | `translator.get(key, fallback)` |
