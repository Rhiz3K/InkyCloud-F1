"""When an e-ink client should next poll an image: the ``X-Next-Refresh-Seconds`` hint.

Battery-powered panels sleep between polls. The image routes send this hint so a client can
pick its deep-sleep length from the content instead of waking every hour. It is the number of
whole seconds until the rendered image is next expected to change:

* a default hours image is refreshed by hourly generation, or when its cached render expires;
* a day count, a "today" label and date-only races change at the next local midnight in the
  requested timezone (a 23 or 25 hour day across a DST change is counted correctly);
* the race status changes at the race start (IN PROGRESS) and three hours later (COMPLETED);
* the automatically selected race moves on ``selection_grace`` after its start;
* weather that is refreshed every hour (current conditions, a race-day forecast) changes with
  the hourly generation, and a race-day forecast first appears when the race enters the
  16-day forecast range.

It is a hint, not a promise: data refreshes (a new calendar, standings, a weather fetch) can
change an image earlier, and a poll can still return ``304 Not Modified`` with a new hint.
Every value is capped at :data:`MAX_REFRESH_SECONDS` (:data:`HOURLY_REFRESH_SECONDS` while
hourly data is shown) and never below :data:`MIN_REFRESH_SECONDS`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Any

from app.config import config
from app.utils.race_times import race_session_start
from app.utils.timezones import get_timezone

REFRESH_HINT_HEADER = "X-Next-Refresh-Seconds"
MIN_REFRESH_SECONDS = 60
HOURLY_REFRESH_SECONDS = 3600
MAX_REFRESH_SECONDS = 86400
# Retry soon after an error image instead of keeping it on the panel for a day.
ERROR_REFRESH_SECONDS = 300
# Wake a little after a change, so the hourly pre-generation has written the new image.
SETTLE_SECONDS = 120

# The renderer shows IN PROGRESS from the race start for this long, then COMPLETED.
RACE_LIVE_WINDOW = timedelta(hours=3)
# countdown=auto shows whole days while the race start is further away than this.
AUTO_DAYS_THRESHOLD = timedelta(hours=48)
# Open-Meteo forecasts cover 16 days including today (see WeatherService.get_race_weather).
FORECAST_RANGE_DAYS = 16
# Standings can change hourly while results and penalties arrive after a points session.
RESULTS_WINDOW = timedelta(hours=24)


class CountdownMode(StrEnum):
    """How the calendar countdown box shows the time to the race start."""

    HOURS = "hours"
    DAYS = "days"
    AUTO = "auto"
    OFF = "off"


def countdown_shows_days(mode: CountdownMode, remaining: timedelta) -> bool:
    """Return whether a timed race shows whole calendar days instead of days and hours."""
    if mode == CountdownMode.DAYS:
        return True
    return mode == CountdownMode.AUTO and remaining > AUTO_DAYS_THRESHOLD


def local_midnight(day: date, zone: tzinfo) -> datetime:
    """Return the instant (UTC) at which ``day`` starts in ``zone``."""
    return datetime.combine(day, time.min, zone).astimezone(UTC)


def next_local_midnight(now: datetime, zone: tzinfo) -> datetime:
    """Return the next start of a local day after ``now`` (UTC), DST-aware."""
    return local_midnight(now.astimezone(zone).date() + timedelta(days=1), zone)


def _next_publication(now: datetime) -> datetime:
    """Return the next future UTC hourly publication after the scheduler settle window."""
    target = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
        seconds=SETTLE_SECONDS
    )
    return target if target > now else target + timedelta(hours=1)


@dataclass(frozen=True)
class RefreshHint:
    """Known upcoming content changes for one image request."""

    now: datetime
    changes: tuple[datetime, ...] = ()
    hourly: bool = False

    @property
    def next_change(self) -> datetime | None:
        """Return the earliest predictable change that is not in the past."""
        upcoming = [change for change in self.changes if change >= self.now]
        return min(upcoming) if upcoming else None

    def cache_token(self) -> str:
        """Identify the content period, so a cached image never outlives its next change."""
        change = self.next_change
        return "none" if change is None else str(int(change.timestamp()))

    def max_age(self, now: datetime, limit: int = HOURLY_REFRESH_SECONDS) -> int:
        """Return an HTTP cache lifetime (seconds) that never outlives the next change.

        ``now`` is read when the response is sent, so time spent rendering is not added on top.
        """
        change = self.next_change
        if change is None:
            return limit
        return max(0, min(limit, math.floor((change - now).total_seconds())))

    def seconds(self, now: datetime | None = None, *, cache_seconds: int | None = None) -> int:
        """Return the remaining time to the earliest publication, expiry or content edge."""
        sent = self.now if now is None else now
        deadline = self.now + timedelta(seconds=MAX_REFRESH_SECONDS)
        if cache_seconds is not None:
            deadline = min(deadline, sent + timedelta(seconds=cache_seconds))
        elif self.hourly:
            deadline = min(deadline, _next_publication(self.now))
        if (change := self.next_change) is not None:
            deadline = min(deadline, change)
        return max(MIN_REFRESH_SECONDS, math.ceil((deadline - sent).total_seconds()))


def _race_day(race_data: Mapping[str, Any]) -> date | None:
    """Return the date of a date-only race session (start time not published yet)."""
    for event in race_data.get("schedule") or []:
        if str(event.get("name", "")).lower() != "race":
            continue
        value = event.get("datetime")
        if isinstance(value, str) and len(value) == 10:
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None
    return None


def _timed_countdown_changes(
    start: datetime, now: datetime, zone: tzinfo, mode: CountdownMode
) -> list[datetime]:
    """Return when the countdown box of a race with a known start next changes."""
    if now >= start + RACE_LIVE_WINDOW:
        return []
    if now >= start:
        return [start + RACE_LIVE_WINDOW]
    changes = [start]
    remaining = start - now
    if mode in (CountdownMode.OFF, CountdownMode.HOURS):
        return changes
    if countdown_shows_days(mode, remaining):
        changes.append(next_local_midnight(now, zone))
        if mode == CountdownMode.AUTO:
            changes.append(start - AUTO_DAYS_THRESHOLD)
    else:
        changes.append(now + timedelta(seconds=remaining.total_seconds() % 3600))
    return changes


def _date_only_changes(
    race_day: date, now: datetime, zone: tzinfo, mode: CountdownMode
) -> list[datetime]:
    """Return when the countdown box of a date-only race next changes (local midnights)."""
    today = now.astimezone(zone).date()
    if today > race_day:
        return []
    if mode == CountdownMode.OFF and today < race_day:
        return [local_midnight(race_day, zone)]
    return [next_local_midnight(now, zone)]


def calendar_refresh_hint(
    race_data: Mapping[str, Any] | None,
    *,
    now: datetime,
    timezone_name: str | None,
    countdown: CountdownMode,
    weather_type: str,
    weather_enabled: bool,
    selection_grace: timedelta | None,
) -> RefreshHint:
    """Predict the next change of a calendar image.

    ``weather_type`` is canonical (``off``, ``current`` or ``race_day``). ``selection_grace``
    is how long the automatic next-race selection keeps a race after its start; None for an
    explicitly selected race.
    """
    if not race_data:
        return RefreshHint(now, hourly=True)
    zone = get_timezone(timezone_name or config.DEFAULT_TIMEZONE)
    start = race_session_start(dict(race_data))
    race_day = None if start is not None else _race_day(race_data)
    cancelled = bool(race_data.get("is_cancelled"))
    changes: list[datetime] = []

    if selection_grace is not None:
        if start is not None:
            changes.append(start + selection_grace)
        elif race_day is not None:
            changes.append(local_midnight(race_day + timedelta(days=1), zone))

    if not cancelled:
        if start is not None:
            changes.extend(_timed_countdown_changes(start, now, zone, countdown))
        elif race_day is not None:
            changes.extend(_date_only_changes(race_day, now, zone, countdown))

    hourly = (
        countdown == CountdownMode.HOURS and not cancelled and start is not None and now < start
    )
    if weather_enabled and not cancelled and weather_type != "off":
        if weather_type == "current":
            hourly = True
        elif start is not None:
            first_forecast_day = start.date() - timedelta(days=FORECAST_RANGE_DAYS - 1)
            forecast_from = datetime.combine(first_forecast_day, time.min, UTC)
            if now < forecast_from:
                changes.append(forecast_from)
            else:
                hourly = True

    return RefreshHint(now, tuple(changes), hourly)


def teams_refresh_hint(
    *,
    now: datetime,
    points_sessions: Iterable[datetime],
    timezone_name: str | None = None,
    day_panel: bool = False,
    hourly: bool = False,
) -> RefreshHint:
    """Predict the next change of a teams image.

    Standings change after a points-scoring session (sprint or race): hourly during
    :data:`RESULTS_WINDOW` after one starts, otherwise not before the next one starts.
    A ``day_panel`` (countdown, flags, track) also changes at local midnight.
    """
    sessions = sorted(points_sessions)
    if any(start <= now < start + RESULTS_WINDOW for start in sessions):
        hourly = True
    changes = [start for start in sessions if start > now][:1]
    if day_panel:
        changes.append(
            next_local_midnight(now, get_timezone(timezone_name or config.DEFAULT_TIMEZONE))
        )
    return RefreshHint(now, tuple(changes), hourly)
