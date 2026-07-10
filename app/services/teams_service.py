"""Teams service for F1 teams and drivers - uses Wikipedia scraped data with API fallback."""

import asyncio
import json
import logging
import time
import unicodedata
import weakref
from pathlib import Path
from typing import Optional

import httpx

from app.config import config
from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services.http_client import get_shared_http_client
from app.utils.f1_season import get_current_f1_season, is_supported_f1_season
from app.utils.http import fetch_with_retry
from app.utils.jolpica import get_jolpica_base_url
from app.utils.standings_metadata import get_season_driver_number_by_id

logger = logging.getLogger(__name__)

SEASONS_DIR = Path(__file__).parent.parent / "assets" / "seasons"

CACHE_TTL_SECONDS = 3600
NEGATIVE_CACHE_TTL_SECONDS = 60
_fetch_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[int, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _get_fetch_lock(year: int) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _fetch_locks.setdefault(loop, {})
    return locks.setdefault(year, asyncio.Lock())


def is_teams_data_cacheable(data: TeamsData) -> bool:
    """Return True only for complete teams data safe to keep in caches/prebuilt images."""
    return bool(data.teams) and data.standings_complete


MANUAL_DRIVER_NUMBER_OVERRIDE_NAME_FALLBACKS = {
    2026: {
        "lando norris": "norris",
        "max verstappen": "verstappen",
        "isack hadjar": "hadjar",
        "liam lawson": "lawson",
        "arvid lindblad": "lindblad",
        "gabriel bortoleto": "bortoleto",
        "nico hulkenberg": "hulkenberg",
        "sergio perez": "perez",
        "valtteri bottas": "bottas",
    }
}


def get_available_teams_years() -> list[int]:
    """Return every season with bundled teams data."""
    return sorted(
        int(path.stem.split("_", 1)[0])
        for path in SEASONS_DIR.glob("*_teams.json")
        if path.stem.split("_", 1)[0].isdigit()
    )


def get_default_teams_year() -> int:
    """Resolve the newest season that has bundled teams data."""
    current_year = get_current_f1_season()
    current_path = SEASONS_DIR / f"{current_year}_teams.json"
    if current_path.exists():
        return current_year

    available_years = get_available_teams_years()
    if not available_years:
        return current_year

    eligible_years = [year for year in available_years if year <= current_year]
    if eligible_years:
        return eligible_years[-1]

    return available_years[-1]


class CacheEntry:
    def __init__(self, data: TeamsData, ttl: int = CACHE_TTL_SECONDS):
        self.data = data
        self.expires_at = time.time() + ttl

    def is_valid(self) -> bool:
        return time.time() < self.expires_at


class TeamsService:
    _shared_cache: dict[str, CacheEntry] = {}
    _negative_cache: dict[int, float] = {}

    def __init__(self):
        self.timeout = config.REQUEST_TIMEOUT
        self._cache = self._shared_cache

    @staticmethod
    def _get_cache_key(year: int) -> str:
        return f"teams_{year}"

    def _get_cached(self, year: int) -> Optional[TeamsData]:
        key = self._get_cache_key(year)
        entry = self._cache.get(key)
        if entry and entry.is_valid():
            logger.debug("Cache hit for %s", key)
            return entry.data
        return None

    def _set_cache(self, year: int, data: TeamsData) -> None:
        key = self._get_cache_key(year)
        self._cache[key] = CacheEntry(data)
        logger.debug("Cached %s", key)

    @staticmethod
    def _validate_year(year: int) -> bool:
        """Validate year before creating cache keys, locks, or outbound requests."""
        return is_supported_f1_season(year)

    @classmethod
    def _is_negative_cached(cls, year: int) -> bool:
        expires_at = cls._negative_cache.get(year)
        if expires_at is None:
            return False
        if time.time() < expires_at:
            return True
        cls._negative_cache.pop(year, None)
        return False

    @staticmethod
    def _normalize_driver_lookup_key(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(ascii_name.replace(" Jr.", "").replace(" jr.", "").lower().split())

    @classmethod
    def _get_override_driver_id(cls, driver: TeamDriverEntry, season: int) -> str | None:
        if get_season_driver_number_by_id(driver.driver_id, season) is not None:
            return driver.driver_id

        name_fallbacks = MANUAL_DRIVER_NUMBER_OVERRIDE_NAME_FALLBACKS.get(season, {})
        normalized_name = cls._normalize_driver_lookup_key(driver.name)
        return name_fallbacks.get(normalized_name, driver.driver_id or None)

    @classmethod
    def _apply_manual_overrides(cls, teams_data: TeamsData) -> TeamsData:
        for team in teams_data.teams:
            for driver in team.drivers:
                override_driver_id = cls._get_override_driver_id(driver, teams_data.season)
                if override_driver_id is None:
                    continue
                override_number = get_season_driver_number_by_id(
                    override_driver_id, teams_data.season
                )
                if override_number is not None:
                    driver.driver_number = override_number

        return teams_data

    def _load_from_json(self, year: int) -> Optional[TeamsData]:
        if not self._validate_year(year):
            logger.warning("Invalid year requested: %s", year)
            return None

        # Security: year is validated to be integer 1950-2030, filename cannot contain
        # path traversal characters. Using str() for explicit type conversion.
        safe_year = str(year)
        if not safe_year.isdigit():
            return None
        safe_filename = f"{safe_year}_teams.json"
        json_path = SEASONS_DIR / safe_filename

        # Verify the resolved path is within SEASONS_DIR (defense in depth)
        resolved_path = json_path.resolve()
        seasons_resolved = SEASONS_DIR.resolve()
        if not str(resolved_path).startswith(str(seasons_resolved)):
            logger.warning("Path traversal attempt blocked for year: %s", year)
            return None

        if not resolved_path.exists():
            logger.debug("No JSON file found at %s", resolved_path)
            return None

        try:
            with open(resolved_path, encoding="utf-8") as f:
                data = json.load(f)

            teams = []
            for team_data in data.get("teams", []):
                drivers = []
                for driver_data in team_data.get("drivers", []):
                    drivers.append(
                        TeamDriverEntry(
                            driver_id=driver_data.get("driver_id", ""),
                            driver_number=driver_data.get("number"),
                            name=driver_data.get("name", ""),
                            nationality=driver_data.get("nationality", ""),
                            rounds=driver_data.get("rounds", "All"),
                        )
                    )

                teams.append(
                    TeamEntry(
                        constructor_name=team_data.get("constructor", ""),
                        entrant=team_data.get("entrant", ""),
                        chassis=team_data.get("chassis", ""),
                        power_unit=team_data.get("power_unit", ""),
                        drivers=drivers,
                    )
                )

            result = TeamsData(season=data.get("year", year), teams=teams)
            result = self._apply_manual_overrides(result)
            logger.info("Loaded %d teams from %s", len(teams), json_path)
            return result

        except Exception as e:
            logger.error("Error loading JSON file: %s", e, exc_info=True)
            return None

    async def _fetch_standings(self, year: int) -> tuple[dict, dict]:
        """Fetch driver and constructor standings from API."""
        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        api_base = get_jolpica_base_url()
        driver_standings_url = f"{api_base}/{year}/driverStandings.json"
        constructor_standings_url = f"{api_base}/{year}/constructorStandings.json"

        logger.info("Fetching standings for %d", year)

        driver_resp, constructor_resp = await asyncio.gather(
            fetch_with_retry(client, driver_standings_url, logger=logger),
            fetch_with_retry(client, constructor_standings_url, logger=logger),
        )

        driver_standings: dict[str, dict] = {}
        driver_data = driver_resp.json()
        standings_lists = (
            driver_data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        )
        if standings_lists:
            for entry in standings_lists[0].get("DriverStandings", []):
                driver_info = entry.get("Driver", {})
                given = driver_info.get("givenName", "")
                family = driver_info.get("familyName", "")
                full_name = f"{given} {family}"

                driver_standings[full_name] = {
                    "position": int(entry.get("position", 0)),
                    "points": float(entry.get("points", 0)),
                    "wins": int(entry.get("wins", 0)),
                }

        constructor_standings: dict[str, dict] = {}
        constructor_data = constructor_resp.json()
        standings_lists = (
            constructor_data.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
        )
        if standings_lists:
            for entry in standings_lists[0].get("ConstructorStandings", []):
                constructor_info = entry.get("Constructor", {})
                name = constructor_info.get("name", "")

                constructor_standings[name] = {
                    "position": int(entry.get("position", 0)),
                    "points": float(entry.get("points", 0)),
                }

        return driver_standings, constructor_standings

    @staticmethod
    def _match_constructor_name(json_name: str, api_names: list[str]) -> Optional[str]:
        """Match JSON constructor name to API name."""
        json_lower = json_name.lower()
        json_base = json_lower.split("-")[0].strip()

        for api_name in api_names:
            api_lower = api_name.lower()
            if api_lower in (json_lower, json_base):
                return api_name
            if json_lower.startswith(api_lower) or api_lower.startswith(json_base):
                return api_name

        name_mappings = {
            "racing bulls": "rb f1 team",
            "kick sauber": "sauber",
            "alpine": "alpine f1 team",
            "williams": "williams",
        }
        for key, val in name_mappings.items():
            if key in json_lower:
                for api_name in api_names:
                    if val in api_name.lower():
                        return api_name

        return None

    def _merge_standings(
        self, teams_data: TeamsData, driver_standings: dict, constructor_standings: dict
    ) -> TeamsData:
        api_names = list(constructor_standings.keys())
        normalized_driver_standings = {
            self._normalize_driver_lookup_key(name): stats
            for name, stats in driver_standings.items()
        }
        standings_complete = bool(driver_standings) and bool(constructor_standings)

        for team in teams_data.teams:
            matched_name = self._match_constructor_name(team.constructor_name, api_names)
            if matched_name:
                stats = constructor_standings[matched_name]
                team.position = stats["position"]
                team.points = stats["points"]
            elif constructor_standings:
                standings_complete = False
                logger.warning("No constructor standings match for %s", team.constructor_name)

            for driver in team.drivers:
                driver_name = driver.name
                driver_match = driver_standings.get(driver_name)

                if not driver_match:
                    clean_name = driver_name.replace(" Jr.", "").replace(" jr.", "")
                    driver_match = driver_standings.get(clean_name)

                if not driver_match:
                    normalized_name = self._normalize_driver_lookup_key(driver_name)
                    driver_match = normalized_driver_standings.get(normalized_name)

                if not driver_match:
                    for name, stats in driver_standings.items():
                        name_parts = self._normalize_driver_lookup_key(driver_name).split()
                        if len(name_parts) >= 2:
                            family = name_parts[-1]
                            if family in self._normalize_driver_lookup_key(name):
                                driver_match = stats
                                break

                if driver_match:
                    driver.position = driver_match["position"]
                    driver.points = driver_match["points"]
                    driver.wins = driver_match["wins"]
                elif driver_standings:
                    standings_complete = False
                    logger.warning("No driver standings match for %s", driver.name)

        teams_data.teams.sort(key=lambda t: t.position if t.position else 999)
        teams_data.standings_complete = standings_complete
        return teams_data

    async def _fetch_from_api(self, year: int) -> TeamsData:
        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        api_base = get_jolpica_base_url()
        drivers_url = f"{api_base}/{year}/drivers.json?limit=50"
        constructors_url = f"{api_base}/{year}/constructors.json"
        driver_standings_url = f"{api_base}/{year}/driverStandings.json"
        constructor_standings_url = f"{api_base}/{year}/constructorStandings.json"

        logger.info("Fetching teams and drivers from API for %d", year)

        (
            drivers_resp,
            constructors_resp,
            driver_standings_resp,
            constructor_standings_resp,
        ) = await asyncio.gather(
            fetch_with_retry(client, drivers_url, logger=logger),
            fetch_with_retry(client, constructors_url, logger=logger),
            fetch_with_retry(client, driver_standings_url, logger=logger),
            fetch_with_retry(client, constructor_standings_url, logger=logger),
        )

        drivers_data = drivers_resp.json()
        constructors_data = constructors_resp.json()
        driver_standings_data = driver_standings_resp.json()
        constructor_standings_data = constructor_standings_resp.json()

        drivers_list = drivers_data.get("MRData", {}).get("DriverTable", {}).get("Drivers", [])
        constructors_list = (
            constructors_data.get("MRData", {}).get("ConstructorTable", {}).get("Constructors", [])
        )

        driver_standings_list = (
            driver_standings_data.get("MRData", {})
            .get("StandingsTable", {})
            .get("StandingsLists", [])
        )
        driver_standings_entries = (
            driver_standings_list[0].get("DriverStandings", []) if driver_standings_list else []
        )

        constructor_standings_list = (
            constructor_standings_data.get("MRData", {})
            .get("StandingsTable", {})
            .get("StandingsLists", [])
        )
        constructor_standings_entries = (
            constructor_standings_list[0].get("ConstructorStandings", [])
            if constructor_standings_list
            else []
        )

        # Fallback: if no standings for future year, use previous year's standings
        if not driver_standings_entries and not constructor_standings_entries and year >= 2026:
            logger.info("No API standings for %d, falling back to %d", year, year - 1)
            fallback_driver_url = f"{api_base}/{year - 1}/driverStandings.json"
            fallback_constructor_url = f"{api_base}/{year - 1}/constructorStandings.json"

            fallback_driver_resp, fallback_constructor_resp = await asyncio.gather(
                fetch_with_retry(client, fallback_driver_url, logger=logger),
                fetch_with_retry(client, fallback_constructor_url, logger=logger),
            )

            fallback_driver_data = fallback_driver_resp.json()
            fallback_constructor_data = fallback_constructor_resp.json()

            fallback_driver_list = (
                fallback_driver_data.get("MRData", {})
                .get("StandingsTable", {})
                .get("StandingsLists", [])
            )
            driver_standings_entries = (
                fallback_driver_list[0].get("DriverStandings", []) if fallback_driver_list else []
            )

            fallback_constructor_list = (
                fallback_constructor_data.get("MRData", {})
                .get("StandingsTable", {})
                .get("StandingsLists", [])
            )
            constructor_standings_entries = (
                fallback_constructor_list[0].get("ConstructorStandings", [])
                if fallback_constructor_list
                else []
            )

        driver_to_constructor: dict[str, str] = {}
        driver_standings_map: dict[str, dict] = {}
        for entry in driver_standings_entries:
            driver_id = entry.get("Driver", {}).get("driverId", "")
            constructors = entry.get("Constructors", [])
            if constructors and driver_id:
                driver_to_constructor[driver_id] = constructors[-1].get("constructorId", "")
            driver_standings_map[driver_id] = {
                "position": int(entry.get("position", 0)),
                "points": float(entry.get("points", 0)),
                "wins": int(entry.get("wins", 0)),
            }

        constructor_standings_map: dict[str, dict] = {}
        for entry in constructor_standings_entries:
            constructor_id = entry.get("Constructor", {}).get("constructorId", "")
            constructor_standings_map[constructor_id] = {
                "position": int(entry.get("position", 0)),
                "points": float(entry.get("points", 0)),
            }

        drivers_by_id: dict[str, TeamDriverEntry] = {}
        for d in drivers_list:
            driver_id = d.get("driverId", "")
            perm_num = d.get("permanentNumber")
            given = d.get("givenName", "")
            family = d.get("familyName", "")

            standings = driver_standings_map.get(driver_id, {})

            drivers_by_id[driver_id] = TeamDriverEntry(
                driver_id=driver_id,
                driver_code=d.get("code", ""),
                driver_number=int(perm_num) if perm_num else None,
                given_name=given,
                family_name=family,
                name=f"{given} {family}",
                nationality=d.get("nationality", ""),
                rounds="All",
                position=standings.get("position"),
                points=standings.get("points", 0.0),
                wins=standings.get("wins", 0),
            )

        teams: list[TeamEntry] = []
        for c in constructors_list:
            constructor_id = c.get("constructorId", "")

            team_drivers = [
                drivers_by_id[driver_id]
                for driver_id, cid in driver_to_constructor.items()
                if cid == constructor_id and driver_id in drivers_by_id
            ]

            team_drivers.sort(key=lambda x: x.position if x.position else 999)

            standings = constructor_standings_map.get(constructor_id, {})

            teams.append(
                TeamEntry(
                    constructor_id=constructor_id,
                    constructor_name=c.get("name", ""),
                    entrant=c.get("name", ""),
                    nationality=c.get("nationality", ""),
                    position=standings.get("position"),
                    points=standings.get("points", 0.0),
                    drivers=team_drivers,
                )
            )

        teams.sort(key=lambda x: x.position if x.position else 999)
        standings_complete = bool(driver_standings_entries) and bool(constructor_standings_entries)
        standings_complete = standings_complete and bool(teams) and all(
            team.position is not None
            and bool(team.drivers)
            and all(driver.position is not None for driver in team.drivers)
            for team in teams
        )
        return self._apply_manual_overrides(
            TeamsData(season=year, teams=teams, standings_complete=standings_complete)
        )

    async def get_teams_and_drivers(self, year: Optional[int] = None) -> TeamsData:
        """Return teams data while coalescing concurrent cold-cache fetches per season."""
        resolved_year = year if year is not None else get_default_teams_year()
        if not self._validate_year(resolved_year):
            raise ValueError(f"Unsupported F1 season: {resolved_year}")
        cached = self._get_cached(resolved_year)
        if cached:
            return cached
        if self._is_negative_cached(resolved_year):
            return TeamsData(season=resolved_year, teams=[], standings_complete=False)

        async with _get_fetch_lock(resolved_year):
            cached = self._get_cached(resolved_year)
            if cached:
                return cached
            data = await self._load_teams_and_drivers(resolved_year)
            if data.teams:
                self._negative_cache.pop(resolved_year, None)
            else:
                self._negative_cache[resolved_year] = time.time() + NEGATIVE_CACHE_TTL_SECONDS
            return data

    async def _load_teams_and_drivers(self, year: int) -> TeamsData:
        if year is None:
            year = get_default_teams_year()

        cached = self._get_cached(year)
        if cached:
            return cached

        try:
            json_data = self._load_from_json(year)
            if json_data:
                try:
                    driver_standings, constructor_standings = await self._fetch_standings(year)

                    if not driver_standings and not constructor_standings and year >= 2026:
                        logger.info("No standings for %d, falling back to %d", year, year - 1)
                        driver_standings, constructor_standings = await self._fetch_standings(
                            year - 1
                        )

                    json_data = self._merge_standings(
                        json_data, driver_standings, constructor_standings
                    )
                except Exception as e:
                    # Standings are enrichment on top of the bundled team/driver JSON. On an
                    # upstream (jolpica) failure, serve the JSON teams without live positions
                    # rather than discarding a perfectly good dashboard — but do NOT cache the
                    # degraded result, so the next request/scheduler run retries immediately
                    # instead of pinning a standings-less dashboard for the full cache TTL.
                    logger.warning(
                        "Standings fetch failed for %d; serving teams without standings: %s",
                        year,
                        e,
                    )
                    return json_data.model_copy(update={"standings_complete": False})

                if is_teams_data_cacheable(json_data):
                    self._set_cache(year, json_data)
                return json_data

            api_data = await self._fetch_from_api(year)
            # Never cache an empty/degraded result, so a transient outage retries instead of
            # pinning a blank or standings-less dashboard for the full cache TTL.
            if is_teams_data_cacheable(api_data):
                self._set_cache(year, api_data)
            return api_data

        except Exception as e:
            logger.error("Error fetching teams and drivers: %s", e, exc_info=True)
            return TeamsData(season=year, teams=[], standings_complete=False)
