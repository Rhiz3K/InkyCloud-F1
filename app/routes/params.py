"""Query parameter rules shared by the BMP, preview and dependency layers.

Every route that renders or looks up a calendar or teams image normalizes its inputs here,
so equivalent requests share one cache entry, one pregenerated file and one renderer input.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.config import VALID_LANGUAGES, config
from app.services.renderers import DISPLAY_TYPES
from app.services.weather_service import TempUnit
from app.utils.timezones import is_valid_timezone, normalize_timezone

# Canonical weather variants. "race" is the documented alias of "race_day".
WEATHER_TYPES = {
    "off": "off",
    "current": "current",
    "race_day": "race_day",
    "race": "race_day",
    "weekend": "weekend",
}


def normalize_lang(lang: str) -> str:
    """Return a supported locale code; an unknown one falls back to DEFAULT_LANG."""
    return lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG


def normalize_display(display: str) -> str:
    """Return a supported renderer key; an unknown one falls back to 1-bit."""
    return display if display in DISPLAY_TYPES else "1bit"


def canonical_weather_type(
    weather_type: str, *, weather: bool = True, default: str = "race_day"
) -> str:
    """Collapse the weather switch and type to ``off``, ``current``, ``race_day``, ``weekend``.

    ``weather=False`` means ``off`` whatever the type, and ``race`` means ``race_day``, so
    aliases never render or cache the same image twice. Only these enum values reach a path.
    """
    if not weather:
        return "off"
    return WEATHER_TYPES.get(weather_type, default)


def weather_file_key(weather_type: str) -> str:
    """Return the weather token used in scheduler filenames (``race`` for race-day weather)."""
    return "race" if weather_type == "race_day" else weather_type


def validate_timezone(tz: str | None) -> str | None:
    """Return the canonical IANA name, None for an unset or empty value; 400 when invalid."""
    if not tz:
        return None
    if not is_valid_timezone(tz):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}")
    return normalize_timezone(tz)


def validate_calendar_selection(year: int | None, race_key: str | None) -> None:
    """Reject a race_key without the season it belongs to."""
    if race_key is not None and year is None:
        raise HTTPException(status_code=400, detail="race_key requires year")


def calendar_cache_key(
    lang: str,
    year: int | None,
    round_num: int | None,
    race_key: str | None,
    tz: str | None,
    weather_type: str,
    display: str,
) -> str:
    """Build the in-process cache key for one canonical calendar variant.

    A non-default ``temp_unit`` is appended by the caller (``temp_unit_cache_suffix``), so
    Celsius keys stay unchanged.
    """
    # Compare with None, not truthiness: 0 is a (rejected) value, not "missing".
    year_key = "next" if year is None else year
    round_key = "next" if round_num is None else round_num
    return (
        f"{lang}:{year_key}:{round_key}:{'auto' if race_key is None else race_key}:"
        f"{tz or 'default'}:{weather_type}:{display}"
    )


def temp_unit_cache_suffix(temp_unit: TempUnit) -> str:
    """Return the cache-key suffix of a temperature unit; empty for the Celsius default."""
    return "" if temp_unit == TempUnit.C else f":temp={temp_unit}"
