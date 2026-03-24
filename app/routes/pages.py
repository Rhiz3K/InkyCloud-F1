"""HTML page routes with language prefix support."""

from __future__ import annotations

import logging
from pathlib import Path

import markdown
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import VALID_LANGUAGES, config
from app.services.analytics import track_pageview
from app.services.database import Database
from app.services.version_service import get_cached_version, refresh_version_info
from app.web.api_docs import build_api_docs_context
from app.web.templates import calc_percent, get_template_context, lang_url, templates

logger = logging.getLogger(__name__)

router = APIRouter()
_HTML_ROUTE_METHODS = ("GET", "HEAD")

_DISPLAY_TYPE_LABELS = {
    "1bit": "1-BIT",
    "spectra6": "SPECTRA 6",
    "bwr": "B/W/R",
    "bwry": "B/W/R/Y",
}


def _strip_empty_unreleased_section(changelog_text: str) -> str:
    """Remove an empty Unreleased heading before the first version section."""
    unreleased_heading = "## [Unreleased]"
    unreleased_start = changelog_text.find(unreleased_heading)
    if unreleased_start == -1:
        return changelog_text

    first_version_start = changelog_text.find("## [", unreleased_start + len(unreleased_heading))
    if first_version_start == -1:
        return changelog_text

    unreleased_body = changelog_text[
        unreleased_start + len(unreleased_heading) : first_version_start
    ]
    if unreleased_body.strip():
        return changelog_text

    return changelog_text[:unreleased_start] + changelog_text[first_version_start:]


def _enrich_display_type_stats(stats: dict) -> None:
    """Add display labels for stats template rendering."""
    for key in ("display_types", "teams_display_types"):
        display_types = stats.get(key, [])
        for display_stat in display_types:
            display_key = display_stat.get("display_type") or ""
            display_stat["display_label"] = _DISPLAY_TYPE_LABELS.get(
                display_key, display_key.upper()
            )


def _head_ok() -> HTMLResponse:
    """Return a lightweight HTML response for HEAD requests."""
    return HTMLResponse(content="", media_type="text/html")


# ============================================================================
# HOME PAGE
# ============================================================================


async def _home_handler(request: Request, ui_lang: str) -> HTMLResponse:
    """Render home page."""
    url = lang_url("/", ui_lang)
    await track_pageview(
        url=url,
        title="F1 E-Ink Calendar",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = get_template_context(request, ui_lang)
    context["active_page"] = "home"
    context["screen_types"] = [
        {
            "id": "calendar",
            "name_key": "screen_calendar_name",
            "desc_key": "screen_calendar_desc",
        },
        {
            "id": "teams",
            "name_key": "screen_teams_name",
            "desc_key": "screen_teams_desc",
        },
    ]

    return templates.TemplateResponse(request, "home.html", context)


@router.api_route("/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def root(request: Request, lang: str = Query(default=None)):
    """Home page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return RedirectResponse(url=f"/{lang}/", status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _home_handler(request, "en")


@router.api_route("/{lang_prefix}/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def root_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Home page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse(url="/", status_code=301)
    if lang is not None:
        return RedirectResponse(url=f"/{lang_prefix}/", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _home_handler(request, lang_prefix)


# ============================================================================
# CONFIGURE PAGE
# ============================================================================


async def _configure_handler(request: Request, screen_type: str, ui_lang: str) -> HTMLResponse:
    """Render configure page."""
    if screen_type not in ["calendar", "teams"]:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    url = lang_url(f"/configure/{screen_type}", ui_lang)
    await track_pageview(
        url=url,
        title=f"Configure {screen_type.title()}",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = get_template_context(request, ui_lang)
    context["active_page"] = "configure"
    context["screen_type"] = screen_type
    context["default_timezone"] = config.DEFAULT_TIMEZONE

    return templates.TemplateResponse(request, "configure.html", context)


@router.api_route(
    "/configure/{screen_type}", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def configure_screen(request: Request, screen_type: str, lang: str = Query(default=None)):
    """Configure page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return RedirectResponse(url=f"/{lang}/configure/{screen_type}", status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url=f"/configure/{screen_type}", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _configure_handler(request, screen_type, "en")


@router.api_route(
    "/{lang_prefix}/configure/{screen_type}",
    methods=_HTML_ROUTE_METHODS,
    response_class=HTMLResponse,
)
async def configure_screen_lang(
    request: Request, lang_prefix: str, screen_type: str, lang: str = Query(default=None)
):
    """Configure page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse(url=f"/configure/{screen_type}", status_code=301)
    if lang is not None:
        return RedirectResponse(url=f"/{lang_prefix}/configure/{screen_type}", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _configure_handler(request, screen_type, lang_prefix)


# ============================================================================
# PRIVACY PAGE
# ============================================================================


async def _privacy_handler(request: Request, ui_lang: str) -> HTMLResponse:
    """Render privacy page."""
    url = lang_url("/privacy", ui_lang)
    await track_pageview(
        url=url,
        title="Privacy Policy",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = get_template_context(request, ui_lang)
    context["active_page"] = "privacy"
    return templates.TemplateResponse(request, "privacy.html", context)


@router.api_route("/privacy", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def privacy(request: Request, lang: str = Query(default=None)):
    """Privacy page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return RedirectResponse(url=f"/{lang}/privacy", status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/privacy", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _privacy_handler(request, "en")


@router.api_route(
    "/{lang_prefix}/privacy", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def privacy_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Privacy page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse(url="/privacy", status_code=301)
    if lang is not None:
        return RedirectResponse(url=f"/{lang_prefix}/privacy", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _privacy_handler(request, lang_prefix)


# ============================================================================
# CHANGELOG PAGE
# ============================================================================


async def _changelog_handler(request: Request, ui_lang: str) -> HTMLResponse:
    """Render changelog page."""
    url = lang_url("/changelog", ui_lang)
    await track_pageview(
        url=url,
        title="Changelog",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    changelog_path = Path(__file__).resolve().parents[2] / "CHANGELOG.md"
    if changelog_path.exists():
        changelog_content = changelog_path.read_text(encoding="utf-8")
        changelog_content = _strip_empty_unreleased_section(changelog_content)
        changelog_html = markdown.markdown(
            changelog_content,
            extensions=["extra", "toc", "md_in_html"],
        )
    else:
        changelog_html = "<p>Changelog not found.</p>"

    version_info = get_cached_version()
    if version_info is None:
        try:
            version_info = await refresh_version_info()
        except Exception as exc:
            logger.warning("Failed to fetch version info: %s", exc)

    context = get_template_context(request, ui_lang)
    context["active_page"] = "changelog"
    context["changelog_html"] = changelog_html
    context["version_info"] = version_info

    return templates.TemplateResponse(request, "changelog.html", context)


@router.api_route("/changelog", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def changelog(request: Request, lang: str = Query(default=None)):
    """Changelog page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return RedirectResponse(url=f"/{lang}/changelog", status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/changelog", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _changelog_handler(request, "en")


@router.api_route(
    "/{lang_prefix}/changelog", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def changelog_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Changelog page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse(url="/changelog", status_code=301)
    if lang is not None:
        return RedirectResponse(url=f"/{lang_prefix}/changelog", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _changelog_handler(request, lang_prefix)


# ============================================================================
# API DOCS PAGE
# ============================================================================


async def _api_docs_handler(request: Request, ui_lang: str) -> HTMLResponse:
    """Render API docs page."""
    url = lang_url("/api/docs/html", ui_lang)
    await track_pageview(
        url=url,
        title="API Documentation",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = get_template_context(request, ui_lang)
    context["active_page"] = "api"
    context.update(build_api_docs_context(ui_lang))

    return templates.TemplateResponse(request, "api_docs.html", context)


@router.api_route("/api/docs/html", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def api_docs_html(request: Request, lang: str = Query(default=None)):
    """API docs page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return RedirectResponse(url=f"/{lang}/api/docs/html", status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/api/docs/html", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _api_docs_handler(request, "en")


@router.api_route(
    "/{lang_prefix}/api/docs/html",
    methods=_HTML_ROUTE_METHODS,
    response_class=HTMLResponse,
)
async def api_docs_html_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """API docs page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse(url="/api/docs/html", status_code=301)
    if lang is not None:
        return RedirectResponse(url=f"/{lang_prefix}/api/docs/html", status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _api_docs_handler(request, lang_prefix)


# ============================================================================
# STATS PAGE
# ============================================================================


async def _stats_handler(request: Request, time_range: str, ui_lang: str) -> HTMLResponse:
    """Render stats page."""
    hours_map = {"1h": 1, "24h": 24, "7d": 168, "30d": 720, "365d": 8760}
    hours = hours_map.get(time_range, 24)

    db = Database()
    stats = await db.get_stats_for_range(hours)
    _enrich_display_type_stats(stats)
    perf_stats = await db.get_perf_stats(hours)

    base_url = lang_url("/stats", ui_lang)
    url = f"{base_url}?range={time_range}" if time_range != "24h" else base_url
    await track_pageview(
        url=url,
        title="Statistics Dashboard",
        lang=ui_lang,
        user_agent=request.headers.get("User-Agent"),
        referrer=request.headers.get("Referer", ""),
    )

    context = get_template_context(request, ui_lang)
    context["active_page"] = "stats"
    context["stats"] = stats
    context["perf_stats"] = perf_stats
    context["selected_range"] = time_range
    context["range_label"] = context["t"].get(
        f"stats_{time_range}", context["t"].get("stats_24h", "Last 24 Hours")
    )

    max_response = stats.get("max_response_ms", 1) or 1
    context["min_pct"] = calc_percent(stats.get("min_response_ms", 0), max_response)
    context["avg_pct"] = calc_percent(int(stats.get("avg_response_ms", 0)), max_response)

    return templates.TemplateResponse(request, "stats.html", context)


@router.api_route("/stats", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def stats_dashboard(
    request: Request,
    time_range: str = Query(default="24h", pattern="^(1h|24h|7d|30d|365d)$", alias="range"),
    lang: str = Query(default=None),
):
    """Stats page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        redirect_url = f"/{lang}/stats"
        if time_range != "24h":
            redirect_url += f"?range={time_range}"
        return RedirectResponse(url=redirect_url, status_code=301)
    if lang is not None and lang not in VALID_LANGUAGES:
        redirect_url = "/stats"
        if time_range != "24h":
            redirect_url += f"?range={time_range}"
        return RedirectResponse(url=redirect_url, status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _stats_handler(request, time_range, "en")


@router.api_route("/{lang_prefix}/stats", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def stats_dashboard_lang(
    request: Request,
    lang_prefix: str,
    time_range: str = Query(default="24h", pattern="^(1h|24h|7d|30d|365d)$", alias="range"),
    lang: str = Query(default=None),
):
    """Stats page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        redirect_url = "/stats"
        if time_range != "24h":
            redirect_url += f"?range={time_range}"
        return RedirectResponse(url=redirect_url, status_code=301)
    if lang is not None:
        redirect_url = f"/{lang_prefix}/stats"
        if time_range != "24h":
            redirect_url += f"?range={time_range}"
        return RedirectResponse(url=redirect_url, status_code=301)
    if request.method == "HEAD":
        return _head_ok()
    return await _stats_handler(request, time_range, lang_prefix)
