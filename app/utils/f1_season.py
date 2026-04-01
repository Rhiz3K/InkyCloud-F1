"""F1 season helper utilities."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SEASON_START_DATES: dict[int, datetime] = {
    2025: datetime(2025, 3, 16, tzinfo=timezone.utc),
    2026: datetime(2026, 3, 8, tzinfo=timezone.utc),
}


def get_current_f1_season(now: datetime | None = None) -> int:
    """Get the current F1 season based on known first-race dates."""
    current_time = now or datetime.now(timezone.utc)
    latest_known_year = max(SEASON_START_DATES)

    if current_time.year > latest_known_year:
        logger.warning(
            "F1 season start dates are only defined through %s; falling back to current year %s",
            latest_known_year,
            current_time.year,
        )
        return current_time.year

    for year in sorted(SEASON_START_DATES, reverse=True):
        if current_time >= SEASON_START_DATES[year]:
            return year

    return min(SEASON_START_DATES) - 1
