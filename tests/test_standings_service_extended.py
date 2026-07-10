"""Extended cache and error coverage for championship standings."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import ConstructorStanding, DriverStanding, StandingsData
from app.services import standings_service as standings


@pytest.fixture(autouse=True)
def clear_caches():
    standings.StandingsService._shared_cache.clear()
    standings.StandingsService._negative_cache.clear()
    yield
    standings.StandingsService._shared_cache.clear()
    standings.StandingsService._negative_cache.clear()


def _driver() -> DriverStanding:
    return DriverStanding(position=1, points=25, driver_code="DRV", driver_name="Driver")


def _constructor() -> ConstructorStanding:
    return ConstructorStanding(position=1, points=40, constructor_name="Team")


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example/standings")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_negative_cache_evicts_expired_entry():
    standings.StandingsService._negative_cache["2026_drivers"] = time.time() - 1

    assert standings.StandingsService._is_negative_cached("2026_drivers") is False
    assert "2026_drivers" not in standings.StandingsService._negative_cache


@pytest.mark.asyncio
async def test_driver_standings_default_invalid_and_inner_negative_cache_paths():
    service = standings.StandingsService()
    with (
        patch("app.services.standings_service.datetime") as dt,
        patch("app.services.standings_service.is_supported_f1_season", return_value=True),
        patch.object(service, "_fetch_driver_standings", new=AsyncMock(return_value=[_driver()])),
    ):
        dt.now.return_value.year = 2026
        assert (await service.get_driver_standings())[0].driver_code == "DRV"

    with (
        patch("app.services.standings_service.is_supported_f1_season", return_value=False),
        pytest.raises(ValueError, match="Unsupported"),
    ):
        await service.get_driver_standings(1800)

    service._cache.clear()
    with (
        patch("app.services.standings_service.is_supported_f1_season", return_value=True),
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_is_negative_cached", side_effect=[False, True]),
    ):
        assert await service.get_driver_standings(2026) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [_http_error(404), _http_error(500), RuntimeError("failed")])
async def test_driver_fetch_maps_http_and_generic_errors_to_empty(error):
    service = standings.StandingsService()
    with (
        patch("app.services.standings_service.get_shared_http_client", return_value=object()),
        patch("app.services.standings_service.fetch_with_retry", new=AsyncMock(side_effect=error)),
    ):
        assert await service._fetch_driver_standings(2026, 10) == []


@pytest.mark.asyncio
async def test_constructor_standings_default_invalid_cached_negative_and_inner_paths():
    service = standings.StandingsService()
    data = StandingsData(season=2026, round=1, constructor_standings=[_constructor()])
    service._set_cache(2026, "constructors", data)
    with patch("app.services.standings_service.is_supported_f1_season", return_value=True):
        assert (await service.get_constructor_standings(2026))[0].constructor_name == "Team"

    service._cache.clear()
    service._negative_cache["2026_constructors"] = time.time() + 60
    with patch("app.services.standings_service.is_supported_f1_season", return_value=True):
        assert await service.get_constructor_standings(2026) == []

    service._negative_cache.clear()
    with (
        patch("app.services.standings_service.datetime") as dt,
        patch("app.services.standings_service.is_supported_f1_season", return_value=True),
        patch.object(
            service, "_fetch_constructor_standings", new=AsyncMock(return_value=[_constructor()])
        ),
    ):
        dt.now.return_value.year = 2026
        assert (await service.get_constructor_standings())[0].constructor_name == "Team"

    with (
        patch("app.services.standings_service.is_supported_f1_season", return_value=False),
        pytest.raises(ValueError, match="Unsupported"),
    ):
        await service.get_constructor_standings(1800)

    with (
        patch("app.services.standings_service.is_supported_f1_season", return_value=True),
        patch.object(service, "_get_cached", side_effect=[None, data]),
    ):
        assert (await service.get_constructor_standings(2026))[0].constructor_name == "Team"

    with (
        patch("app.services.standings_service.is_supported_f1_season", return_value=True),
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_is_negative_cached", side_effect=[False, True]),
    ):
        assert await service.get_constructor_standings(2026) == []


@pytest.mark.asyncio
async def test_constructor_fetch_handles_empty_http_and_generic_errors():
    service = standings.StandingsService()
    response = SimpleNamespace(json=lambda: {})
    with (
        patch("app.services.standings_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.standings_service.fetch_with_retry", new=AsyncMock(return_value=response)
        ),
    ):
        assert await service._fetch_constructor_standings(2026, 10) == []

    for error in (_http_error(500), RuntimeError("failed")):
        with (
            patch("app.services.standings_service.get_shared_http_client", return_value=object()),
            patch(
                "app.services.standings_service.fetch_with_retry",
                new=AsyncMock(side_effect=error),
            ),
        ):
            assert await service._fetch_constructor_standings(2026, 10) == []


@pytest.mark.asyncio
async def test_all_standings_default_year_and_fully_cached_snapshot():
    service = standings.StandingsService()
    drivers = StandingsData(season=2026, round=2, driver_standings=[_driver()])
    constructors = StandingsData(
        season=2026,
        round=3,
        constructor_standings=[_constructor()],
    )
    service._set_cache(2026, "drivers", drivers)
    service._set_cache(2026, "constructors", constructors)
    with patch("app.services.standings_service.datetime") as dt:
        dt.now.return_value.year = 2026
        result = await service.get_all_standings()

    assert result.round == 3
    assert result.driver_standings[0].driver_code == "DRV"
    assert result.constructor_standings[0].constructor_name == "Team"
