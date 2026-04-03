"""Test weather service."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.http_client import _reset_shared_http_clients_for_tests
from app.services.weather_service import (
    WEATHER_ICONS,
    WeatherData,
    WeatherService,
    clear_circuit_weather_cache,
    clear_weather_cache,
    get_cached_circuit_weather,
    get_weather_context,
    load_circuit_weather_to_cache,
    set_cached_circuit_weather,
)


class TestWeatherData:
    @staticmethod
    def test_icon_clear_sky():
        data = WeatherData(temperature_c=25.0, weather_code=0, precipitation_probability=10)
        assert data.icon == WEATHER_ICONS[0]

    @staticmethod
    def test_icon_rain():
        data = WeatherData(temperature_c=15.0, weather_code=61, precipitation_probability=80)
        assert data.icon == WEATHER_ICONS[61]

    @staticmethod
    def test_icon_unknown_code_fallback():
        data = WeatherData(temperature_c=20.0, weather_code=999, precipitation_probability=0)
        assert data.icon == "\u2601"

    @staticmethod
    def test_temp_display_rounds_correctly():
        data = WeatherData(temperature_c=25.7, weather_code=0, precipitation_probability=0)
        assert data.temp_display == "26\u00b0"

        data = WeatherData(temperature_c=25.4, weather_code=0, precipitation_probability=0)
        assert data.temp_display == "25\u00b0"

    @staticmethod
    def test_precip_display():
        data = WeatherData(temperature_c=20.0, weather_code=0, precipitation_probability=45)
        assert data.precip_display == "45%"


class TestWeatherIcons:
    @staticmethod
    def test_all_codes_have_icons():
        expected_codes = [
            0,
            1,
            2,
            3,
            45,
            48,
            51,
            53,
            55,
            61,
            63,
            65,
            71,
            73,
            75,
            80,
            81,
            82,
            95,
        ]
        for code in expected_codes:
            assert code in WEATHER_ICONS


class TestWeatherService:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _reset_shared_http_clients_for_tests()
        clear_weather_cache()
        clear_circuit_weather_cache()
        yield
        clear_weather_cache()
        clear_circuit_weather_cache()
        _reset_shared_http_clients_for_tests()

    @staticmethod
    @pytest.mark.asyncio
    async def test_invalid_coordinates_returns_none():
        service = WeatherService()
        race_dt = datetime.now(timezone.utc) + timedelta(days=2)
        result = await service.get_race_weather(lat=100, lon=0, race_datetime=race_dt)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_past_race_returns_historical_weather(monkeypatch):
        captured = {}

        race_dt = datetime.now(timezone.utc) - timedelta(days=1)
        race_hour = race_dt.strftime("%Y-%m-%dT%H:00")

        mock_response_data = {
            "hourly": {
                "time": [race_hour],
                "temperature_2m": [19.0],
                "weather_code": [61],
                "precipitation": [0.8],
            }
        }

        class MockResponse:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @staticmethod
            async def get(url, params=None):
                captured["url"] = url
                captured["params"] = params
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
        assert result is not None
        assert result.temperature_c == 19.0
        assert result.weather_code == 61
        assert result.precipitation_probability == 0
        assert result.precip_display == "0.8 mm"
        assert captured["url"] == "https://archive-api.open-meteo.com/v1/archive"
        assert captured["params"]["start_date"] == race_dt.strftime("%Y-%m-%d")

    @staticmethod
    @pytest.mark.asyncio
    async def test_race_too_far_returns_none():
        service = WeatherService()
        race_dt = datetime.now(timezone.utc) + timedelta(days=20)
        result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_invalid_coordinates():
        service = WeatherService()
        result = await service.get_current_weather(lat=100, lon=0)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_invalid_longitude():
        service = WeatherService()
        result = await service.get_current_weather(lat=50, lon=200)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_success(monkeypatch):
        mock_response_data = {
            "current": {"temperature_2m": 22.5, "weather_code": 1},
            "hourly": {"precipitation_probability": [15, 20, 25]},
        }

        class MockResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_current_weather(lat=52.52, lon=13.41)
        assert result is not None
        assert result.temperature_c == 22.5
        assert result.weather_code == 1
        assert result.precipitation_probability == 15
        assert result.icon == WEATHER_ICONS[1]
        assert result.temp_display == "22°"
        assert result.precip_display == "15%"

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_cache_hit(monkeypatch):
        call_count = 0
        mock_response_data = {
            "current": {"temperature_2m": 18.0, "weather_code": 3},
            "hourly": {"precipitation_probability": [30]},
        }

        class MockResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                nonlocal call_count
                call_count += 1
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result1 = await service.get_current_weather(lat=48.85, lon=2.35)
        assert result1 is not None
        assert call_count == 1

        result2 = await service.get_current_weather(lat=48.85, lon=2.35)
        assert result2 is not None
        assert call_count == 1
        assert result1.temperature_c == result2.temperature_c

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_timeout(monkeypatch):
        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                raise httpx.TimeoutException("Connection timed out")

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_current_weather(lat=35.68, lon=139.69)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_http_error(monkeypatch):
        class MockResponse:
            status_code = 500

            def raise_for_status(self):
                mock_request = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
                mock_response = httpx.Response(500, request=mock_request)
                raise httpx.HTTPStatusError(
                    "Server error", request=mock_request, response=mock_response
                )

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_current_weather(lat=40.71, lon=-74.01)
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_current_weather_empty_precipitation(monkeypatch):
        mock_response_data = {
            "current": {"temperature_2m": 25.0, "weather_code": 0},
            "hourly": {"precipitation_probability": []},
        }

        class MockResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_current_weather(lat=51.51, lon=-0.13)
        assert result is not None
        assert result.precipitation_probability == 0

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_race_weather_requests_forecast_covering_race_date(monkeypatch):
        base_now = datetime.now(timezone.utc)
        race_dt = base_now + timedelta(days=11, hours=8)
        race_hour = race_dt.strftime("%Y-%m-%dT%H:00")
        expected_forecast_days = (race_dt.date() - base_now.date()).days + 1

        captured_params: dict[str, int] = {}
        mock_response_data = {
            "hourly": {
                "time": [race_hour],
                "temperature_2m": [21.0],
                "weather_code": [1],
                "precipitation_probability": [25],
            }
        }

        class MockResponse:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @staticmethod
            async def get(url, params=None):
                assert params is not None
                captured_params["forecast_days"] = int(params["forecast_days"])
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
        assert result is not None
        assert captured_params["forecast_days"] == expected_forecast_days

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_race_weather_fallback_uses_nearest_hour(monkeypatch):
        race_dt = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
            hour=4,
            minute=30,
            second=0,
            microsecond=0,
        )
        race_date = race_dt.strftime("%Y-%m-%d")
        mock_response_data = {
            "hourly": {
                "time": [
                    f"{race_date}T02:00",
                    f"{race_date}T05:00",
                    f"{race_date}T08:00",
                ],
                "temperature_2m": [10.0, 25.0, 30.0],
                "weather_code": [3, 1, 0],
                "precipitation_probability": [60, 20, 5],
            }
        }

        class MockResponse:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json():
                return mock_response_data

        class MockAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @staticmethod
            async def get(url, params=None):
                return MockResponse()

        monkeypatch.setattr(
            "app.services.weather_service.httpx.AsyncClient",
            lambda **kwargs: MockAsyncClient(),
        )

        service = WeatherService()
        result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
        assert result is not None
        assert result.temperature_c == 25.0
        assert result.weather_code == 1
        assert result.precipitation_probability == 20

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_weather_context_returns_race_forecast(monkeypatch):
        race_data = {
            "circuit": {"circuitId": "test_circuit", "lat": "50.0", "long": "14.0"},
            "schedule": [
                {
                    "name": "race",
                    "datetime": "2026-03-08T15:00:00+02:00",
                },
            ],
        }

        captured: dict[str, datetime] = {}

        async def fake_current(self, lat, lon):
            return WeatherData(temperature_c=15.0, weather_code=1, precipitation_probability=10)

        async def fake_race(self, lat, lon, race_datetime):
            captured["race_datetime"] = race_datetime
            return WeatherData(temperature_c=30.0, weather_code=3, precipitation_probability=80)

        monkeypatch.setattr(WeatherService, "get_current_weather", fake_current)
        monkeypatch.setattr(WeatherService, "get_race_weather", fake_race)

        current, race, weather_by_type = await get_weather_context(race_data)
        assert current is not None
        assert race is not None
        assert current.temperature_c != race.temperature_c
        assert weather_by_type["current"] is not None
        assert weather_by_type["race"] is not None
        assert weather_by_type["current"].temperature_c == 15.0
        assert weather_by_type["race"].temperature_c == 30.0
        assert captured["race_datetime"] == datetime(2026, 3, 8, 13, 0, tzinfo=timezone.utc)

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_weather_context_returns_historical_race_weather(monkeypatch):
        race_data = {
            "circuit": {"circuitId": "test_circuit", "lat": "50.0", "long": "14.0"},
            "schedule": [
                {
                    "name": "Race",
                    "datetime": "2026-03-01T15:00:00+00:00",
                },
            ],
        }

        async def fake_current(self, lat, lon):
            return WeatherData(temperature_c=12.0, weather_code=2, precipitation_probability=5)

        async def fake_race(self, lat, lon, race_datetime):
            return WeatherData(
                temperature_c=18.0,
                weather_code=3,
                precipitation_probability=0,
                precipitation_display_override="1.4 mm",
            )

        monkeypatch.setattr(WeatherService, "get_current_weather", fake_current)
        monkeypatch.setattr(WeatherService, "get_race_weather", fake_race)

        current, race, weather_by_type = await get_weather_context(race_data)
        assert current is not None
        assert race is not None
        assert weather_by_type["race"] is not None
        assert weather_by_type["race"].precip_display == "1.4 mm"

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_weather_context_invalid_coordinates(monkeypatch):
        race_data = {
            "circuit": {"circuitId": "test_circuit", "lat": "invalid", "long": "14.0"},
            "schedule": [
                {"name": "Race", "datetime": datetime.now(timezone.utc).isoformat()},
            ],
        }

        async def fail_current(self, lat, lon):
            raise AssertionError("get_current_weather should not be called")

        async def fail_race(self, lat, lon, race_datetime):
            raise AssertionError("get_race_weather should not be called")

        monkeypatch.setattr(WeatherService, "get_current_weather", fail_current)
        monkeypatch.setattr(WeatherService, "get_race_weather", fail_race)

        current, race, weather_by_type = await get_weather_context(race_data)
        assert current is None
        assert race is None
        assert weather_by_type == {"off": None}


class TestCircuitWeatherCache:
    """Tests for the circuit weather cache functions used by scheduler."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """
        Clears the circuit weather cache before and after a test runs.

        Ensures each test executes with a clean cache state.
        """
        clear_circuit_weather_cache()
        yield
        clear_circuit_weather_cache()

    @staticmethod
    def test_get_cached_circuit_weather_not_found():
        """Test that missing circuit returns None."""
        result = get_cached_circuit_weather("nonexistent_circuit")
        assert result is None

    @staticmethod
    def test_set_and_get_cached_circuit_weather():
        """Test setting and retrieving circuit weather."""
        weather = WeatherData(
            temperature_c=25.0,
            weather_code=0,
            precipitation_probability=10,
        )
        set_cached_circuit_weather("albert_park", weather)

        result = get_cached_circuit_weather("albert_park")
        assert result is not None
        assert result.temperature_c == 25.0
        assert result.weather_code == 0
        assert result.precipitation_probability == 10

    @staticmethod
    def test_set_overwrites_existing():
        """Test that setting same circuit overwrites previous value."""
        weather1 = WeatherData(temperature_c=20.0, weather_code=0, precipitation_probability=0)
        weather2 = WeatherData(temperature_c=30.0, weather_code=61, precipitation_probability=80)

        set_cached_circuit_weather("monaco", weather1)
        set_cached_circuit_weather("monaco", weather2)

        result = get_cached_circuit_weather("monaco")
        assert result is not None
        assert result.temperature_c == 30.0
        assert result.weather_code == 61

    @staticmethod
    def test_load_circuit_weather_to_cache():
        """Test bulk loading weather data from dict."""
        weather_dict = {
            "albert_park": {
                "temperature_c": 22.5,
                "weather_code": 1,
                "precipitation_probability": 15,
            },
            "monaco": {
                "temperature_c": 28.0,
                "weather_code": 0,
                "precipitation_probability": 5,
            },
        }

        count = load_circuit_weather_to_cache(weather_dict)
        assert count == 2

        albert = get_cached_circuit_weather("albert_park")
        assert albert is not None
        assert albert.temperature_c == 22.5

        monaco = get_cached_circuit_weather("monaco")
        assert monaco is not None
        assert monaco.temperature_c == 28.0

    @staticmethod
    def test_load_circuit_weather_with_defaults():
        """Test bulk loading with missing fields uses defaults."""
        weather_dict = {
            "silverstone": {},  # All fields missing
            "spa": {"temperature_c": 18.0},  # Only temp provided
        }

        count = load_circuit_weather_to_cache(weather_dict)
        assert count == 2

        silverstone = get_cached_circuit_weather("silverstone")
        assert silverstone is not None
        assert silverstone.temperature_c == 20.0  # Default
        assert silverstone.weather_code == 0  # Default
        assert silverstone.precipitation_probability == 0  # Default

        spa = get_cached_circuit_weather("spa")
        assert spa is not None
        assert spa.temperature_c == 18.0
        assert spa.weather_code == 0  # Default

    @staticmethod
    def test_load_circuit_weather_skips_invalid():
        """Test that invalid data is skipped gracefully."""
        weather_dict = {
            "valid": {"temperature_c": 25.0, "weather_code": 0, "precipitation_probability": 10},
            "invalid": {"temperature_c": "not_a_number"},  # Invalid type
        }

        count = load_circuit_weather_to_cache(weather_dict)
        # Should load valid entry, skip invalid
        assert count >= 1

        valid = get_cached_circuit_weather("valid")
        assert valid is not None

    @staticmethod
    def test_clear_circuit_weather_cache():
        """Test clearing the circuit weather cache."""
        weather = WeatherData(temperature_c=25.0, weather_code=0, precipitation_probability=10)
        set_cached_circuit_weather("test_circuit", weather)

        assert get_cached_circuit_weather("test_circuit") is not None

        clear_circuit_weather_cache()

        assert get_cached_circuit_weather("test_circuit") is None
