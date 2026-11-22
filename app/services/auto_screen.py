"""Which screen ``/auto.bmp`` shows now, and when that choice next changes.

One URL for panels (Živýobraz, simple ESP32 firmware) that can only poll a single address:

* ``race_week`` (default) shows the calendar from ``lead_hours`` before the first session of
  the next race weekend until the automatic next-race selection moves on
  (:data:`~app.services.f1_service.NEXT_RACE_GRACE` after a timed race start, or the end of
  the local race day while the start time is unpublished). Outside that window, during the
  off-season and when no race is known, it shows teams.
* ``daily`` alternates by the local date in the requested timezone: calendar when
  ``date.toordinal()`` is even, teams when it is odd, switching at local midnight (DST-aware).

The choice is stateless: it depends only on the URL parameters, the bundled schedule and the
current instant.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any

from app.services.f1_service import NEXT_RACE_GRACE
from app.utils.race_times import parse_utc_instant, race_session_start
from app.utils.refresh_hint import local_midnight, next_local_midnight

ACTIVE_SCREEN_HEADER = "X-Active-Screen"
DEFAULT_LEAD_HOURS = 48
MAX_LEAD_HOURS = 168


class AutoMode(StrEnum):
    """How ``/auto.bmp`` picks between the calendar and teams screens."""

    RACE_WEEK = "race_week"
    DAILY = "daily"


class AutoScreen(StrEnum):
    """The screen ``/auto.bmp`` serves."""

    CALENDAR = "calendar"
    TEAMS = "teams"


@dataclass(frozen=True)
class ScreenChoice:
    """The active screen and the instant it next switches (None when not predictable)."""

    screen: AutoScreen
    next_switch: datetime | None = None


def _session_start(event: Mapping[str, Any], zone: tzinfo) -> datetime | None:
    """Return a session start; a date-only (TBA) session starts at local midnight."""
    value = event.get("datetime")
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return local_midnight(date.fromisoformat(value), zone)
        return parse_utc_instant(value)
    except ValueError:
        return None


def race_week_window(
    race_data: Mapping[str, Any] | None, *, lead: timedelta, zone: tzinfo
) -> tuple[datetime, datetime] | None:
    """Return ``[start, end)`` in which ``race_week`` shows the calendar, or None.

    ``start`` is ``lead`` before the earliest session of the weekend (FP1). ``end`` matches the
    next-race selection: ``NEXT_RACE_GRACE`` after a timed race start, or the local midnight
    after a date-only race day. A cancelled race or one without a race session has no window.
    """
    if not race_data or race_data.get("is_cancelled"):
        return None
    race_start = race_session_start(dict(race_data))
    if race_start is not None:
        end = race_start + NEXT_RACE_GRACE
    else:
        race_event = next(
            (
                event
                for event in race_data.get("schedule") or []
                if str(event.get("name", "")).lower() == "race"
            ),
            None,
        )
        race_day = _session_start(race_event, zone) if race_event else None
        if race_day is None:
            return None
        # The local day may last 23 or 25 hours across a DST change.
        end = local_midnight(race_day.astimezone(zone).date() + timedelta(days=1), zone)
    starts = [
        start
        for event in race_data.get("schedule") or []
        if (start := _session_start(event, zone)) is not None
    ]
    first_session = min(starts)
    return first_session - lead, end


def choose_screen(
    mode: AutoMode,
    *,
    now: datetime,
    race_data: Mapping[str, Any] | None,
    lead_hours: int,
    zone: tzinfo,
) -> ScreenChoice:
    """Pick the active screen for ``now`` (aware UTC) and the next switch instant."""
    if mode == AutoMode.DAILY:
        today = now.astimezone(zone).date()
        screen = AutoScreen.CALENDAR if today.toordinal() % 2 == 0 else AutoScreen.TEAMS
        return ScreenChoice(screen, next_local_midnight(now, zone))

    window = race_week_window(race_data, lead=timedelta(hours=lead_hours), zone=zone)
    if window is None:
        return ScreenChoice(AutoScreen.TEAMS)
    start, end = window
    if now < start:
        return ScreenChoice(AutoScreen.TEAMS, start)
    if now < end:
        return ScreenChoice(AutoScreen.CALENDAR, end)
    # The selection has not moved on (off-season: the last completed race): no known switch.
    return ScreenChoice(AutoScreen.TEAMS)
