"""This weekend's qualifying and race podiums for ``weekend_mode=auto`` (#185 F5).

The daily historical refresh only stores a circuit's podium once qualifying *and* the race
are complete, so on Saturday evening the calendar footer still shows last year. This module
fetches the current weekend's qualifying and race top three from Jolpica, only in short
windows after each session:

* qualifying from :data:`FETCH_DELAYS` after its start, the race likewise, each until
  :data:`FETCH_CUTOFF` after the start;
* at most once per :data:`RETRY_INTERVAL` per session (a failed or empty answer is
  remembered as a negative-cache entry, also across restarts), and never again once a
  complete top three is stored.

A weekend therefore costs about two to six Jolpica calls, through the shared Jolpica pacer,
retry policy and HTTP client; outside the windows the hourly job makes no request at all.
Podiums are stored in the ``weekend_results`` table and mirrored in memory for rendering.
Only ``weekend_mode=auto`` calendars use them; the default calendar keeps the bundled
history. Results are Jolpica data (CC BY-NC-SA 4.0), attributed like the bundled history.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import weakref
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import config
from app.models import (
    ConstructorInfo,
    DriverInfo,
    HistoricalData,
    QualifyingResultEntry,
    RaceResultEntry,
)
from app.services.database import get_database
from app.services.f1_service import F1Service
from app.services.historical_refresh import (
    PODIUM_SIZE,
    _format_qualifying_result,
    _format_race_result,
)
from app.services.http_client import get_shared_http_client
from app.services.weekend_phase import (
    WeekendMode,
    WeekendPhase,
    phase_cache_token,
    weekend_changes,
    weekend_phase,
)
from app.utils.async_locks import LoopLockRegistry, get_loop_lock
from app.utils.http import AsyncPacer, fetch_with_retry
from app.utils.jolpica import get_jolpica_base_url, get_jolpica_pacer
from app.utils.race_times import parse_utc_instant
from app.utils.refresh_hint import CountdownMode, RefreshHint
from app.utils.result_entries import sort_entries_by_position
from app.utils.timezones import get_timezone

logger = logging.getLogger(__name__)

QUALIFYING = "qualifying"
RACE = "race"
# Schedule session name, Jolpica endpoint and result list for each stored session.
_SESSIONS: Mapping[str, tuple[str, str, str]] = {
    QUALIFYING: ("qualifying", "qualifying.json", "QualifyingResults"),
    RACE: ("race", "results.json", "Results"),
}
# Qualifying runs about an hour, the race about two; Jolpica publishes some time after that.
FETCH_DELAYS: Mapping[str, timedelta] = {
    QUALIFYING: timedelta(minutes=75),
    RACE: timedelta(hours=2),
}
FETCH_CUTOFF = timedelta(hours=12)
# The job runs hourly; a shorter interval only guards against manual or overlapping runs.
RETRY_INTERVAL = timedelta(minutes=50)
# Bounds one job run, including the shared retry policy's back-off on rate limits.
JOB_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class SessionResult:
    """A stored top three (None while not available) and when it was last checked."""

    podium: tuple[dict[str, Any], ...] | None
    checked_at: datetime


_results: dict[tuple[int, int, str], SessionResult] = {}
_refresh_locks: LoopLockRegistry = weakref.WeakKeyDictionary()


def _reset_for_tests() -> None:
    """Forget all stored results (tests only)."""
    _results.clear()


def race_key(race_data: Mapping[str, Any] | None) -> tuple[int, int] | None:
    """Return ``(season, round)`` of a scheduled race, None when either is unknown."""
    if not race_data:
        return None
    try:
        season, round_num = int(race_data.get("season") or 0), int(race_data.get("round") or 0)
    except TypeError, ValueError:
        return None
    return (season, round_num) if season > 0 and round_num > 0 else None


def fetch_windows(race_data: Mapping[str, Any]) -> list[tuple[str, datetime, datetime]]:
    """Return ``(session, from, until)`` fetch windows of the timed qualifying and race."""
    windows = []
    for event in race_data.get("schedule") or []:
        name = str(event.get("name") or "").lower()
        session = next((key for key, spec in _SESSIONS.items() if spec[0] == name), None)
        value = event.get("datetime")
        if session is None or not isinstance(value, str) or len(value) == 10:
            continue
        try:
            start = parse_utc_instant(value)
        except ValueError:
            continue
        windows.append((session, start + FETCH_DELAYS[session], start + FETCH_CUTOFF))
    return windows


def _is_due(key: tuple[int, int, str], now: datetime) -> bool:
    """Return whether a session still lacks a podium and was not checked recently."""
    stored = _results.get(key)
    if stored is None:
        return True
    return stored.podium is None and now - stored.checked_at >= RETRY_INTERVAL


def due_fetches(races: Iterable[Mapping[str, Any]], now: datetime) -> list[tuple[int, int, str]]:
    """Return the ``(season, round, session)`` podiums to fetch now (no network involved)."""
    due = []
    for race_data in races:
        key = race_key(race_data)
        if key is None or race_data.get("is_cancelled"):
            continue
        for session, start, end in fetch_windows(race_data):
            if start <= now < end and _is_due((*key, session), now):
                due.append((*key, session))
    return due


def results_pending(race_data: Mapping[str, Any] | None, now: datetime) -> bool:
    """Return whether a podium of this race may still arrive within its fetch window."""
    key = race_key(race_data)
    if key is None or race_data is None or race_data.get("is_cancelled"):
        return False
    return any(
        start <= now < end and stored_podium(*key, session) is None
        for session, start, end in fetch_windows(race_data)
    )


def stored_podium(season: int, round_num: int, session: str) -> tuple[dict[str, Any], ...] | None:
    """Return a stored complete top three, None while not available."""
    stored = _results.get((season, round_num, session))
    return stored.podium if stored is not None else None


def results_token(race_data: Mapping[str, Any] | None) -> str:
    """Identify the stored podiums of a race, so a new result changes the cache key."""
    key = race_key(race_data)
    podiums = {session: stored_podium(*key, session) for session in _SESSIONS} if key else {}
    if not any(podiums.values()):
        return "none"
    payload = json.dumps(podiums, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _store(key: tuple[int, int, str], podium: tuple[dict, ...] | None, now: datetime) -> None:
    """Remember a result in memory; a complete podium is never replaced by a miss."""
    previous = _results.get(key)
    if previous is not None and previous.podium is not None:
        podium = previous.podium
    _results[key] = SessionResult(podium, now)


async def load_weekend_results_from_db() -> int:
    """Load stored podiums and negative-cache entries into memory; return the row count."""
    rows = await get_database().load_weekend_results()
    for row in rows:
        try:
            podium = _valid_podium(json.loads(row["podium"])) if row["podium"] else None
            checked_at = parse_utc_instant(row["checked_at"])
            key = (int(row["season"]), int(row["round"]), str(row["session"]))
        except TypeError, ValueError, KeyError:
            logger.warning("Skipping malformed weekend result row")
            continue
        _store(key, podium, checked_at)
    return len(rows)


def _valid_podium(value: Any) -> tuple[dict[str, Any], ...]:
    """Return a stored podium only when it has the three rows the renderer needs."""
    required = {"pos", "code", "name", "team"}
    if (
        not isinstance(value, list)
        or len(value) != PODIUM_SIZE
        or not all(isinstance(row, dict) and required <= row.keys() for row in value)
    ):
        raise ValueError("Malformed weekend podium")
    return tuple(value)


def _parse_podium(
    payload: Any, season: int, round_num: int, session: str
) -> tuple[dict[str, object], ...] | None:
    """Return a complete, formatted top three from a Jolpica answer, or None."""
    races = payload.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    if not races:
        return None
    race = races[0]
    if str(race.get("season")) != str(season) or str(race.get("round")) != str(round_num):
        return None
    _name, _endpoint, list_key = _SESSIONS[session]
    entries = sort_entries_by_position(race.get(list_key))[:PODIUM_SIZE]
    # Exactly P1-P3: a missing P3 would display the fourth-placed driver in third place.
    if [position for position, _entry in entries] != list(range(1, PODIUM_SIZE + 1)):
        return None
    formatter = _format_qualifying_result if session == QUALIFYING else _format_race_result
    try:
        return tuple(formatter(position, entry) for position, entry in entries)
    except ValueError:
        # A row without a time is rejected exactly like the daily historical refresh does.
        return None


async def _fetch_podium(
    client: httpx.AsyncClient, pacer: AsyncPacer, season: int, round_num: int, session: str
) -> tuple[dict, ...] | None:
    """Fetch one session's top three; None when not published yet or on an upstream error."""
    _name, endpoint, _list_key = _SESSIONS[session]
    url = f"{get_jolpica_base_url()}/{season}/{round_num}/{endpoint}?limit=30"
    try:
        response = await fetch_with_retry(
            client,
            url,
            max_retries=config.JOLPICA_MAX_RETRIES,
            retry_base_delay=2.0,
            pacer=pacer,
            logger=logger,
        )
        return _parse_podium(response.json(), season, round_num, session)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        logger.warning(
            "Weekend %s results for %s/%s unavailable: %s", session, season, round_num, exc
        )
        return None


def _candidate_races(now: datetime) -> list[dict]:
    """Return the bundled races whose fetch windows can contain ``now``."""
    service = F1Service()
    seasons = sorted({(now - FETCH_CUTOFF).year, now.year})
    return [race for season in seasons for race in service.get_all_races_from_static(season)]


async def _refresh(now: datetime) -> list[tuple[int, int, str]]:
    """Fetch every due podium once; return the sessions that gained a complete top three."""
    due = due_fetches(_candidate_races(now), now)
    if not due:
        return []
    client = get_shared_http_client(httpx.AsyncClient, timeout=30)
    pacer = get_jolpica_pacer()
    completed = []
    for key in due:
        podium = await _fetch_podium(client, pacer, *key)
        _store(key, podium, now)
        if podium is not None:
            completed.append(key)
            logger.info("Stored weekend %s podium for %s/%s", key[2], key[0], key[1])
        try:
            await get_database().save_weekend_result(
                *key, json.dumps(list(podium)) if podium is not None else None, now
            )
        except Exception as exc:  # noqa: BLE001 - memory keeps serving the result
            logger.warning("Could not persist weekend %s result: %s", key[2], exc)
    return completed


async def refresh_weekend_results(now: datetime | None = None) -> list[tuple[int, int, str]]:
    """Run one bounded, non-overlapping refresh (the hourly scheduler job)."""
    lock = get_loop_lock(_refresh_locks)
    if lock.locked():
        logger.info("Weekend results refresh already running; skipping")
        return []
    async with lock:
        try:
            async with asyncio.timeout(JOB_TIMEOUT_SECONDS):
                return await _refresh(now or datetime.now(UTC))
        except TimeoutError:
            logger.warning("Weekend results refresh timed out")
            return []


def _driver(row: Mapping[str, Any]) -> tuple[DriverInfo, ConstructorInfo]:
    """Build the result models of one stored podium row."""
    return (
        DriverInfo(code=row["code"], given_name="", family_name=row["name"]),
        ConstructorInfo(name=row["team"]),
    )


def apply_weekend_results(
    historical: HistoricalData | None, race_data: Mapping[str, Any] | None
) -> HistoricalData | None:
    """Prefer this weekend's stored podiums over the bundled history, column by column.

    Each replaced column carries its own season, so the footer can show e.g. this year's
    qualifying next to last year's race. Without stored podiums the input is returned as is.
    """
    key = race_key(race_data)
    if key is None:
        return historical
    qualifying = stored_podium(*key, QUALIFYING)
    race = stored_podium(*key, RACE)
    if qualifying is None and race is None:
        return historical
    data = historical.model_copy() if historical is not None else HistoricalData(is_new_track=True)
    season = key[0]
    if qualifying is not None:
        data.qualifying_results = [
            QualifyingResultEntry(
                position=row["pos"], driver=driver, constructor=team, q3_time=row.get("time")
            )
            for row in qualifying
            for driver, team in [_driver(row)]
        ]
        data.qualifying_season = season
    if race is not None:
        data.race_results = [
            RaceResultEntry(
                position=row["pos"], driver=driver, constructor=team, time=row.get("time")
            )
            for row in race
            for driver, team in [_driver(row)]
        ]
        data.race_season = season
    return data


@dataclass(frozen=True)
class WeekendContext:
    """What ``weekend_mode=auto`` changes for one calendar request.

    ``mode`` is the effective mode: ``off`` outside the weekend while no podium of the race is
    stored, because the image is then the default one and can share its caches and
    pregenerated files. ``changes`` and ``pending`` extend the refresh hint either way.
    """

    phase: WeekendPhase | None
    mode: WeekendMode
    podiums: str
    changes: tuple[datetime, ...]
    pending: bool

    def cache_suffix(self, hint: RefreshHint) -> str:
        """Return the cache-key part: session boundaries and new podiums start new entries."""
        if self.mode == WeekendMode.OFF:
            return ""
        return f":weekend={phase_cache_token(self.phase)}:{self.podiums}:{hint.cache_token()}"


def weekend_context(
    mode: WeekendMode,
    race_data: Mapping[str, Any] | None,
    *,
    now: datetime,
    timezone_name: str | None,
    countdown: CountdownMode,
) -> WeekendContext:
    """Return the weekend state of one calendar request at ``now``; ``off`` changes nothing."""
    if mode == WeekendMode.OFF:
        return WeekendContext(None, WeekendMode.OFF, "none", (), False)
    zone = get_timezone(timezone_name or config.DEFAULT_TIMEZONE)
    phase = weekend_phase(race_data, now=now, zone=zone)
    podiums = results_token(race_data)
    active = phase is not None or podiums != "none"
    return WeekendContext(
        phase=phase,
        mode=WeekendMode.AUTO if active else WeekendMode.OFF,
        podiums=podiums,
        changes=tuple(weekend_changes(race_data, now=now, zone=zone, countdown=countdown)),
        pending=results_pending(race_data, now),
    )
