"""F1 E-Ink calendar service main application."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from app.config import VALID_LANGUAGES, config
from app.routes.api import router as api_router
from app.routes.health import router as health_router
from app.routes.images import router as images_router
from app.routes.pages import router as pages_router
from app.routes.previews import router as previews_router
from app.routes.seo import router as seo_router
from app.services.scheduler import run_initial_generation, start_scheduler, stop_scheduler
from app.services.warmup import warm_teams_renderer_assets
from app.utils.race_times import (  # noqa: F401
    convert_race_times_to_timezone as _convert_race_times_to_timezone,
)
from app.web.templates import detect_ui_language, get_template_context, templates

# Register font MIME types (Python's mimetypes doesn't know TTF by default)
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize Sentry/GlitchTip
if config.SENTRY_DSN:
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        environment=config.SENTRY_ENVIRONMENT,
        traces_sample_rate=config.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=config.SENTRY_TRACES_SAMPLE_RATE,
    )
    logger.info("Sentry/GlitchTip initialized")

# Persistence check marker file
_PERSISTENCE_MARKER = Path(config.DATABASE_PATH).parent / ".persistence_marker"


def _check_persistent_storage() -> bool:
    """Warn if database storage seems non-persistent."""
    if os.environ.get("SKIP_PERSISTENCE_CHECK", "").lower() == "true":
        logger.debug("Persistence check skipped via SKIP_PERSISTENCE_CHECK env var")
        return True

    if _PERSISTENCE_MARKER.exists():
        logger.debug("Persistence marker found - storage is persistent")
        return True

    try:
        _PERSISTENCE_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _PERSISTENCE_MARKER.write_text(
            f"created: {datetime.now(timezone.utc).isoformat()}\n"
            "This file verifies persistent storage. Do not delete.\n"
        )
        logger.info("Created persistence marker at %s", _PERSISTENCE_MARKER)
    except OSError as exc:
        logger.error("Failed to create persistence marker: %s", exc)
        return False

    db_path = Path(config.DATABASE_PATH)
    if db_path.exists():
        logger.warning(
            "WARNING: Database exists but persistence marker was missing! "
            "This may indicate that /app/data is NOT on persistent storage. "
            "Data may be lost on container restart. "
            "Please configure a persistent volume for /app/data"
        )
        return False

    logger.info("First deployment detected - persistence will be verified on next restart")
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting F1 E-Ink calendar service")

    _check_persistent_storage()
    try:
        await asyncio.to_thread(warm_teams_renderer_assets)
    except Exception as exc:
        logger.error("Teams renderer warmup failed: %s", exc, exc_info=True)
        sentry_sdk.capture_exception(exc)

    start_scheduler()
    asyncio.create_task(run_initial_generation())

    yield

    stop_scheduler()
    logger.info("Shutting down F1 E-Ink calendar service")


app = FastAPI(
    title="F1 E-Ink Calendar",
    description="Generates 800x480 1-bit BMPs for F1 E-Ink displays (LaskaKit)",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)


class StaticCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(  # skipcq: PYL-R0201 - must be instance method (BaseHTTPMiddleware)
        self, request: StarletteRequest, call_next
    ) -> StarletteResponse:
        response = await call_next(request)
        path = request.url.path

        if path.startswith("/static/"):
            if "/fonts/" in path:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.endswith((".png", ".jpg", ".bmp", ".ico", ".svg", ".webmanifest")):
                response.headers["Cache-Control"] = "public, max-age=86400"
            elif path.endswith((".css", ".js")):
                response.headers["Cache-Control"] = "public, max-age=3600"

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(  # skipcq: PYL-R0201 - must be instance method (BaseHTTPMiddleware)
        self, request: StarletteRequest, call_next
    ) -> StarletteResponse:
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        canonical_host = str(config.SITE_URL).split("://", 1)[-1].rstrip("/")
        if host == f"www.{canonical_host}":
            target = f"{str(config.SITE_URL).rstrip('/')}{request.url.path}"
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(url=target, status_code=301)

        response = await call_next(request)

        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"

        return response


def _resolve_error_ui_language(request: StarletteRequest) -> str:
    """Prefer a valid language from the request path before falling back to cookies."""
    path_parts = [part for part in request.url.path.split("/") if part]
    if path_parts and path_parts[0] in VALID_LANGUAGES:
        return path_parts[0]
    return detect_ui_language(request)


def _should_render_html_404(request: StarletteRequest) -> bool:
    """Return True when a browser-facing route should get the themed HTML 404 page."""
    if request.method not in {"GET", "HEAD"}:
        return False

    path = request.url.path
    excluded_paths = {"/favicon.ico", "/robots.txt", "/sitemap.xml", "/sw.js"}
    excluded_prefixes = ("/api", "/static", "/preview/", "/health")
    excluded_suffixes = (".bmp", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
    accept = request.headers.get("accept", "")

    if (
        path in excluded_paths
        or path.startswith(excluded_prefixes)
        or path.endswith(excluded_suffixes)
    ):
        return False

    return "text/html" in accept or "*/*" in accept


@app.exception_handler(404)
async def not_found_handler(request: StarletteRequest, exc: StarletteHTTPException):
    """Render a themed HTML 404 for browser routes and JSON elsewhere."""
    if not _should_render_html_404(request):
        return JSONResponse(status_code=404, content={"detail": exc.detail or "Not found"})

    if request.method == "HEAD":
        return StarletteResponse(status_code=404, media_type="text/html")

    ui_lang = _resolve_error_ui_language(request)
    context = get_template_context(request, ui_lang)
    context["active_page"] = None
    context["not_found_path"] = request.url.path
    return templates.TemplateResponse(request, "404.html", context, status_code=404)


app.add_middleware(StaticCacheMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)

app.mount("/static", StaticFiles(directory="app/assets"), name="static")

# Routers - order matters! More specific routes first
app.include_router(previews_router)  # /preview/* must be before pages (/{lang}/* patterns)
app.include_router(seo_router)
app.include_router(health_router)
app.include_router(api_router)
app.include_router(images_router)
app.include_router(pages_router)  # Generic /{lang}/* patterns last


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.DEBUG,
    )
