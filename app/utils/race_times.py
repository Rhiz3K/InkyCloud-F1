"""Race time parsing and conversion helpers.

One contract for every consumer of Jolpica-style schedules:

* a session ``time`` is UTC; a value without an offset is read as UTC, never as the
  host's local time, and an explicit offset is honoured;
* a missing time (TBA) stays unknown: the session keeps its published calendar date in
  every timezone and never gets a synthetic hour;
* known sessions are ordered by their instant, so a DST fold cannot swap them, and
  date-only sessions sort last on their day.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from app.config import config
from app.utils.timezones import ZoneInfoNotFoundError, get_timezone

logger = logging.getLogger(__name__)


def today_in_timezone(timezone_name: str | None = None) -> date:
    """Return today's calendar date in the requested or configured timezone."""
    return datetime.now(get_timezone(timezone_name or config.DEFAULT_TIMEZONE)).date()


def to_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime; a naive value is read as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def parse_utc_instant(value: str) -> datetime:
    """Parse an ISO 8601 timestamp as aware UTC (naive means UTC). Raises ValueError."""
    return to_utc(datetime.fromisoformat(value))


def parse_session_start(date_str: str, time_str: str | None) -> datetime | None:
    """Return a session start as aware UTC, or None while its time is unpublished.

    Raises ValueError for a malformed date or time.
    """
    if not time_str:
        return None
    return parse_utc_instant(f"{date_str}T{time_str}")


def local_session_start(date_str: str, time_str: str | None, zone: tzinfo) -> datetime:
    """Return a session start in ``zone``; a date-only session is local midnight of its date."""
    start = parse_session_start(date_str, time_str)
    if start is not None:
        return start.astimezone(zone)
    # Converting UTC midnight would move a TBA session to the previous day west of UTC.
    return datetime.combine(date.fromisoformat(date_str), datetime.min.time(), zone)


def session_over(
    date_str: str,
    time_str: str | None,
    zone: tzinfo,
    now: datetime,
    *,
    grace: timedelta = timedelta(0),
) -> bool:
    """A timed session is over ``grace`` after it starts; a date-only one after its local day."""
    start = parse_session_start(date_str, time_str)
    if start is not None:
        return start + grace <= now
    return local_session_start(date_str, None, zone) + timedelta(days=1) <= now


def race_session_start(race_data: dict[str, Any]) -> datetime | None:
    """Return the UTC start of the race session in a normalized weekend schedule.

    None when there is no race entry or its time is unknown (date-only) or malformed.
    """
    session = next(
        (
            event
            for event in race_data.get("schedule") or []
            if str(event.get("name", "")).lower() == "race"
        ),
        None,
    )
    value = session.get("datetime") if session else None
    if not isinstance(value, str) or len(value) == 10:
        return None
    try:
        return parse_utc_instant(value)
    except ValueError:
        logger.debug("Invalid race datetime: %s", value)
        return None


def schedule_sort_key(event: dict[str, Any]) -> tuple[str, int, float]:
    """Order normalized schedule events by local day, then instant; unknown hours last."""
    value = str(event.get("datetime") or "")
    day = str(event.get("date") or value[:10])
    if event.get("time") and len(value) > 10:
        try:
            return day, 0, parse_utc_instant(value).timestamp()
        except ValueError:
            pass
    return day, 1, 0.0


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
            dt_local = parse_utc_instant(iso_str).astimezone(target_tz)
            event["datetime"] = dt_local.isoformat()
            event["display_time"] = dt_local.strftime("%a %H:%M")
            event["date"] = dt_local.strftime("%Y-%m-%d")
            event["time"] = dt_local.strftime("%H:%M")
        except (ValueError, TypeError) as exc:
            logger.warning("Error converting time %s: %s", iso_str, exc)

    # Instant order survives a DST fold; unknown hours stay last on their local day.
    schedule.sort(key=schedule_sort_key)

    if schedule:
        for event in schedule:
            if event.get("name") != "Race":
                continue

            iso_str = event.get("datetime")
            if iso_str:
                try:
                    dt = datetime.fromisoformat(iso_str)
                    result["race_date"] = dt.strftime("%d.%m.%Y")
                    if len(iso_str) != 10:
                        # Keep the top-level race start in the same zone as the schedule.
                        result["datetime"] = dt.isoformat()
                        result["time"] = dt.strftime("%H:%M")
                except ValueError, TypeError:
                    pass
            break

    result["timezone"] = normalized_tz_str
    return result


WEEKEND_SESSIONS = ("sprint", "qualifying", "race")


def weekend_session_starts(race_data: dict) -> tuple[date | None, list[tuple[str, datetime]]]:
    """Return the race's UTC date and the timed weekend sessions shown by weather_type=weekend.

    A date-only race still provides its date, so qualifying can be shown before the race
    time is published.
    """
    race_date: date | None = None
    sessions: list[tuple[str, datetime]] = []
    for event in race_data.get("schedule") or []:
        kind = str(event.get("name", "")).strip().lower()
        value = event.get("datetime")
        if kind not in WEEKEND_SESSIONS or not isinstance(value, str):
            continue
        try:
            if len(value) == 10:
                if kind == "race":
                    race_date = date.fromisoformat(value)
                continue
            start = parse_utc_instant(value)
        except ValueError:
            continue
        if kind == "race":
            race_date = start.date()
        sessions.append((kind, start))
    sessions.sort(key=lambda item: WEEKEND_SESSIONS.index(item[0]))
    return race_date, sessions


def forecast_period(sessions: Sequence[tuple[str, datetime]], now: datetime) -> str:
    """Identify the weekend sessions whose forecasts have not yet started."""
    return (
        ",".join(f"{kind}@{int(start.timestamp())}" for kind, start in sessions if start > now)
        or "finished"
    )
