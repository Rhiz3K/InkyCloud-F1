"""Teams service for F1 teams and drivers - uses Wikipedia scraped data with API fallback."""

import asyncio
import json
import logging
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.config import config
from app.models import TeamDriverEntry, TeamEntry, TeamsData

logger = logging.getLogger(__name__)

JOLPICA_BASE_URL = "https://api.jolpi.ca/ergast/f1"
SEASONS_DIR = Path(__file__).parent.parent / "assets" / "seasons"

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
CACHE_TTL_SECONDS = 3600

SEASON_START_DATES = {
    2025: datetime(2025, 3, 16, tzinfo=timezone.utc),
    2026: datetime(2026, 3, 8, tzinfo=timezone.utc),
}

MANUAL_DRIVER_NUMBER_OVERRIDES = {
    2026: {
        "norris": 1,
        "verstappen": 3,
        "hadjar": 6,
        "lawson": 30,
        "lindblad": 41,
        "bortoleto": 5,
        "hulkenberg": 27,
        "perez": 11,
        "bottas": 77,
    }
}

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


def _get_current_f1_season() -> int:
    now = datetime.now(timezone.utc)
    if now >= SEASON_START_DATES[2026]:
        return 2026
    if now >= SEASON_START_DATES[2025]:
        return 2025
    return 2024


def get_default_teams_year() -> int:
    """Resolve the newest season that has bundled teams data."""
    current_year = _get_current_f1_season()
    current_path = SEASONS_DIR / f"{current_year}_teams.json"
    if current_path.exists():
        return current_year

    available_years = sorted(
        int(path.stem.split("_", 1)[0])
        for path in SEASONS_DIR.glob("*_teams.json")
        if path.stem.split("_", 1)[0].isdigit()
    )
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
    def __init__(self):
        self.timeout = config.REQUEST_TIMEOUT
        self._cache: dict[str, CacheEntry] = {}

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
        """Validate year is within acceptable F1 data range (1950-2030)."""
        return isinstance(year, int) and 1950 <= year <= 2030

    @staticmethod
    def _normalize_driver_lookup_key(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(ascii_name.replace(" Jr.", "").replace(" jr.", "").lower().split())

    @classmethod
    def _get_override_driver_id(cls, driver: TeamDriverEntry, season: int) -> str | None:
        if driver.driver_id:
            return driver.driver_id

        name_fallbacks = MANUAL_DRIVER_NUMBER_OVERRIDE_NAME_FALLBACKS.get(season, {})
        normalized_name = cls._normalize_driver_lookup_key(driver.name)
        return name_fallbacks.get(normalized_name)

    @classmethod
    def _apply_manual_overrides(cls, teams_data: TeamsData) -> TeamsData:
        driver_number_overrides = MANUAL_DRIVER_NUMBER_OVERRIDES.get(teams_data.season, {})
        if not driver_number_overrides:
            return teams_data

        for team in teams_data.teams:
            for driver in team.drivers:
                override_driver_id = cls._get_override_driver_id(driver, teams_data.season)
                if override_driver_id is None:
                    continue
                override_number = driver_number_overrides.get(override_driver_id)
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

    @staticmethod
    async def _fetch_with_retry(
        client: httpx.AsyncClient, url: str, max_retries: int = MAX_RETRIES
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
                            "Rate limited (429), retry %d/%d in %ss",
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise
        assert last_exception is not None
        raise last_exception

    async def _fetch_standings(self, year: int) -> tuple[dict, dict]:
        """Fetch driver and constructor standings from API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            driver_standings_url = f"{JOLPICA_BASE_URL}/{year}/driverStandings.json"
            constructor_standings_url = f"{JOLPICA_BASE_URL}/{year}/constructorStandings.json"

            logger.info("Fetching standings for %d", year)

            driver_resp, constructor_resp = await asyncio.gather(
                self._fetch_with_retry(client, driver_standings_url),
                self._fetch_with_retry(client, constructor_standings_url),
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
                constructor_data.get("MRData", {})
                .get("StandingsTable", {})
                .get("StandingsLists", [])
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

        for team in teams_data.teams:
            matched_name = self._match_constructor_name(team.constructor_name, api_names)
            if matched_name:
                stats = constructor_standings[matched_name]
                team.position = stats["position"]
                team.points = stats["points"]

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

        teams_data.teams.sort(key=lambda t: t.position if t.position else 999)
        return teams_data

    async def _fetch_from_api(self, year: int) -> TeamsData:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            drivers_url = f"{JOLPICA_BASE_URL}/{year}/drivers.json?limit=50"
            constructors_url = f"{JOLPICA_BASE_URL}/{year}/constructors.json"
            driver_standings_url = f"{JOLPICA_BASE_URL}/{year}/driverStandings.json"
            constructor_standings_url = f"{JOLPICA_BASE_URL}/{year}/constructorStandings.json"

            logger.info("Fetching teams and drivers from API for %d", year)

            (
                drivers_resp,
                constructors_resp,
                driver_standings_resp,
                constructor_standings_resp,
            ) = await asyncio.gather(
                self._fetch_with_retry(client, drivers_url),
                self._fetch_with_retry(client, constructors_url),
                self._fetch_with_retry(client, driver_standings_url),
                self._fetch_with_retry(client, constructor_standings_url),
            )

            drivers_data = drivers_resp.json()
            constructors_data = constructors_resp.json()
            driver_standings_data = driver_standings_resp.json()
            constructor_standings_data = constructor_standings_resp.json()

            drivers_list = drivers_data.get("MRData", {}).get("DriverTable", {}).get("Drivers", [])
            constructors_list = (
                constructors_data.get("MRData", {})
                .get("ConstructorTable", {})
                .get("Constructors", [])
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
                fallback_driver_url = f"{JOLPICA_BASE_URL}/{year - 1}/driverStandings.json"
                fallback_constructor_url = (
                    f"{JOLPICA_BASE_URL}/{year - 1}/constructorStandings.json"
                )

                fallback_driver_resp, fallback_constructor_resp = await asyncio.gather(
                    self._fetch_with_retry(client, fallback_driver_url),
                    self._fetch_with_retry(client, fallback_constructor_url),
                )

                fallback_driver_data = fallback_driver_resp.json()
                fallback_constructor_data = fallback_constructor_resp.json()

                fallback_driver_list = (
                    fallback_driver_data.get("MRData", {})
                    .get("StandingsTable", {})
                    .get("StandingsLists", [])
                )
                driver_standings_entries = (
                    fallback_driver_list[0].get("DriverStandings", [])
                    if fallback_driver_list
                    else []
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
                    driver_to_constructor[driver_id] = constructors[0].get("constructorId", "")
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
            return self._apply_manual_overrides(TeamsData(season=year, teams=teams))

    async def get_teams_and_drivers(self, year: Optional[int] = None) -> TeamsData:
        if year is None:
            year = get_default_teams_year()

        cached = self._get_cached(year)
        if cached:
            return cached

        try:
            json_data = self._load_from_json(year)
            if json_data:
                driver_standings, constructor_standings = await self._fetch_standings(year)

                if not driver_standings and not constructor_standings and year >= 2026:
                    logger.info("No standings for %d, falling back to %d", year, year - 1)
                    driver_standings, constructor_standings = await self._fetch_standings(year - 1)

                json_data = self._merge_standings(
                    json_data, driver_standings, constructor_standings
                )
                self._set_cache(year, json_data)
                return json_data

            api_data = await self._fetch_from_api(year)
            self._set_cache(year, api_data)
            return api_data

        except Exception as e:
            logger.error("Error fetching teams and drivers: %s", e, exc_info=True)
            return TeamsData(season=year, teams=[])
