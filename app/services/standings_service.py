"""Standings service for F1 championship standings from Jolpica API."""

import asyncio
import logging
import time
import weakref
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import config
from app.models import ConstructorStanding, DriverStanding, StandingsData
from app.services.http_client import get_shared_http_client
from app.utils.f1_season import is_supported_f1_season
from app.utils.http import fetch_with_retry
from app.utils.jolpica import get_jolpica_base_url

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600
NEGATIVE_CACHE_TTL_SECONDS = 60
_fetch_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[int, str], asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _get_fetch_lock(year: int, standings_type: str) -> asyncio.Lock:
    """Return an event-loop-local lock for one season and standings type."""
    loop = asyncio.get_running_loop()
    locks = _fetch_locks.setdefault(loop, {})
    return locks.setdefault((year, standings_type), asyncio.Lock())


class CacheEntry:
    """Simple cache entry with TTL."""

    def __init__(self, data: StandingsData, ttl: int = CACHE_TTL_SECONDS):
        """Store standings data with an absolute wall-clock expiry timestamp."""
        self.data = data
        self.expires_at = time.time() + ttl

    def is_valid(self) -> bool:
        """Return whether this entry remains inside its TTL."""
        return time.time() < self.expires_at


class StandingsService:
    """Service for fetching F1 championship standings from Jolpica API."""

    _shared_cache: dict[str, CacheEntry] = {}
    _negative_cache: dict[str, float] = {}

    def __init__(self):
        """Initialize service configuration and attach the shared cache."""
        self.timeout = config.REQUEST_TIMEOUT
        self._cache = self._shared_cache

    @staticmethod
    def _get_cache_key(year: int, standings_type: str) -> str:
        """Build the shared-cache key for a season and standings category."""
        return f"{year}_{standings_type}"

    def _get_cached(self, year: int, standings_type: str) -> Optional[StandingsData]:
        """Return valid cached standings or ``None`` after a miss or expiry."""
        key = self._get_cache_key(year, standings_type)
        entry = self._cache.get(key)
        if entry and entry.is_valid():
            logger.debug("Cache hit for %s", key)
            return entry.data
        return None

    def _set_cache(self, year: int, standings_type: str, data: StandingsData) -> None:
        """Store standings data under its season/category cache key."""
        key = self._get_cache_key(year, standings_type)
        self._cache[key] = CacheEntry(data)
        logger.debug("Cached %s", key)

    @classmethod
    def _is_negative_cached(cls, key: str) -> bool:
        """Return whether a recent empty upstream result is still cached."""
        expires_at = cls._negative_cache.get(key)
        if expires_at is None:
            return False
        if time.time() < expires_at:
            return True
        cls._negative_cache.pop(key, None)
        return False

    @staticmethod
    def _derive_standings_base_url(api_url: str) -> str:
        """Derive the season endpoint root from either a Jolpica base URL or race endpoint."""
        return get_jolpica_base_url(api_url)

    @staticmethod
    def _is_missing_standings_error(exc: httpx.HTTPStatusError) -> bool:
        """Return whether an HTTP error represents unavailable standings."""
        return exc.response.status_code == 404

    async def get_driver_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[DriverStanding]:
        """Return bounded driver standings with cache and fetch coalescing."""
        if year is None:
            year = datetime.now(timezone.utc).year

        if not is_supported_f1_season(year):
            raise ValueError(f"Unsupported F1 season: {year}")

        key = self._get_cache_key(year, "drivers")
        cached = self._get_cached(year, "drivers")
        if cached:
            return cached.driver_standings[:limit]
        if self._is_negative_cached(key):
            return []

        async with _get_fetch_lock(year, "drivers"):
            cached = self._get_cached(year, "drivers")
            if cached:
                return cached.driver_standings[:limit]
            if self._is_negative_cached(key):
                return []
            result = await self._fetch_driver_standings(year, limit)
            if result:
                self._negative_cache.pop(key, None)
            else:
                self._negative_cache[key] = time.time() + NEGATIVE_CACHE_TTL_SECONDS
            return result

    async def _fetch_driver_standings(self, year: int, limit: int) -> list[DriverStanding]:
        """Fetch, normalize, cache, and bound driver standings from Jolpica."""
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            base_url = self._derive_standings_base_url(str(config.JOLPICA_API_URL))
            url = f"{base_url}/{year}/driverStandings.json"
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

        except httpx.HTTPStatusError as e:
            if self._is_missing_standings_error(e):
                logger.warning(
                    "Driver standings are not available for %s at %s", year, e.request.url
                )
                return []
            logger.error("HTTP error fetching driver standings: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("Error fetching driver standings: %s", e, exc_info=True)
            return []

    async def get_constructor_standings(
        self, year: Optional[int] = None, limit: int = 10
    ) -> list[ConstructorStanding]:
        """Return bounded constructor standings with cache and fetch coalescing."""
        if year is None:
            year = datetime.now(timezone.utc).year

        if not is_supported_f1_season(year):
            raise ValueError(f"Unsupported F1 season: {year}")

        key = self._get_cache_key(year, "constructors")
        cached = self._get_cached(year, "constructors")
        if cached:
            return cached.constructor_standings[:limit]
        if self._is_negative_cached(key):
            return []

        async with _get_fetch_lock(year, "constructors"):
            cached = self._get_cached(year, "constructors")
            if cached:
                return cached.constructor_standings[:limit]
            if self._is_negative_cached(key):
                return []
            result = await self._fetch_constructor_standings(year, limit)
            if result:
                self._negative_cache.pop(key, None)
            else:
                self._negative_cache[key] = time.time() + NEGATIVE_CACHE_TTL_SECONDS
            return result

    async def _fetch_constructor_standings(
        self, year: int, limit: int
    ) -> list[ConstructorStanding]:
        """Fetch, normalize, cache, and bound constructor standings from Jolpica."""
        try:
            client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
            base_url = self._derive_standings_base_url(str(config.JOLPICA_API_URL))
            url = f"{base_url}/{year}/constructorStandings.json"
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

        except httpx.HTTPStatusError as e:
            if self._is_missing_standings_error(e):
                logger.warning(
                    "Constructor standings are not available for %s at %s", year, e.request.url
                )
                return []
            logger.error("HTTP error fetching constructor standings: %s", e, exc_info=True)
            return []
        except Exception as e:
            logger.error("Error fetching constructor standings: %s", e, exc_info=True)
            return []

    async def get_all_standings(self, year: Optional[int] = None, limit: int = 10) -> StandingsData:
        """Return a combined driver and constructor standings snapshot."""
        if year is None:
            year = datetime.now(timezone.utc).year

        cached_drivers = self._get_cached(year, "drivers")
        cached_constructors = self._get_cached(year, "constructors")

        if cached_drivers and cached_constructors:
            return StandingsData(
                season=year,
                round=max(cached_drivers.round, cached_constructors.round),
                driver_standings=cached_drivers.driver_standings[:limit],
                constructor_standings=cached_constructors.constructor_standings[:limit],
            )

        driver_standings, constructor_standings = await asyncio.gather(
            self.get_driver_standings(year, limit),
            self.get_constructor_standings(year, limit),
        )

        cached_drivers = self._get_cached(year, "drivers")
        cached_constructors = self._get_cached(year, "constructors")
        round_num = max(
            cached_drivers.round if cached_drivers else 0,
            cached_constructors.round if cached_constructors else 0,
        )

        return StandingsData(
            season=year,
            round=round_num,
            driver_standings=driver_standings,
            constructor_standings=constructor_standings,
        )
