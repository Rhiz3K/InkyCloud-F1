"""Standings service for F1 championship standings from Jolpica API."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import httpx

from app.config import config
from app.models import ConstructorStanding, DriverStanding, StandingsData

logger = logging.getLogger(__name__)

JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0

CACHE_TTL_SECONDS = 3600


class CacheEntry:
    """Simple cache entry with TTL."""

    def __init__(self, data: StandingsData, ttl: int = CACHE_TTL_SECONDS):
        self.data = data
        self.expires_at = time.time() + ttl

    def is_valid(self) -> bool:
        return time.time() < self.expires_at


class StandingsService:
    """Service for fetching F1 championship standings from Jolpica API."""

    def __init__(self):
        self.timeout = config.REQUEST_TIMEOUT
        self._cache: dict[str, CacheEntry] = {}

    def _get_cache_key(self, year: int, standings_type: str) -> str:
        return f"{year}_{standings_type}"

    def _get_cached(self, year: int, standings_type: str) -> Optional[StandingsData]:
        key = self._get_cache_key(year, standings_type)
        entry = self._cache.get(key)
        if entry and entry.is_valid():
            logger.debug(f"Cache hit for {key}")
            return entry.data
        return None

    def _set_cache(self, year: int, standings_type: str, data: StandingsData) -> None:
        key = self._get_cache_key(year, standings_type)
        self._cache[key] = CacheEntry(data)
        logger.debug(f"Cached {key}")

    async def _fetch_with_retry(
        self, client: httpx.AsyncClient, url: str, max_retries: int = MAX_RETRIES
    ) -> httpx.Response:
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
        assert last_exception is not None
        raise last_exception

    async def get_driver_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[DriverStanding]:
        if year is None:
            year = datetime.now().year

        cached = self._get_cached(year, "drivers")
        if cached:
            return cached.driver_standings[:limit]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{JOLPICA_BASE_URL}/{year}/driverStandings.json"
                logger.info(f"Fetching driver standings from {url}")
                response = await self._fetch_with_retry(client, url)

                data = response.json()
                standings_list = (
                    data.get("MRData", {})
                    .get("StandingsTable", {})
                    .get("StandingsLists", [])
                )

                if not standings_list:
                    logger.warning(f"No driver standings found for {year}")
                    return []

                standings_data = standings_list[0]
                round_num = int(standings_data.get("round", 0))
                driver_standings = []

                for entry in standings_data.get("DriverStandings", []):
                    driver_data = entry.get("Driver", {})
                    constructors = entry.get("Constructors", [])
                    constructor_name = (
                        constructors[0].get("name", "") if constructors else ""
                    )

                    driver_standings.append(
                        DriverStanding(
                            position=int(entry.get("position", 0)),
                            points=float(entry.get("points", 0)),
                            wins=int(entry.get("wins", 0)),
                            driver_code=driver_data.get("code", ""),
                            driver_name=driver_data.get("familyName", ""),
                            driver_given_name=driver_data.get("givenName", ""),
                            nationality=driver_data.get("nationality", ""),
                            constructor_name=constructor_name,
                        )
                    )

                full_data = StandingsData(
                    season=year,
                    round=round_num,
                    driver_standings=driver_standings,
                )
                self._set_cache(year, "drivers", full_data)

                return driver_standings[:limit]

        except Exception as e:
            logger.error(f"Error fetching driver standings: {e}", exc_info=True)
            return []

    async def get_constructor_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[ConstructorStanding]:
        if year is None:
            year = datetime.now().year

        cached = self._get_cached(year, "constructors")
        if cached:
            return cached.constructor_standings[:limit]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{JOLPICA_BASE_URL}/{year}/constructorStandings.json"
                logger.info(f"Fetching constructor standings from {url}")
                response = await self._fetch_with_retry(client, url)

                data = response.json()
                standings_list = (
                    data.get("MRData", {})
                    .get("StandingsTable", {})
                    .get("StandingsLists", [])
                )

                if not standings_list:
                    logger.warning(f"No constructor standings found for {year}")
                    return []

                standings_data = standings_list[0]
                round_num = int(standings_data.get("round", 0))
                constructor_standings = []

                for entry in standings_data.get("ConstructorStandings", []):
                    constructor_data = entry.get("Constructor", {})

                    constructor_standings.append(
                        ConstructorStanding(
                            position=int(entry.get("position", 0)),
                            points=float(entry.get("points", 0)),
                            wins=int(entry.get("wins", 0)),
                            constructor_name=constructor_data.get("name", ""),
                            nationality=constructor_data.get("nationality", ""),
                        )
                    )

                full_data = StandingsData(
                    season=year,
                    round=round_num,
                    constructor_standings=constructor_standings,
                )
                self._set_cache(year, "constructors", full_data)

                return constructor_standings[:limit]

        except Exception as e:
            logger.error(f"Error fetching constructor standings: {e}", exc_info=True)
            return []

    async def get_all_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> StandingsData:
        if year is None:
            year = datetime.now().year

        cached_drivers = self._get_cached(year, "drivers")
        cached_constructors = self._get_cached(year, "constructors")

        if cached_drivers and cached_constructors:
            return StandingsData(
                season=year,
                round=cached_drivers.round,
                driver_standings=cached_drivers.driver_standings[:limit],
                constructor_standings=cached_constructors.constructor_standings[:limit],
            )

        driver_standings, constructor_standings = await asyncio.gather(
            self.get_driver_standings(year, limit),
            self.get_constructor_standings(year, limit),
        )

        cached_drivers = self._get_cached(year, "drivers")
        round_num = cached_drivers.round if cached_drivers else 0

        return StandingsData(
            season=year,
            round=round_num,
            driver_standings=driver_standings,
            constructor_standings=constructor_standings,
        )
