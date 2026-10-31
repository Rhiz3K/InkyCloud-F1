"""Race time conversion helpers."""

from __future__ import annotations

import copy
import logging
from datetime import date, datetime

from app.config import config
from app.utils.timezones import ZoneInfoNotFoundError, get_timezone

logger = logging.getLogger(__name__)


def today_in_timezone(timezone_name: str | None = None) -> date:
    """Return today's calendar date in the requested or configured timezone."""
    return datetime.now(get_timezone(timezone_name or config.DEFAULT_TIMEZONE)).date()


def convert_race_times_to_timezone(race_data: dict, target_tz_str: str) -> dict:
    """Convert schedule times in race_data to the specified timezone."""
    try:
        target_tz = get_timezone(target_tz_str)
        normalized_tz_str = target_tz.key
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %s, returning original data", target_tz_str)
        return race_data

    result = copy.deepcopy(race_data)

    schedule = result.get("schedule", [])
    for event in schedule:
        iso_str = event.get("datetime")
        if not iso_str or len(iso_str) == 10:
            continue

        try:
            dt = datetime.fromisoformat(iso_str)
            dt_local = dt.astimezone(target_tz)
            event["datetime"] = dt_local.isoformat()
            event["display_time"] = dt_local.strftime("%a %H:%M")
            event["date"] = dt_local.strftime("%Y-%m-%d")
            event["time"] = dt_local.strftime("%H:%M")
        except (ValueError, TypeError) as exc:
            logger.warning("Error converting time %s: %s", iso_str, exc)

    # Known schedules already follow UTC order; retain it even across a DST fold.
    # Mixed schedules need local ordering, with unknown hours last on their day.
    if any(len(event.get("datetime") or "") == 10 for event in schedule):
        schedule.sort(
            key=lambda event: (
                event.get("date") or event.get("datetime", "")[:10],
                event.get("datetime", "")[11:19] if event.get("time") else "24:00",
            )
        )

    if schedule:
        for event in schedule:
            if event.get("name") != "Race":
                continue

            iso_str = event.get("datetime")
            if iso_str:
                try:
                    dt = datetime.fromisoformat(iso_str)
                    result["race_date"] = dt.strftime("%d.%m.%Y")
                except ValueError, TypeError:
                    pass
            break

    result["timezone"] = normalized_tz_str
    return result
