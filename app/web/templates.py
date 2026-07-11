"""Shared Jinja2 templates and context helpers."""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import LANGUAGE_CODES, VALID_LANGUAGES, config
from app.paths import TEMPLATES_DIR
from app.services.analytics import get_umami_script_tag
from app.services.i18n import get_translator

# Register font MIME types (Python's mimetypes doesn't know TTF by default)
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

LANGUAGE_LABELS: dict[str, str] = {
    "cs": "CZ",
    "de": "DE",
    "en": "EN",
    "es": "ES",
    "fr": "FR",
    "it": "IT",
    "ja": "JA",
    "nl": "NL",
    "pl": "PL",
    "pt-BR": "PT",
    "sk": "SK",
    "tr": "TR",
    "zh-CN": "ZH",
}

OG_LOCALES: dict[str, str] = {
    "cs": "cs_CZ",
    "de": "de_DE",
    "en": "en_US",
    "es": "es_ES",
    "fr": "fr_FR",
    "it": "it_IT",
    "ja": "ja_JP",
    "nl": "nl_NL",
    "pl": "pl_PL",
    "pt-BR": "pt_BR",
    "sk": "sk_SK",
    "tr": "tr_TR",
    "zh-CN": "zh_CN",
}


def _build_configure_ui_text(translations: dict[str, Any]) -> dict[str, Any]:
    """Build the client-side configure UI translation payload."""
    configure = translations.get("configure", {})
    if not isinstance(configure, dict):
        configure = {}

    filter_labels = configure.get("filterLabels", {})
    if not isinstance(filter_labels, dict):
        filter_labels = {}

    return {
        "nextRace": translations.get("next_race", "Next Race"),
        "seasonTitle": translations.get("screen_calendar_name", "Calendar"),
        "cancelled": translations.get("cancelled", "CANCELLED"),
        "footerPrivacy": translations.get("footer_privacy", "Privacy Policy"),
        "seasonLeaders": translations.get("season_leaders", "Season Leaders"),
        "currentSeason": translations.get("current_season", "Current"),
        "loadingText": translations.get("loading", "Loading..."),
        "copyLabel": translations.get("api_docs_copy", "Copy"),
        "einkBadge": "E-INK",
        "tzLabel": configure.get("tzLabel", "Timezone"),
        "raceLabel": configure.get("raceLabel", "Race"),
        "yearLabel": configure.get("yearLabel", "Season"),
        "settingsBtn": configure.get("settingsBtn", "Settings"),
        "errorText": configure.get("errorText", "FAILED TO LOAD"),
        "retryBtn": configure.get("retryBtn", "RETRY"),
        "refreshBtn": configure.get("refreshBtn", "REFRESH"),
        "epaperSetupTitle": configure.get("epaperSetupTitle", "ePaper Display Setup"),
        "epaperStep1BeforeLink": configure.get("epaperStep1BeforeLink", "In "),
        "epaperStep1BetweenLinkAndUrl": configure.get("epaperStep1BetweenLinkAndUrl", " select "),
        "epaperStep1AfterUrl": configure.get("epaperStep1AfterUrl", " as content source"),
        "epaperStep2Text": configure.get(
            "epaperStep2Text", "Copy the URL above into the image URL field"
        ),
        "weatherLabel": configure.get("weatherLabel", "Weather"),
        "weatherCurrentLabel": configure.get("weatherCurrentLabel", "Current"),
        "weatherRaceDayLabel": configure.get("weatherRaceDayLabel", "Race day"),
        "weatherTooltipText": configure.get("weatherTooltipText", "Activates 14 days before race"),
        "displayLabel": configure.get("displayLabel", "Display"),
        "searchPlaceholder": configure.get("searchPlaceholder", "SEARCH..."),
        "noResults": configure.get("noResults", "No results"),
        "detectedLabel": configure.get("detectedLabel", "DETECTED"),
        "failedToLoadRaces": configure.get("failedToLoadRaces", "FAILED TO LOAD RACES"),
        "loadingSeason": configure.get("loadingSeason", "Loading {year} season..."),
        "autoLabel": configure.get("autoLabel", "AUTO"),
        "filterLabels": {
            "ALL": filter_labels.get("ALL", "ALL"),
            "DETECTED": filter_labels.get("DETECTED", "DETECTED"),
            "EUROPE": filter_labels.get("EUROPE", "EUROPE"),
            "AMERICA": filter_labels.get("AMERICA", "AMERICA"),
            "ASIA": filter_labels.get("ASIA", "ASIA"),
            "AFRICA": filter_labels.get("AFRICA", "AFRICA"),
            "OCEANIA": filter_labels.get("OCEANIA", "OCEANIA"),
            "OTHER": filter_labels.get("OTHER", "OTHER"),
        },
    }


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


def lang_url(path: str, lang: str) -> str:
    """Generate URL with language prefix (empty for English, /cs for Czech)."""
    if lang == "en":
        return path
    return f"/{lang}{path}"


def get_template_context(request: Request, ui_lang: str = "en") -> dict[str, Any]:
    """Build shared Jinja2 template context used by HTML views."""
    t = get_translator(ui_lang)

    nav = {
        "nav_home": t.get("nav_home", "Home"),
        "nav_stats": t.get("nav_stats", "Stats"),
        "nav_api": t.get("nav_api", "API"),
        "nav_menu": t.get("nav_menu", "Menu"),
        "nav_language": t.get("nav_language", "Language"),
        "nav_privacy": t.get("nav_privacy", "Privacy"),
        "nav_changelog": t.get("nav_changelog", "Changelog"),
    }

    # Get base path without language prefix for hreflang generation
    request_path = request.url.path
    base_path = request_path
    for lang_code in LANGUAGE_CODES:
        prefix = f"/{lang_code}"
        if request_path.startswith(prefix + "/") or request_path == prefix:
            base_path = request_path[len(prefix) :] or "/"
            break

    supported_languages = [
        {
            "code": code,
            "label": LANGUAGE_LABELS.get(code, code.upper()),
            "selected": "selected" if ui_lang == code else "",
            "url": lang_url(base_path, code),
        }
        for code in LANGUAGE_CODES
    ]

    return {
        "request": request,
        "ui_lang": ui_lang,
        "umami_script": get_umami_script_tag(),
        "t": t,
        "nav": nav,
        "site_url": str(config.SITE_URL).rstrip("/"),
        "format_bytes": format_bytes,
        "calc_percent": calc_percent,
        "lang_url": lambda path: lang_url(path, ui_lang),
        "base_path": base_path,  # Path without language prefix (for hreflang)
        "og_locale": OG_LOCALES.get(ui_lang, "en_US"),
        "supported_languages": supported_languages,
        "supported_language_codes": list(LANGUAGE_CODES),
        "configure_ui_text": _build_configure_ui_text(t),
    }
