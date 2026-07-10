"""Extended edge-case coverage for weather fetching and cache restoration."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import weather_service as weather


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
async def test_fetch_current_weather_rejects_missing_temperature_and_matches_current_hour():
    service = weather.WeatherService()
    response = SimpleNamespace(json=lambda: {"current": {}, "hourly": {}})
    with (
        patch("app.services.weather_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.weather_service.fetch_with_retry", new=AsyncMock(return_value=response)
        ),
    ):
        assert await service._fetch_current_weather(1.0, 2.0) is None

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
