"""F1 data service using Jolpica API and static data."""

import json
import logging
import re
import weakref
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
from cachetools import TTLCache
from pydantic import ValidationError

from app.config import config
from app.models import (
    ConstructorInfo,
    DriverInfo,
    HistoricalData,
    QualifyingResultEntry,
    Race,
    RaceResultEntry,
)
from app.services.circuit_data import get_circuits_data_path, load_circuits_data
from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.http_client import get_shared_http_client
from app.utils.async_locks import KeyedLoopLockRegistry, get_keyed_loop_lock
from app.utils.http import fetch_with_retry
from app.utils.jolpica import get_jolpica_pacer
from app.utils.timezones import UTC, ZoneInfoNotFoundError, get_timezone, normalize_timezone

logger = logging.getLogger(__name__)

# Static data paths
ASSETS_DIR = Path(__file__).parent.parent / "assets"
SEASONS_DIR = ASSETS_DIR / "seasons"

# Default UTC time used when a session/race time is missing from the data. Shared by the
# display conversion and the next-race selection so the two never disagree at the boundary.
DEFAULT_SESSION_TIME_UTC = "12:00:00Z"
# Keep a race selected as "next" until this long after lights-out, so the calendar doesn't
# advance to the following Grand Prix the instant the current race starts.
NEXT_RACE_GRACE = timedelta(hours=4)

# Raw Jolpica payloads for seasons and rounds without a static file. Positive entries follow
# the hourly generation cadence; failures and empty answers are remembered briefly so a burst
# of requests for an uncached season cannot queue hundreds of paced upstream calls.
REMOTE_CACHE_TTL_SECONDS = 3600
REMOTE_NEGATIVE_CACHE_TTL_SECONDS = 60


class _RemotePayloadCache:
    """Cache raw upstream payloads with separate positive and negative TTLs."""

    def __init__(self, *, maxsize: int, ttl: float, negative_ttl: float) -> None:
        """Create bounded positive and negative caches with the given lifetimes."""
        self._positive: TTLCache[tuple, object] = TTLCache(maxsize=maxsize, ttl=ttl)
        self._negative: TTLCache[tuple, object | None] = TTLCache(maxsize=maxsize, ttl=negative_ttl)

    def lookup(self, key: tuple) -> tuple[bool, object | None]:
        """Return ``(hit, payload)``; a remembered empty answer or failure hits too."""
        if key in self._positive:
            return True, self._positive[key]
        if key in self._negative:
            return True, self._negative[key]
        return False, None

    def store(self, key: tuple, payload: object | None, *, valid: bool) -> None:
        """Cache valid data positively, empty answers negatively, and ignore malformed data."""
        if payload and valid:
            self._positive[key] = payload
            self._negative.pop(key, None)
        elif not payload:
            self._negative[key] = payload

    def clear(self) -> None:
        """Drop every cached payload."""
        self._positive.clear()
        self._negative.clear()


_remote_season_cache = _RemotePayloadCache(
    maxsize=64, ttl=REMOTE_CACHE_TTL_SECONDS, negative_ttl=REMOTE_NEGATIVE_CACHE_TTL_SECONDS
)
_remote_race_cache = _RemotePayloadCache(
    maxsize=256, ttl=REMOTE_CACHE_TTL_SECONDS, negative_ttl=REMOTE_NEGATIVE_CACHE_TTL_SECONDS
)
_remote_fetch_locks: KeyedLoopLockRegistry = weakref.WeakKeyDictionary()


def _reset_remote_caches_for_tests() -> None:
    """Clear remote payload caches and fetch locks between tests."""
    _remote_season_cache.clear()
    _remote_race_cache.clear()
    _remote_fetch_locks.clear()


def _is_valid_remote_season_payload(payload: object | None) -> bool:
    """Return whether every season row is complete and usable by the list converter."""
    if not isinstance(payload, list):
        return False
    try:
        for race in payload:
            Race.model_validate(race)
            race_date = race.get("date", "")
            if race_date:
                race_time = race.get("time", DEFAULT_SESSION_TIME_UTC)
                datetime.fromisoformat(f"{race_date}T{race_time}".replace("Z", "+00:00"))
    except AttributeError, TypeError, ValueError, ValidationError:
        return False
    return True


def _is_valid_remote_race_payload(payload: object | None) -> bool:
    """Return whether a round payload can be parsed as a complete race."""
    if not isinstance(payload, dict):
        return False
    try:
        Race.model_validate(payload)
    except ValidationError:
        return False
    return True


@lru_cache(maxsize=32)
def _parse_static_season(payload: str) -> tuple[Race, ...]:
    """Parse and validate a season snapshot cached by its contents."""
    data = json.loads(payload)

    races: list[Race] = []
    for race_data in data.get("races", []):
        try:
            races.append(Race(**race_data))
        except Exception as exc:
            logger.warning("Failed to parse race: %s: %s", race_data.get("raceName"), exc)
    return tuple(races)


@lru_cache(maxsize=32)
def _load_static_season_file(path_value: str, _mtime_ns: int, _size: int) -> tuple[Race, ...]:
    """Parse a season file once per on-disk version instead of re-reading it per request."""
    return _parse_static_season(Path(path_value).read_text(encoding="utf-8"))


def _load_static_season(path: Path) -> tuple[Race, ...]:
    """Load a season file through the mtime- and size-keyed parse cache."""
    stat = path.stat()
    return _load_static_season_file(str(path), stat.st_mtime_ns, stat.st_size)


def _find_static_season_path(year: int) -> Path | None:
    """Find an allowlisted season file without deriving a filesystem path from input."""
    expected_name = f"{year}.json"
    try:
        return next(
            candidate
            for candidate in SEASONS_DIR.iterdir()
            if not candidate.is_symlink()
            and candidate.is_file()
            and candidate.name == expected_name
        )
    except OSError, StopIteration:
        return None


class F1Service:
    """Service for fetching F1 race data from Jolpica API."""

    def __init__(self, timezone_name: str | None = None, timezone: str | None = None):
        """
        Initialize F1 service.

        Args:
            timezone_name: Preferred timezone string (e.g., 'Europe/Prague').
            timezone: Backward-compatible alias for legacy callers.
        """
        self.api_url = config.JOLPICA_API_URL
        self.api_base_url = self._derive_api_base_url(self.api_url)
        self.timeout = config.REQUEST_TIMEOUT
        effective_timezone = timezone_name if timezone_name is not None else timezone
        self.timezone_str = normalize_timezone(effective_timezone or config.DEFAULT_TIMEZONE)
        try:
            self.target_tz = get_timezone(self.timezone_str)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown timezone %s, falling back to UTC", self.timezone_str)
            self.target_tz = UTC
            self.timezone_str = "UTC"

    @staticmethod
    def _derive_api_base_url(api_url: str) -> str:
        """Derive the Jolpica API root from the configured next-race endpoint."""
        normalized = api_url.rstrip("/")
        for suffix in ("/current/next.json", "/current.json", ".json"):
            if normalized.endswith(suffix):
                return normalized[: -len(suffix)].rstrip("/")
        return normalized

    @staticmethod
    def _has_scheduled_round(race_data: dict) -> bool:
        """Return True when the race has a valid round number assigned."""
        round_value = race_data.get("round")

        if not isinstance(round_value, (str, int, float)) or round_value == "":
            return False

        try:
            return int(round_value) > 0
        except TypeError, ValueError:
            return False

    @classmethod
    def _is_cancelled_race(cls, race_data: dict) -> bool:
        """Return True when the race has been removed from the active calendar."""
        return not cls._has_scheduled_round(race_data)

    @staticmethod
    def _slugify_race_part(value: str) -> str:
        """Convert a race identifier fragment into a stable ASCII slug."""
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    @classmethod
    def _build_race_key(
        cls,
        *,
        season: str | int | None,
        round_value: str | int | None,
        race_name: str,
        circuit_id: str,
        race_date: str,
    ) -> str:
        """Build a stable identifier for selecting a race from the UI/API."""
        key_parts = [cls._slugify_race_part(str(season or "race"))]

        if round_value not in (None, ""):
            key_parts.append(f"round-{cls._slugify_race_part(str(round_value))}")
        else:
            key_parts.append("cancelled")

        key_parts.append(cls._slugify_race_part(circuit_id or race_name or "grand-prix"))

        if race_date:
            key_parts.append(cls._slugify_race_part(race_date))

        return "-".join(part for part in key_parts if part)

    @classmethod
    def _extract_round_number(cls, race_data: dict) -> Optional[int]:
        """Return the integer round number when available."""
        round_value = race_data.get("round")

        if round_value in (None, ""):
            return None

        try:
            round_num = int(str(round_value))
        except TypeError, ValueError:
            return None

        return round_num if round_num > 0 else None

    def _merge_static_cancelled_races(self, year: int, races: list[dict]) -> list[dict]:
        """Merge cancelled races from static season data when live API omits them."""
        merged = list(races)
        existing_keys = {race.get("race_key") for race in merged}

        try:
            for static_race in self.get_all_races_from_static(year):
                if not static_race.get("is_cancelled"):
                    continue
                if static_race.get("race_key") in existing_keys:
                    continue

                merged.append(static_race)
                existing_keys.add(static_race.get("race_key"))
        except Exception as exc:
            logger.warning("Failed to merge static cancelled races for %s: %s", year, exc)

        merged.sort(
            key=lambda item: (
                item.get("is_cancelled", False),
                item.get("round") is None,
                item.get("round") or 999,
                item.get("date") or "9999-12-31",
            )
        )
        return merged

    def _convert_race_times(self, race: Race) -> dict:
        """
        Convert Race UTC times to target timezone and return structured payload.

        Parses session/race times (default "12:00:00Z" if missing), converts to
        target timezone, and produces a sorted schedule. Invalid times are omitted.

        Parameters:
            race: Race model with UTC date/time strings and circuit metadata.

        Returns:
            dict with race_name, round, season, circuit (circuitId, name,
            location, country, lat, long), schedule (list of events with name,
            datetime, display_time), race_date, and timezone.
        """
        schedule_events = []

        # Helper function to parse and convert time
        def parse_and_convert(date_str: str, time_str: Optional[str]) -> Optional[datetime]:
            """Parse date and time, convert to target timezone."""
            if not time_str:
                # If no time, use a stable default shared with next-race selection.
                time_str = DEFAULT_SESSION_TIME_UTC

            # Combine date and time
            dt_str = f"{date_str}T{time_str}"
            try:
                # Parse as UTC
                dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                # Convert to target timezone
                dt_local = dt_utc.astimezone(self.target_tz)
                return dt_local
            except ValueError as e:
                logger.warning("Error parsing datetime %s: %s", dt_str, e)
                return None

        # Add race sessions to schedule
        sessions = [
            ("FirstPractice", "FP1"),
            ("SecondPractice", "FP2"),
            ("ThirdPractice", "FP3"),
            ("Qualifying", "Qualifying"),
            ("SprintQualifying", "SprintQualifying"),
            ("Sprint", "Sprint"),
        ]

        for session_key, display_name in sessions:
            session = getattr(race, session_key, None)
            if session:
                dt = parse_and_convert(session.date, session.time)
                if dt:
                    schedule_events.append(
                        {
                            "name": display_name,
                            "datetime": dt.isoformat(),
                            "display_time": dt.strftime("%a %H:%M"),
                        }
                    )

        # Add main race
        race_dt = parse_and_convert(race.date, race.time)
        if race_dt:
            schedule_events.append(
                {
                    "name": "Race",
                    "datetime": race_dt.isoformat(),
                    "display_time": race_dt.strftime("%a %H:%M"),
                }
            )

        # Sort events by datetime
        schedule_events.sort(key=lambda x: x["datetime"])

        now = datetime.now(self.target_tz)
        return {
            "race_name": race.raceName,
            "round": race.round,
            "race_key": self._build_race_key(
                season=race.season,
                round_value=race.round,
                race_name=race.raceName,
                circuit_id=race.Circuit.circuitId,
                race_date=race.date,
            ),
            "is_cancelled": race.round in (None, ""),
            "season": race.season,
            "circuit": {
                "circuitId": race.Circuit.circuitId,
                "name": race.Circuit.circuitName,
                "location": race.Circuit.Location.locality,
                "country": race.Circuit.Location.country,
                "lat": race.Circuit.Location.lat or None,
                "long": race.Circuit.Location.long or None,
            },
            "schedule": schedule_events,
            "race_date": race_dt.strftime("%d.%m.%Y") if race_dt else race.date,
            "date": race.date,
            "datetime": race_dt.isoformat() if race_dt else None,
            "is_past": race_dt < now if race_dt else False,
            "circuit_id": race.Circuit.circuitId,
            "circuit_name": race.Circuit.circuitName,
            "country": race.Circuit.Location.country,
            "timezone": self.timezone_str,
        }

    async def _fetch_season_payload(self, year: int) -> list[dict] | None:
        """Fetch raw season races from Jolpica, returning ``None`` when the call fails."""
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{self.api_base_url}/{year}.json"
            logger.info("Fetching season races from %s", url)
            response = await fetch_with_retry(
                client,
                url,
                pacer=get_jolpica_pacer(self.api_base_url),
                logger=logger,
            )
            races = response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            return races if isinstance(races, list) else []
        except Exception as e:
            logger.error("Error fetching season races: %s", e, exc_info=True)
            return None

    async def _get_remote_season_payload(self, year: int) -> list[dict] | None:
        """Return cached raw season races, coalescing concurrent misses per season.

        ``None`` means the upstream call failed recently; an empty list is a real (cached)
        empty calendar, which callers still merge with static cancellations.
        """
        cache_key = (self.api_base_url, year)
        hit, cached = _remote_season_cache.lookup(cache_key)
        if not hit:
            async with get_keyed_loop_lock(_remote_fetch_locks, ("season", *cache_key)):
                hit, cached = _remote_season_cache.lookup(cache_key)
                if not hit:
                    cached = await self._fetch_season_payload(year)
                    _remote_season_cache.store(
                        cache_key,
                        cached,
                        valid=_is_valid_remote_season_payload(cached),
                    )
        if cached is None:
            return None
        return list(cached) if isinstance(cached, list) else []

    async def get_season_races(self, year: int) -> list[dict]:
        """
        Fetch all races for a given season.

        Args:
            year: The season year (e.g., 2025)

        Returns:
            List of race dictionaries with basic info
        """
        races = await self._get_remote_season_payload(year)
        if races is None:
            return []
        result = []
        now = datetime.now(self.target_tz)

        for race in races:
            try:
                race_date_str = race.get("date", "")
                race_time_str = race.get("time", DEFAULT_SESSION_TIME_UTC)
                round_num = self._extract_round_number(race)
                circuit = race.get("Circuit") or {}
                location = circuit.get("Location") or {}

                # Parse race datetime - if parsing fails, use None
                dt_local = None
                is_past = False
                if race_date_str:
                    dt_str = f"{race_date_str}T{race_time_str}"
                    dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    dt_local = dt_utc.astimezone(self.target_tz)
                    is_past = dt_local < now

                result.append(
                    {
                        "round": round_num,
                        "race_key": self._build_race_key(
                            season=race.get("season"),
                            round_value=race.get("round"),
                            race_name=race.get("raceName", ""),
                            circuit_id=circuit.get("circuitId", ""),
                            race_date=race_date_str,
                        ),
                        "race_name": race.get("raceName", ""),
                        "circuit_id": circuit.get("circuitId", ""),
                        "circuit_name": circuit.get("circuitName", ""),
                        "country": location.get("country", ""),
                        "date": race_date_str,
                        "datetime": dt_local.isoformat() if dt_local else None,
                        "is_past": is_past,
                        "is_cancelled": self._is_cancelled_race(race),
                    }
                )
            except (AttributeError, KeyError, ValueError, TypeError) as e:
                race_name = race.get("raceName", "N/A") if isinstance(race, dict) else "N/A"
                logger.warning("Skipping malformed race: %s. Error: %s", race_name, e)
                continue

        return self._merge_static_cancelled_races(year, result)

    async def _fetch_race_payload(self, year: int, round_num: int) -> dict | None:
        """Fetch one raw race from Jolpica; ``None`` covers both failure and an unknown round."""
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{self.api_base_url}/{year}/{round_num}.json"
            logger.info("Fetching race from %s", url)
            response = await fetch_with_retry(
                client,
                url,
                pacer=get_jolpica_pacer(self.api_base_url),
                logger=logger,
            )
            races = response.json().get("MRData", {}).get("RaceTable", {}).get("Races", [])
            return races[0] if isinstance(races, list) and races else None
        except Exception as e:
            logger.error("Error fetching race by round: %s", e, exc_info=True)
            return None

    async def get_race_by_round(self, year: int, round_num: int) -> Optional[dict]:
        """
        Fetch a specific race by year and round number.

        Args:
            year: The season year
            round_num: The round number

        Returns:
            Dictionary with race data including converted times, or None if failed
        """
        cache_key = (self.api_base_url, year, round_num)
        hit, cached = _remote_race_cache.lookup(cache_key)
        if not hit:
            async with get_keyed_loop_lock(_remote_fetch_locks, ("race", *cache_key)):
                hit, cached = _remote_race_cache.lookup(cache_key)
                if not hit:
                    cached = await self._fetch_race_payload(year, round_num)
                    _remote_race_cache.store(
                        cache_key,
                        cached,
                        valid=_is_valid_remote_race_payload(cached),
                    )

        if not isinstance(cached, dict):
            return None
        try:
            # Convert to Race model using Pydantic validation
            return self._convert_race_times(Race(**cached))
        except Exception as e:
            logger.error("Error converting race by round: %s", e, exc_info=True)
            return None

    # =========================================================================
    # Static data methods - load from JSON files instead of API
    # =========================================================================

    @staticmethod
    def get_season_from_static(year: int) -> list[Race]:
        """
        Load season calendar from static JSON file.

        Args:
            year: The season year (e.g., 2025)

        Returns:
            List of Race objects from static data
        """
        if not isinstance(year, int) or not (2000 <= year <= 2100):
            logger.warning("Invalid year value: %s", year)
            return []

        try:
            season_path = _find_static_season_path(year)
            if season_path is None:
                logger.warning("Static season file not found for year: %s", year)
                return []
            races = list(_load_static_season(season_path))
            logger.debug("Loaded %s races from static file for %s", len(races), year)
            return races

        except Exception as e:
            logger.error("Error loading static season data: %s", e, exc_info=True)
            return []

    def get_next_race_from_static(self) -> Optional[dict]:
        """
        Find the next race from static data based on current date.

        Returns:
            Dictionary with race data including converted times, or None if not found
        """
        now = datetime.now(dt_timezone.utc)
        current_year = now.year
        latest_completed: tuple[datetime, Race] | None = None

        # Check current year and next year
        for year in [current_year, current_year + 1]:
            races = F1Service.get_season_from_static(year)

            for race in races:
                try:
                    if race.round in (None, ""):
                        continue

                    # Parse race datetime
                    race_time = race.time or DEFAULT_SESSION_TIME_UTC
                    dt_str = f"{race.date}T{race_time}"
                    race_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    if race_dt.tzinfo is None:
                        race_dt = race_dt.replace(tzinfo=dt_timezone.utc)

                    # Keep the current race selected until NEXT_RACE_GRACE after lights-out, so
                    # the display doesn't flip to the next GP mid-race.
                    if race_dt + NEXT_RACE_GRACE > now:
                        logger.debug(
                            "Found next race from static: %s (%s)", race.raceName, race.date
                        )
                        return self._convert_race_times(race)

                    if latest_completed is None or race_dt > latest_completed[0]:
                        latest_completed = (race_dt, race)

                except Exception as e:
                    logger.warning("Error parsing race date for %s: %s", race.raceName, e)
                    continue

        # During the winter the next season file may intentionally be an empty placeholder.
        # Keep displays useful by showing the most recent completed race until a new calendar
        # is published, rather than degrading every automatic request to an error image.
        if latest_completed is None:
            for race in F1Service.get_season_from_static(current_year - 1):
                try:
                    if race.round in (None, ""):
                        continue
                    race_time = race.time or DEFAULT_SESSION_TIME_UTC
                    race_dt = datetime.fromisoformat(
                        f"{race.date}T{race_time}".replace("Z", "+00:00")
                    )
                    if race_dt.tzinfo is None:
                        race_dt = race_dt.replace(tzinfo=dt_timezone.utc)
                    if race_dt <= now and (
                        latest_completed is None or race_dt > latest_completed[0]
                    ):
                        latest_completed = (race_dt, race)
                except Exception as exc:
                    logger.warning("Error parsing race date for %s: %s", race.raceName, exc)

        if latest_completed is not None:
            race = latest_completed[1]
            logger.debug("Using last completed race during off-season: %s", race.raceName)
            return self._convert_race_times(race)

        logger.warning("No future races found in static data")
        return None

    def get_all_races_from_static(self, year: int) -> list[dict]:
        """
        Get all races for a season from static data with converted times.

        Args:
            year: The season year

        Returns:
            List of race dictionaries with converted times
        """
        races = F1Service.get_season_from_static(year)
        result = []

        for race in races:
            try:
                result.append(self._convert_race_times(race))
            except Exception as e:
                logger.warning("Error converting race %s: %s", race.raceName, e)

        return result

    @staticmethod
    def get_historical_from_static(circuit_id: str) -> HistoricalData:
        """
        Load historical results from static circuits_data.json.

        Args:
            circuit_id: The circuit identifier (e.g., "albert_park")

        Returns:
            HistoricalData object with results or is_new_track=True
        """
        # Map API circuit IDs to our static data IDs
        mapped_id = CIRCUIT_ID_MAP.get(circuit_id, circuit_id)

        try:
            circuits_data = load_circuits_data()

            circuit = circuits_data.get(mapped_id, {})
            historical = circuit.get("historical")

            if not historical:
                logger.info("No historical data for circuit %s", mapped_id)
                return HistoricalData(is_new_track=True)

            qualifying_results = []
            for q in historical.get("qualifying", []):
                try:
                    qualifying_results.append(
                        QualifyingResultEntry(
                            position=q["pos"],
                            driver=DriverInfo(
                                code=q["code"],
                                given_name="",
                                family_name=q["name"],
                            ),
                            constructor=ConstructorInfo(name=q["team"]),
                            q3_time=q.get("time"),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Skipping malformed qualifying history for %s: %s", mapped_id, exc
                    )

            race_results = []
            for r in historical.get("race", []):
                try:
                    race_results.append(
                        RaceResultEntry(
                            position=r["pos"],
                            driver=DriverInfo(
                                code=r["code"],
                                given_name="",
                                family_name=r["name"],
                            ),
                            constructor=ConstructorInfo(name=r["team"]),
                            time=r.get("time"),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Skipping malformed race history for %s: %s", mapped_id, exc)

            return HistoricalData(
                season=historical.get("season"),
                is_new_track=False,
                qualifying_results=qualifying_results,
                race_results=race_results,
            )

        except FileNotFoundError:
            logger.error("Circuits data file not found: %s", get_circuits_data_path())
            return HistoricalData(is_new_track=True)
        except Exception as e:
            logger.error("Error loading historical data for %s: %s", circuit_id, e, exc_info=True)
            return HistoricalData(is_new_track=True)
