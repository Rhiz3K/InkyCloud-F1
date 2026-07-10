"""Test standings service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import AnyHttpUrl

from app.models import DriverStanding, StandingsData
from app.services.http_client import _reset_shared_http_clients_for_tests
from app.services.standings_service import StandingsService

MOCK_DRIVER_STANDINGS_RESPONSE = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [
                {
                    "season": "2024",
                    "round": "10",
                    "DriverStandings": [
                        {
                            "position": "1",
                            "points": "255",
                            "wins": "7",
                            "Driver": {
                                "code": "VER",
                                "givenName": "Max",
                                "familyName": "Verstappen",
                                "nationality": "Dutch",
                            },
                            "Constructors": [{"name": "Red Bull"}],
                        },
                        {
                            "position": "2",
                            "points": "150",
                            "wins": "2",
                            "Driver": {
                                "code": "NOR",
                                "givenName": "Lando",
                                "familyName": "Norris",
                                "nationality": "British",
                            },
                            "Constructors": [{"name": "McLaren"}],
                        },
                    ],
                }
            ]
        }
    }
}

MOCK_CONSTRUCTOR_STANDINGS_RESPONSE = {
    "MRData": {
        "StandingsTable": {
            "StandingsLists": [
                {
                    "season": "2024",
                    "round": "10",
                    "ConstructorStandings": [
                        {
                            "position": "1",
                            "points": "400",
                            "wins": "9",
                            "Constructor": {
                                "name": "Red Bull",
                                "nationality": "Austrian",
                            },
                        },
                        {
                            "position": "2",
                            "points": "280",
                            "wins": "3",
                            "Constructor": {
                                "name": "Ferrari",
                                "nationality": "Italian",
                            },
                        },
                    ],
                }
            ]
        }
    }
}


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def reset_shared_clients():
    _reset_shared_http_clients_for_tests()
    StandingsService._shared_cache.clear()
    StandingsService._negative_cache.clear()
    yield
    _reset_shared_http_clients_for_tests()
    StandingsService._shared_cache.clear()
    StandingsService._negative_cache.clear()


@pytest.mark.asyncio
async def test_get_driver_standings():
    service = StandingsService()
    service._cache.clear()

    with patch("app.services.standings_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(MOCK_DRIVER_STANDINGS_RESPONSE))
        mock_client.return_value = mock_instance

        standings = await service.get_driver_standings(2024)

        assert len(standings) == 2
        assert standings[0].position == 1
        assert standings[0].driver_code == "VER"
        assert standings[0].driver_name == "Verstappen"
        assert standings[0].points == 255.0
        assert standings[0].constructor_name == "Red Bull"


@pytest.mark.asyncio
async def test_get_constructor_standings():
    service = StandingsService()
    service._cache.clear()

    with patch("app.services.standings_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(
            return_value=MockResponse(MOCK_CONSTRUCTOR_STANDINGS_RESPONSE)
        )
        mock_client.return_value = mock_instance

        standings = await service.get_constructor_standings(2024)

        assert len(standings) == 2
        assert standings[0].position == 1
        assert standings[0].constructor_name == "Red Bull"
        assert standings[0].points == 400.0
        assert standings[1].constructor_name == "Ferrari"


@pytest.mark.asyncio
async def test_get_all_standings():
    service = StandingsService()
    service._cache.clear()

    async def mock_get(url):
        if "driverStandings" in url:
            return MockResponse(MOCK_DRIVER_STANDINGS_RESPONSE)
        if "constructorStandings" in url:
            return MockResponse(MOCK_CONSTRUCTOR_STANDINGS_RESPONSE)
        raise ValueError(f"Unexpected URL: {url}")

    with patch("app.services.standings_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(side_effect=mock_get)
        mock_client.return_value = mock_instance

        standings_data = await service.get_all_standings(2024)

        assert standings_data.season == 2024
        assert len(standings_data.driver_standings) == 2
        assert len(standings_data.constructor_standings) == 2
        assert standings_data.driver_standings[0].driver_code == "VER"
        assert standings_data.constructor_standings[0].constructor_name == "Red Bull"


@pytest.mark.asyncio
async def test_standings_cache():
    service = StandingsService()
    service._cache.clear()

    call_count = 0

    async def mock_get(url):
        nonlocal call_count
        call_count += 1
        return MockResponse(MOCK_DRIVER_STANDINGS_RESPONSE)

    with patch("app.services.standings_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(side_effect=mock_get)
        mock_client.return_value = mock_instance

        await service.get_driver_standings(2024)
        await service.get_driver_standings(2024)

        assert call_count == 1


@pytest.mark.asyncio
async def test_standings_use_configured_jolpica_api_base_url():
    service = StandingsService()
    service._cache.clear()
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_DRIVER_STANDINGS_RESPONSE

    with (
        patch(
            "app.services.standings_service.config.JOLPICA_API_URL",
            AnyHttpUrl("https://mirror.example.test/ergast/f1"),
        ),
        patch(
            "app.services.standings_service.get_shared_http_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.standings_service.fetch_with_retry",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_fetch,
    ):
        await service.get_driver_standings(2024)
        await service.get_constructor_standings(2024)

    fetched_urls = [call.args[1] for call in mock_fetch.await_args_list]
    assert fetched_urls == [
        "https://mirror.example.test/ergast/f1/2024/driverStandings.json",
        "https://mirror.example.test/ergast/f1/2024/constructorStandings.json",
    ]


@pytest.mark.asyncio
async def test_standings_derives_base_url_from_next_race_endpoint():
    service = StandingsService()
    service._cache.clear()
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_CONSTRUCTOR_STANDINGS_RESPONSE

    with (
        patch(
            "app.services.standings_service.config.JOLPICA_API_URL",
            "https://api.jolpi.ca/ergast/f1/current/next.json",
        ),
        patch(
            "app.services.standings_service.get_shared_http_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.standings_service.fetch_with_retry",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_fetch,
    ):
        await service.get_constructor_standings(2025)

    assert mock_fetch.await_args.args[1] == (
        "https://api.jolpi.ca/ergast/f1/2025/constructorStandings.json"
    )


@pytest.mark.asyncio
async def test_constructor_standings_404_returns_empty_without_error_log(caplog):
    service = StandingsService()
    service._cache.clear()
    url = "https://api.jolpi.ca/ergast/f1/2025/constructorStandings.json"
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    missing_standings = httpx.HTTPStatusError(
        "Client error '404 Not Found'",
        request=request,
        response=response,
    )

    with (
        patch(
            "app.services.standings_service.config.JOLPICA_API_URL",
            "https://api.jolpi.ca/ergast/f1/current/next.json",
        ),
        patch(
            "app.services.standings_service.get_shared_http_client",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.standings_service.fetch_with_retry",
            new_callable=AsyncMock,
            side_effect=missing_standings,
        ),
        caplog.at_level("WARNING", logger="app.services.standings_service"),
    ):
        standings = await service.get_constructor_standings(2025)

    assert standings == []
    assert "Constructor standings are not available for 2025" in caplog.text
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


@pytest.mark.asyncio
async def test_empty_standings_response():
    service = StandingsService()
    service._cache.clear()

    empty_response = {"MRData": {"StandingsTable": {"StandingsLists": []}}}

    with patch("app.services.standings_service.httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(empty_response))
        mock_client.return_value = mock_instance

        standings = await service.get_driver_standings(2024)

        assert standings == []


@pytest.mark.asyncio
async def test_concurrent_driver_standings_requests_are_coalesced(monkeypatch):
    service = StandingsService()
    calls = 0

    async def fetch_driver_standings(year: int, limit: int) -> list[DriverStanding]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        standings = [
            DriverStanding(
                position=1,
                points=25,
                wins=1,
                driver_code="NOR",
                driver_name="Norris",
                driver_given_name="Lando",
                nationality="British",
                constructor_name="McLaren",
            )
        ]
        service._set_cache(
            year,
            "drivers",
            StandingsData(season=year, round=1, driver_standings=standings),
        )
        return standings[:limit]

    monkeypatch.setattr(service, "_fetch_driver_standings", fetch_driver_standings)

    results = await asyncio.gather(*(service.get_driver_standings(2026) for _ in range(5)))

    assert calls == 1
    assert all(result[0].driver_code == "NOR" for result in results)


@pytest.mark.asyncio
async def test_empty_driver_standings_are_negative_cached(monkeypatch):
    service = StandingsService()
    fetch_driver_standings = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "_fetch_driver_standings", fetch_driver_standings)

    assert await service.get_driver_standings(2026) == []
    assert await service.get_driver_standings(2026) == []

    fetch_driver_standings.assert_awaited_once_with(2026, 10)
