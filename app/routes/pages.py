"""HTML page routes with language prefix support."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import markdown
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import VALID_LANGUAGES, config
from app.paths import CHANGELOG_PATH
from app.services.analytics import track_pageview
from app.services.database import Database
from app.services.teams_service import get_available_teams_years
from app.services.version_service import get_cached_version, refresh_version_info
from app.utils.f1_season import get_current_f1_season
from app.utils.rate_limit import enforce_rate_limit
from app.utils.timezones import TIMEZONE_ALIASES
from app.web.api_docs import build_api_docs_context
from app.web.templates import calc_percent, get_template_context, lang_url, templates

logger = logging.getLogger(__name__)

router = APIRouter()
_HTML_ROUTE_METHODS = ["GET", "HEAD"]

# Elements that should never survive in rendered changelog HTML.
_UNSAFE_HTML_TAGS = ("script", "style", "iframe", "object", "embed", "link", "meta", "base")
_URI_HTML_ATTRIBUTES = {"href", "src", "xlink:href", "action", "formaction", "poster"}


def _sanitize_rendered_html(html: str) -> str:
    """Strip script/style/event-handler/javascript: vectors from rendered markdown HTML.

    The changelog is rendered with raw-HTML-passthrough markdown extensions and emitted via
    ``| safe``. CHANGELOG.md is maintainer-controlled, so this is defense-in-depth: it keeps a
    future where less-trusted content reaches that file from becoming stored XSS.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    # Snapshot the result sets with list() before mutating the tree (also avoids a pylint
    # E1133 false positive: it can't infer that bs4 result sets are iterable).
    for tag in list(soup(_UNSAFE_HTML_TAGS)):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        for attr, value in list(tag.attrs.items()):
            lowered = attr.lower()
            if lowered.startswith("on") or lowered == "style":
                del tag.attrs[attr]
            elif lowered in _URI_HTML_ATTRIBUTES and isinstance(value, str):
                # Strip control/whitespace chars first so "java\tscript:" / "java\nscript:"
                # can't slip through, and block data:/vbscript: URI vectors as well.
                cleaned = "".join(ch for ch in value if ord(ch) > 32).lower()
                if cleaned.startswith(("javascript:", "data:", "vbscript:")):
                    del tag.attrs[attr]
    return str(soup)


_DISPLAY_TYPE_LABELS = {
    "1bit": "1-BIT",
    "spectra6": "SPECTRA 6",
    "bwr": "B/W/R",
    "bwry": "B/W/R/Y",
}
_VALID_SCREEN_TYPES = {"calendar", "teams"}
_STATS_TIME_RANGES = ("1h", "24h", "7d", "30d", "365d")
_STATIC_CANONICAL_PATHS = {
    "privacy": "/privacy",
    "changelog": "/changelog",
    "api_docs": "/api/docs/html",
    "stats": "/stats",
}
_LOCALIZED_ROOT_PATHS = {lang: "/" if lang == "en" else f"/{lang}/" for lang in VALID_LANGUAGES}
_LOCALIZED_CANONICAL_PATHS = {
    page_key: {
        lang: canonical_path if lang == "en" else f"/{lang}{canonical_path}"
        for lang in VALID_LANGUAGES
    }
    for page_key, canonical_path in _STATIC_CANONICAL_PATHS.items()
}
_LOCALIZED_CONFIGURE_PATHS = {
    lang: {
        screen_type: (
            f"/configure/{screen_type}" if lang == "en" else f"/{lang}/configure/{screen_type}"
        )
        for screen_type in sorted(_VALID_SCREEN_TYPES)
    }
    for lang in VALID_LANGUAGES
}
_LOCALIZED_STATS_PATHS = {
    lang: {
        time_range: (
            _LOCALIZED_CANONICAL_PATHS["stats"][lang]
            if time_range == "24h"
            else f"{_LOCALIZED_CANONICAL_PATHS['stats'][lang]}?range={time_range}"
        )
        for time_range in _STATS_TIME_RANGES
    }
    for lang in VALID_LANGUAGES
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


@lru_cache(maxsize=4)
def _render_changelog_file(path_value: str, mtime_ns: int) -> str:
    """Read and render a specific changelog file version."""
    del mtime_ns
    changelog_content = Path(path_value).read_text(encoding="utf-8")
    changelog_content = _strip_empty_unreleased_section(changelog_content)
    return _sanitize_rendered_html(
        markdown.markdown(
            changelog_content,
            extensions=["extra", "toc", "md_in_html"],
        )
    )


def _load_changelog_html(changelog_path: Path) -> str:
    """Load cached changelog HTML without doing file or parser work on the event loop."""
    try:
        mtime_ns = changelog_path.stat().st_mtime_ns
    except FileNotFoundError:
        return "<p>Changelog not found.</p>"
    return _render_changelog_file(str(changelog_path), mtime_ns)


def _empty_stats() -> dict:
    """Return the complete template contract for an unavailable stats database."""
    return {
        "total_requests": 0,
        "avg_response_ms": 0,
        "min_response_ms": 0,
        "max_response_ms": 0,
        "total_bytes": 0,
        "endpoints": [],
        "languages": [],
        "display_types": [],
        "teams_display_types": [],
        "races": [],
        "timezones": [],
    }


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


def _redirect_path(
    request: Request, target_path: str, *, preserve_query: bool = True
) -> RedirectResponse:
    """Return a permanent redirect using a relative path to avoid scheme downgrades."""
    if any(ord(char) < 32 for char in target_path):
        raise ValueError(f"Redirect target contains control characters: {target_path!r}")

    target_parts = urlsplit(target_path)
    decoded_path = unquote(target_parts.path)
    if (
        target_parts.scheme
        or target_parts.netloc
        or not target_path.startswith("/")
        or target_path.startswith("//")
        or "\\" in decoded_path
        or decoded_path.startswith("//")
    ):
        raise ValueError(f"Redirect target must stay on-site: {target_path}")

    if preserve_query and request.url.query:
        merged_query = parse_qsl(target_parts.query, keep_blank_values=True)
        merged_query.extend(parse_qsl(request.url.query, keep_blank_values=True))
        target_path = urlunsplit(
            (
                "",
                "",
                target_parts.path,
                urlencode(merged_query, doseq=True),
                target_parts.fragment,
            )
        )

    return RedirectResponse(url=target_path, status_code=301)


def _canonical_root_path(lang_prefix: str) -> str:
    """Return the canonical root path for a validated UI language."""
    return _LOCALIZED_ROOT_PATHS[lang_prefix]


def _canonical_page_path(page_key: str, lang_prefix: str) -> str:
    """Return the canonical localized path for a static public page."""
    return _LOCALIZED_CANONICAL_PATHS[page_key][lang_prefix]


def _validate_screen_type(screen_type: str) -> str:
    """Return a validated configure screen type or raise a localized 404."""
    if screen_type not in _VALID_SCREEN_TYPES:
        raise HTTPException(status_code=404, detail="Unknown screen type")
    return screen_type


def _canonical_configure_path(lang_prefix: str, screen_type: str) -> str:
    """Return the canonical localized configure path for a validated screen type."""
    return _LOCALIZED_CONFIGURE_PATHS[lang_prefix][_validate_screen_type(screen_type)]


def _canonical_stats_path(lang_prefix: str, time_range: str) -> str:
    """Return the canonical localized stats path for a validated range."""
    return _LOCALIZED_STATS_PATHS[lang_prefix][time_range]


def _redirect_localized_public_page(
    request: Request, lang_prefix: str, page_key: str
) -> RedirectResponse:
    """Redirect a localized public URL to its canonical path or 404 for unknown languages."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    return _redirect_path(request, _canonical_page_path(page_key, lang_prefix))


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
        return _redirect_path(request, _canonical_root_path(lang), preserve_query=False)
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(request, "/", preserve_query=False)
    if request.method == "HEAD":
        return _head_ok()
    return await _home_handler(request, "en")


@router.api_route("/{lang_prefix}/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def root_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Home page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return _redirect_path(request, "/", preserve_query=False)
    if lang is not None:
        return _redirect_path(request, _canonical_root_path(lang_prefix), preserve_query=False)
    if request.method == "HEAD":
        return _head_ok()
    return await _home_handler(request, lang_prefix)


# ============================================================================
# CONFIGURE PAGE
# ============================================================================


async def _configure_handler(request: Request, screen_type: str, ui_lang: str) -> HTMLResponse:
    """Render configure page."""
    if screen_type not in _VALID_SCREEN_TYPES:
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
    context["timezone_aliases"] = TIMEZONE_ALIASES
    current_teams_season = get_current_f1_season()
    context["current_teams_season"] = current_teams_season
    context["available_teams_seasons"] = sorted(
        {current_teams_season, *get_available_teams_years()}, reverse=True
    )

    return templates.TemplateResponse(request, "configure.html", context)


@router.api_route(
    "/configure/{screen_type}/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def configure_screen_slash_redirect(request: Request, screen_type: str):
    """Normalize configure page URLs to the canonical no-trailing-slash form."""
    if screen_type not in _VALID_SCREEN_TYPES:
        raise HTTPException(status_code=404, detail="Unknown screen type")
    return _redirect_path(request, _canonical_configure_path("en", screen_type))


@router.api_route(
    "/configure/{screen_type}", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def configure_screen(request: Request, screen_type: str, lang: str = Query(default=None)):
    """Configure page (English default)."""
    screen_type = _validate_screen_type(screen_type)
    if lang in VALID_LANGUAGES and lang != "en":
        return _redirect_path(
            request, _canonical_configure_path(lang, screen_type), preserve_query=False
        )
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(
            request, _canonical_configure_path("en", screen_type), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _configure_handler(request, screen_type, "en")


@router.api_route(
    "/{lang_prefix}/configure/{screen_type}/",
    methods=_HTML_ROUTE_METHODS,
    response_class=HTMLResponse,
)
async def configure_screen_lang_slash_redirect(
    request: Request, lang_prefix: str, screen_type: str
):
    """Normalize localized configure URLs to the canonical no-trailing-slash form."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if screen_type not in _VALID_SCREEN_TYPES:
        raise HTTPException(status_code=404, detail="Unknown screen type")
    if lang_prefix == "en":
        return _redirect_path(request, _canonical_configure_path("en", screen_type))
    return _redirect_path(request, _canonical_configure_path(lang_prefix, screen_type))


@router.api_route(
    "/{lang_prefix}/configure/{screen_type}",
    methods=_HTML_ROUTE_METHODS,
    response_class=HTMLResponse,
)
async def configure_screen_lang(
    request: Request, lang_prefix: str, screen_type: str, lang: str = Query(default=None)
):
    """Configure page with language prefix."""
    screen_type = _validate_screen_type(screen_type)
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return _redirect_path(
            request, _canonical_configure_path("en", screen_type), preserve_query=False
        )
    if lang is not None:
        return _redirect_path(
            request, _canonical_configure_path(lang_prefix, screen_type), preserve_query=False
        )
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
        return _redirect_path(request, _canonical_page_path("privacy", lang), preserve_query=False)
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(request, _canonical_page_path("privacy", "en"), preserve_query=False)
    if request.method == "HEAD":
        return _head_ok()
    return await _privacy_handler(request, "en")


@router.api_route("/privacy/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def privacy_slash_redirect(request: Request):
    """Normalize privacy URLs to the canonical no-trailing-slash form."""
    return _redirect_path(request, _canonical_page_path("privacy", "en"))


@router.api_route(
    "/{lang_prefix}/privacy", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def privacy_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Privacy page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return _redirect_path(request, _canonical_page_path("privacy", "en"), preserve_query=False)
    if lang is not None:
        return _redirect_path(
            request, _canonical_page_path("privacy", lang_prefix), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _privacy_handler(request, lang_prefix)


@router.api_route(
    "/{lang_prefix}/privacy/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def privacy_lang_slash_redirect(request: Request, lang_prefix: str):
    """Normalize localized privacy URLs to the canonical no-trailing-slash form."""
    return _redirect_localized_public_page(request, lang_prefix, "privacy")


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

    changelog_html = await asyncio.to_thread(_load_changelog_html, CHANGELOG_PATH)

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
        return _redirect_path(
            request, _canonical_page_path("changelog", lang), preserve_query=False
        )
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(
            request, _canonical_page_path("changelog", "en"), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _changelog_handler(request, "en")


@router.api_route("/changelog/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def changelog_slash_redirect(request: Request):
    """Normalize changelog URLs to the canonical no-trailing-slash form."""
    return _redirect_path(request, _canonical_page_path("changelog", "en"))


@router.api_route(
    "/{lang_prefix}/changelog", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def changelog_lang(request: Request, lang_prefix: str, lang: str = Query(default=None)):
    """Changelog page with language prefix."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return _redirect_path(
            request, _canonical_page_path("changelog", "en"), preserve_query=False
        )
    if lang is not None:
        return _redirect_path(
            request, _canonical_page_path("changelog", lang_prefix), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _changelog_handler(request, lang_prefix)


@router.api_route(
    "/{lang_prefix}/changelog/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse
)
async def changelog_lang_slash_redirect(request: Request, lang_prefix: str):
    """Normalize localized changelog URLs to the canonical no-trailing-slash form."""
    return _redirect_localized_public_page(request, lang_prefix, "changelog")


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
    context.update(build_api_docs_context(ui_lang, str(config.SITE_URL).rstrip("/")))

    return templates.TemplateResponse(request, "api_docs.html", context)


@router.api_route("/api/docs/html", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def api_docs_html(request: Request, lang: str = Query(default=None)):
    """API docs page (English default)."""
    if lang in VALID_LANGUAGES and lang != "en":
        return _redirect_path(request, _canonical_page_path("api_docs", lang), preserve_query=False)
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(request, _canonical_page_path("api_docs", "en"), preserve_query=False)
    if request.method == "HEAD":
        return _head_ok()
    return await _api_docs_handler(request, "en")


@router.api_route("/api/docs/html/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def api_docs_html_slash_redirect(request: Request):
    """Normalize API docs URLs to the canonical no-trailing-slash form."""
    return _redirect_path(request, _canonical_page_path("api_docs", "en"))


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
        return _redirect_path(request, _canonical_page_path("api_docs", "en"), preserve_query=False)
    if lang is not None:
        return _redirect_path(
            request, _canonical_page_path("api_docs", lang_prefix), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _api_docs_handler(request, lang_prefix)


@router.api_route(
    "/{lang_prefix}/api/docs/html/",
    methods=_HTML_ROUTE_METHODS,
    response_class=HTMLResponse,
)
async def api_docs_html_lang_slash_redirect(request: Request, lang_prefix: str):
    """Normalize localized API docs URLs to the canonical no-trailing-slash form."""
    return _redirect_localized_public_page(request, lang_prefix, "api_docs")


# ============================================================================
# STATS PAGE
# ============================================================================


async def _stats_handler(request: Request, time_range: str, ui_lang: str) -> HTMLResponse:
    """Render stats page."""
    enforce_rate_limit(request, bucket="stats_read", limit=config.STATS_RATE_LIMIT_PER_MINUTE)
    hours_map = {"1h": 1, "24h": 24, "7d": 168, "30d": 720, "365d": 8760}
    hours = hours_map.get(time_range, 24)

    db: Database | None = None
    try:
        db = Database()
        stats = await db.get_stats_for_range(hours)
        _enrich_display_type_stats(stats)
        perf_stats = await db.get_perf_stats(hours)
    except Exception as exc:
        logger.error("Failed to load statistics dashboard data: %s", exc, exc_info=True)
        stats = _empty_stats()
        perf_stats = {"sample_count": 0}
    finally:
        if db is not None:
            await db.close()

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
        return _redirect_path(
            request, _canonical_stats_path(lang, time_range), preserve_query=False
        )
    if lang is not None and lang not in VALID_LANGUAGES:
        return _redirect_path(
            request, _canonical_stats_path("en", time_range), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _stats_handler(request, time_range, "en")


@router.api_route("/stats/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def stats_dashboard_slash_redirect(request: Request):
    """Normalize stats URLs to the canonical no-trailing-slash form."""
    return _redirect_path(request, _canonical_page_path("stats", "en"))


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
        return _redirect_path(
            request, _canonical_stats_path("en", time_range), preserve_query=False
        )
    if lang is not None:
        return _redirect_path(
            request, _canonical_stats_path(lang_prefix, time_range), preserve_query=False
        )
    if request.method == "HEAD":
        return _head_ok()
    return await _stats_handler(request, time_range, lang_prefix)


@router.api_route("/{lang_prefix}/stats/", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def stats_dashboard_lang_slash_redirect(request: Request, lang_prefix: str):
    """Normalize localized stats URLs to the canonical no-trailing-slash form."""
    return _redirect_localized_public_page(request, lang_prefix, "stats")


@router.api_route("/{lang_prefix}", methods=_HTML_ROUTE_METHODS, response_class=HTMLResponse)
async def root_lang_no_slash(request: Request, lang_prefix: str):
    """Normalize language-root URLs and return 404 directly for invalid single segments."""
    if lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return _redirect_path(request, "/", preserve_query=False)
    return _redirect_path(request, _canonical_root_path(lang_prefix), preserve_query=False)
