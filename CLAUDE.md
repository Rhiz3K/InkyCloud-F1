# Claude Code Instructions for F1 E-Ink Calendar

## Quick Reference
- **Run tests**: `pytest` | Single: `pytest tests/test_renderer.py -v`
- **Lint/Format**: `ruff check .` && `ruff format .`
- **Dev server**: `uvicorn app.main:app --reload`
- **Docker build**: `docker build -t f1-eink-cal:test .`

## Architecture
FastAPI service generating **800x480 1-bit BMP images** for E-Ink displays. Fetches F1 data from Jolpica API, converts UTC to Europe/Prague timezone, renders with Pillow.

**Key files**: `app/main.py` (endpoints), `app/services/renderer.py` (BMP rendering), `app/services/f1_service.py` (API client), `app/config.py` (env vars)

## Critical Rules

### 1-Bit Rendering (MUST follow)
```python
# CORRECT - 1-bit mode for E-Ink
image = Image.new("1", (800, 480), 1)  # 1=white background
draw.text(..., fill=0)  # 0=black, 1=white

# WRONG - never use RGB/L modes
image = Image.new("RGB", ...)
```

### Layout Constants
NEVER hardcode pixel coordinates. Use `self.layout` dict in `renderer.py`. Changing one value may break neighboring elements.

### Timezone Conversion
ALL race times are UTC from API. Convert to Prague:
```python
dt_prague = dt_utc.astimezone(self.prague_tz)
display = dt_prague.strftime("%a %H:%M")
```

### Error Handling
Endpoints NEVER raise exceptions. Always return error BMP:
```python
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
    sentry_sdk.capture_exception(e)
    return StreamingResponse(BytesIO(renderer.render_error(str(e))), ...)
```

### Async HTTP
Use `httpx.AsyncClient`, never `requests`. Analytics is fire-and-forget via `asyncio.create_task()`.

## Code Style
- Python 3.14.3+ | Line length: 100 chars
- Imports: stdlib -> third-party -> local (ruff I rules)
- `snake_case` functions/vars, `PascalCase` classes
- Config from env vars only (`app/config.py`)

## Testing Requirements
- All renderer changes need tests in `tests/test_renderer.py`
- Verify: `img.mode == "1"`, `img.size == (800, 480)`, BMP format
- Test both `cs` and `en` translations

## Translations
- Use `translator.get(key, fallback)` pattern
- Session keys: `session_race`, `session_qualifying`, `session_fp1`, etc.
- Source of truth: `translations/en.json`
