"""F1 data service using Jolpica API and static data."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import pytz

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

logger = logging.getLogger(__name__)

# Base URL for Jolpica API (without the specific endpoint)
JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"

# Static data paths
ASSETS_DIR = Path(__file__).parent.parent / "assets"
SEASONS_DIR = ASSETS_DIR / "seasons"
CIRCUITS_DATA_PATH = ASSETS_DIR / "circuits_data.json"

# Circuit ID mapping (API uses different IDs than our static data)
CIRCUIT_ID_MAP = {
    "vegas": "las_vegas",  # API uses 'vegas', we use 'las_vegas'
}

# Minimum year for historical data - qualifying data in modern format (Q1/Q2/Q3) started in 2006
# Using 2003 as a safe minimum when qualifying results became reliably available in Ergast
MIN_HISTORICAL_YEAR = 2003

# Retry configuration for rate limiting
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds


class F1Service:
    """Service for fetching F1 race data from Jolpica API."""

    def __init__(self, timezone: str | None = None):
        """
        Initialize F1 service.

        Args:
            timezone: Timezone string (e.g., 'Europe/Prague'). Defaults to config value.
        """
        self.api_url = config.JOLPICA_API_URL
        self.timeout = config.REQUEST_TIMEOUT
        self.timezone_str = timezone or config.DEFAULT_TIMEZONE
        try:
            self.target_tz = pytz.timezone(self.timezone_str)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone {self.timezone_str}, falling back to UTC")
            self.target_tz = pytz.UTC
            self.timezone_str = "UTC"

    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, url: str, max_retries: int = MAX_RETRIES
    ) -> httpx.Response:
        """
        Fetch URL with exponential backoff retry for rate limiting.

        Args:
            client: HTTP client
            url: URL to fetch
            max_retries: Maximum number of retry attempts

        Returns:
            HTTP response

        Raises:
            httpx.HTTPStatusError: If all retries fail
        """
        last_exception: httpx.HTTPStatusError | None = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_exception = e
                    if attempt < max_retries:
                        delay = RETRY_BASE_DELAY * (2**attempt)
                        logger.warning(
                            f"Rate limited (429), retry {attempt + 1}/{max_retries} in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                raise
        # This should only be reached if we exhausted retries on 429
        assert last_exception is not None
        raise last_exception

    async def get_next_race(self) -> Optional[dict]:
        """
        Fetch the next F1 race from Jolpica API.

        Returns:
            Dictionary with race data including converted times, or None if failed
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"Fetching next race from {self.api_url}")
                response = await client.get(self.api_url)
                response.raise_for_status()

                data = response.json()
                f1_response = F1Response(**data)
                race = f1_response.race

                if not race:
                    logger.error("No race found in API response")
                    return None

                # Convert times to Europe/Prague timezone
                return self._convert_race_times(race)

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching race data: {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error fetching race data: {str(e)}", exc_info=True)
            return None

    def _convert_race_times(self, race: Race) -> dict:
        """
        Convert race times from UTC to target timezone.

        Args:
            race: Race object with UTC times

        Returns:
            Dictionary with race data and converted schedule events
        """
        schedule_events = []

        # Helper function to parse and convert time
        def parse_and_convert(date_str: str, time_str: Optional[str]) -> Optional[datetime]:
            """Parse date and time, convert to target timezone."""
            if not time_str:
                # If no time, use noon UTC as default
                time_str = "12:00:00Z"

            # Combine date and time
            dt_str = f"{date_str}T{time_str}"
            try:
                # Parse as UTC
                dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                # Convert to target timezone
                dt_local = dt_utc.astimezone(self.target_tz)
                return dt_local
            except ValueError as e:
                logger.warning(f"Error parsing datetime {dt_str}: {e}")
                return None

        # Add race sessions to schedule
        sessions = [
            ("FirstPractice", "FP1"),
            ("SecondPractice", "FP2"),
            ("ThirdPractice", "FP3"),
            ("Qualifying", "Qualifying"),
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
            "season": race.season,
            "circuit": {
                "circuitId": race.Circuit.circuitId,
                "name": race.Circuit.circuitName,
                "location": race.Circuit.Location.locality,
                "country": race.Circuit.Location.country,
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Step 1: Find the most recent previous race at this circuit
                previous_season = await self._find_previous_race_season(
                    client, circuit_id, current_season
                )

                if previous_season is None:
                    logger.info(f"No previous race found for circuit {circuit_id}")
                    return HistoricalData(is_new_track=True)

                logger.info(f"Found previous race at {circuit_id} in season {previous_season}")

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
            logger.error(f"Error fetching historical data: {e}", exc_info=True)
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
        url = f"{JOLPICA_BASE_URL}/circuits/{circuit_id}/races.json?limit=100"
        logger.info(f"Fetching race history for circuit {circuit_id}")

        response = await self._fetch_with_retry(client, url)

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
        url = f"{JOLPICA_BASE_URL}/{season}/circuits/{circuit_id}/qualifying.json?limit=3"
        logger.info(f"Fetching qualifying results: {url}")

        try:
            response = await self._fetch_with_retry(client, url)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not races:
                return []

            qualifying_data = races[0].get("QualifyingResults", [])
            results = []

            for entry in qualifying_data[:3]:
                driver_data = entry.get("Driver", {})
                constructor_data = entry.get("Constructor", {})

                results.append(
                    QualifyingResultEntry(
                        position=int(entry.get("position", 0)),
                        driver=DriverInfo(
                            code=driver_data.get("code", ""),
                            given_name=driver_data.get("givenName", ""),
                            family_name=driver_data.get("familyName", ""),
                        ),
                        constructor=ConstructorInfo(
                            name=constructor_data.get("name", ""),
                        ),
                        q3_time=entry.get("Q3"),
                    )
                )

            return results

        except Exception as e:
            logger.warning(f"Failed to fetch qualifying results: {e}")
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
        url = f"{JOLPICA_BASE_URL}/{season}/circuits/{circuit_id}/results.json?limit=3"
        logger.info(f"Fetching race results: {url}")

        try:
            response = await self._fetch_with_retry(client, url)

            data = response.json()
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

            if not races:
                return []

            results_data = races[0].get("Results", [])
            results = []

            for entry in results_data[:3]:
                driver_data = entry.get("Driver", {})
                constructor_data = entry.get("Constructor", {})
                time_data = entry.get("Time", {})

                results.append(
                    RaceResultEntry(
                        position=int(entry.get("position", 0)),
                        driver=DriverInfo(
                            code=driver_data.get("code", ""),
                            given_name=driver_data.get("givenName", ""),
                            family_name=driver_data.get("familyName", ""),
                        ),
                        constructor=ConstructorInfo(
                            name=constructor_data.get("name", ""),
                        ),
                        time=time_data.get("time"),
                    )
                )

            return results

        except Exception as e:
            logger.warning(f"Failed to fetch race results: {e}")
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{JOLPICA_BASE_URL}/{year}.json"
                logger.info(f"Fetching season races from {url}")
                response = await self._fetch_with_retry(client, url)

                data = response.json()
                races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

                result = []
                now = datetime.now(self.target_tz)

                for race in races:
                    try:
                        race_date_str = race.get("date", "")
                        race_time_str = race.get("time", "14:00:00Z")

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
                                "round": int(race.get("round", 0)),
                                "race_name": race.get("raceName", ""),
                                "circuit_id": race.get("Circuit", {}).get("circuitId", ""),
                                "circuit_name": race.get("Circuit", {}).get("circuitName", ""),
                                "country": race.get("Circuit", {})
                                .get("Location", {})
                                .get("country", ""),
                                "date": race_date_str,
                                "datetime": dt_local.isoformat() if dt_local else None,
                                "is_past": is_past,
                            }
                        )
                    except (KeyError, ValueError, TypeError) as e:
                        race_name = race.get("raceName", "N/A")
                        logger.warning(f"Skipping malformed race: {race_name}. Error: {e}")
                        continue

                return result

        except Exception as e:
            logger.error(f"Error fetching season races: {e}", exc_info=True)
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{JOLPICA_BASE_URL}/{year}/{round_num}.json"
                logger.info(f"Fetching race from {url}")
                response = await self._fetch_with_retry(client, url)

                data = response.json()
                races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])

                if not races:
                    return None

                race_data = races[0]
                # Convert to Race model using Pydantic validation
                race = Race(**race_data)

                return self._convert_race_times(race)

        except Exception as e:
            logger.error(f"Error fetching race by round: {e}", exc_info=True)
            return None

    # =========================================================================
    # Static data methods - load from JSON files instead of API
    # =========================================================================

    def get_season_from_static(self, year: int) -> list[Race]:
        """
        Load season calendar from static JSON file.

        Args:
            year: The season year (e.g., 2025)

        Returns:
            List of Race objects from static data
        """
        json_path = SEASONS_DIR / f"{year}.json"

        if not json_path.exists():
            logger.warning(f"Static season file not found: {json_path}")
            return []

        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)

            races = []
            for race_data in data.get("races", []):
                try:
                    races.append(Race(**race_data))
                except Exception as e:
                    logger.warning(f"Failed to parse race: {race_data.get('raceName')}: {e}")

            logger.info(f"Loaded {len(races)} races from static file for {year}")
            return races

        except Exception as e:
            logger.error(f"Error loading static season data: {e}", exc_info=True)
            return []

    def get_next_race_from_static(self) -> Optional[dict]:
        """
        Find the next race from static data based on current date.

        Returns:
            Dictionary with race data including converted times, or None if not found
        """
        now = datetime.now(timezone.utc)
        current_year = now.year

        # Check current year and next year
        for year in [current_year, current_year + 1]:
            races = self.get_season_from_static(year)

            for race in races:
                try:
                    # Parse race datetime
                    race_time = race.time or "14:00:00Z"
                    dt_str = f"{race.date}T{race_time}"
                    race_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

                    # If race is in the future, this is the next race
                    if race_dt > now:
                        logger.info(f"Found next race from static: {race.raceName} ({race.date})")
                        return self._convert_race_times(race)

                except Exception as e:
                    logger.warning(f"Error parsing race date for {race.raceName}: {e}")
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
        races = self.get_season_from_static(year)
        result = []

        for race in races:
            try:
                result.append(self._convert_race_times(race))
            except Exception as e:
                logger.warning(f"Error converting race {race.raceName}: {e}")

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
                logger.info(f"No historical data for circuit {mapped_id}")
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
            logger.error(f"Circuits data file not found: {CIRCUITS_DATA_PATH}")
            return HistoricalData(is_new_track=True)
        except Exception as e:
            logger.error(f"Error loading historical data for {circuit_id}: {e}", exc_info=True)
            return HistoricalData(is_new_track=True)
