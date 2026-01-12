"""Shared Jinja2 templates and context helpers."""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import VALID_LANGUAGES, config
from app.services.analytics import get_umami_script_tag
from app.services.i18n import get_translator

# Register font MIME types (Python's mimetypes doesn't know TTF by default)
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")

templates = Jinja2Templates(directory="app/templates")


def format_bytes(bytes_val: int) -> str:
    """Format bytes to a human readable string."""
    if bytes_val >= 1_000_000_000:
        return f"{bytes_val / 1_000_000_000:.2f} GB"
    if bytes_val >= 1_000_000:
        return f"{bytes_val / 1_000_000:.2f} MB"
    if bytes_val >= 1_000:
        return f"{bytes_val / 1_000:.1f} KB"
    return f"{bytes_val} B"


def calc_percent(value: int, total: int) -> float:
    """Compute `value` as a percentage of `total`, rounded to 1 decimal place."""
    if total == 0:
        return 0
    return round((value / total) * 100, 1)


def detect_ui_language(request: Request) -> str:
    """Get UI language from cookie or default to English."""
    preferred = request.cookies.get("preferredLang")
    if preferred in VALID_LANGUAGES:
        return preferred
    return "en"


def resolve_ui_language(request: Request, lang: str | None) -> str:
    """Resolve UI language from query param or cookie (English default)."""
    if lang in VALID_LANGUAGES:
        return lang
    return detect_ui_language(request)


def lang_url(path: str, lang: str) -> str:
    """Generate URL with language prefix (empty for English, /cs for Czech)."""
    if lang == "en":
        return path
    return f"/{lang}{path}"


def canonical_path(path: str, lang: str) -> str:
    """Generate canonical path for current page (used in hreflang)."""
    # Remove any existing language prefix
    for prefix in ["/cs", "/en"]:
        if path.startswith(prefix + "/") or path == prefix:
            path = path[len(prefix) :] or "/"
            break
    return lang_url(path, lang)


def get_template_context(request: Request, ui_lang: str = "en") -> dict[str, Any]:
    """Build shared Jinja2 template context used by HTML views."""
    t = get_translator(ui_lang)

    nav = {
        "nav_home": t.get("nav_home", "Home"),
        "nav_stats": t.get("nav_stats", "Stats"),
        "nav_api": t.get("nav_api", "API"),
        "nav_privacy": t.get("nav_privacy", "Privacy"),
        "nav_changelog": t.get("nav_changelog", "Changelog"),
    }

    # Get base path without language prefix for hreflang generation
    request_path = request.url.path
    base_path = request_path
    for prefix in ["/cs", "/en"]:
        if request_path.startswith(prefix + "/") or request_path == prefix:
            base_path = request_path[len(prefix) :] or "/"
            break

    return {
        "request": request,
        "ui_lang": ui_lang,
        "lang_selected_en": "selected" if ui_lang == "en" else "",
        "lang_selected_cs": "selected" if ui_lang == "cs" else "",
        "umami_script": get_umami_script_tag(),
        "t": t,
        "nav": nav,
        "site_url": str(config.SITE_URL).rstrip("/"),
        "format_bytes": format_bytes,
        "calc_percent": calc_percent,
        "lang_url": lambda path: lang_url(path, ui_lang),
        "base_path": base_path,  # Path without language prefix (for hreflang)
    }
