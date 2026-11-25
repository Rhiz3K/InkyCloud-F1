"""Where the race weekend stands now: the ``weekend_mode=auto`` calendar content (#185 F5).

During a race weekend the calendar can show which sessions are over, highlight the session
that runs now or starts next, and count down to that session instead of only to the race.
Everything here is derived from the bundled schedule and the current instant, without any
network call.

The schedule publishes only session *starts*. When a session really ends (red flags,
delays) is unknown, so a session counts as over from its start plus a conservative
per-type estimate (:data:`SESSION_DURATIONS`). The estimates are deliberately on the long
side: a session is drawn as over a little late rather than while it may still be running.
The race uses the same three hours as the race status (``IN PROGRESS``, then
``COMPLETED``).

The weekend runs from :data:`WEEKEND_LEAD` before the first session until the automatic
next-race selection moves on (see :func:`~app.services.auto_screen.race_week_window`).
Outside it the calendar is drawn exactly as with ``weekend_mode=off``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from enum import StrEnum
from typing import Any

from app.services.auto_screen import race_week_window
from app.services.f1_service import NEXT_RACE_GRACE, F1Service
from app.services.renderer_text import normalize_session_name
from app.utils.race_times import local_session_start, parse_utc_instant, session_over
from app.utils.refresh_hint import (
    RACE_LIVE_WINDOW,
    CountdownMode,
    countdown_shows_days,
    next_local_midnight,
)


class WeekendMode(StrEnum):
    """Whether the calendar follows the race weekend (``auto``) or not (``off``, default)."""

    OFF = "off"
    AUTO = "auto"

    @property
    def selection_grace(self) -> timedelta:
        """Keep the automatically selected timed weekend available for late podiums."""
        return timedelta(hours=24) if self == WeekendMode.AUTO else NEXT_RACE_GRACE


def select_weekend_race(service: F1Service, mode: WeekendMode) -> dict | None:
    """Select the race using the display window requested by the calendar."""
    if mode == WeekendMode.AUTO:
        return service.get_next_race_from_static(grace=mode.selection_grace)
    return service.get_next_race_from_static()


# The weekend view starts this long before the first session (FP1).
WEEKEND_LEAD = timedelta(hours=12)

# Conservative estimates of how long each session type runs (the real end is not published).
# Practice is scheduled for 60 minutes, sprint qualifying for about 45, qualifying for about
# 60 and the sprint for about 30; the margins absorb typical red-flag delays.
SESSION_DURATIONS: Mapping[str, timedelta] = {
    "practice": timedelta(minutes=75),
    "sprintqualifying": timedelta(minutes=60),
    "sprint": timedelta(minutes=60),
    "qualifying": timedelta(minutes=90),
    "race": RACE_LIVE_WINDOW,
}
DEFAULT_SESSION_DURATION = timedelta(minutes=75)


def session_kind(name: str) -> str:
    """Return the session type: practice, sprintqualifying, sprint, qualifying or race."""
    normalized = normalize_session_name(name)
    if normalized in {"fp1", "fp2", "fp3"}:
        return "practice"
    return normalized


def session_duration(name: str) -> timedelta:
    """Return the conservative running time used to decide that a session is over."""
    return SESSION_DURATIONS.get(session_kind(name), DEFAULT_SESSION_DURATION)


@dataclass(frozen=True)
class WeekendPhase:
    """The weekend at one instant; session names are those of the schedule.

    ``current`` is the session drawn highlighted: the one running now or, between sessions,
    the next one. ``countdown_session`` is set when the countdown box shows a session other
    than the race (``countdown_start`` is its UTC start and ``live`` means it has started);
    otherwise the box shows the race exactly as with ``weekend_mode=off``.
    """

    past: frozenset[str] = frozenset()
    current: str | None = None
    live: bool = False
    countdown_session: str | None = None
    countdown_start: datetime | None = None

    @property
    def token(self) -> str:
        """Identify the drawn state for cache keys: it changes at every session boundary."""
        state = "live" if self.live else "next"
        return f"{len(self.past)}:{self.current or '-'}:{state}"


@dataclass(frozen=True)
class _Session:
    """One schedule row as the weekend view sees it.

    ``start`` is None for a date-only (TBA) session. ``ends`` is when it counts as over: the
    estimated end of a timed session, or the local midnight after a date-only session's day.
    """

    name: str
    start: datetime | None
    ends: datetime
    over: bool


def _sessions(race_data: Mapping[str, Any], now: datetime, zone: tzinfo) -> list[_Session]:
    """Return the schedule in its drawn order with UTC starts (None for a TBA hour)."""
    sessions: list[_Session] = []
    for event in race_data.get("schedule") or []:
        name = str(event.get("name") or "")
        value = event.get("datetime")
        if not name or not isinstance(value, str):
            continue
        try:
            if len(value) == 10:
                # A date-only session is over when its local day is over (as session_over).
                ends = local_session_start(value, None, zone) + timedelta(days=1)
                sessions.append(_Session(name, None, ends, session_over(value, None, zone, now)))
                continue
            start = parse_utc_instant(value)
        except ValueError:
            continue
        ends = start + session_duration(name)
        sessions.append(_Session(name, start, ends, ends <= now))
    return sessions


def weekend_window(
    race_data: Mapping[str, Any] | None, zone: tzinfo
) -> tuple[datetime, datetime] | None:
    """Return ``[start, end)`` of the weekend view, or None (cancelled or no race session)."""
    return race_week_window(race_data, lead=WEEKEND_LEAD, zone=zone)


def weekend_phase(
    race_data: Mapping[str, Any] | None, *, now: datetime, zone: tzinfo
) -> WeekendPhase | None:
    """Return the weekend state at ``now`` (aware UTC), or None outside the weekend window."""
    window = weekend_window(race_data, zone)
    if race_data is None or window is None or not window[0] <= now < window[1]:
        return None
    sessions = _sessions(race_data, now, zone)
    past = frozenset(session.name for session in sessions if session.over)
    upcoming = next((session for session in sessions if not session.over), None)
    # A TBA session cannot be counted down to; never skip past it to a later one.
    if upcoming is None or upcoming.start is None:
        return WeekendPhase(past=past)
    live = upcoming.start <= now
    if session_kind(upcoming.name) == "race":
        return WeekendPhase(past=past, current=upcoming.name, live=live)
    return WeekendPhase(
        past=past,
        current=upcoming.name,
        live=live,
        countdown_session=upcoming.name,
        countdown_start=upcoming.start,
    )


def weekend_changes(
    race_data: Mapping[str, Any] | None,
    *,
    now: datetime,
    zone: tzinfo,
    countdown: CountdownMode,
) -> list[datetime]:
    """Return when the ``weekend_mode=auto`` calendar next changes beyond the default hint.

    The window edges, every session start and estimated end (the local midnight after a
    date-only session), and the ticks of a session
    countdown (each hour, or local midnight for whole days); the race countdown itself is
    already part of the default calendar hint.
    """
    window = weekend_window(race_data, zone)
    if race_data is None or window is None or now >= window[1]:
        return []
    changes = [edge for edge in window if edge > now]
    if now < window[0]:
        return changes
    for session in _sessions(race_data, now, zone):
        # A date-only session has no start to wake for, but it still ends at local midnight.
        changes.extend(
            instant
            for instant in (session.start, session.ends)
            if instant is not None and instant > now
        )
    phase = weekend_phase(race_data, now=now, zone=zone)
    start = phase.countdown_start if phase is not None else None
    if start is not None and start > now and countdown != CountdownMode.OFF:
        remaining = start - now
        if countdown_shows_days(countdown, remaining):
            changes.append(next_local_midnight(now, zone))
        else:
            changes.append(now + timedelta(seconds=remaining.total_seconds() % 3600))
    return changes


def phase_cache_token(phase: WeekendPhase | None) -> str:
    """Return the cache-key part of a phase (``off`` outside the weekend)."""
    return "off" if phase is None else phase.token
