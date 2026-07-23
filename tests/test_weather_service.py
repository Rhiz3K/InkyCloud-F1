"""Test weather service."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import weather_service as weather
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
    async def test_get_current_weather_invalid_coordinates(caplog):
        service = WeatherService()
        result = await service.get_current_weather(lat=100, lon=0)
        assert result is None
        assert "100" not in caplog.text

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
        fetched_at = datetime.now(timezone.utc).isoformat()
        weather_dict = {
            "albert_park": {
                "temperature_c": 22.5,
                "weather_code": 1,
                "precipitation_probability": 15,
                "fetched_at": fetched_at,
            },
            "monaco": {
                "temperature_c": 28.0,
                "weather_code": 0,
                "precipitation_probability": 5,
                "fetched_at": fetched_at,
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
    def test_load_circuit_weather_requires_temperature_and_timestamp():
        """Persisted entries missing provenance fields must not manufacture live weather."""
        weather_dict = {
            "silverstone": {},
            "spa": {"temperature_c": 18.0},
        }

        count = load_circuit_weather_to_cache(weather_dict)
        assert count == 0
        assert get_cached_circuit_weather("silverstone") is None
        assert get_cached_circuit_weather("spa") is None

    @staticmethod
    def test_load_circuit_weather_skips_invalid():
        """Test that invalid data is skipped gracefully."""
        fetched_at = datetime.now(timezone.utc).isoformat()
        weather_dict = {
            "valid": {
                "temperature_c": 25.0,
                "weather_code": 0,
                "precipitation_probability": 10,
                "fetched_at": fetched_at,
            },
            "invalid": {
                "temperature_c": "not_a_number",
                "fetched_at": fetched_at,
            },
            "invalid_timestamp": {
                "temperature_c": 18.0,
                "fetched_at": "not-a-timestamp",
            },
        }

        count = load_circuit_weather_to_cache(weather_dict)
        assert count == 1

        valid = get_cached_circuit_weather("valid")
        assert valid is not None

    @staticmethod
    @pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
    def test_load_circuit_weather_rejects_non_finite_temperature(temperature):
        """Persisted non-finite temperatures must not enter the live cache."""
        weather_dict = {
            "invalid": {
                "temperature_c": temperature,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        assert load_circuit_weather_to_cache(weather_dict) == 0
        assert get_cached_circuit_weather("invalid") is None

    @staticmethod
    def test_clear_circuit_weather_cache():
        """Test clearing the circuit weather cache."""
        weather = WeatherData(temperature_c=25.0, weather_code=0, precipitation_probability=10)
        set_cached_circuit_weather("test_circuit", weather)

        assert get_cached_circuit_weather("test_circuit") is not None

        clear_circuit_weather_cache()

        assert get_cached_circuit_weather("test_circuit") is None


# Extended edge-case coverage for weather fetching and cache restoration.


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://weather.example")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.asyncio
async def test_current_weather_handles_generic_failure():
    service = weather.WeatherService()
    with patch.object(
        service, "_fetch_current_weather", new=AsyncMock(side_effect=RuntimeError("failed"))
    ):
        assert await service.get_current_weather(1.0, 2.0) is None

    with patch.object(service, "_fetch_current_weather", new=AsyncMock(return_value=None)):
        assert await service.get_current_weather(1.0, 2.0) is None


@pytest.mark.asyncio
async def test_fetch_current_weather_rejects_missing_temperature_and_matches_current_hour(caplog):
    service = weather.WeatherService()
    response = SimpleNamespace(json=lambda: {"current": {}, "hourly": {}})
    with (
        patch("app.services.weather_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.weather_service.fetch_with_retry", new=AsyncMock(return_value=response)
        ),
    ):
        assert await service._fetch_current_weather(1.0, 2.0) is None
    assert "1.0" not in caplog.text
    assert "2.0" not in caplog.text

    response = SimpleNamespace(
        json=lambda: {
            "current": {
                "time": "2026-07-10T15:00",
                "temperature_2m": 21.5,
                "weather_code": 2,
            },
            "hourly": {
                "time": ["2026-07-10T14:00", "2026-07-10T15:00"],
                "precipitation_probability": [10, 70],
            },
        }
    )
    with (
        patch("app.services.weather_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.weather_service.fetch_with_retry", new=AsyncMock(return_value=response)
        ),
    ):
        result = await service._fetch_current_weather(1.0, 2.0)

    assert result == weather.WeatherData(21.5, 2, 70)

    response = SimpleNamespace(
        json=lambda: {
            "current": {"time": "2026-07-10T15:00", "temperature_2m": 21.5},
            "hourly": {
                "time": ["2026-07-10T14:00"],
                "precipitation_probability": [10],
            },
        }
    )
    with (
        patch("app.services.weather_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.weather_service.fetch_with_retry", new=AsyncMock(return_value=response)
        ),
    ):
        result = await service._fetch_current_weather(1.0, 2.0)
    assert result.precipitation_probability == 10


@pytest.mark.asyncio
async def test_race_weather_returns_cached_value():
    service = weather.WeatherService()
    race_dt = datetime.now(timezone.utc) + timedelta(days=1)
    value = weather.WeatherData(20.0, 1, 0)
    key = weather._race_weather_cache_key(1.0, 2.0, race_dt)
    weather._weather_cache[key] = (value, datetime.now(timezone.utc) + timedelta(minutes=5))

    assert await service.get_race_weather(1.0, 2.0, race_dt) is value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("timeout"),
        _http_status_error(503),
        RuntimeError("failed"),
    ],
)
async def test_race_weather_handles_forecast_failures(error):
    service = weather.WeatherService()
    race_dt = datetime.now(timezone.utc) + timedelta(days=1)
    with patch.object(service, "_fetch_weather", new=AsyncMock(side_effect=error)):
        assert await service.get_race_weather(1.0, 2.0, race_dt) is None


@pytest.mark.asyncio
async def test_race_and_historical_weather_do_not_cache_empty_responses():
    service = weather.WeatherService()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    past = datetime.now(timezone.utc) - timedelta(days=10)
    with patch.object(service, "_fetch_weather", new=AsyncMock(return_value=None)):
        assert await service.get_race_weather(1.0, 2.0, future) is None
    with patch.object(service, "_fetch_historical_weather", new=AsyncMock(return_value=None)):
        assert await service.get_historical_race_weather(1.0, 2.0, past) is None


@pytest.mark.asyncio
async def test_historical_weather_returns_cache_and_rejects_bad_coordinates():
    service = weather.WeatherService()
    race_dt = datetime.now(timezone.utc) - timedelta(days=10)
    value = weather.WeatherData(12.0, 3, 0)
    key = f"historical_1.0_2.0_{race_dt.isoformat()}"
    weather._weather_cache[key] = (value, datetime.now(timezone.utc) + timedelta(minutes=5))
    assert await service.get_historical_race_weather(1.0, 2.0, race_dt) is value
    assert await service.get_historical_race_weather(100.0, 2.0, race_dt) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        httpx.TimeoutException("timeout"),
        _http_status_error(503),
        RuntimeError("failed"),
    ],
)
async def test_historical_weather_handles_archive_failures(error):
    service = weather.WeatherService()
    race_dt = datetime.now(timezone.utc) - timedelta(days=10)
    with patch.object(service, "_fetch_historical_weather", new=AsyncMock(side_effect=error)):
        assert await service.get_historical_race_weather(1.0, 2.0, race_dt) is None


def test_weather_response_cache_evicts_expired_entry():
    value = weather.WeatherData(20.0, 1, 0)
    weather._weather_cache["expired"] = (value, datetime.now(timezone.utc) - timedelta(seconds=1))

    assert weather.WeatherService._get_cached("expired") is None
    assert "expired" not in weather._weather_cache


def test_circuit_cache_returns_and_evicts_by_ttl():
    value = weather.WeatherData(20.0, 1, 0)
    weather._circuit_weather_cache["fresh"] = (
        value,
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    weather._circuit_weather_cache["expired"] = (
        value,
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert weather.get_cached_circuit_weather("fresh") is value
    assert weather.get_cached_circuit_weather("expired") is None
    assert "expired" not in weather._circuit_weather_cache


def test_load_circuit_weather_handles_naive_aware_expired_and_invalid_timestamps():
    now = datetime.now(timezone.utc)
    values = {
        "naive": {"temperature_c": 20, "fetched_at": now.replace(tzinfo=None).isoformat()},
        "aware": {"temperature_c": 21, "fetched_at": now.astimezone().isoformat()},
        "expired": {
            "temperature_c": 22,
            "fetched_at": (now - timedelta(hours=3)).isoformat(),
        },
        "invalid": {"temperature_c": 23, "fetched_at": 123},
    }

    assert weather.load_circuit_weather_to_cache(values) == 2
    assert weather.get_cached_circuit_weather("naive").temperature_c == 20
    assert weather.get_cached_circuit_weather("aware").temperature_c == 21
    assert weather.get_cached_circuit_weather("expired") is None
    assert weather.get_cached_circuit_weather("invalid") is None


@pytest.mark.parametrize(
    ("hourly", "race_dt", "precipitation_key", "as_amount", "expected"),
    [
        ({}, datetime(2026, 7, 10, tzinfo=timezone.utc), "precip", False, None),
        (
            {"time": ["2026-07-11T10:00"]},
            datetime(2026, 7, 10, tzinfo=timezone.utc),
            "precip",
            False,
            None,
        ),
        (
            {
                "time": ["2026-07-10Tinvalid", "2026-07-10T13:00"],
                "temperature_2m": [19.0, 21.0],
                "weather_code": [None, None],
                "precip": [5],
            },
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
            "precip",
            False,
            weather.WeatherData(21.0, 0, 0),
        ),
        (
            {
                "time": ["2026-07-10T14:00"],
                "temperature_2m": [],
                "weather_code": [],
                "precip": [1.25],
            },
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
            "precip",
            True,
            None,
        ),
        (
            {
                "time": ["2026-07-10T14:00"],
                "temperature_2m": [20.0],
                "weather_code": [1],
                "precip": [1.25],
            },
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
            "precip",
            True,
            weather.WeatherData(20.0, 1, 0, "1.2 mm"),
        ),
    ],
)
def test_match_hourly_weather_edge_cases(hourly, race_dt, precipitation_key, as_amount, expected):
    assert (
        weather._match_hourly_weather(
            hourly,
            race_dt,
            precipitation_key=precipitation_key,
            precipitation_as_amount=as_amount,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        ([], None),
        ([{"name": "Race"}], None),
        ([{"name": "Race", "datetime": "invalid"}], None),
        (
            [{"name": "Race", "datetime": "2026-07-10T14:00:00Z"}],
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
        ),
    ],
)
def test_extract_race_datetime_validates_schedule(schedule, expected):
    assert weather._extract_race_datetime({"schedule": schedule}) == expected


def test_parse_coordinate_returns_none_for_missing_value():
    assert weather._parse_coordinate(None) is None


@pytest.mark.asyncio
async def test_resolve_current_weather_uses_circuit_cache_or_missing_coordinates():
    cached = weather.WeatherData(20.0, 1, 0)
    weather.set_cached_circuit_weather("monza", cached)
    service = MagicMock()
    assert await weather._resolve_current_weather(
        circuit_id="monza", lat=1.0, lon=2.0, weather_service=service
    ) == (cached, service)
    assert await weather._resolve_current_weather(
        circuit_id="", lat=None, lon=2.0, weather_service=service
    ) == (None, service)

    service.get_current_weather = AsyncMock(side_effect=[weather.WeatherData(21.0, 2, 10), None])
    resolved, _ = await weather._resolve_current_weather(
        circuit_id="", lat=1.0, lon=2.0, weather_service=service
    )
    assert resolved == weather.WeatherData(21.0, 2, 10)
    assert await weather._resolve_current_weather(
        circuit_id="", lat=1.0, lon=2.0, weather_service=service
    ) == (None, service)


@pytest.mark.asyncio
async def test_weather_context_returns_off_when_disabled_or_race_missing():
    with patch("app.services.weather_service.config.WEATHER_ENABLED", False):
        assert await weather.get_weather_context({"circuit": {}}) == (
            None,
            None,
            {"off": None},
        )
    with patch("app.services.weather_service.config.WEATHER_ENABLED", True):
        assert await weather.get_weather_context(None) == (None, None, {"off": None})


@pytest.mark.parametrize(
    ("race_data", "expected"),
    [
        (None, None),
        ({"circuit": {}}, None),
        ({"circuit": {"lat": "bad", "long": "2"}}, None),
        (
            {
                "circuit": {"lat": "1", "long": "2"},
                "schedule": [],
            },
            None,
        ),
        (
            {
                "circuit": {"lat": "1", "long": "2"},
                "schedule": [{"name": "Race"}],
            },
            None,
        ),
        (
            {
                "circuit": {"lat": "1", "long": "2"},
                "schedule": [{"name": "Race", "datetime": "invalid"}],
            },
            None,
        ),
        (
            {
                "circuit": {"lat": "1", "long": "2"},
                "schedule": [{"name": "Race", "datetime": "2026-07-12T14:00:00+00:00"}],
            },
            (1.0, 2.0, datetime(2026, 7, 12, 14, tzinfo=timezone.utc)),
        ),
    ],
)
def test_get_next_race_details_validates_static_data(race_data, expected):
    f1_service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=race_data))
    with patch("app.services.f1_service.F1Service", return_value=f1_service):
        assert weather._get_next_race_details() == expected


@pytest.mark.asyncio
async def test_prefetch_weather_handles_missing_details_current_only_and_race_result():
    db = SimpleNamespace(save_weather_cache=AsyncMock())
    with patch("app.services.weather_service._get_next_race_details", return_value=None):
        assert await weather.prefetch_weather_for_next_race(db) is None

    race_dt = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    current = weather.WeatherData(20.0, 1, 10)
    race = weather.WeatherData(22.0, 2, 20)
    service = MagicMock()
    service.get_current_weather = AsyncMock(return_value=current)
    service.get_race_weather = AsyncMock(side_effect=[None, race])
    with (
        patch(
            "app.services.weather_service._get_next_race_details", return_value=(1.0, 2.0, race_dt)
        ),
        patch("app.services.weather_service.WeatherService", return_value=service),
    ):
        assert await weather.prefetch_weather_for_next_race(db) is current
        assert await weather.prefetch_weather_for_next_race(db) is race

    assert db.save_weather_cache.await_count == 3

    service.get_current_weather = AsyncMock(return_value=None)
    service.get_race_weather = AsyncMock(return_value=None)
    with (
        patch(
            "app.services.weather_service._get_next_race_details",
            return_value=(1.0, 2.0, race_dt),
        ),
        patch("app.services.weather_service.WeatherService", return_value=service),
    ):
        assert await weather.prefetch_weather_for_next_race(db) is None


@pytest.mark.asyncio
async def test_get_cached_weather_from_db_handles_hit_and_miss():
    db = SimpleNamespace(
        get_weather_cache=AsyncMock(side_effect=[(20.0, 1, 10, "2026-07-10T14:00:00+00:00"), None])
    )

    assert await weather.get_cached_weather_from_db(db, "hit") == weather.WeatherData(20.0, 1, 10)
    assert await weather.get_cached_weather_from_db(db, "miss") is None


@pytest.mark.asyncio
async def test_load_prefetched_weather_handles_missing_partial_and_timestamp_variants():
    db = SimpleNamespace(get_weather_cache=AsyncMock())
    with patch("app.services.weather_service._get_next_race_details", return_value=None):
        assert await weather.load_prefetched_weather_from_db(db) == 0

    race_dt = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    db.get_weather_cache.side_effect = [
        None,
        (22.0, 2, 20, "not-a-date"),
    ]
    with patch(
        "app.services.weather_service._get_next_race_details", return_value=(1.0, 2.0, race_dt)
    ):
        assert await weather.load_prefetched_weather_from_db(db) == 1

    db.get_weather_cache.side_effect = [
        (20.0, 1, 10, now.replace(tzinfo=None).isoformat()),
        (22.0, 2, 20, now.isoformat()),
    ]
    with patch(
        "app.services.weather_service._get_next_race_details", return_value=(1.0, 2.0, race_dt)
    ):
        assert await weather.load_prefetched_weather_from_db(db) == 2
