"""Timezone helpers backed by the standard-library zoneinfo module."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

VALID_TIMEZONES: frozenset[str] = frozenset(available_timezones())
UTC = ZoneInfo("UTC")


def is_valid_timezone(value: str) -> bool:
    """Return True when the given timezone key exists in the local tz database."""
    return value in VALID_TIMEZONES


def get_timezone(value: str) -> ZoneInfo:
    """Resolve a timezone key to a ZoneInfo instance."""
    return ZoneInfo(value)


__all__ = [
    "UTC",
    "VALID_TIMEZONES",
    "ZoneInfoNotFoundError",
    "get_timezone",
    "is_valid_timezone",
]
