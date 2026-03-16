"""Weather service using Open-Meteo APIs for race weekend weather."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from app.config import config

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

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_weather_cache: dict[str, tuple["WeatherData", datetime]] = {}

# Circuit weather cache - populated by scheduler, read by renderer
# Maps circuit_id -> WeatherData
_circuit_weather_cache: dict[str, "WeatherData"] = {}


def _to_utc_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class WeatherData:
    temperature_c: float
    weather_code: int
    precipitation_probability: int
    precipitation_display_override: str | None = None

    @property
    def icon(self) -> str:
        return WEATHER_ICONS.get(self.weather_code, "\u2601")

    @property
    def temp_display(self) -> str:
        return f"{int(round(self.temperature_c))}\u00b0"

    @property
    def precip_display(self) -> str:
        if self.precipitation_display_override is not None:
            return self.precipitation_display_override
        return f"{self.precipitation_probability}%"


class WeatherService:
    def __init__(self, timeout: int = 10, cache_minutes: int = 60):
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
        cache_key = f"current_{round(lat, 2)}_{round(lon, 2)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Current weather cache hit for %s", cache_key)
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning("Invalid coordinates: lat=%s, lon=%s", lat, lon)
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
            WeatherData with temperature_c, weather_code, precipitation_probability.
            Uses defaults (20.0C, code 0, prob 0) if API omits values.
        """
        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "current": "temperature_2m,weather_code",
            "hourly": "precipitation_probability",
            "timezone": "auto",
            "forecast_days": 1,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            data = response.json()

        current = data.get("current", {})
        hourly = data.get("hourly", {})

        temp = current.get("temperature_2m", 20.0)
        code = current.get("weather_code", 0)

        precip_probs = hourly.get("precipitation_probability", [])
        precip = precip_probs[0] if precip_probs else 0

        return WeatherData(
            temperature_c=temp,
            weather_code=code,
            precipitation_probability=precip or 0,
        )

    async def get_race_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        race_datetime_utc = _to_utc_datetime(race_datetime)

        cache_key = f"{round(lat, 2)}_{round(lon, 2)}_{race_datetime_utc.isoformat()}"
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

        # Open-Meteo forecast_days includes today, so to include race date
        # we need date_diff + 1 days.
        forecast_days = (race_datetime_utc.date() - now.date()).days + 1
        if forecast_days > 16:
            logger.debug(
                "Race requires %d forecast days, outside 16-day forecast range",
                forecast_days,
            )
            return None

        try:
            weather_data = await self._fetch_weather(lat, lon, race_datetime_utc, forecast_days)
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
        race_datetime_utc = _to_utc_datetime(race_datetime)

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
        forecast_days: int,
    ) -> Optional[WeatherData]:
        """
        Fetch hourly forecast for race datetime from Open-Meteo.

        Parameters:
            lat: Latitude.
            lon: Longitude.
            race_datetime: Target datetime (matches exact hour or same day).
            forecast_days: Number of forecast days to request (max 16).

        Returns:
            WeatherData for matching hour/day, or None if no data found.
        """
        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "timezone": "UTC",
            "forecast_days": min(forecast_days, 16),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            data = response.json()

        return _match_hourly_weather(
            data.get("hourly", {}),
            race_datetime,
            precipitation_key="precipitation_probability",
        )

    async def _fetch_historical_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
    ) -> Optional[WeatherData]:
        race_dt_utc = _to_utc_datetime(race_datetime)
        race_date = race_dt_utc.strftime("%Y-%m-%d")

        params: dict[str, str | int | float] = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "start_date": race_date,
            "end_date": race_date,
            "hourly": "temperature_2m,weather_code,precipitation",
            "timezone": "UTC",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(OPEN_METEO_ARCHIVE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        return _match_hourly_weather(
            data.get("hourly", {}),
            race_datetime,
            precipitation_key="precipitation",
            precipitation_as_amount=True,
        )

    def _get_cached(self, key: str) -> Optional[WeatherData]:
        if key in _weather_cache:
            data, cached_at = _weather_cache[key]
            if datetime.now() - cached_at < timedelta(minutes=self.cache_minutes):
                return data
            del _weather_cache[key]
        return None

    @staticmethod
    def _set_cached(key: str, data: WeatherData) -> None:
        _weather_cache[key] = (data, datetime.now())


def clear_weather_cache() -> None:
    """
    Clear the in-memory weather cache used for storing fetched WeatherData.

    Removes all cached entries so subsequent requests will fetch fresh data.
    """
    _weather_cache.clear()


# =========================================================================
# Circuit Weather Cache Functions (used by scheduler and renderer)
# =========================================================================


def get_cached_circuit_weather(circuit_id: str) -> Optional[WeatherData]:
    """
    Retrieve pre-fetched weather for a circuit from the in-memory cache.

    Parameters:
        circuit_id: Circuit identifier (e.g., "albert_park").

    Returns:
        Cached WeatherData or None if not found.
    """
    return _circuit_weather_cache.get(circuit_id)


def set_cached_circuit_weather(circuit_id: str, data: WeatherData) -> None:
    """
    Store WeatherData for a circuit ID in the in-memory circuit weather cache.

    Parameters:
        circuit_id (str): Circuit identifier to associate with the weather data.
        data (WeatherData): WeatherData to store; overwrites any existing entry.
    """
    _circuit_weather_cache[circuit_id] = data


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
            _circuit_weather_cache[circuit_id] = WeatherData(
                temperature_c=data.get("temperature_c", 20.0),
                weather_code=data.get("weather_code", 0),
                precipitation_probability=data.get("precipitation_probability", 0),
            )
            count += 1
        except (TypeError, ValueError) as e:
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
    return WeatherService(
        timeout=config.REQUEST_TIMEOUT,
        cache_minutes=config.WEATHER_CACHE_MINUTES,
    )


def _parse_coordinate(value: str | int | float | None) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_circuit_context(race_data: dict) -> tuple[str, Optional[float], Optional[float]]:
    circuit = race_data.get("circuit", {})
    circuit_id = circuit.get("circuitId") or circuit.get("circuit_id", "")
    displayed_lon = circuit.get("long") if circuit.get("long") is not None else circuit.get("lon")

    lat = _parse_coordinate(circuit.get("lat"))
    lon = _parse_coordinate(displayed_lon)

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
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weather_code", [])
    precip_values = hourly.get(precipitation_key, [])

    if not times:
        logger.warning("Empty weather response")
        return None

    race_dt_utc = _to_utc_datetime(race_datetime)
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
                candidate_dt = _to_utc_datetime(datetime.fromisoformat(t.replace("Z", "+00:00")))
            except ValueError:
                continue

            delta_seconds = abs((candidate_dt - race_dt_utc).total_seconds())
            if closest_delta_seconds is None or delta_seconds < closest_delta_seconds:
                closest_delta_seconds = delta_seconds
                matched_index = i

    if matched_index is None:
        logger.warning("Could not find weather for %s", race_hour_str)
        return None

    precip_value = precip_values[matched_index] if matched_index < len(precip_values) else 0
    precip_probability = 0
    precip_display_override = None
    if precipitation_as_amount:
        precip_display_override = _format_precipitation_amount(precip_value)
    else:
        precip_probability = int(precip_value or 0)

    return WeatherData(
        temperature_c=temps[matched_index] if matched_index < len(temps) else 20.0,
        weather_code=codes[matched_index] if matched_index < len(codes) else 0,
        precipitation_probability=precip_probability,
        precipitation_display_override=precip_display_override,
    )


def _extract_race_datetime(race_data: dict) -> Optional[datetime]:
    schedule = race_data.get("schedule", [])
    race_session = next(
        (session for session in schedule if str(session.get("name", "")).lower() == "race"),
        None,
    )
    if not race_session:
        return None

    dt_str = race_session.get("datetime")
    if not dt_str:
        return None

    try:
        return _to_utc_datetime(datetime.fromisoformat(dt_str.replace("Z", "+00:00")))
    except ValueError:
        logger.debug("Invalid race datetime: %s", dt_str)
        return None


async def _resolve_current_weather(
    *,
    circuit_id: str,
    lat: Optional[float],
    lon: Optional[float],
    weather_service: Optional[WeatherService],
) -> tuple[Optional[WeatherData], Optional[WeatherService]]:
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
    if lat is None or lon is None or race_dt is None:
        return None, weather_service

    service = weather_service or _build_weather_service()
    race_weather = await service.get_race_weather(lat, lon, race_dt)
    return race_weather, service


async def get_weather_context(
    race_data: dict | None,
) -> tuple[
    Optional[WeatherData],
    Optional[WeatherData],
    dict[str, Optional[WeatherData]],
]:
    """
    Build weather context (current and race-day) for a given race.

    Returns a tuple of (current_weather, race_weather, weather_by_type).
    weather_by_type always includes "off": None and adds "current"/"race" when data exists.
    """
    weather_by_type: dict[str, Optional[WeatherData]] = {"off": None}

    if not config.WEATHER_ENABLED or not race_data:
        return None, None, weather_by_type

    circuit_id, lat, lon = _extract_circuit_context(race_data)
    race_dt = _extract_race_datetime(race_data)

    current_weather, weather_service = await _resolve_current_weather(
        circuit_id=circuit_id,
        lat=lat,
        lon=lon,
        weather_service=None,
    )
    if current_weather:
        weather_by_type["current"] = current_weather

    race_weather, _ = await _resolve_race_weather(
        lat=lat,
        lon=lon,
        race_dt=race_dt,
        weather_service=weather_service,
    )
    if race_weather:
        weather_by_type["race"] = race_weather

    return current_weather, race_weather, weather_by_type


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
    except (ValueError, TypeError):
        logger.warning("Invalid coordinates: lat=%s, lon=%s", lat, lon)
        return None

    schedule = race_data.get("schedule", [])
    race_session = next((s for s in schedule if s.get("name") == "Race"), None)

    if not race_session:
        logger.warning("No race session found for weather prefetch")
        return None

    race_dt_str = race_session.get("datetime")
    if not race_dt_str:
        logger.warning("No race datetime for weather prefetch")
        return None

    try:
        race_dt = datetime.fromisoformat(race_dt_str)
    except ValueError:
        logger.warning("Invalid race datetime: %s", race_dt_str)
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
        cache_key = f"current_{round(lat, 2)}_{round(lon, 2)}"
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
        cache_key = f"{round(lat, 2)}_{round(lon, 2)}_{race_dt.isoformat()}"
        await db.save_weather_cache(
            cache_key=cache_key,
            temperature_c=race_weather.temperature_c,
            weather_code=race_weather.weather_code,
            precipitation_probability=race_weather.precipitation_probability,
            ttl_minutes=120,
        )
        logger.info("Prefetched race day weather: %s", race_weather.temp_display)
        return race_weather

    return current_weather


async def get_cached_weather_from_db(db: "Database", cache_key: str) -> Optional[WeatherData]:
    """Get weather from DB cache."""
    cached = await db.get_weather_cache(cache_key)
    if cached:
        temp_c, code, precip = cached
        return WeatherData(
            temperature_c=temp_c,
            weather_code=code,
            precipitation_probability=precip,
        )
    return None
