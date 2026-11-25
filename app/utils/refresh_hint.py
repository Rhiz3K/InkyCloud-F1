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
from app.utils.race_times import race_session_start, weekend_session_starts
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
# weather_type=weekend also forecasts qualifying and a sprint, up to this many days before the
# race day (the forecast request covers these days and the race day).
WEEKEND_FORECAST_LEAD_DAYS = 2
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


def _publication_at_or_after(change: datetime) -> datetime:
    """Return the first UTC hourly publication that can include a content change."""
    target = change.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
        seconds=SETTLE_SECONDS
    )
    return target if target >= change else target + timedelta(hours=1)


@dataclass(frozen=True)
class RefreshHint:
    """Known upcoming content changes for one image request."""

    now: datetime
    changes: tuple[datetime, ...] = ()
    hourly: bool = False
    # Edges absent from the cache key become visible only on publication or cache expiry.
    publication_changes: tuple[datetime, ...] = ()

    @property
    def next_change(self) -> datetime | None:
        """Return the earliest predictable change that is not in the past."""
        changes = (*self.changes, *self.publication_changes)
        return min((change for change in changes if change >= self.now), default=None)

    def with_changes(
        self, changes: Iterable[datetime], *, hourly: bool = False, period_keyed: bool = False
    ) -> RefreshHint:
        """Add on-demand edges, including all content edges when the key tracks their period."""
        exact = (*self.changes, *changes)
        delayed = self.publication_changes
        if period_keyed:
            exact += tuple(change for change in delayed if change > self.now)
            delayed = ()
        return RefreshHint(self.now, exact, self.hourly or hourly, delayed)

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

    def seconds(
        self,
        now: datetime | None = None,
        *,
        cache_seconds: float | None = None,
        cache_ttl: float = HOURLY_REFRESH_SECONDS,
    ) -> int:
        """Return the remaining time to the earliest publication, expiry or content edge."""
        sent = self.now if now is None else now
        deadlines = [self.now + timedelta(seconds=MAX_REFRESH_SECONDS)]
        deadlines.extend(change for change in self.changes if change >= self.now)
        if cache_seconds is None:
            refresh = _publication_at_or_after(self.now)
            if refresh == self.now:
                refresh += timedelta(hours=1)
            source_time = refresh - timedelta(hours=1)
        else:
            refresh = sent + timedelta(seconds=cache_seconds)
            source_time = refresh - timedelta(seconds=cache_ttl)
        if self.hourly:
            deadlines.append(refresh)
        for change in self.publication_changes:
            if change > source_time:
                deadlines.append(
                    _publication_at_or_after(change)
                    if cache_seconds is None
                    else max(change, refresh)
                )
        return max(MIN_REFRESH_SECONDS, math.ceil((min(deadlines) - sent).total_seconds()))


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
    if mode == CountdownMode.HOURS:
        # An already-crossed status edge may still be waiting for publication.
        return [start, start + RACE_LIVE_WINDOW]
    if now >= start + RACE_LIVE_WINDOW:
        return []
    if now >= start:
        return [start + RACE_LIVE_WINDOW]
    changes = [start]
    remaining = start - now
    if mode == CountdownMode.OFF:
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
    if mode == CountdownMode.OFF and today < race_day:
        return [local_midnight(race_day, zone)]
    changes = [next_local_midnight(now, zone)] if today <= race_day else []
    if mode == CountdownMode.HOURS and today - race_day <= timedelta(days=1):
        # A poll just after midnight can still see yesterday's image until publication.
        changes.append(local_midnight(today, zone))
    return changes


def _forecast_start(
    start: datetime | None, race_day: date | None, weather_type: str
) -> datetime | None:
    """Return when a race-day or weekend forecast first exists, None without a race date.

    A date-only race has no race-day forecast, but its weekend forecast can still show a
    timed qualifying.
    """
    weekend = weather_type == "weekend"
    if start is not None:
        forecast_day = start.date()
    elif weekend and race_day is not None:
        forecast_day = race_day
    else:
        return None
    lead_days = WEEKEND_FORECAST_LEAD_DAYS if weekend else 0
    first_day = forecast_day - timedelta(days=FORECAST_RANGE_DAYS - 1 + lead_days)
    return datetime.combine(first_day, time.min, UTC)


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

    ``weather_type`` is canonical (``off``, ``current``, ``race_day`` or ``weekend``; the
    weekend forecast starts with qualifying, before the race forecast). ``selection_grace``
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
        if weather_type == "weekend":
            changes.extend(
                start for _, start in weekend_session_starts(dict(race_data))[1] if start > now
            )
        if weather_type == "current":
            hourly = True
        elif (forecast_from := _forecast_start(start, race_day, weather_type)) is not None:
            if now < forecast_from:
                changes.append(forecast_from)
            else:
                hourly = True

    if countdown == CountdownMode.HOURS:
        return RefreshHint(now, hourly=hourly, publication_changes=tuple(changes))
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
