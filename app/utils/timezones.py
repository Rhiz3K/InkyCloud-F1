"""Timezone helpers backed by the standard-library zoneinfo module."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

VALID_TIMEZONES: frozenset[str] = frozenset(available_timezones())
TIMEZONE_ALIASES: dict[str, str] = {
    "Asia/Calcutta": "Asia/Kolkata",
}
UTC = ZoneInfo("UTC")


def normalize_timezone(value: str) -> str:
    """Return the canonical timezone key for known legacy browser aliases."""
    return TIMEZONE_ALIASES.get(value, value)


def is_valid_timezone(value: str) -> bool:
    """Return True when the given timezone key exists in the local tz database."""
    return normalize_timezone(value) in VALID_TIMEZONES


def get_timezone(value: str) -> ZoneInfo:
    """Resolve a timezone key to a ZoneInfo instance."""
    return ZoneInfo(normalize_timezone(value))


__all__ = [
    "UTC",
    "TIMEZONE_ALIASES",
    "VALID_TIMEZONES",
    "ZoneInfoNotFoundError",
    "get_timezone",
    "is_valid_timezone",
    "normalize_timezone",
]
