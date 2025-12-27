"""F1 E-Ink calendar service main application."""

import asyncio
import logging
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

import pytz
import sentry_sdk
from cachetools import TTLCache
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import config
from app.services.analytics import get_umami_script_tag, track_event, track_pageview
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.scheduler import run_initial_generation, start_scheduler, stop_scheduler

# In-memory cache for rendered BMP images
# TTL of 1 hour (3600 seconds), max 100 entries
_bmp_cache: TTLCache = TTLCache(maxsize=100, ttl=3600)

# Buffer for API calls to be flushed to SQLite every minute
_api_calls_buffer: list = []

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


def _get_build_info() -> dict:
    """
    Get build/release information from git or environment.

    Returns:
        Dictionary with version, commit, and build_time
    """
    build_info = {
        "version": "dev",
        "commit": "unknown",
        "build_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    try:
        # Try to get version from git tag
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            build_info["version"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        # Try to get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            build_info["commit"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    try:
        # Try to get commit timestamp
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            build_info["build_time"] = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return build_info


# Cache build info at startup (it won't change during runtime)
_build_info: dict = _get_build_info()
logger.info(f"Build info: {_build_info}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logger.info("Starting F1 E-Ink calendar service")

    # Start background scheduler
    start_scheduler()

    # Run initial data collection (in background to not block startup)
    asyncio.create_task(run_initial_generation())

    yield

    # Stop scheduler on shutdown
    stop_scheduler()
    logger.info("Shutting down F1 E-Ink calendar service")


# Initialize FastAPI app
app = FastAPI(
    title="F1 E-Ink Calendar",
    description="Generates 800x480 1-bit BMPs for F1 E-Ink displays (LaskaKit)",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files (flags, etc.)
app.mount("/static", StaticFiles(directory="app/assets"), name="static")

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="app/templates")


def _format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable string."""
    if bytes_val >= 1_000_000_000:
        return f"{bytes_val / 1_000_000_000:.2f} GB"
    elif bytes_val >= 1_000_000:
        return f"{bytes_val / 1_000_000:.2f} MB"
    elif bytes_val >= 1_000:
        return f"{bytes_val / 1_000:.1f} KB"
    return f"{bytes_val} B"


def _calc_percent(value: int, total: int) -> float:
    """Calculate percentage safely."""
    if total == 0:
        return 0
    return round((value / total) * 100, 1)


def _get_template_context(request: Request, ui_lang: str = "en") -> dict:
    """
    Build common template context with translations and shared data.

    Args:
        request: FastAPI request object
        ui_lang: UI language code (en or cs)

    Returns:
        Dictionary with common template context
    """
    t = get_translator(ui_lang)

    # Common translations used across all pages
    common_translations = {
        "nav_home": t.get("nav_home", "Home"),
        "nav_stats": t.get("nav_stats", "Stats"),
        "nav_api": t.get("nav_api", "API"),
        "nav_privacy": t.get("nav_privacy", "Privacy"),
    }

    return {
        "request": request,
        "ui_lang": ui_lang,
        "lang_selected_en": "selected" if ui_lang == "en" else "",
        "lang_selected_cs": "selected" if ui_lang == "cs" else "",
        "umami_script": get_umami_script_tag(),
        "t": t,  # Full translator for page-specific translations
        "nav": common_translations,
        "site_url": str(config.SITE_URL).rstrip("/"),  # For SEO canonical/OG URLs
        # Helper functions for templates
        "format_bytes": _format_bytes,
        "calc_percent": _calc_percent,
    }


def _detect_ui_language(request: Request) -> str:
    """
    Detect preferred UI language from request headers.

    CZ/SK users get Czech, everyone else gets English.
    """
    accept_lang = request.headers.get("accept-language", "").lower()
    if "cs" in accept_lang or "sk" in accept_lang:
        return "cs"
    return "en"


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, lang: str = Query(default=None)):
    """
    Main preview page - interactive parameter selection and preview.

    This is the index page of the application.
    Language can be overridden via ?lang= query parameter.
    """
    if lang in ["en", "cs"]:
        ui_lang = lang
    else:
        ui_lang = _detect_ui_language(request)

    # Track pageview server-side (for non-JS clients)
    # Always include effective language in URL for consistent analytics
    url = f"/?lang={ui_lang}"
    await track_pageview(
        url=url,
        title="F1 E-Ink Calendar",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    # Build template context
    context = _get_template_context(request, ui_lang)
    context["active_page"] = "home"
    context["default_timezone"] = config.DEFAULT_TIMEZONE

    return templates.TemplateResponse(request, "index.html", context)


@app.get("/preview")
async def preview_redirect():
    """Redirect /preview to / for backwards compatibility."""
    return RedirectResponse(url="/", status_code=301)


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon as SVG with F1 car emoji."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<text y=".9em" font-size="90">🏎️</text>
</svg>"""
    return StreamingResponse(
        iter([svg.encode()]),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/site.webmanifest")
async def site_webmanifest():
    """
    Serve the PWA web app manifest file.

    Dedicated route to avoid static file serving issues and ensure correct MIME type.
    """
    manifest_path = Path("app/assets/favicon/site.webmanifest")
    try:
        content = manifest_path.read_text()
        return Response(
            content=content,
            media_type="application/manifest+json",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except FileNotFoundError:
        logger.error(f"Manifest file not found: {manifest_path}")
        raise HTTPException(status_code=404, detail="Manifest not found")


@app.get("/api")
@app.get("/api/docs")
async def api_info():
    """API documentation endpoint."""
    return {
        "service": "F1 E-Ink Calendar API",
        "version": "0.1.0",
        "description": "Generate 800x480 1-bit BMP images for E-Ink displays showing F1 race schedules",
        "endpoints": {
            "/": {
                "method": "GET",
                "description": "Interactive preview page with live image generation",
            },
            "/calendar.bmp": {
                "method": "GET",
                "description": "Generate F1 calendar as 1-bit BMP image (800x480)",
                "parameters": {
                    "lang": {
                        "type": "string",
                        "description": "Language code for calendar text",
                        "values": ["en", "cs"],
                        "default": "en",
                        "example": "?lang=cs",
                    },
                    "year": {
                        "type": "integer",
                        "description": "Season year for specific race",
                        "example": "?year=2025",
                        "optional": True,
                    },
                    "round": {
                        "type": "integer",
                        "description": "Round number (1-24) for specific race",
                        "example": "?round=5",
                        "optional": True,
                    },
                    "tz": {
                        "type": "string",
                        "description": "Timezone for schedule times (IANA format)",
                        "example": "?tz=America/New_York",
                        "default": "Europe/Prague",
                        "optional": True,
                    },
                },
                "response": {
                    "content_type": "image/bmp",
                    "dimensions": "800x480",
                    "color_depth": "1-bit (black and white)",
                },
                "examples": [
                    "/calendar.bmp",
                    "/calendar.bmp?lang=cs",
                    "/calendar.bmp?year=2025&round=1",
                    "/calendar.bmp?lang=en&tz=America/Los_Angeles",
                ],
            },
            "/api": {
                "method": "GET",
                "description": "API documentation (this endpoint)",
            },
            "/api/docs": {
                "method": "GET",
                "description": "API documentation (alias for /api)",
            },
            "/api/stats": {
                "method": "GET",
                "description": "Request statistics (last hour and 24h counts)",
            },
            "/api/stats/history": {
                "method": "GET",
                "description": "Historical hourly request statistics",
            },
            "/api/races/{year}": {
                "method": "GET",
                "description": "Get list of races for a season",
                "parameters": {
                    "year": {
                        "type": "integer",
                        "description": "Season year",
                        "in": "path",
                    }
                },
            },
            "/api/race/{year}/{round_num}": {
                "method": "GET",
                "description": "Get detailed race information",
                "parameters": {
                    "year": {"type": "integer", "description": "Season year", "in": "path"},
                    "round_num": {
                        "type": "integer",
                        "description": "Round number",
                        "in": "path",
                    },
                },
            },
            "/health": {
                "method": "GET",
                "description": "Health check endpoint",
            },
        },
        "e_ink_usage": {
            "description": "For E-Ink displays, fetch /calendar.bmp and display directly",
            "recommended_refresh": "Every 1-6 hours (data updates hourly)",
            "display_compatibility": 'Any 800x480 E-Ink display (e.g., Waveshare 7.5")',
        },
    }


@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request, lang: str = Query(default=None)):
    """
    Privacy Policy page with language detection.

    Language can be overridden via ?lang= query parameter.
    """
    if lang in ["en", "cs"]:
        ui_lang = lang
    else:
        ui_lang = _detect_ui_language(request)

    # Track pageview server-side
    # Always include effective language in URL for consistent analytics
    url = f"/privacy?lang={ui_lang}"
    await track_pageview(
        url=url,
        title="Privacy Policy",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = _get_template_context(request, ui_lang)
    context["active_page"] = "privacy"
    return templates.TemplateResponse(request, "privacy.html", context)


@app.get("/api/docs/html", response_class=HTMLResponse)
async def api_docs_html(request: Request, lang: str = Query(default=None)):
    """
    API Documentation page with language detection.

    Interactive HTML documentation with code examples and "Try it" functionality.
    Language can be overridden via ?lang= query parameter.
    """
    if lang in ["en", "cs"]:
        ui_lang = lang
    else:
        ui_lang = _detect_ui_language(request)

    # Track pageview server-side
    # Always include effective language in URL for consistent analytics
    url = f"/api/docs/html?lang={ui_lang}"
    await track_pageview(
        url=url,
        title="API Documentation",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    # Build template context
    context = _get_template_context(request, ui_lang)
    context["active_page"] = "api"

    # Localized code examples
    if ui_lang == "cs":
        curl_comment1 = "# Stáhnout kalendář dalšího závodu"
        curl_comment2 = "# S českým jazykem a časovým pásmem"
        curl_comment3 = "# Konkrétní závod (rok a kolo)"
        python_docstring = "Stáhne F1 kalendář jako BMP obrázek."
        python_print = "Kalendář uložen jako calendar.bmp"
        python_usage = "# Použití"
        js_comment1 = "// Načíst a zobrazit kalendář"
        js_comment2 = "// Zobrazit v img elementu"
        js_comment3 = "// Stáhnout jako soubor"
    else:
        curl_comment1 = "# Download next race calendar"
        curl_comment2 = "# With Czech language and timezone"
        curl_comment3 = "# Specific race (year and round)"
        python_docstring = "Download F1 calendar as BMP image."
        python_print = "Calendar saved as calendar.bmp"
        python_usage = "# Usage"
        js_comment1 = "// Fetch and display calendar"
        js_comment2 = "// Display in img element"
        js_comment3 = "// Download as file"

    context["code_curl"] = f"""{curl_comment1}
curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp"

{curl_comment2}
curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?lang=cs&tz=Europe/Prague"

{curl_comment3}
curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?year=2025&round=5\""""

    context["code_python"] = f'''import httpx

async def get_f1_calendar(lang: str = "en", tz: str = "Europe/Prague"):
    """{python_docstring}"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://f1-eink.example.com/calendar.bmp",
            params={{"lang": lang, "tz": tz}}
        )
        response.raise_for_status()
        
        with open("calendar.bmp", "wb") as f:
            f.write(response.content)
        
        print("{python_print}")

{python_usage}
import asyncio
asyncio.run(get_f1_calendar(lang="cs"))'''

    context["code_javascript"] = f"""{js_comment1}
async function loadF1Calendar(lang = 'en', tz = 'Europe/Prague') {{
    const url = new URL('https://f1-eink.example.com/calendar.bmp');
    url.searchParams.set('lang', lang);
    url.searchParams.set('tz', tz);
    
    const response = await fetch(url);
    const blob = await response.blob();
    
    {js_comment2}
    const img = document.getElementById('calendar');
    img.src = URL.createObjectURL(blob);
}}

{js_comment3}
async function downloadCalendar() {{
    const response = await fetch('/calendar.bmp?lang=cs');
    const blob = await response.blob();
    
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'f1-calendar.bmp';
    link.click();
}}"""

    # Localized descriptions
    eg = "např." if ui_lang == "cs" else "e.g."
    context.update(
        {
            "lang_desc": (
                "Kód jazyka pro text kalendáře"
                if ui_lang == "cs"
                else "Language code for calendar text"
            ),
            "year_desc": (
                "Rok sezóny pro konkrétní závod"
                if ui_lang == "cs"
                else "Season year for specific race"
            ),
            "round_desc": (
                "Číslo kola (1-24) pro konkrétní závod"
                if ui_lang == "cs"
                else "Round number (1-24) for specific race"
            ),
            "tz_desc": (
                "Časové pásmo pro časy v harmonogramu (IANA formát)"
                if ui_lang == "cs"
                else "Timezone for schedule times (IANA format)"
            ),
            "calendar_desc": (
                "Generuje F1 kalendář jako 1-bit BMP obrázek (800×480) pro E-Ink displeje."
                if ui_lang == "cs"
                else "Generates F1 calendar as 1-bit BMP image (800×480) for E-Ink displays."
            ),
            "eg": eg,
            "dimensions_label": "Rozměry" if ui_lang == "cs" else "Dimensions",
            "color_depth_label": "Barevná hloubka" if ui_lang == "cs" else "Color depth",
            "races_desc": (
                "Seznam všech závodů pro danou sezónu"
                if ui_lang == "cs"
                else "List of all races for a given season"
            ),
            "race_desc": (
                "Detailní informace o konkrétním závodě včetně harmonogramu"
                if ui_lang == "cs"
                else "Detailed race information including schedule"
            ),
            "stats_desc": (
                "Statistiky požadavků (počet za hodinu a 24 hodin)"
                if ui_lang == "cs"
                else "Request statistics (last hour and 24h counts)"
            ),
            "health_desc": "Kontrola zdraví služby" if ui_lang == "cs" else "Service health check",
            "json_api_desc": (
                "Dokumentace API ve formátu JSON"
                if ui_lang == "cs"
                else "API documentation in JSON format"
            ),
            "laskakit_title": (
                "Pro LaskaKit / zivyobraz.eu:"
                if ui_lang == "cs"
                else "For LaskaKit / zivyobraz.eu:"
            ),
            "laskakit_step1": (
                "V zivyobraz.eu vyberte jako zdroj obsahu: URL s obrázkem"
                if ui_lang == "cs"
                else "In zivyobraz.eu select content source: URL with image"
            ),
            "laskakit_step2": "Vložte URL" if ui_lang == "cs" else "Paste URL",
            "laskakit_step3": (
                "Nastavte interval obnovování na 1-6 hodin"
                if ui_lang == "cs"
                else "Set refresh interval to 1-6 hours"
            ),
            "close_btn": "Zavřít" if ui_lang == "cs" else "Close",
            "loading_text": "Načítání..." if ui_lang == "cs" else "Loading...",
            "error_text": "Chyba" if ui_lang == "cs" else "Error",
        }
    )

    return templates.TemplateResponse(request, "api_docs.html", context)


@app.get("/stats", response_class=HTMLResponse)
async def stats_dashboard(
    request: Request,
    range: str = Query(default="24h", pattern="^(1h|24h|7d|30d|365d)$"),
    lang: str = Query(default=None),
):
    """
    Statistics dashboard page with API usage metrics.

    Shows request counts, response times, endpoint breakdown, language stats, etc.
    """
    if lang in ["en", "cs"]:
        ui_lang = lang
    else:
        ui_lang = _detect_ui_language(request)

    # Convert range to hours
    hours_map = {"1h": 1, "24h": 24, "7d": 168, "30d": 720, "365d": 8760}
    hours = hours_map.get(range, 24)

    # Get stats from database
    db = Database()
    stats = await db.get_stats_for_range(hours)

    # Track pageview
    url = f"/stats?range={range}&lang={ui_lang}"
    await track_pageview(
        url=url,
        title="Statistics Dashboard",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    # Build template context
    context = _get_template_context(request, ui_lang)
    context["active_page"] = "stats"
    context["stats"] = stats
    context["selected_range"] = range

    # Range label for display
    range_labels = {
        "1h": "Last Hour",
        "24h": "Last 24 Hours",
        "7d": "Last 7 Days",
        "30d": "Last 30 Days",
        "365d": "Last 365 Days",
    }
    context["range_label"] = range_labels.get(range, "Last 24 Hours")

    # Calculate percentages for bar charts
    max_response = stats.get("max_response_ms", 1) or 1
    context["min_pct"] = _calc_percent(stats.get("min_response_ms", 0), max_response)
    context["avg_pct"] = _calc_percent(int(stats.get("avg_response_ms", 0)), max_response)

    # Add build/release info
    context["build_info"] = _build_info

    return templates.TemplateResponse(request, "stats.html", context)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/robots.txt")
async def robots_txt():
    """
    Serve robots.txt for search engine crawlers.

    Allows all crawlers to access all pages and points to sitemap.
    """
    from fastapi.responses import PlainTextResponse

    site_url = str(config.SITE_URL).rstrip("/")
    content = f"""User-agent: *
Allow: /

Sitemap: {site_url}/sitemap.xml
"""
    return PlainTextResponse(content, media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap_xml():
    """
    Serve sitemap.xml for search engine indexing.

    Lists all public pages with both English and Czech language versions.
    """
    from fastapi.responses import Response

    site_url = str(config.SITE_URL).rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Define pages with their priorities and change frequencies
    pages = [
        {"loc": "/", "priority": "1.0", "changefreq": "daily"},
        {"loc": "/api/docs/html", "priority": "0.8", "changefreq": "monthly"},
        {"loc": "/stats", "priority": "0.6", "changefreq": "hourly"},
        {"loc": "/privacy", "priority": "0.3", "changefreq": "yearly"},
    ]

    # Build sitemap XML
    urls = []
    for page in pages:
        for lang in ["en", "cs"]:
            url_loc = f"{site_url}{page['loc']}?lang={lang}"
            urls.append(
                f"""  <url>
    <loc>{url_loc}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>{page["changefreq"]}</changefreq>
    <priority>{page["priority"]}</priority>
  </url>"""
            )

    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    return Response(content=sitemap_content, media_type="application/xml")


def _record_api_call(
    endpoint: str,
    response_time_ms: float,
    response_size_bytes: int,
    lang: str | None = None,
    tz: str | None = None,
    year: int | None = None,
    round_num: int | None = None,
    race_name: str | None = None,
    is_auto_selected: bool = False,
) -> None:
    """
    Record an API call to the buffer for later DB flush.

    Args:
        endpoint: API endpoint path (e.g., "/calendar.bmp")
        response_time_ms: Response time in milliseconds
        response_size_bytes: Size of response in bytes
        lang: Language parameter (optional)
        tz: Timezone parameter (optional)
        year: Season year (optional, for /calendar.bmp)
        round_num: Round number (optional, for /calendar.bmp)
        race_name: Race name (optional, for /calendar.bmp)
        is_auto_selected: Whether "next race" was auto-selected (default False)
    """
    call = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "response_time_ms": response_time_ms,
        "response_size_bytes": response_size_bytes,
        "lang": lang,
        "tz": tz,
        "year": year,
        "round": round_num,
        "race_name": race_name,
        "is_auto_selected": 1 if is_auto_selected else 0,
    }
    _api_calls_buffer.append(call)


def get_and_clear_api_calls_buffer() -> list:
    """
    Get all buffered API calls and clear the buffer.

    Used by scheduler to flush calls to database.

    Returns:
        List of API call dictionaries
    """
    global _api_calls_buffer
    calls = _api_calls_buffer.copy()
    _api_calls_buffer = []
    return calls


@app.get("/api/stats")
async def get_stats():
    """Get API request statistics from database."""
    db = Database()
    stats = await db.get_api_calls_stats_24h()
    return {
        "requests": {
            "last_24h": stats["count_24h"],
            "avg_response_ms": stats["avg_response_ms"],
            "total_bytes_24h": stats["total_bytes_24h"],
        },
        "cache_size": len(_bmp_cache),
        "cache_max_size": _bmp_cache.maxsize,
    }


@app.get("/api/stats/history")
async def get_stats_history(limit: int = Query(default=168, le=720)):
    """
    Get historical request statistics.

    Args:
        limit: Maximum number of records (default 168 = 7 days, max 720 = 30 days)

    Returns:
        List of hourly statistics snapshots
    """

    db = Database()
    history = await db.get_request_stats_history(limit=limit)
    return {"history": history, "count": len(history)}


# Dependency injection for F1Service
def get_f1_service(
    tz: str = Query(default=None, description="Timezone for F1Service"),
) -> F1Service:
    """Provide F1Service instance for dependency injection."""
    return F1Service(timezone=tz)


@app.get("/api/races/{year}")
async def get_season_races(year: int, f1_service: F1Service = Depends(get_f1_service)):
    """
    Get all races for a given season.

    Args:
        year: Season year (e.g., 2025)

    Returns:
        List of races with basic info
    """
    races = await f1_service.get_season_races(year)
    return {"year": year, "races": races}


@app.get("/api/race/{year}/{round_num}")
async def get_race_detail(
    year: int, round_num: int, f1_service: F1Service = Depends(get_f1_service)
):
    """
    Get details for a specific race.

    Args:
        year: Season year
        round_num: Round number

    Returns:
        Race details with schedule
    """
    race = await f1_service.get_race_by_round(year, round_num)
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


def _get_cache_key(lang: str, year: int | None, round_num: int | None, tz: str | None) -> str:
    """Generate cache key for BMP image."""
    return f"{lang}:{year or 'next'}:{round_num or 'next'}:{tz or 'default'}"


def _convert_race_times_to_timezone(race_data: dict, target_tz_str: str) -> dict:
    """
    Convert race schedule times to a different timezone.

    The race_data contains ISO datetime strings that we can parse and convert.

    Args:
        race_data: Race data dictionary with schedule
        target_tz_str: Target timezone string (e.g., 'America/New_York')

    Returns:
        Race data with converted schedule times
    """
    from datetime import datetime

    try:
        target_tz = pytz.timezone(target_tz_str)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone {target_tz_str}, returning original data")
        return race_data

    # Deep copy to avoid modifying original
    import copy

    result = copy.deepcopy(race_data)

    # Convert schedule times
    schedule = result.get("schedule", [])
    for event in schedule:
        iso_str = event.get("datetime")
        if iso_str:
            try:
                # Parse ISO datetime string
                dt = datetime.fromisoformat(iso_str)
                # Convert to target timezone
                dt_local = dt.astimezone(target_tz)
                # Update both datetime and display_time
                event["datetime"] = dt_local.isoformat()
                event["display_time"] = dt_local.strftime("%a %H:%M")
            except (ValueError, TypeError) as e:
                logger.warning(f"Error converting time {iso_str}: {e}")

    # Update race_date to target timezone format
    if schedule:
        # Find the race event to update race_date
        for event in schedule:
            if event.get("name") == "Race":
                iso_str = event.get("datetime")
                if iso_str:
                    try:
                        dt = datetime.fromisoformat(iso_str)
                        result["race_date"] = dt.strftime("%d.%m.%Y")
                    except (ValueError, TypeError):
                        pass
                break

    # Update timezone field
    result["timezone"] = target_tz_str

    return result


def clear_bmp_cache() -> None:
    """Clear the BMP image cache. Called by scheduler after regeneration."""
    _bmp_cache.clear()
    logger.info("BMP cache cleared")


@app.get("/calendar.bmp")
async def get_calendar_bmp(
    request: Request,
    lang: str = Query(default="en", description="Language code (cs, en)"),
    year: int = Query(default=None, description="Season year (e.g., 2025)"),
    round: int = Query(default=None, description="Round number"),
    tz: str = Query(default=None, description="Timezone"),
    f1_service: F1Service = Depends(get_f1_service),
):
    """
    Generate F1 calendar BMP image.

    Serves cached/pre-generated image if available, otherwise generates on-the-fly.

    Args:
        request: FastAPI Request object (for User-Agent header)
        lang: Language code for translations (cs for Czech, en for English)
        year: Optional season year for specific race
        round: Optional round number for specific race
        tz: Optional timezone for schedule times
        f1_service: F1Service instance (injected, accepts tz query param for timezone)

    Returns:
        800x480 1-bit BMP image
    """
    start_time = time.time()

    # Extract headers for analytics
    user_agent = request.headers.get("User-Agent")
    referrer = request.headers.get("Referer", "")

    # Helper function for tracking calendar requests
    async def track_calendar_analytics():
        """Track both pageview and custom event for calendar BMP requests."""
        # Build URL with query parameters
        query_params = {"lang": lang}
        if tz:
            query_params["tz"] = tz
        if year is not None:
            query_params["year"] = str(year)
        if round is not None:
            query_params["round"] = str(round)

        url = f"/calendar.bmp?{urlencode(query_params)}"

        # Track pageview
        await track_pageview(
            url=url,
            title=f"Calendar BMP - {lang}",
            lang=lang,
            user_agent=user_agent,
            referrer=referrer,
        )

        # Track custom event with detailed data
        await track_event(
            url="/calendar.bmp",
            event_name="calendar_download",
            lang=lang,
            user_agent=user_agent,
            event_data={
                "language": lang,
                "timezone": tz or "default",
                "year": year,
                "round": round,
                "source": "direct" if not referrer else "referral",
            },
        )

    try:
        # Validate language
        if lang not in ["cs", "en"]:
            lang = config.DEFAULT_LANG

        # Determine if this is auto-selected (next race) or manual selection
        is_auto_selected = year is None and round is None

        # Get race info early for statistics (fast - reads from static JSON)
        race_info_for_stats = None
        actual_year = year
        actual_round = round
        actual_race_name = None

        if year and round:
            # Specific race requested
            all_races = f1_service.get_all_races_from_static(year)
            for race in all_races:
                if int(race.get("round", 0)) == round:
                    race_info_for_stats = race
                    actual_race_name = race.get("race_name", "Unknown")
                    break
        else:
            # Next race (auto-selected)
            race_info_for_stats = f1_service.get_next_race_from_static()
            if race_info_for_stats:
                actual_year = int(race_info_for_stats.get("season", 0)) or None
                actual_round = int(race_info_for_stats.get("round", 0)) or None
                actual_race_name = race_info_for_stats.get("race_name", "Next Race")

        # Check in-memory cache first
        cache_key = _get_cache_key(lang, year, round, tz)
        cached_bmp = _bmp_cache.get(cache_key)
        if cached_bmp is not None:
            logger.debug(f"Cache hit for {cache_key}")
            _record_api_call(
                "/calendar.bmp",
                (time.time() - start_time) * 1000,
                len(cached_bmp),
                lang,
                tz,
                actual_year,
                actual_round,
                actual_race_name,
                is_auto_selected,
            )
            await track_calendar_analytics()
            return StreamingResponse(
                BytesIO(cached_bmp),
                media_type="image/bmp",
                headers={
                    "Content-Disposition": 'inline; filename="calendar.bmp"',
                    "Cache-Control": "public, max-age=3600",
                    "X-Cache": "HIT",
                },
            )

        logger.info(f"Cache miss for {cache_key}, generating...")

        # Try to serve pre-generated image first (only for next race, not specific year/round)
        if not year and not round:
            # Build image key based on lang and optional tz
            target_tz_for_key = tz or config.DEFAULT_TIMEZONE
            if target_tz_for_key != config.DEFAULT_TIMEZONE:
                tz_safe = target_tz_for_key.replace("/", "_")
                image_key = f"calendar_{lang}_{tz_safe}"
            else:
                image_key = f"calendar_{lang}"

            image_path = Path(config.IMAGES_PATH) / f"{image_key}.bmp"

            if image_path.exists():
                logger.info(f"Serving pre-generated image: {image_path}")
                # Read and cache the file
                bmp_data = image_path.read_bytes()
                _bmp_cache[cache_key] = bmp_data
                _record_api_call(
                    "/calendar.bmp",
                    (time.time() - start_time) * 1000,
                    len(bmp_data),
                    lang,
                    tz,
                    actual_year,
                    actual_round,
                    actual_race_name,
                    is_auto_selected,
                )
                await track_calendar_analytics()
                return FileResponse(
                    path=str(image_path),
                    media_type="image/bmp",
                    filename="calendar.bmp",
                    headers={"Cache-Control": "public, max-age=3600", "X-Cache": "MISS"},
                )

        # Generate on-the-fly for specific race or when no pre-generated image exists
        logger.info(f"Generating image on-the-fly (year={year}, round={round}, tz={tz})")

        # Get translator
        translator = get_translator(lang)

        # Determine target timezone
        target_tz = tz or config.DEFAULT_TIMEZONE

        # Fetch race data from static JSON files (no API calls)
        race_data = None

        if year and round:
            # Get specific race from static data
            all_races = f1_service.get_all_races_from_static(year)
            for race in all_races:
                if int(race.get("round", 0)) == round:
                    race_data = race
                    break
            if race_data:
                logger.debug(f"Using static race data for {year}/{round}")
            else:
                logger.warning(f"Race {year}/{round} not found in static data")
        else:
            # Get next race from static data
            race_data = f1_service.get_next_race_from_static()
            if race_data:
                logger.debug("Using static next race data")

        # Convert timezone if needed
        if race_data:
            cached_tz = race_data.get("timezone", config.DEFAULT_TIMEZONE)
            if cached_tz != target_tz:
                logger.debug(f"Converting times from {cached_tz} to {target_tz}")
                race_data = _convert_race_times_to_timezone(race_data, target_tz)

        if not race_data:
            logger.error("Failed to get race data from static files")
            # Return error image (don't cache errors)
            renderer = Renderer(translator)
            bmp_data = renderer.render_error("Failed to fetch race data")
        else:
            # Get historical data from static JSON (no API calls)
            circuit_id = race_data.get("circuit", {}).get("circuitId", "")
            historical_data = None

            if circuit_id:
                historical_data = F1Service.get_historical_from_static(circuit_id)
                if historical_data:
                    logger.debug(
                        f"Historical data for {circuit_id}: season={historical_data.season}, "
                        f"new_track={historical_data.is_new_track}"
                    )

            # Render the calendar with historical data
            renderer = Renderer(translator)
            bmp_data = renderer.render_calendar(race_data, historical_data)

            # Cache the result
            _bmp_cache[cache_key] = bmp_data

        # Update race info from actual rendered data (may have been fetched fresh)
        if race_data:
            actual_year = int(race_data.get("season", 0)) or actual_year
            actual_round = int(race_data.get("round", 0)) or actual_round
            actual_race_name = race_data.get("race_name", actual_race_name)

        # Record request with response time and size
        _record_api_call(
            "/calendar.bmp",
            (time.time() - start_time) * 1000,
            len(bmp_data),
            lang,
            tz,
            actual_year,
            actual_round,
            actual_race_name,
            is_auto_selected,
        )

        # Track analytics asynchronously
        await track_calendar_analytics()

        # Return BMP image
        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="calendar.bmp"',
                "Cache-Control": "public, max-age=3600",
                "X-Cache": "MISS",
            },
        )

    except Exception as e:
        logger.error(f"Error generating calendar: {str(e)}", exc_info=True)
        sentry_sdk.capture_exception(e)

        # Return error image (don't cache errors)
        translator = get_translator(lang)
        renderer = Renderer(translator)
        bmp_data = renderer.render_error(str(e))

        # Record request with response time and size (even for errors)
        # Note: is_auto_selected may not be defined if error occurred early
        auto_selected = year is None and round is None
        _record_api_call(
            "/calendar.bmp",
            (time.time() - start_time) * 1000,
            len(bmp_data),
            lang,
            tz,
            year,  # Use original params for errors (race_info may not exist)
            round,
            None,  # No race name for errors
            auto_selected,
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={"Content-Disposition": 'inline; filename="calendar.bmp"'},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.APP_HOST,
        port=config.APP_PORT,
        reload=config.DEBUG,
    )
