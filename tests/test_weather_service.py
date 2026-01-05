"""Test weather service."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.weather_service import (
    WEATHER_ICONS,
    WeatherData,
    WeatherService,
    clear_circuit_weather_cache,
    clear_weather_cache,
    get_cached_circuit_weather,
    load_circuit_weather_to_cache,
    set_cached_circuit_weather,
)


class TestWeatherData:
    def test_icon_clear_sky(self):
        data = WeatherData(temperature_c=25.0, weather_code=0, precipitation_probability=10)
        assert data.icon == WEATHER_ICONS[0]

    def test_icon_rain(self):
        data = WeatherData(temperature_c=15.0, weather_code=61, precipitation_probability=80)
        assert data.icon == WEATHER_ICONS[61]

    def test_icon_unknown_code_fallback(self):
        data = WeatherData(temperature_c=20.0, weather_code=999, precipitation_probability=0)
        assert data.icon == "\u2601"

    def test_temp_display_rounds_correctly(self):
        data = WeatherData(temperature_c=25.7, weather_code=0, precipitation_probability=0)
        assert data.temp_display == "26\u00b0"

        data = WeatherData(temperature_c=25.4, weather_code=0, precipitation_probability=0)
        assert data.temp_display == "25\u00b0"

    def test_precip_display(self):
        data = WeatherData(temperature_c=20.0, weather_code=0, precipitation_probability=45)
        assert data.precip_display == "45%"


class TestWeatherIcons:
    def test_all_codes_have_icons(self):
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
        clear_weather_cache()
        yield
        clear_weather_cache()

    def test_invalid_coordinates_returns_none(self):
        import asyncio

        async def run_test():
            service = WeatherService()
            race_dt = datetime.now(timezone.utc) + timedelta(days=2)
            result = await service.get_race_weather(lat=100, lon=0, race_datetime=race_dt)
            assert result is None

        asyncio.run(run_test())

    def test_past_race_returns_none(self):
        import asyncio

        async def run_test():
            service = WeatherService()
            race_dt = datetime.now(timezone.utc) - timedelta(days=1)
            result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
            assert result is None

        asyncio.run(run_test())

    def test_race_too_far_returns_none(self):
        import asyncio

        async def run_test():
            service = WeatherService()
            race_dt = datetime.now(timezone.utc) + timedelta(days=20)
            result = await service.get_race_weather(lat=52.52, lon=13.41, race_datetime=race_dt)
            assert result is None

        asyncio.run(run_test())

    def test_get_current_weather_invalid_coordinates(self):
        import asyncio

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=100, lon=0)
            assert result is None

        asyncio.run(run_test())

    def test_get_current_weather_invalid_longitude(self):
        import asyncio

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=50, lon=200)
            assert result is None

        asyncio.run(run_test())

    def test_get_current_weather_success(self, monkeypatch):
        import asyncio

        mock_response_data = {
            "current": {"temperature_2m": 22.5, "weather_code": 1},
            "hourly": {"precipitation_probability": [15, 20, 25]},
        }

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
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

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=52.52, lon=13.41)
            assert result is not None
            assert result.temperature_c == 22.5
            assert result.weather_code == 1
            assert result.precipitation_probability == 15
            assert result.icon == WEATHER_ICONS[1]
            assert result.temp_display == "22°"
            assert result.precip_display == "15%"

        asyncio.run(run_test())

    def test_get_current_weather_cache_hit(self, monkeypatch):
        import asyncio

        call_count = 0
        mock_response_data = {
            "current": {"temperature_2m": 18.0, "weather_code": 3},
            "hourly": {"precipitation_probability": [30]},
        }

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
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

        async def run_test():
            nonlocal call_count
            service = WeatherService()
            # First call - should fetch from API
            result1 = await service.get_current_weather(lat=48.85, lon=2.35)
            assert result1 is not None
            assert call_count == 1

            # Second call - should use cache
            result2 = await service.get_current_weather(lat=48.85, lon=2.35)
            assert result2 is not None
            assert call_count == 1  # No additional API call

            # Both results should be equal
            assert result1.temperature_c == result2.temperature_c

        asyncio.run(run_test())

    def test_get_current_weather_timeout(self, monkeypatch):
        import asyncio

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

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=35.68, lon=139.69)
            assert result is None

        asyncio.run(run_test())

    def test_get_current_weather_http_error(self, monkeypatch):
        import asyncio

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

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=40.71, lon=-74.01)
            assert result is None

        asyncio.run(run_test())

    def test_get_current_weather_empty_precipitation(self, monkeypatch):
        import asyncio

        mock_response_data = {
            "current": {"temperature_2m": 25.0, "weather_code": 0},
            "hourly": {"precipitation_probability": []},
        }

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
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

        async def run_test():
            service = WeatherService()
            result = await service.get_current_weather(lat=51.51, lon=-0.13)
            assert result is not None
            assert result.precipitation_probability == 0

        asyncio.run(run_test())


class TestCircuitWeatherCache:
    """Tests for the circuit weather cache functions used by scheduler."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """
        Clears the circuit weather cache before and after a test runs.

        This fixture ensures each test executes with a clean cache state by clearing the circuit weather cache prior to the test and again after the test completes.
        """
        clear_circuit_weather_cache()
        yield
        clear_circuit_weather_cache()

    def test_get_cached_circuit_weather_not_found(self):
        """Test that missing circuit returns None."""
        result = get_cached_circuit_weather("nonexistent_circuit")
        assert result is None

    def test_set_and_get_cached_circuit_weather(self):
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

    def test_set_overwrites_existing(self):
        """Test that setting same circuit overwrites previous value."""
        weather1 = WeatherData(temperature_c=20.0, weather_code=0, precipitation_probability=0)
        weather2 = WeatherData(temperature_c=30.0, weather_code=61, precipitation_probability=80)

        set_cached_circuit_weather("monaco", weather1)
        set_cached_circuit_weather("monaco", weather2)

        result = get_cached_circuit_weather("monaco")
        assert result.temperature_c == 30.0
        assert result.weather_code == 61

    def test_load_circuit_weather_to_cache(self):
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

    def test_load_circuit_weather_with_defaults(self):
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

    def test_load_circuit_weather_skips_invalid(self):
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

    def test_clear_circuit_weather_cache(self):
        """Test clearing the circuit weather cache."""
        weather = WeatherData(temperature_c=25.0, weather_code=0, precipitation_probability=10)
        set_cached_circuit_weather("test_circuit", weather)

        assert get_cached_circuit_weather("test_circuit") is not None

        clear_circuit_weather_cache()

        assert get_cached_circuit_weather("test_circuit") is None
