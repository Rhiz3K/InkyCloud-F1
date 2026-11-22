"""Weather service using Open-Meteo APIs for race weekend weather."""

import asyncio
import logging
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

import httpx

from app.config import config
from app.services.circuit_metadata import canonical_circuit_id
from app.services.http_client import get_shared_http_client
from app.services.weather_budget import WEATHER_BUDGET
from app.utils.http import fetch_with_retry
from app.utils.race_times import parse_utc_instant, race_session_start, to_utc
from app.utils.refresh_hint import FORECAST_RANGE_DAYS, WEEKEND_FORECAST_LEAD_DAYS

if TYPE_CHECKING:
    from app.services.database import Database

logger = logging.getLogger(__name__)

# WMO Weather codes (4677) to Weather Icons font mapping
# Font: https://github.com/erikflowers/weather-icons (SIL OFL 1.1 license)
# Reference: https://open-meteo.com/en/docs
WEATHER_ICONS: dict[int, str] = {
    0: "\uf00d",  # wi-day-sunny - Clear sky
    1: "\uf00d",  # wi-day-sunny - Mainly clear
    2: "\uf002",  # wi-day-cloudy - Partly cloudy
    3: "\uf013",  # wi-cloudy - Overcast
    45: "\uf014",  # wi-fog - Fog
    48: "\uf014",  # wi-fog - Depositing rime fog
    51: "\uf01c",  # wi-sprinkle - Light drizzle
    53: "\uf01c",  # wi-sprinkle - Moderate drizzle
    55: "\uf01c",  # wi-sprinkle - Dense drizzle
    56: "\uf017",  # wi-rain-mix - Freezing drizzle light
    57: "\uf017",  # wi-rain-mix - Freezing drizzle dense
    61: "\uf019",  # wi-rain - Slight rain
    63: "\uf019",  # wi-rain - Moderate rain
    65: "\uf019",  # wi-rain - Heavy rain
    66: "\uf017",  # wi-rain-mix - Freezing rain light
    67: "\uf017",  # wi-rain-mix - Freezing rain heavy
    71: "\uf01b",  # wi-snow - Slight snow
    73: "\uf01b",  # wi-snow - Moderate snow
    75: "\uf01b",  # wi-snow - Heavy snow
    77: "\uf01b",  # wi-snow - Snow grains
    80: "\uf01a",  # wi-showers - Slight rain showers
    81: "\uf01a",  # wi-showers - Moderate rain showers
    82: "\uf01a",  # wi-showers - Violent rain showers
    85: "\uf01b",  # wi-snow - Slight snow showers
    86: "\uf01b",  # wi-snow - Heavy snow showers
    95: "\uf01e",  # wi-thunderstorm - Thunderstorm
    96: "\uf01e",  # wi-thunderstorm - Thunderstorm with slight hail
    99: "\uf01e",  # wi-thunderstorm - Thunderstorm with heavy hail
}

RAINDROP_ICON = "\uf078"  # wi-raindrop

OPEN_METEO_URL = config.OPEN_METEO_URL
OPEN_METEO_ARCHIVE_URL = config.OPEN_METEO_ARCHIVE_URL

_weather_cache: dict[str, tuple["WeatherData", datetime]] = {}
# Hourly forecast arrays of one race-weekend window, shared by race_day and weekend lookups so
# both read one Open-Meteo response instead of requesting the same days twice.
_hourly_forecast_cache: dict[str, tuple[dict, datetime]] = {}

# Hourly variables of a forecast request. Open-Meteo bills a request with up to 10 variables
# and up to 14 days of data as one call; the 3-day weekend window stays within one call, and
# the shared quota (WEATHER_BUDGET) reserves exactly one unit per request.
FORECAST_HOURLY_VARIABLES = "temperature_2m,weather_code,precipitation_probability"
# Sessions summarized by weather_type=weekend, in weekend order. The sprint is optional.
WEEKEND_SESSIONS = ("sprint", "qualifying", "race")

# Circuit weather cache - populated by scheduler, read by renderer
# Maps circuit_id -> WeatherData
_circuit_weather_cache: dict[str, tuple["WeatherData", datetime]] = {}
CIRCUIT_WEATHER_TTL_MINUTES = 120


def _current_weather_cache_key(lat: float, lon: float) -> str:
    """Cache key for current weather — single source for readers, prefetch, and warm-load."""
    return f"current_{round(lat, 2)}_{round(lon, 2)}"


def _race_weather_cache_key(lat: float, lon: float, race_datetime: datetime) -> str:
    """Cache key for race-day weather, normalized to UTC.

    All sites (reader, prefetch save, startup warm-load) must build this identically:
    a local-offset isoformat here once made the restart warm-load populate a key the
    reader never looked up.
    """
    return f"{round(lat, 2)}_{round(lon, 2)}_{to_utc(race_datetime).isoformat()}"


def _finite_number(value: object) -> float | None:
    """Return a finite float for numeric provider values, otherwise None (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


class TempUnit(StrEnum):
    """Temperature unit used when drawing weather; data and caches always stay in Celsius."""

    C = "c"
    F = "f"


def convert_temperature(temperature_c: float, unit: TempUnit = TempUnit.C) -> int:
    """Return the rounded temperature in ``unit`` (Fahrenheit is converted before rounding)."""
    if unit == TempUnit.F:
        return int(round(temperature_c * 9 / 5 + 32))
    return int(round(temperature_c))


@dataclass
class WeatherData:
    """Normalized weather values required by the e-ink renderers."""

    temperature_c: float
    weather_code: int
    precipitation_probability: int
    precipitation_display_override: str | None = None

    @property
    def icon(self) -> str:
        """Return the weather-icons glyph for the WMO condition code."""
        return WEATHER_ICONS.get(self.weather_code, "\u2601")

    @property
    def temp_display(self) -> str:
        """Return rounded Celsius temperature text for the display."""
        return self.format_temp(TempUnit.C)

    def format_temp(self, unit: TempUnit = TempUnit.C) -> str:
        """Return the rounded temperature with a degree sign in the requested unit."""
        return f"{convert_temperature(self.temperature_c, unit)}\u00b0"

    @property
    def precip_display(self) -> str:
        """Return precipitation probability or a preformatted amount."""
        if self.precipitation_display_override is not None:
            return self.precipitation_display_override
        return f"{self.precipitation_probability}%"


@dataclass(frozen=True)
class SessionWeather:
    """Forecast for the start of one weekend session (``sprint``, ``qualifying`` or ``race``)."""

    session: str
    weather: WeatherData


@dataclass(frozen=True)
class WeekendWeather:
    """Weather of the race weekend's key sessions that have data, in weekend order.

    Sessions beyond the forecast horizon, without a published start or already in the past
    are left out, so the renderer shows what is available.
    """

    sessions: tuple[SessionWeather, ...]

    @property
    def temp_display(self) -> str:
        """Return a compact Celsius summary for logs, e.g. ``qualifying 19° race 22°``."""
        return " ".join(f"{item.session} {item.weather.temp_display}" for item in self.sessions)


# What a calendar can draw in its status box: one forecast or a weekend summary.
WeatherDisplay = WeatherData | WeekendWeather


def forecast_window(race_date: date, today: date) -> tuple[date, date] | None:
    """Return the UTC days of the race weekend that the forecast can cover, or None.

    The window spans the race day and the days before it that hold qualifying and a sprint,
    clipped to today and to the 16-day forecast horizon (today included).
    """
    start = max(today, race_date - timedelta(days=WEEKEND_FORECAST_LEAD_DAYS))
    end = min(race_date, today + timedelta(days=FORECAST_RANGE_DAYS - 1))
    return (start, end) if start <= end else None


def weekend_session_starts(race_data: dict) -> tuple[date | None, list[tuple[str, datetime]]]:
    """Return the race's UTC date and the timed weekend sessions shown by weather_type=weekend.

    A date-only race still provides its date, so qualifying can be shown before the race
    time is published.
    """
    race_date: date | None = None
    sessions: list[tuple[str, datetime]] = []
    for event in race_data.get("schedule") or []:
        kind = str(event.get("name", "")).strip().lower()
        value = event.get("datetime")
        if kind not in WEEKEND_SESSIONS or not isinstance(value, str):
            continue
        try:
            if len(value) == 10:
                if kind == "race":
                    race_date = date.fromisoformat(value)
                continue
            start = parse_utc_instant(value)
        except ValueError:
            continue
        if kind == "race":
            race_date = start.date()
        sessions.append((kind, start))
    sessions.sort(key=lambda item: WEEKEND_SESSIONS.index(item[0]))
    return race_date, sessions


class WeatherService:
    """Fetch and cache current, forecast, and historical Open-Meteo data."""

    def __init__(self, timeout: int = 10, cache_minutes: int = 60):
        """Configure HTTP timeout and in-memory response TTL."""
        self.timeout = httpx.Timeout(timeout)
        self.cache_minutes = cache_minutes

    async def get_current_weather(self, lat: float, lon: float) -> Optional[WeatherData]:
        """
        Retrieve current weather for coordinates, using internal cache.

        On cache miss, fetches from API and caches result. Invalid coordinates
        return None.

        Returns:
            WeatherData or None if invalid coords, API failure, or no data.
        """
        cache_key = _current_weather_cache_key(lat, lon)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Current weather cache hit for %s", cache_key)
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning("Invalid weather coordinates")
            return None

        try:
            weather_data = await self._fetch_current_weather(lat, lon)
            if weather_data:
                self._set_cached(cache_key, weather_data)
            return weather_data

        except httpx.TimeoutException:
            logger.warning("Weather API request timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("Weather API HTTP error: %s", e.response.status_code)
            return None
        except Exception as e:
            logger.warning("Failed to fetch current weather: %s", e)
            return None

    async def _fetch_current_weather(self, lat: float, lon: float) -> Optional[WeatherData]:
        """
        Fetch current weather from Open-Meteo API.

        Parameters:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.

        Returns:
            WeatherData with temperature_c, weather_code, precipitation_probability, or None
            when the response lacks a current temperature (rather than fabricating defaults).
        """
        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "current": "temperature_2m,weather_code",
            "hourly": "precipitation_probability",
            "timezone": "auto",
            "forecast_days": 1,
        }

        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        response = await fetch_with_retry(
            client, OPEN_METEO_URL, params=params, pacer=WEATHER_BUDGET, logger=logger
        )
        data = response.json()

        current = data.get("current") or {}
        hourly = data.get("hourly") or {}

        # Don't fabricate weather: a 200 response with a missing/partial current block must not
        # be cached and displayed as 20C/sunny. Return None so the caller falls back gracefully.
        # An explicit null (or NaN) is as missing as an absent key; caching it would crash
        # temp_display for every render that reads this entry until the cache expires.
        temperature = _finite_number(current.get("temperature_2m"))
        if temperature is None:
            logger.warning("open-meteo returned no current temperature")
            return None

        # Precipitation probability lives only in the hourly array; pick the slot matching the
        # current local hour instead of index 0 (which is 00:00 and wrong for most of the day).
        precip_probs = hourly.get("precipitation_probability") or []
        precip_times = hourly.get("time") or []
        current_hour = str(current.get("time", ""))[:13]  # YYYY-MM-DDTHH
        precip = 0
        if precip_probs:
            # Prefer the hourly slot matching the API's reported current time; only fall back to
            # the first (00:00) slot when the time field is missing or no slot matches.
            idx = 0
            if current_hour:
                for i, t in enumerate(precip_times):
                    if str(t)[:13] == current_hour and i < len(precip_probs):
                        idx = i
                        break
            precip = precip_probs[idx] or 0

        weather_code = _finite_number(current.get("weather_code"))
        precip_value = _finite_number(precip)
        return WeatherData(
            temperature_c=temperature,
            weather_code=int(weather_code) if weather_code is not None else 0,
            precipitation_probability=int(precip_value) if precip_value is not None else 0,
        )

    async def get_race_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        """Return cached, forecast, or historical weather for a race timestamp."""
        race_datetime_utc = to_utc(race_datetime)

        cache_key = _race_weather_cache_key(lat, lon, race_datetime)
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Weather cache hit for %s", cache_key)
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning("Invalid coordinates: lat=%s, lon=%s", lat, lon)
            return None

        now = datetime.now(timezone.utc)
        if race_datetime_utc < now:
            logger.debug("Race already started, fetching historical weather")
            return await self.get_historical_race_weather(lat, lon, race_datetime_utc)

        # The 16-day availability horizon includes today. Fetch only the race weekend, not
        # all intervening days: each request stays within one provider call unit.
        forecast_days = (race_datetime_utc.date() - now.date()).days + 1
        if forecast_days > FORECAST_RANGE_DAYS:
            logger.debug(
                "Race requires %d forecast days, outside 16-day forecast range",
                forecast_days,
            )
            return None

        try:
            weather_data = await self._fetch_weather(lat, lon, race_datetime_utc)
            if weather_data:
                self._set_cached(cache_key, weather_data)
            return weather_data

        except httpx.TimeoutException:
            logger.warning("Weather API request timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("Weather API HTTP error: %s", e.response.status_code)
            return None
        except Exception as e:
            logger.warning("Failed to fetch weather: %s", e)
            return None

    async def get_historical_race_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        """Return cached or archived Open-Meteo weather for a past race."""
        race_datetime_utc = to_utc(race_datetime)

        cache_key = f"historical_{round(lat, 2)}_{round(lon, 2)}_{race_datetime_utc.isoformat()}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Historical weather cache hit for %s", cache_key)
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning("Invalid coordinates: lat=%s, lon=%s", lat, lon)
            return None

        try:
            weather_data = await self._fetch_historical_weather(lat, lon, race_datetime_utc)
            if weather_data:
                self._set_cached(cache_key, weather_data)
            return weather_data

        except httpx.TimeoutException:
            logger.warning("Historical weather API request timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("Historical weather API HTTP error: %s", e.response.status_code)
            return None
        except Exception as e:
            logger.warning("Failed to fetch historical weather: %s", e)
            return None

    async def _fetch_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        """
        Fetch the race weekend's hourly forecast and pick the race hour.

        Parameters:
            lat: Latitude.
            lon: Longitude.
            race_datetime: Target datetime (matches exact hour or same day).

        Returns:
            WeatherData for matching hour/day, or None if no data found.
        """
        hourly = await self._weekend_hourly(lat, lon, to_utc(race_datetime).date())
        if hourly is None:
            return None
        return _match_hourly_weather(
            hourly,
            race_datetime,
            precipitation_key="precipitation_probability",
        )

    async def _weekend_hourly(self, lat: float, lon: float, race_date: date) -> Optional[dict]:
        """Return the hourly forecast of the race weekend window, fetching it at most once.

        One request covers qualifying, a sprint and the race (the days before the race up to
        the race day, clipped to the forecast horizon); race_day and weekend lookups share it.
        """
        window = forecast_window(race_date, datetime.now(timezone.utc).date())
        if window is None:
            return None
        start, end = window
        key = f"hourly_{round(lat, 2)}_{round(lon, 2)}_{start.isoformat()}_{end.isoformat()}"
        cached = _hourly_forecast_cache.get(key)
        if cached is not None:
            hourly, expires_at = cached
            if datetime.now(timezone.utc) < expires_at:
                return hourly
            del _hourly_forecast_cache[key]

        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": FORECAST_HOURLY_VARIABLES,
            "timezone": "UTC",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        response = await fetch_with_retry(
            client, OPEN_METEO_URL, params=params, pacer=WEATHER_BUDGET, logger=logger
        )
        hourly = response.json().get("hourly") or {}
        if hourly.get("time"):
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.cache_minutes)
            _hourly_forecast_cache[key] = (hourly, expires_at)
        return hourly

    async def get_weekend_weather(
        self,
        lat: float,
        lon: float,
        race_date: date | None,
        sessions: Sequence[tuple[str, datetime]],
    ) -> Optional[WeekendWeather]:
        """Return the forecast of the weekend's qualifying, sprint and race starts.

        Uses the same single request as the race-day forecast. Each session's forecast is
        cached under its own start (the race-day key format), so the hourly prefetch can
        persist and restore it. Once the race has started only its (archived) race weather
        is shown. Sessions without data are left out; None when none has data.
        """
        if race_date is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None
        now = datetime.now(timezone.utc)
        race_start = next((start for kind, start in sessions if kind == "race"), None)
        if race_start is not None and race_start < now:
            race_weather = await self.get_race_weather(lat, lon, race_start)
            return _weekend_from([("race", race_weather)])

        found: list[tuple[str, Optional[WeatherData]]] = []
        hourly: Optional[dict] = None
        for kind, start in sessions:
            key = _race_weather_cache_key(lat, lon, start)
            weather = self._get_cached(key)
            if weather is None and _in_weekend_window(start, race_date, now):
                if hourly is None:
                    try:
                        hourly = await self._weekend_hourly(lat, lon, race_date) or {}
                    except Exception as e:
                        logger.warning("Failed to fetch weekend weather: %s", e)
                        hourly = {}
                weather = _match_session_hour(hourly, start)
                if weather is not None:
                    self._set_cached(key, weather)
            found.append((kind, weather))
        return _weekend_from(found)

    async def _fetch_historical_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        """Fetch and normalize archived hourly weather for a race location."""
        race_dt_utc = to_utc(race_datetime)
        race_date = race_dt_utc.strftime("%Y-%m-%d")

        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "start_date": race_date,
            "end_date": race_date,
            "hourly": "temperature_2m,weather_code,precipitation",
            "timezone": "UTC",
        }

        client = get_shared_http_client(httpx.AsyncClient, timeout=self.timeout)
        response = await fetch_with_retry(
            client, OPEN_METEO_ARCHIVE_URL, params=params, pacer=WEATHER_BUDGET, logger=logger
        )
        data = response.json()

        return _match_hourly_weather(
            data.get("hourly", {}),
            race_datetime,
            precipitation_key="precipitation",
            precipitation_as_amount=True,
        )

    @staticmethod
    def get_cached_value(key: str) -> Optional[WeatherData]:
        """Read fresh in-memory weather only, without fetching or refreshing data.

        Expired entries are evicted by the existing cache lookup.
        """
        return WeatherService._get_cached(key)

    @staticmethod
    def _get_cached(key: str) -> Optional[WeatherData]:
        """Return a fresh cached weather response and evict expired entries."""
        if key in _weather_cache:
            data, expires_at = _weather_cache[key]
            if datetime.now(timezone.utc) < expires_at:
                return data
            del _weather_cache[key]
        return None

    def _set_cached(self, key: str, data: WeatherData) -> None:
        """Cache a weather response using this service's configured TTL."""
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.cache_minutes)
        _weather_cache[key] = (data, expires_at)


def clear_weather_cache() -> None:
    """
    Clear the in-memory weather cache used for storing fetched WeatherData.

    Removes all cached entries so subsequent requests will fetch fresh data.
    """
    _weather_cache.clear()
    _hourly_forecast_cache.clear()


def _in_weekend_window(start: datetime, race_date: date, now: datetime) -> bool:
    """Return whether a session's UTC day lies in the weekend forecast window."""
    window = forecast_window(race_date, now.date())
    return window is not None and window[0] <= to_utc(start).date() <= window[1]


def _match_session_hour(hourly: dict, start: datetime) -> Optional[WeatherData]:
    """Pick one session's forecast hour; a session without a matching hour has no data."""
    if not hourly.get("time"):
        return None
    return _match_hourly_weather(hourly, start, precipitation_key="precipitation_probability")


def _weekend_from(found: Sequence[tuple[str, Optional[WeatherData]]]) -> Optional[WeekendWeather]:
    """Keep the sessions with data; None when nothing is left to show."""
    sessions = tuple(SessionWeather(kind, weather) for kind, weather in found if weather)
    return WeekendWeather(sessions) if sessions else None


# =========================================================================
# Circuit Weather Cache Functions (used by scheduler and renderer)
# =========================================================================


def get_cached_circuit_weather(circuit_id: str) -> Optional[WeatherData]:
    """
    Retrieve pre-fetched weather for a circuit from the in-memory cache.

    Parameters:
        circuit_id: Circuit identifier (e.g., "albert_park"); provider aliases such as
            "vegas" resolve to the same entry as the local ID.

    Returns:
        Cached WeatherData or None if not found.
    """
    circuit_id = canonical_circuit_id(circuit_id)
    cached = _circuit_weather_cache.get(circuit_id)
    if cached is None:
        return None
    data, expires_at = cached
    if datetime.now(timezone.utc) >= expires_at:
        del _circuit_weather_cache[circuit_id]
        return None
    return data


def set_cached_circuit_weather(circuit_id: str, data: WeatherData) -> None:
    """
    Store WeatherData for a circuit ID in the in-memory circuit weather cache.

    Parameters:
        circuit_id (str): Circuit identifier to associate with the weather data.
        data (WeatherData): WeatherData to store; overwrites any existing entry.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=CIRCUIT_WEATHER_TTL_MINUTES)
    _circuit_weather_cache[canonical_circuit_id(circuit_id)] = (data, expires_at)


def load_circuit_weather_to_cache(weather_dict: dict[str, dict]) -> int:
    """
    Load multiple circuit weather entries into the in-memory circuit weather cache.

    Parameters:
        weather_dict (dict[str, dict]): Mapping of circuit_id to weather dict with keys:
            temperature_c, weather_code, precipitation_probability.

    Returns:
        int: Number of circuits successfully loaded into the cache.
    """
    count = 0
    for circuit_id, data in weather_dict.items():
        try:
            temperature_raw = data["temperature_c"]
            fetched_at_raw = data["fetched_at"]
            if isinstance(temperature_raw, bool) or not isinstance(temperature_raw, (int, float)):
                raise TypeError("temperature_c must be numeric")
            temperature = float(temperature_raw)
            if not math.isfinite(temperature):
                raise ValueError("temperature_c must be finite")
            if not isinstance(fetched_at_raw, str):
                raise TypeError("fetched_at is not a string")
            fetched_at = datetime.fromisoformat(fetched_at_raw)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            else:
                fetched_at = fetched_at.astimezone(timezone.utc)

            weather = WeatherData(
                temperature_c=temperature,
                weather_code=data.get("weather_code", 0),
                precipitation_probability=data.get("precipitation_probability", 0),
            )
            expires_at = fetched_at + timedelta(minutes=CIRCUIT_WEATHER_TTL_MINUTES)
            if expires_at <= datetime.now(timezone.utc):
                continue
            _circuit_weather_cache[canonical_circuit_id(circuit_id)] = (weather, expires_at)
            count += 1
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Invalid weather data for %s: %s", circuit_id, e)
            continue
    return count


def clear_circuit_weather_cache() -> None:
    """
    Clear the in-memory cache of pre-fetched circuit weather data.

    Removes all entries so subsequent reads will miss until data is repopulated.
    """
    _circuit_weather_cache.clear()


def _build_weather_service() -> WeatherService:
    """Construct a weather service from the active application configuration."""
    return WeatherService(
        timeout=config.REQUEST_TIMEOUT,
        cache_minutes=config.WEATHER_CACHE_MINUTES,
    )


def parse_coordinate(value: str | int | float | None) -> Optional[float]:
    """Parse a coordinate-like value, returning ``None`` when invalid."""
    if value is None:
        return None

    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _extract_circuit_context(race_data: dict) -> tuple[str, Optional[float], Optional[float]]:
    """Extract a circuit identifier and parsed coordinates from race data."""
    circuit = race_data.get("circuit", {})
    circuit_id = circuit.get("circuitId") or circuit.get("circuit_id", "")
    displayed_lon = circuit.get("long") if circuit.get("long") is not None else circuit.get("lon")

    lat = parse_coordinate(circuit.get("lat"))
    lon = parse_coordinate(displayed_lon)

    if (circuit.get("lat") is not None and lat is None) or (
        (circuit.get("long") or circuit.get("lon")) is not None and lon is None
    ):
        logger.debug(
            "Invalid circuit coordinates: lat=%s lon=%s",
            circuit.get("lat"),
            displayed_lon,
        )

    return circuit_id, lat, lon


def _format_precipitation_amount(value: float | int | None) -> str:
    """Format a precipitation amount in millimetres without redundant zeros."""
    amount = float(value or 0)
    formatted = f"{amount:.1f}".rstrip("0").rstrip(".")
    return f"{formatted} mm"


def _match_hourly_weather(
    hourly: dict,
    race_datetime: datetime,
    *,
    precipitation_key: str,
    precipitation_as_amount: bool = False,
) -> Optional[WeatherData]:
    """Select the closest usable hourly weather row for a race timestamp."""
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weather_code", [])
    precip_values = hourly.get(precipitation_key, [])

    if not times:
        logger.warning("Empty weather response")
        return None

    race_dt_utc = to_utc(race_datetime)
    race_hour_str = race_dt_utc.strftime("%Y-%m-%dT%H:00")

    matched_index: Optional[int] = None
    for i, t in enumerate(times):
        if t == race_hour_str:
            matched_index = i
            break

    if matched_index is None:
        logger.debug("Exact hour %s not found, finding closest hour on race date", race_hour_str)
        race_date_str = race_dt_utc.strftime("%Y-%m-%d")
        closest_delta_seconds: Optional[float] = None

        for i, t in enumerate(times):
            if not t.startswith(race_date_str):
                continue

            try:
                candidate_dt = parse_utc_instant(t)
            except ValueError:
                continue

            delta_seconds = abs((candidate_dt - race_dt_utc).total_seconds())
            if closest_delta_seconds is None or delta_seconds < closest_delta_seconds:
                closest_delta_seconds = delta_seconds
                matched_index = i

    if matched_index is None:
        logger.warning("Could not find weather for %s", race_hour_str)
        return None

    return _hourly_weather_row(temps, codes, precip_values, matched_index, precipitation_as_amount)


def _hourly_weather_row(
    temps: list, codes: list, precipitation: list, index: int, as_amount: bool
) -> Optional[WeatherData]:
    """Read one provider row, preserving the defaults for missing optional fields."""
    temperature = _finite_number(temps[index]) if index < len(temps) else None
    raw_code = codes[index] if index < len(codes) else None
    raw_precip = precipitation[index] if index < len(precipitation) else None
    code = _finite_number(0 if raw_code is None else raw_code)
    precip = _finite_number(0 if raw_precip is None else raw_precip)
    if temperature is None or code is None or precip is None:
        return None
    return WeatherData(
        temperature_c=temperature,
        weather_code=int(code),
        precipitation_probability=0 if as_amount else int(precip),
        precipitation_display_override=_format_precipitation_amount(precip) if as_amount else None,
    )


async def _resolve_current_weather(
    *,
    circuit_id: str,
    lat: Optional[float],
    lon: Optional[float],
    weather_service: Optional[WeatherService],
) -> tuple[Optional[WeatherData], Optional[WeatherService]]:
    """Resolve current circuit weather while reusing a lazily built service."""
    current_weather: Optional[WeatherData] = (
        get_cached_circuit_weather(circuit_id) if circuit_id else None
    )
    if current_weather is not None:
        return current_weather, weather_service

    if lat is None or lon is None:
        return None, weather_service

    service = weather_service or _build_weather_service()
    current_weather = await service.get_current_weather(lat, lon)

    if current_weather and circuit_id:
        set_cached_circuit_weather(circuit_id, current_weather)

    return current_weather, service


async def _resolve_race_weather(
    *,
    lat: Optional[float],
    lon: Optional[float],
    race_dt: Optional[datetime],
    weather_service: Optional[WeatherService],
) -> tuple[Optional[WeatherData], Optional[WeatherService]]:
    """Resolve race-time weather while reusing a lazily built service."""
    if lat is None or lon is None or race_dt is None:
        return None, weather_service

    service = weather_service or _build_weather_service()
    race_weather = await service.get_race_weather(lat, lon, race_dt)
    return race_weather, service


async def _get_weather_context_unbounded(
    race_data: dict | None,
    weather_by_type: dict[str, Optional[WeatherDisplay]],
    *,
    include_weekend: bool = False,
    wanted: Collection[str] = ("current", "race"),
) -> tuple[
    Optional[WeatherData],
    Optional[WeatherData],
    dict[str, Optional[WeatherDisplay]],
]:
    """
    Build weather context (current, race-day and optionally weekend) for a given race.

    Returns a tuple of (current_weather, race_weather, weather_by_type).
    weather_by_type always includes "off": None and adds "current"/"race" (and "weekend"
    with ``include_weekend``) when data exists. It is filled in place, so the bounded caller
    keeps whatever already resolved if the remaining lookups time out.
    """
    weather_by_type.setdefault("off", None)

    if not config.WEATHER_ENABLED or not race_data:
        return None, None, weather_by_type

    circuit_id, lat, lon = _extract_circuit_context(race_data)
    race_dt = race_session_start(race_data)

    current_weather = race_weather = None
    weather_service = None
    if "current" in wanted:
        current_weather, weather_service = await _resolve_current_weather(
            circuit_id=circuit_id,
            lat=lat,
            lon=lon,
            weather_service=None,
        )
        if current_weather:
            weather_by_type["current"] = current_weather
    if "race" in wanted:
        race_weather, weather_service = await _resolve_race_weather(
            lat=lat,
            lon=lon,
            race_dt=race_dt,
            weather_service=weather_service,
        )
        if race_weather:
            weather_by_type["race"] = race_weather

    if include_weekend and lat is not None and lon is not None:
        race_date, sessions = weekend_session_starts(race_data)
        service = weather_service or _build_weather_service()
        weekend_weather = await service.get_weekend_weather(lat, lon, race_date, sessions)
        if weekend_weather:
            weather_by_type["weekend"] = weekend_weather

    return current_weather, race_weather, weather_by_type


async def get_weather_context(
    race_data: dict | None,
    *,
    wanted: Collection[str] = ("current", "race"),
    include_weekend: bool = False,
) -> tuple[WeatherData | None, WeatherData | None, dict[str, WeatherDisplay | None]]:
    """Bound optional weather enrichment for both scheduled and on-demand calendars.

    ``include_weekend`` adds the weather_type=weekend summary; it reuses the race-day
    forecast request whenever the race itself is within the forecast horizon.
    """
    weather_by_type: dict[str, WeatherDisplay | None] = {"off": None}
    try:
        async with asyncio.timeout(config.WEATHER_ENRICHMENT_TIMEOUT_SECONDS):
            return await _get_weather_context_unbounded(
                race_data, weather_by_type, include_weekend=include_weekend, wanted=wanted
            )
    except TimeoutError:
        # Keep a lookup that already finished (e.g. current weather) instead of discarding it
        # because a slower one (e.g. the race-day forecast) ran out of budget.
        logger.warning(
            "Weather enrichment timed out; rendering with partial weather (%s)",
            ", ".join(key for key in weather_by_type if key != "off") or "none",
        )
        current, race = weather_by_type.get("current"), weather_by_type.get("race")
        return (
            current if isinstance(current, WeatherData) else None,
            race if isinstance(race, WeatherData) else None,
            weather_by_type,
        )


# =========================================================================
# Weather Prefetch Functions (used by scheduler)
# =========================================================================


def _get_next_race_details() -> Optional[tuple[float, float, datetime]]:
    """Extract coordinates and race datetime from next race static data."""
    from app.services.f1_service import F1Service

    f1_service = F1Service()
    race_data = f1_service.get_next_race_from_static()

    if not race_data:
        logger.warning("No upcoming race for weather prefetch")
        return None

    circuit = race_data.get("circuit", {})
    lat = circuit.get("lat")
    lon = circuit.get("long")

    if not lat or not lon:
        logger.warning("No coordinates for weather prefetch")
        return None

    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except ValueError, TypeError:
        logger.warning("Invalid coordinates: lat=%s, lon=%s", lat, lon)
        return None

    race_dt = race_session_start(race_data)
    if race_dt is None:
        logger.warning("No known race start for weather prefetch")
        return None

    return (lat_f, lon_f, race_dt)


async def prefetch_weather_for_next_race(db: "Database") -> Optional[WeatherData]:
    """
    Pre-fetch weather for next race and store in DB cache.
    Called by scheduler at :55 each hour before image generation.
    """
    details = _get_next_race_details()
    if not details:
        return None

    lat, lon, race_dt = details
    weather_service = WeatherService(cache_minutes=120)

    current_weather = await weather_service.get_current_weather(lat, lon)
    if current_weather:
        cache_key = _current_weather_cache_key(lat, lon)
        await db.save_weather_cache(
            cache_key=cache_key,
            temperature_c=current_weather.temperature_c,
            weather_code=current_weather.weather_code,
            precipitation_probability=current_weather.precipitation_probability,
            ttl_minutes=120,
        )
        logger.info("Prefetched current weather: %s", current_weather.temp_display)

    race_weather = await weather_service.get_race_weather(lat, lon, race_dt)
    if race_weather:
        cache_key = _race_weather_cache_key(lat, lon, race_dt)
        await db.save_weather_cache(
            cache_key=cache_key,
            temperature_c=race_weather.temperature_c,
            weather_code=race_weather.weather_code,
            precipitation_probability=race_weather.precipitation_probability,
            ttl_minutes=120,
            precipitation_display_override=race_weather.precipitation_display_override,
        )
        logger.info("Prefetched race day weather: %s", race_weather.temp_display)

    if "weekend" in config.pregenerate_weather_types:
        await _prefetch_weekend_weather(db, weather_service, lat, lon)

    return race_weather or current_weather


def _next_weekend_sessions() -> tuple[date | None, list[tuple[str, datetime]]]:
    """Return the next race's UTC date and its timed weekend sessions from static data."""
    from app.services.f1_service import F1Service

    return weekend_session_starts(F1Service().get_next_race_from_static() or {})


async def _prefetch_weekend_weather(
    db: "Database", weather_service: WeatherService, lat: float, lon: float
) -> None:
    """Persist the qualifying and sprint forecasts for pregenerated weekend calendars.

    Runs only when PREGENERATE_WEATHER_TYPES lists ``weekend``. The race forecast is saved by
    the caller; the sessions reuse its request (same weekend window).
    """
    race_date, sessions = _next_weekend_sessions()
    weekend = await weather_service.get_weekend_weather(lat, lon, race_date, sessions)
    if weekend is None:
        return
    starts = dict(sessions)
    for item in weekend.sessions:
        if item.session == "race":
            continue
        await db.save_weather_cache(
            cache_key=_race_weather_cache_key(lat, lon, starts[item.session]),
            temperature_c=item.weather.temperature_c,
            weather_code=item.weather.weather_code,
            precipitation_probability=item.weather.precipitation_probability,
            ttl_minutes=120,
        )
    logger.info("Prefetched weekend weather: %s", weekend.temp_display)


def _weather_from_db_row(row: tuple) -> WeatherData:
    """Rebuild WeatherData from a weather_cache row, keeping a stored precipitation amount."""
    temp_c, code, precip = row[:3]
    return WeatherData(
        temperature_c=temp_c,
        weather_code=code,
        precipitation_probability=precip,
        precipitation_display_override=row[4] if len(row) > 4 else None,
    )


async def get_cached_weather_from_db(db: "Database", cache_key: str) -> Optional[WeatherData]:
    """Get weather from DB cache."""
    cached = await db.get_weather_cache(cache_key)
    if cached:
        return _weather_from_db_row(cached)
    return None


async def load_prefetched_weather_from_db(db: "Database") -> int:
    """Warm the in-memory next-race weather cache from the persisted DB cache on startup.

    Without this the hourly prefetch's DB writes were never read back, so a restart discarded
    the prefetched weather until the next :55 run. Keys are built via the shared
    _current_weather_cache_key/_race_weather_cache_key helpers used by save and read sites.
    """
    details = _get_next_race_details()
    if not details:
        return 0

    lat, lon, race_dt = details
    keys = [
        _current_weather_cache_key(lat, lon),
        _race_weather_cache_key(lat, lon, race_dt),
    ]
    if "weekend" in config.pregenerate_weather_types:
        _, sessions = _next_weekend_sessions()
        keys.extend(
            _race_weather_cache_key(lat, lon, start) for kind, start in sessions if kind != "race"
        )

    loaded = 0
    now = datetime.now(timezone.utc)
    for cache_key in keys:
        cached = await db.get_weather_cache(cache_key)
        if not cached:
            continue
        data = _weather_from_db_row(cached)
        cached_at_str = cached[3]
        # Stamp with the ORIGINAL fetch time so the in-memory TTL measures true data age;
        # stamping "now" would serve up-to-2h-old data as fresh for another full window.
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
        except TypeError, ValueError:
            cached_at = now
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        _weather_cache[cache_key] = (data, cached_at + timedelta(minutes=120))
        loaded += 1
    return loaded
