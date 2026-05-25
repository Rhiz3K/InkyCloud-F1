"""Race time conversion helpers."""

from __future__ import annotations

import copy
import logging
from datetime import datetime

from app.utils.timezones import ZoneInfoNotFoundError, get_timezone, normalize_timezone

logger = logging.getLogger(__name__)


def convert_race_times_to_timezone(race_data: dict, target_tz_str: str) -> dict:
    """Convert schedule times in race_data to the specified timezone."""
    try:
        normalized_tz_str = normalize_timezone(target_tz_str)
        target_tz = get_timezone(target_tz_str)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %s, returning original data", target_tz_str)
        return race_data

    result = copy.deepcopy(race_data)

    schedule = result.get("schedule", [])
    for event in schedule:
        iso_str = event.get("datetime")
        if not iso_str:
            continue

        try:
            dt = datetime.fromisoformat(iso_str)
            dt_local = dt.astimezone(target_tz)
            event["datetime"] = dt_local.isoformat()
            event["display_time"] = dt_local.strftime("%a %H:%M")
        except (ValueError, TypeError) as exc:
            logger.warning("Error converting time %s: %s", iso_str, exc)

    if schedule:
        for event in schedule:
            if event.get("name") != "Race":
                continue

            iso_str = event.get("datetime")
            if iso_str:
                try:
                    dt = datetime.fromisoformat(iso_str)
                    result["race_date"] = dt.strftime("%d.%m.%Y")
                except (ValueError, TypeError):
                    pass
            break

    result["timezone"] = normalized_tz_str
    return result
