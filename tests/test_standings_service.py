"""Test standings service."""

from unittest.mock import AsyncMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_get_driver_standings():
    service = StandingsService()
    service._cache.clear()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(MOCK_DRIVER_STANDINGS_RESPONSE))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
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

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(
            return_value=MockResponse(MOCK_CONSTRUCTOR_STANDINGS_RESPONSE)
        )
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
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
        elif "constructorStandings" in url:
            return MockResponse(MOCK_CONSTRUCTOR_STANDINGS_RESPONSE)
        raise ValueError(f"Unexpected URL: {url}")

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = mock_get
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
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

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = mock_get
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        await service.get_driver_standings(2024)
        await service.get_driver_standings(2024)

        assert call_count == 1


@pytest.mark.asyncio
async def test_empty_standings_response():
    service = StandingsService()
    service._cache.clear()

    empty_response = {"MRData": {"StandingsTable": {"StandingsLists": []}}}

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(empty_response))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        standings = await service.get_driver_standings(2024)

        assert standings == []
