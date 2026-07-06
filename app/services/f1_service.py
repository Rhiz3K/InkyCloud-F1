"""F1 data service using Jolpica API and static data."""

import json
import logging
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import config
from app.models import (
    ConstructorInfo,
    DriverInfo,
    F1Response,
    HistoricalData,
    QualifyingResultEntry,
    Race,
    RaceResultEntry,
)
from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.http_client import get_shared_http_client
from app.utils.http import fetch_with_retry
from app.utils.result_entries import ResultEntry, get_result_mapping, sort_entries_by_position
from app.utils.timezones import UTC, ZoneInfoNotFoundError, get_timezone, normalize_timezone

logger = logging.getLogger(__name__)

# Static data paths
ASSETS_DIR = Path(__file__).parent.parent / "assets"
SEASONS_DIR = ASSETS_DIR / "seasons"
CIRCUITS_DATA_PATH = ASSETS_DIR / "circuits_data.json"

# Minimum year for historical data - qualifying data in modern format (Q1/Q2/Q3) started in 2006
# Using 2003 as a safe minimum when qualifying results became reliably available in Ergast
MIN_HISTORICAL_YEAR = 2003

# Default UTC time used when a session/race time is missing from the data. Shared by the
# display conversion and the next-race selection so the two never disagree at the boundary.
DEFAULT_SESSION_TIME_UTC = "12:00:00Z"
# Keep a race selected as "next" until this long after lights-out, so the calendar doesn't
# advance to the following Grand Prix the instant the current race starts.
NEXT_RACE_GRACE = timedelta(hours=4)


def _driver_info_from_result(entry: ResultEntry) -> DriverInfo:
    """Build driver info from a normalized result entry."""
    driver_data = get_result_mapping(entry, "Driver")
    return DriverInfo(
        code=driver_data.get("code", ""),
        given_name=driver_data.get("givenName", ""),
        family_name=driver_data.get("familyName", ""),
    )


def _constructor_info_from_result(entry: ResultEntry) -> ConstructorInfo:
    """Build constructor info from a normalized result entry."""
    constructor_data = get_result_mapping(entry, "Constructor")
    return ConstructorInfo(name=constructor_data.get("name", ""))


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

    async def get_next_race(self) -> Optional[dict]:
        """
        Fetch the next F1 race from Jolpica API.

        Returns:
            Dictionary with race data including converted times, or None if failed
        """
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            logger.info("Fetching next race from %s", self.api_url)
            response = await fetch_with_retry(client, self.api_url, logger=logger)

            data = response.json()
            f1_response = F1Response(**data)
            race = f1_response.race

            if not race:
                logger.error("No race found in API response")
                return None

            # Convert times to Europe/Prague timezone
            return self._convert_race_times(race)

        except httpx.HTTPError as e:
            logger.error("HTTP error fetching race data: %s", str(e), exc_info=True)
            return None
        except Exception as e:
            logger.error("Error fetching race data: %s", str(e), exc_info=True)
            return None

    @staticmethod
    def _has_scheduled_round(race_data: dict) -> bool:
        """Return True when the race has a valid round number assigned."""
        round_value = race_data.get("round")

        if round_value in (None, ""):
            return False

        try:
            return int(str(round_value)) > 0
        except (TypeError, ValueError):
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
        except (TypeError, ValueError):
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
            "timezone": self.timezone_str,
        }

    async def get_historical_data(self, circuit_id: str, current_season: int) -> HistoricalData:
        """
        Fetch historical race results for the most recent previous race at this circuit.

        Logic:
        1. Fetch all races held at this circuit.
        2. Find the most recent season < current_season.
        3. Fetch qualifying and race results for that season.
        4. If no previous race exists, mark as new track.

        Args:
            circuit_id: The circuit identifier (e.g., "albert_park")
            current_season: The current/upcoming race season year

        Returns:
            HistoricalData object with results or is_new_track=True
        """
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            # Step 1: Find the most recent previous race at this circuit
            previous_season = await self._find_previous_race_season(
                client, circuit_id, current_season
            )

            if previous_season is None:
                logger.info("No previous race found for circuit %s", circuit_id)
                return HistoricalData(is_new_track=True)

            logger.info("Found previous race at %s in season %s", circuit_id, previous_season)

            # Step 2: Fetch qualifying and race results for that season
            qualifying_results = await self._fetch_qualifying_results(
                client, circuit_id, previous_season
            )
            race_results = await self._fetch_race_results(client, circuit_id, previous_season)

            return HistoricalData(
                season=previous_season,
                is_new_track=False,
                qualifying_results=qualifying_results,
                race_results=race_results,
            )

        except Exception as e:
            logger.error("Error fetching historical data: %s", e, exc_info=True)
            return HistoricalData(is_new_track=True)

    async def _find_previous_race_season(
        self, client: httpx.AsyncClient, circuit_id: str, current_season: int
    ) -> Optional[int]:
        """
        Find the most recent season where a race was held at this circuit.

        Only considers seasons >= MIN_HISTORICAL_YEAR to ensure qualifying
        data is available in a usable format.

        Args:
            client: HTTP client
            circuit_id: The circuit identifier
            current_season: The current season to compare against

        Returns:
            The previous season year, or None if no previous race exists
        """
        url = f"{self.api_base_url}/circuits/{circuit_id}/races.json?limit=100"
        logger.info("Fetching race history for circuit %s", circuit_id)

        response = await fetch_with_retry(client, url, logger=logger)

        data = response.json()
        races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

        # Filter races from previous seasons, ensuring they're recent enough
        # to have reliable qualifying data (MIN_HISTORICAL_YEAR onwards)
        previous_seasons = [
            int(race["season"])
            for race in races
            if int(race["season"]) < current_season and int(race["season"]) >= MIN_HISTORICAL_YEAR
        ]

        if not previous_seasons:
            return None

        return max(previous_seasons)

    async def _fetch_qualifying_results(
        self, client: httpx.AsyncClient, circuit_id: str, season: int
    ) -> list[QualifyingResultEntry]:
        """
        Fetch top 3 qualifying results for a specific circuit and season.

        Args:
            client: HTTP client
            circuit_id: The circuit identifier
            season: The season year

        Returns:
            List of QualifyingResultEntry objects (top 3)
        """
        url = f"{self.api_base_url}/{season}/circuits/{circuit_id}/qualifying.json?limit=100"
        logger.info("Fetching qualifying results: %s", url)

        try:
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not races:
                return []

            results = []
            qualifying_data = sort_entries_by_position(races[0].get("QualifyingResults"))

            for position, entry in qualifying_data[:3]:
                results.append(
                    QualifyingResultEntry(
                        position=position,
                        driver=_driver_info_from_result(entry),
                        constructor=_constructor_info_from_result(entry),
                        q3_time=entry.get("Q3"),
                    )
                )

            return results

        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to fetch qualifying results: %s", e)
            return []

    async def _fetch_race_results(
        self, client: httpx.AsyncClient, circuit_id: str, season: int
    ) -> list[RaceResultEntry]:
        """
        Fetch top 3 race results for a specific circuit and season.

        Args:
            client: HTTP client
            circuit_id: The circuit identifier
            season: The season year

        Returns:
            List of RaceResultEntry objects (top 3)
        """
        url = f"{self.api_base_url}/{season}/circuits/{circuit_id}/results.json?limit=100"
        logger.info("Fetching race results: %s", url)

        try:
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not races:
                return []

            results = []
            results_data = sort_entries_by_position(races[0].get("Results"))

            for position, entry in results_data[:3]:
                time_data = get_result_mapping(entry, "Time")

                results.append(
                    RaceResultEntry(
                        position=position,
                        driver=_driver_info_from_result(entry),
                        constructor=_constructor_info_from_result(entry),
                        time=time_data.get("time"),
                    )
                )

            return results

        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to fetch race results: %s", e)
            return []

    async def get_season_races(self, year: int) -> list[dict]:
        """
        Fetch all races for a given season.

        Args:
            year: The season year (e.g., 2025)

        Returns:
            List of race dictionaries with basic info
        """
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{self.api_base_url}/{year}.json"
            logger.info("Fetching season races from %s", url)
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

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
                except (KeyError, ValueError, TypeError) as e:
                    race_name = race.get("raceName", "N/A")
                    logger.warning("Skipping malformed race: %s. Error: %s", race_name, e)
                    continue

            return self._merge_static_cancelled_races(year, result)

        except Exception as e:
            logger.error("Error fetching season races: %s", e, exc_info=True)
            return []

    async def get_race_by_round(self, year: int, round_num: int) -> Optional[dict]:
        """
        Fetch a specific race by year and round number.

        Args:
            year: The season year
            round_num: The round number

        Returns:
            Dictionary with race data including converted times, or None if failed
        """
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{self.api_base_url}/{year}/{round_num}.json"
            logger.info("Fetching race from %s", url)
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not races:
                return None

            race_data = races[0]
            # Convert to Race model using Pydantic validation
            race = Race(**race_data)

            return self._convert_race_times(race)

        except Exception as e:
            logger.error("Error fetching race by round: %s", e, exc_info=True)
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

        # Build allowlist of valid season files that exist
        allowed_files: dict[int, Path] = {}
        for season_file in SEASONS_DIR.glob("*.json"):
            try:
                file_year = int(season_file.stem)
                if 2000 <= file_year <= 2100:
                    allowed_files[file_year] = season_file.resolve()
            except ValueError:
                continue

        if year not in allowed_files:
            logger.warning("Static season file not found for year: %s", year)
            return []

        resolved_path = allowed_files[year]

        try:
            with open(resolved_path, encoding="utf-8") as season_handle:
                data = json.load(season_handle)

            races = []
            for race_data in data.get("races", []):
                try:
                    races.append(Race(**race_data))
                except Exception as e:
                    logger.warning("Failed to parse race: %s: %s", race_data.get("raceName"), e)

            logger.info("Loaded %s races from static file for %s", len(races), year)
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

                    # Keep the current race selected until NEXT_RACE_GRACE after lights-out, so
                    # the display doesn't flip to the next GP mid-race.
                    if race_dt + NEXT_RACE_GRACE > now:
                        logger.info(
                            "Found next race from static: %s (%s)", race.raceName, race.date
                        )
                        return self._convert_race_times(race)

                except Exception as e:
                    logger.warning("Error parsing race date for %s: %s", race.raceName, e)
                    continue

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
            with open(CIRCUITS_DATA_PATH, encoding="utf-8") as f:
                circuits_data = json.load(f)

            circuit = circuits_data.get(mapped_id, {})
            historical = circuit.get("historical")

            if not historical:
                logger.info("No historical data for circuit %s", mapped_id)
                return HistoricalData(is_new_track=True)

            # Parse qualifying results
            qualifying_results = []
            for q in historical.get("qualifying", []):
                qualifying_results.append(
                    QualifyingResultEntry(
                        position=q["pos"],
                        driver=DriverInfo(
                            code=q["code"],
                            given_name="",  # Not stored in static data
                            family_name=q["name"],
                        ),
                        constructor=ConstructorInfo(name=q["team"]),
                        q3_time=q.get("time"),
                    )
                )

            # Parse race results
            race_results = []
            for r in historical.get("race", []):
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

            return HistoricalData(
                season=historical.get("season"),
                is_new_track=False,
                qualifying_results=qualifying_results,
                race_results=race_results,
            )

        except FileNotFoundError:
            logger.error("Circuits data file not found: %s", CIRCUITS_DATA_PATH)
            return HistoricalData(is_new_track=True)
        except Exception as e:
            logger.error("Error loading historical data for %s: %s", circuit_id, e, exc_info=True)
            return HistoricalData(is_new_track=True)
