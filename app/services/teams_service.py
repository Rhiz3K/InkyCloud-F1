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
from app.utils.http import AsyncPacer, fetch_with_retry
from app.utils.jolpica import get_jolpica_base_url, get_jolpica_pacer
from app.utils.standings_metadata import get_season_driver_number_by_id

logger = logging.getLogger(__name__)

SEASONS_DIR = Path(__file__).parent.parent / "assets" / "seasons"

CACHE_TTL_SECONDS = 3600
# Rosters whose standings are missing or partial stay cached only briefly: long enough that a
# burst of requests cannot amplify into repeated paced upstream fetches, short enough that the
# hourly scheduler and later requests retry soon after an upstream hiccup.
DEGRADED_CACHE_TTL_SECONDS = 300
NEGATIVE_CACHE_TTL_SECONDS = 60
_fetch_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[int, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _get_fetch_lock(year: int) -> asyncio.Lock:
    """Return the event-loop-local lock that coalesces fetches for a season."""
    loop = asyncio.get_running_loop()
    locks = _fetch_locks.setdefault(loop, {})
    return locks.setdefault(year, asyncio.Lock())


def is_teams_data_cacheable(data: TeamsData) -> bool:
    """Return True only for complete teams data safe to keep in caches/prebuilt images."""
    return bool(data.teams) and data.standings_complete


def _extract_table_rows(payload: dict, table_name: str, rows_name: str) -> list[dict]:
    """Extract a list from a Jolpica MRData table without trusting its shape."""
    rows = payload.get("MRData", {}).get(table_name, {}).get(rows_name, [])
    return rows if isinstance(rows, list) else []


def _extract_standings_rows(payload: dict, rows_name: str) -> list[dict]:
    """Extract the first season standings list from a Jolpica response."""
    standings_lists = payload.get("MRData", {}).get("StandingsTable", {}).get("StandingsLists", [])
    if not isinstance(standings_lists, list) or not standings_lists:
        return []
    first_standings = standings_lists[0]
    if not isinstance(first_standings, dict):
        return []
    rows = first_standings.get(rows_name, [])
    return rows if isinstance(rows, list) else []


async def _fetch_api_standings_rows(
    client: httpx.AsyncClient,
    api_base: str,
    year: int,
    *,
    pacer: AsyncPacer | None = None,
) -> tuple[list[dict], list[dict]]:
    """Fetch and extract driver and constructor standings for one season."""
    pacer = pacer or get_jolpica_pacer(api_base)
    driver_response, constructor_response = await asyncio.gather(
        fetch_with_retry(
            client,
            f"{api_base}/{year}/driverStandings.json",
            pacer=pacer,
            logger=logger,
        ),
        fetch_with_retry(
            client,
            f"{api_base}/{year}/constructorStandings.json",
            pacer=pacer,
            logger=logger,
        ),
    )
    return (
        _extract_standings_rows(driver_response.json(), "DriverStandings"),
        _extract_standings_rows(constructor_response.json(), "ConstructorStandings"),
    )


def _build_driver_api_maps(
    standings_rows: list[dict], driver_rows: list[dict]
) -> tuple[dict[str, str], dict[str, TeamDriverEntry]]:
    """Build constructor assignments and normalized driver models from API rows."""
    driver_to_constructor: dict[str, str] = {}
    standings_by_driver: dict[str, dict] = {}
    for entry in standings_rows:
        driver_id = entry.get("Driver", {}).get("driverId", "")
        constructors = entry.get("Constructors", [])
        if constructors and driver_id:
            driver_to_constructor[driver_id] = constructors[-1].get("constructorId", "")
        standings_by_driver[driver_id] = {
            "position": int(entry.get("position", 0)),
            "points": float(entry.get("points", 0)),
            "wins": int(entry.get("wins", 0)),
        }

    drivers_by_id: dict[str, TeamDriverEntry] = {}
    for driver in driver_rows:
        driver_id = driver.get("driverId", "")
        permanent_number = driver.get("permanentNumber")
        given_name = driver.get("givenName", "")
        family_name = driver.get("familyName", "")
        standings = standings_by_driver.get(driver_id, {})
        drivers_by_id[driver_id] = TeamDriverEntry(
            driver_id=driver_id,
            driver_code=driver.get("code", ""),
            driver_number=int(permanent_number) if permanent_number else None,
            given_name=given_name,
            family_name=family_name,
            name=f"{given_name} {family_name}",
            nationality=driver.get("nationality", ""),
            rounds="All",
            position=standings.get("position"),
            points=standings.get("points", 0.0),
            wins=standings.get("wins", 0),
        )
    return driver_to_constructor, drivers_by_id


def _build_constructor_api_map(standings_rows: list[dict]) -> dict[str, dict]:
    """Index normalized constructor standings by Jolpica constructor ID."""
    result: dict[str, dict] = {}
    for entry in standings_rows:
        constructor_id = entry.get("Constructor", {}).get("constructorId", "")
        result[constructor_id] = {
            "position": int(entry.get("position", 0)),
            "points": float(entry.get("points", 0)),
        }
    return result


def _build_api_teams(
    constructor_rows: list[dict],
    driver_to_constructor: dict[str, str],
    drivers_by_id: dict[str, TeamDriverEntry],
    standings_by_constructor: dict[str, dict],
) -> list[TeamEntry]:
    """Assemble sorted team models from normalized driver and constructor maps."""
    teams: list[TeamEntry] = []
    for constructor in constructor_rows:
        constructor_id = constructor.get("constructorId", "")
        team_drivers = [
            drivers_by_id[driver_id]
            for driver_id, assigned_constructor in driver_to_constructor.items()
            if assigned_constructor == constructor_id and driver_id in drivers_by_id
        ]
        team_drivers.sort(key=lambda driver: driver.position if driver.position else 999)
        standings = standings_by_constructor.get(constructor_id, {})
        teams.append(
            TeamEntry(
                constructor_id=constructor_id,
                constructor_name=constructor.get("name", ""),
                entrant=constructor.get("name", ""),
                nationality=constructor.get("nationality", ""),
                position=standings.get("position"),
                points=standings.get("points", 0.0),
                drivers=team_drivers,
            )
        )
    teams.sort(key=lambda team: team.position if team.position else 999)
    return teams


def _has_complete_api_standings(
    teams: list[TeamEntry], driver_rows: list[dict], constructor_rows: list[dict]
) -> bool:
    """Return whether every API team and assigned driver has a standings position."""
    return (
        bool(driver_rows)
        and bool(constructor_rows)
        and bool(teams)
        and all(
            team.position is not None
            and bool(team.drivers)
            and all(driver.position is not None for driver in team.drivers)
            for team in teams
        )
    )


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


def _find_bundled_teams_path(year: int) -> Path | None:
    """Find an allowlisted bundled roster without constructing a path from request data."""
    expected_name = f"{year}_teams.json"
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
    """Hold one teams response and its wall-clock cache deadline."""

    def __init__(self, data: TeamsData, ttl: int = CACHE_TTL_SECONDS):
        """Store data with a TTL measured from the current wall clock."""
        self.data = data
        self.expires_at = time.time() + ttl

    def is_valid(self) -> bool:
        """Return whether the cache deadline has not elapsed."""
        return time.time() < self.expires_at


class TeamsService:
    """Load teams and driver rosters from bundled data with API enrichment."""

    _shared_cache: dict[str, CacheEntry] = {}
    _negative_cache: dict[int, float] = {}

    def __init__(self):
        """Initialize request timeout and process-wide cache access."""
        self.timeout = config.REQUEST_TIMEOUT
        self._cache = self._shared_cache

    @staticmethod
    def _get_cache_key(year: int) -> str:
        """Build the stable in-memory cache key for a season."""
        return f"teams_{year}"

    def _get_cached(self, year: int) -> Optional[TeamsData]:
        """Return a fresh cached response for a season when available."""
        key = self._get_cache_key(year)
        entry = self._cache.get(key)
        if entry and entry.is_valid():
            logger.debug("Cache hit for %s", key)
            return entry.data
        return None

    def _set_cache(self, year: int, data: TeamsData, *, ttl: int = CACHE_TTL_SECONDS) -> None:
        """Store a teams response in the shared cache for ``ttl`` seconds."""
        key = self._get_cache_key(year)
        self._cache[key] = CacheEntry(data, ttl=ttl)
        logger.debug("Cached %s for %ss", key, ttl)

    @staticmethod
    def _validate_year(year: int) -> bool:
        """Validate year before creating cache keys, locks, or outbound requests."""
        return is_supported_f1_season(year)

    @classmethod
    def _is_negative_cached(cls, year: int) -> bool:
        """Return whether a recent empty response suppresses another upstream call."""
        expires_at = cls._negative_cache.get(year)
        if expires_at is None:
            return False
        if time.time() < expires_at:
            return True
        cls._negative_cache.pop(year, None)
        return False

    @staticmethod
    def _normalize_driver_lookup_key(name: str) -> str:
        """Normalize accents, suffixes, case, and whitespace for driver matching."""
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(ascii_name.replace(" Jr.", "").replace(" jr.", "").lower().split())

    @classmethod
    def _get_override_driver_id(cls, driver: TeamDriverEntry, season: int) -> str | None:
        """Resolve the driver ID used for a manual number override."""
        if get_season_driver_number_by_id(driver.driver_id, season) is not None:
            return driver.driver_id

        name_fallbacks = MANUAL_DRIVER_NUMBER_OVERRIDE_NAME_FALLBACKS.get(season, {})
        normalized_name = cls._normalize_driver_lookup_key(driver.name)
        return name_fallbacks.get(normalized_name, driver.driver_id or None)

    @classmethod
    def _apply_manual_overrides(cls, teams_data: TeamsData) -> TeamsData:
        """Apply season-specific permanent-number corrections in place."""
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
        """Load the bundled roster for a validated season."""
        if not self._validate_year(year):
            logger.warning("Invalid year requested: %s", year)
            return None

        json_path = _find_bundled_teams_path(year)
        if json_path is None:
            logger.debug("No bundled teams file found for %s", year)
            return None

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))

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
        pacer = get_jolpica_pacer(api_base)
        driver_standings_url = f"{api_base}/{year}/driverStandings.json"
        constructor_standings_url = f"{api_base}/{year}/constructorStandings.json"

        logger.info("Fetching standings for %d", year)

        driver_resp, constructor_resp = await asyncio.gather(
            fetch_with_retry(client, driver_standings_url, pacer=pacer, logger=logger),
            fetch_with_retry(client, constructor_standings_url, pacer=pacer, logger=logger),
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
        """Enrich bundled roster data with normalized live standings."""
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
        """Build teams data entirely from Jolpica when no bundled roster exists."""
        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        api_base = get_jolpica_base_url()
        pacer = get_jolpica_pacer(api_base)
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
            fetch_with_retry(client, drivers_url, pacer=pacer, logger=logger),
            fetch_with_retry(client, constructors_url, pacer=pacer, logger=logger),
            fetch_with_retry(client, driver_standings_url, pacer=pacer, logger=logger),
            fetch_with_retry(client, constructor_standings_url, pacer=pacer, logger=logger),
        )

        drivers_data = drivers_resp.json()
        constructors_data = constructors_resp.json()
        driver_standings_data = driver_standings_resp.json()
        constructor_standings_data = constructor_standings_resp.json()

        drivers_list = _extract_table_rows(drivers_data, "DriverTable", "Drivers")
        constructors_list = _extract_table_rows(
            constructors_data, "ConstructorTable", "Constructors"
        )
        driver_standings_entries = _extract_standings_rows(driver_standings_data, "DriverStandings")
        constructor_standings_entries = _extract_standings_rows(
            constructor_standings_data, "ConstructorStandings"
        )

        # Fallback: if no standings for future year, use previous year's standings
        if not driver_standings_entries and not constructor_standings_entries and year >= 2026:
            logger.info("No API standings for %d, falling back to %d", year, year - 1)
            (
                driver_standings_entries,
                constructor_standings_entries,
            ) = await _fetch_api_standings_rows(client, api_base, year - 1, pacer=pacer)
        driver_to_constructor, drivers_by_id = _build_driver_api_maps(
            driver_standings_entries, drivers_list
        )
        constructor_standings_map = _build_constructor_api_map(constructor_standings_entries)
        teams = _build_api_teams(
            constructors_list,
            driver_to_constructor,
            drivers_by_id,
            constructor_standings_map,
        )
        standings_complete = _has_complete_api_standings(
            teams, driver_standings_entries, constructor_standings_entries
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
            if self._is_negative_cached(resolved_year):
                return TeamsData(season=resolved_year, teams=[], standings_complete=False)
            data = await self._load_teams_and_drivers(resolved_year)
            if data.teams:
                self._negative_cache.pop(resolved_year, None)
                if not is_teams_data_cacheable(data):
                    self._set_cache(resolved_year, data, ttl=DEGRADED_CACHE_TTL_SECONDS)
            else:
                self._negative_cache[resolved_year] = time.time() + NEGATIVE_CACHE_TTL_SECONDS
            return data

    async def _load_teams_and_drivers(self, year: int) -> TeamsData:
        """Load roster data and enrich it without caching degraded responses."""
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
