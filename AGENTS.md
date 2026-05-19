# AGENTS.md

## Cursor Cloud specific instructions

### Overview

This is a single-service Python/FastAPI application (F1 E-Ink Calendar) that generates 800x480 BMP images for E-Ink displays. It uses an embedded SQLite database — no external database servers are required.

### Running the dev server

```bash
source /workspace/venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The server starts on http://localhost:8000. Key endpoints:
- `GET /health` — health check
- `GET /calendar.bmp?lang=en` — calendar BMP image
- `GET /teams.bmp?lang=en` — teams BMP image
- `GET /en/configure/calendar` — interactive calendar config page

### Lint and test

```bash
source /workspace/venv/bin/activate
ruff check .          # linting
ruff format --check . # format check
pytest                # test suite (expect 2 pre-existing failures on stats auth tests)
```

The 2 test failures in `test_api_stats_endpoint_returns_correct_structure` and `test_api_stats_history_endpoint_returns_hourly_history` are pre-existing — they test admin-token-protected endpoints without providing a token.

### Environment setup notes

- Python 3.13+ is required (installed from deadsnakes PPA).
- The virtualenv lives at `/workspace/venv`.
- `.env` is copied from `.env.local.example` for local development (relative paths for `DATABASE_PATH` and `IMAGES_PATH`).
- The `data/` directory must exist for SQLite and image storage.
- No external services (Redis, PostgreSQL, Docker) are needed — SQLite is embedded and API calls to Jolpica/Open-Meteo go to public endpoints.
- TailwindCSS (Node.js) is only needed if modifying CSS files; pre-compiled CSS is checked in.
