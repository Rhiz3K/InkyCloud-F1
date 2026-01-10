"""HTML page routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import VALID_LANGUAGES, config
from app.services.analytics import track_pageview
from app.services.database import Database
from app.services.version_service import get_cached_version, refresh_version_info
from app.web.api_docs import build_api_docs_context
from app.web.templates import calc_percent, get_template_context, resolve_ui_language, templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def root(request: Request, lang: str = Query(default=None)):
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    url = f"/?lang={ui_lang}" if ui_lang != "en" else "/"
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


@router.get("/configure/{screen_type}", response_class=HTMLResponse)
async def configure_screen(request: Request, screen_type: str, lang: str = Query(default=None)):
    if screen_type not in ["calendar", "teams"]:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url=f"/configure/{screen_type}", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    url = (
        f"/configure/{screen_type}?lang={ui_lang}"
        if ui_lang != "en"
        else f"/configure/{screen_type}"
    )
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


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request, lang: str = Query(default=None)):
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/privacy", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    url = f"/privacy?lang={ui_lang}" if ui_lang != "en" else "/privacy"
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


@router.get("/changelog", response_class=HTMLResponse)
async def changelog(request: Request, lang: str = Query(default=None)):
    import markdown

    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/changelog", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    url = f"/changelog?lang={ui_lang}" if ui_lang != "en" else "/changelog"
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


@router.get("/api/docs/html", response_class=HTMLResponse)
async def api_docs_html(request: Request, lang: str = Query(default=None)):
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url="/api/docs/html", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    url = f"/api/docs/html?lang={ui_lang}" if ui_lang != "en" else "/api/docs/html"
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


@router.get("/stats", response_class=HTMLResponse)
async def stats_dashboard(
    request: Request,
    time_range: str = Query(default="24h", pattern="^(1h|24h|7d|30d|365d)$", alias="range"),
    lang: str = Query(default=None),
):
    if lang is not None and lang not in VALID_LANGUAGES:
        return RedirectResponse(url=f"/stats?range={time_range}", status_code=301)

    ui_lang = resolve_ui_language(request, lang)

    hours_map = {"1h": 1, "24h": 24, "7d": 168, "30d": 720, "365d": 8760}
    hours = hours_map.get(time_range, 24)

    db = Database()
    stats = await db.get_stats_for_range(hours)
    perf_stats = await db.get_perf_stats(hours)
    perf_by_page = await db.get_perf_stats_by_page(hours)
    perf_trends = await db.get_perf_trends(hours)

    url = (
        f"/stats?range={time_range}&lang={ui_lang}"
        if ui_lang != "en"
        else f"/stats?range={time_range}"
    )
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
    context["perf_by_page"] = perf_by_page
    context["perf_trends"] = perf_trends
    context["selected_range"] = time_range

    range_labels = {
        "1h": "Last Hour",
        "24h": "Last 24 Hours",
        "7d": "Last 7 Days",
        "30d": "Last 30 Days",
        "365d": "Last 365 Days",
    }
    context["range_label"] = range_labels.get(time_range, "Last 24 Hours")

    max_response = stats.get("max_response_ms", 1) or 1
    context["min_pct"] = calc_percent(stats.get("min_response_ms", 0), max_response)
    context["avg_pct"] = calc_percent(int(stats.get("avg_response_ms", 0)), max_response)

    return templates.TemplateResponse(request, "stats.html", context)
