"""Standings service for F1 championship standings from Jolpica API."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import config
from app.models import ConstructorStanding, DriverStanding, StandingsData
from app.services.http_client import get_shared_http_client
from app.utils.http import fetch_with_retry

logger = logging.getLogger(__name__)

JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"

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

    _shared_cache: dict[str, CacheEntry] = {}

    def __init__(self):
        self.timeout = config.REQUEST_TIMEOUT
        self._cache = self._shared_cache

    @staticmethod
    def _get_cache_key(year: int, standings_type: str) -> str:
        return f"{year}_{standings_type}"

    def _get_cached(self, year: int, standings_type: str) -> Optional[StandingsData]:
        key = self._get_cache_key(year, standings_type)
        entry = self._cache.get(key)
        if entry and entry.is_valid():
            logger.debug("Cache hit for %s", key)
            return entry.data
        return None

    def _set_cache(self, year: int, standings_type: str, data: StandingsData) -> None:
        key = self._get_cache_key(year, standings_type)
        self._cache[key] = CacheEntry(data)
        logger.debug("Cached %s", key)

    async def get_driver_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[DriverStanding]:
        if year is None:
            year = datetime.now(timezone.utc).year

        cached = self._get_cached(year, "drivers")
        if cached:
            return cached.driver_standings[:limit]

        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{JOLPICA_BASE_URL}/{year}/driverStandings.json"
            logger.info("Fetching driver standings from %s", url)
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            standings_list = (
                data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
            )

            if not standings_list:
                logger.warning("No driver standings found for %s", year)
                return []

            standings_data = standings_list[0]
            round_num = int(standings_data.get("round", 0))
            driver_standings = []

            for entry in standings_data.get("DriverStandings", []):
                driver_data = entry.get("Driver", {})
                constructors = entry.get("Constructors", [])
                constructor_name = constructors[0].get("name", "") if constructors else ""

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
            logger.error("Error fetching driver standings: %s", e, exc_info=True)
            return []

    async def get_constructor_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[ConstructorStanding]:
        if year is None:
            year = datetime.now(timezone.utc).year

        cached = self._get_cached(year, "constructors")
        if cached:
            return cached.constructor_standings[:limit]

        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            url = f"{JOLPICA_BASE_URL}/{year}/constructorStandings.json"
            logger.info("Fetching constructor standings from %s", url)
            response = await fetch_with_retry(client, url, logger=logger)

            data = response.json()
            standings_list = (
                data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
            )

            if not standings_list:
                logger.warning("No constructor standings found for %s", year)
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
            logger.error("Error fetching constructor standings: %s", e, exc_info=True)
            return []

    async def get_all_standings(self, year: Optional[int] = None, limit: int = 10) -> StandingsData:
        if year is None:
            year = datetime.now(timezone.utc).year

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
