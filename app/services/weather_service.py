"""Weather service using Open-Meteo API for race weekend forecasts."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

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

_weather_cache: dict[str, tuple["WeatherData", datetime]] = {}


@dataclass
class WeatherData:
    temperature_c: float
    weather_code: int
    precipitation_probability: int

    @property
    def icon(self) -> str:
        return WEATHER_ICONS.get(self.weather_code, "\u2601")

    @property
    def temp_display(self) -> str:
        return f"{int(round(self.temperature_c))}\u00b0"

    @property
    def precip_display(self) -> str:
        return f"{self.precipitation_probability}%"


class WeatherService:
    def __init__(self, timeout: int = 10, cache_minutes: int = 60):
        self.timeout = httpx.Timeout(timeout)
        self.cache_minutes = cache_minutes

    async def get_current_weather(self, lat: float, lon: float) -> Optional[WeatherData]:
        cache_key = f"current_{round(lat, 2)}_{round(lon, 2)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Current weather cache hit for {cache_key}")
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning(f"Invalid coordinates: lat={lat}, lon={lon}")
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
            logger.warning(f"Weather API HTTP error: {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch current weather: {e}")
            return None

    async def _fetch_current_weather(self, lat: float, lon: float) -> Optional[WeatherData]:
        params = {
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
        cache_key = f"{round(lat, 2)}_{round(lon, 2)}_{race_datetime.isoformat()}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            logger.debug(f"Weather cache hit for {cache_key}")
            return cached

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            logger.warning(f"Invalid coordinates: lat={lat}, lon={lon}")
            return None

        now = datetime.now(race_datetime.tzinfo) if race_datetime.tzinfo else datetime.utcnow()
        delta = race_datetime - now
        days_until_race = delta.days

        if days_until_race < 0:
            logger.debug("Race already started, no weather forecast needed")
            return None

        if days_until_race > 16:
            logger.debug(f"Race {days_until_race} days away, outside 16-day forecast range")
            return None

        try:
            weather_data = await self._fetch_weather(lat, lon, race_datetime, days_until_race)
            if weather_data:
                self._set_cached(cache_key, weather_data)
            return weather_data

        except httpx.TimeoutException:
            logger.warning("Weather API request timed out")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning(f"Weather API HTTP error: {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch weather: {e}")
            return None

    async def _fetch_weather(
        self,
        lat: float,
        lon: float,
        race_datetime: datetime,
        days_ahead: int,
    ) -> Optional[WeatherData]:
        params = {
            "latitude": round(lat, 2),
            "longitude": round(lon, 2),
            "hourly": "temperature_2m,weather_code,precipitation_probability",
            "timezone": "auto",
            "forecast_days": min(days_ahead + 1, 16),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        codes = hourly.get("weather_code", [])
        precip = hourly.get("precipitation_probability", [])

        if not times:
            logger.warning("Empty weather response")
            return None

        race_hour_str = race_datetime.strftime("%Y-%m-%dT%H:00")

        for i, t in enumerate(times):
            if t == race_hour_str:
                return WeatherData(
                    temperature_c=temps[i] if i < len(temps) else 20.0,
                    weather_code=codes[i] if i < len(codes) else 0,
                    precipitation_probability=(precip[i] if i < len(precip) and precip[i] else 0),
                )

        logger.debug(f"Exact hour {race_hour_str} not found, finding closest")
        race_date_str = race_datetime.strftime("%Y-%m-%d")
        for i, t in enumerate(times):
            if t.startswith(race_date_str):
                return WeatherData(
                    temperature_c=temps[i] if i < len(temps) else 20.0,
                    weather_code=codes[i] if i < len(codes) else 0,
                    precipitation_probability=(precip[i] if i < len(precip) and precip[i] else 0),
                )

        logger.warning(f"Could not find weather for {race_hour_str}")
        return None

    def _get_cached(self, key: str) -> Optional[WeatherData]:
        if key in _weather_cache:
            data, cached_at = _weather_cache[key]
            if datetime.now() - cached_at < timedelta(minutes=self.cache_minutes):
                return data
            del _weather_cache[key]
        return None

    def _set_cached(self, key: str, data: WeatherData) -> None:
        _weather_cache[key] = (data, datetime.now())


def clear_weather_cache() -> None:
    _weather_cache.clear()
