"""F1 season helper utilities."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)
MIN_F1_SEASON = 1950
SEASONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "seasons"

SEASON_START_DATES: dict[int, datetime] = {
    2025: datetime(2025, 3, 16, tzinfo=timezone.utc),
    2026: datetime(2026, 3, 8, tzinfo=timezone.utc),
}


@lru_cache(maxsize=16)
def bundled_season_start(year: int) -> datetime | None:
    """Return the first bundled race day of a season, or None when no calendar is published.

    Cancelled races stay in bundled calendars with their original dates but without a round;
    they never start a season, and a calendar with only cancelled races counts as unpublished.
    """
    try:
        races = json.loads((SEASONS_DIR / f"{year}.json").read_text(encoding="utf-8"))["races"]
        first_day = min(date.fromisoformat(race["date"]) for race in races if race.get("round"))
    except OSError, ValueError, TypeError, KeyError:
        return None
    return datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc)


def _season_start(year: int) -> datetime | None:
    """Prefer the maintained start date, then the bundled calendar."""
    return SEASON_START_DATES.get(year) or bundled_season_start(year)


def get_current_f1_season(now: datetime | None = None) -> int:
    """Return the newest season whose first race day has been reached.

    A new calendar year does not start a new season. Until that season's calendar is bundled
    and its first race day arrives, the previous season stays current, so the winter break keeps
    serving the last season's results, standings and teams.
    """
    current_time = now or datetime.now(timezone.utc)
    earliest_known_year = min(SEASON_START_DATES)
    for year in range(current_time.year, earliest_known_year - 1, -1):
        start = _season_start(year)
        if start is not None and current_time >= start:
            if year < current_time.year - 1:
                logger.warning(
                    "No F1 calendar is bundled after season %s; it stays current in %s",
                    year,
                    current_time.year,
                )
            return year

    return earliest_known_year - 1


def get_max_supported_f1_season(now: datetime | None = None) -> int:
    """Allow the current calendar year plus the next announced season."""
    current_time = now or datetime.now(timezone.utc)
    return current_time.year + 1


def is_supported_f1_season(year: object, now: datetime | None = None) -> bool:
    """Return whether a value is an integer inside the supported season range."""
    return (
        isinstance(year, int)
        and not isinstance(year, bool)
        and MIN_F1_SEASON <= year <= get_max_supported_f1_season(now)
    )


def race_selection_error(year: int, round_num: int) -> str | None:
    """Return the validation error for a season and round, or None when supported."""
    if not is_supported_f1_season(year):
        return "Unsupported F1 season"
    if not 1 <= round_num <= 30:
        return "Invalid race round"
    return None
