"""Tests for F1 service cancelled-race handling."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Circuit, Location, Race, RaceSession
from app.services import f1_service as f1_service_module
from app.services.f1_service import F1Service

MOCK_SEASON_RESPONSE = {
    "MRData": {
        "RaceTable": {
            "Races": [
                {
                    "season": "2026",
                    "round": "1",
                    "raceName": "Australian Grand Prix",
                    "Circuit": {
                        "circuitId": "albert_park",
                        "circuitName": "Albert Park Grand Prix Circuit",
                        "Location": {
                            "locality": "Melbourne",
                            "country": "Australia",
                        },
                    },
                    "date": "2026-03-08",
                    "time": "04:00:00Z",
                },
                {
                    "season": "2026",
                    "round": "2",
                    "raceName": "Chinese Grand Prix",
                    "Circuit": {
                        "circuitId": "shanghai",
                        "circuitName": "Shanghai International Circuit",
                        "Location": {
                            "locality": "Shanghai",
                            "country": "China",
                        },
                    },
                    "date": "2026-03-15",
                    "time": "07:00:00Z",
                },
                {
                    "season": "2026",
                    "raceName": "Bahrain Grand Prix",
                    "Circuit": {
                        "circuitId": "bahrain",
                        "circuitName": "Bahrain International Circuit",
                        "Location": {
                            "locality": "Sakhir",
                            "country": "Bahrain",
                        },
                    },
                    "date": "2026-04-12",
                    "time": "15:00:00Z",
                },
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
async def test_get_next_race_uses_shared_retry_helper(monkeypatch):
    service = F1Service()
    mock_response = MockResponse({
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "Circuit": {
                            "circuitId": "albert_park",
                            "circuitName": "Albert Park Grand Prix Circuit",
                            "Location": {
                                "locality": "Melbourne",
                                "country": "Australia",
                                "lat": "-37.8497",
                                "long": "144.968",
                            },
                        },
                        "date": "2026-03-08",
                        "time": "04:00:00Z",
                    }
                ]
            }
        }
    })
    mock_fetch = AsyncMock(return_value=mock_response)

    monkeypatch.setattr(f1_service_module, "fetch_with_retry", mock_fetch)

    result = await service.get_next_race()

    assert result is not None
    assert result["race_name"] == "Australian Grand Prix"
    mock_fetch.assert_awaited_once()
    client_arg, url_arg = mock_fetch.await_args.args[:2]
    assert url_arg == service.api_url
    assert client_arg is not None


@pytest.mark.asyncio
async def test_get_season_races_keeps_cancelled_races_at_end():
    service = F1Service()
    with (
        patch.object(F1Service, "get_all_races_from_static", return_value=[]),
        patch("httpx.AsyncClient") as mock_client,
    ):
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(MOCK_SEASON_RESPONSE))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        races = await service.get_season_races(2026)

    assert [race["race_name"] for race in races] == [
        "Australian Grand Prix",
        "Chinese Grand Prix",
        "Bahrain Grand Prix",
    ]
    assert races[2]["round"] is None
    assert races[2]["is_cancelled"] is True
    assert races[2]["race_key"] == "2026-cancelled-bahrain-2026-04-12"


def test_get_all_races_from_static_marks_cancelled_races(tmp_path, monkeypatch):
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()

    season_file = seasons_dir / "2026.json"
    season_file.write_text(
        json.dumps(
            {
                "races": [
                    {
                        "season": "2026",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "Circuit": {
                            "circuitId": "albert_park",
                            "circuitName": "Albert Park Grand Prix Circuit",
                            "Location": {
                                "locality": "Melbourne",
                                "country": "Australia",
                            },
                        },
                        "date": "2026-03-08",
                        "time": "04:00:00Z",
                    },
                    {
                        "season": "2026",
                        "raceName": "Saudi Arabian Grand Prix",
                        "Circuit": {
                            "circuitId": "jeddah",
                            "circuitName": "Jeddah Corniche Circuit",
                            "Location": {
                                "locality": "Jeddah",
                                "country": "Saudi Arabia",
                            },
                        },
                        "date": "2026-04-19",
                        "time": "17:00:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(f1_service_module, "SEASONS_DIR", seasons_dir)

    races = F1Service().get_all_races_from_static(2026)

    assert len(races) == 2
    assert races[0]["is_cancelled"] is False
    assert races[1]["is_cancelled"] is True
    assert races[1]["round"] is None
    assert races[1]["race_key"] == "2026-cancelled-jeddah-2026-04-19"


@pytest.mark.asyncio
async def test_get_season_races_merges_cancelled_races_from_static(tmp_path, monkeypatch):
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()

    season_file = seasons_dir / "2026.json"
    season_file.write_text(
        json.dumps(
            {
                "races": [
                    {
                        "season": "2026",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "Circuit": {
                            "circuitId": "albert_park",
                            "circuitName": "Albert Park Grand Prix Circuit",
                            "Location": {
                                "locality": "Melbourne",
                                "country": "Australia",
                            },
                        },
                        "date": "2026-03-08",
                        "time": "04:00:00Z",
                    },
                    {
                        "season": "2026",
                        "raceName": "Bahrain Grand Prix",
                        "Circuit": {
                            "circuitId": "bahrain",
                            "circuitName": "Bahrain International Circuit",
                            "Location": {
                                "locality": "Sakhir",
                                "country": "Bahrain",
                            },
                        },
                        "date": "2026-04-12",
                        "time": "15:00:00Z",
                    },
                    {
                        "season": "2026",
                        "raceName": "Saudi Arabian Grand Prix",
                        "Circuit": {
                            "circuitId": "jeddah",
                            "circuitName": "Jeddah Corniche Circuit",
                            "Location": {
                                "locality": "Jeddah",
                                "country": "Saudi Arabia",
                            },
                        },
                        "date": "2026-04-19",
                        "time": "17:00:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(f1_service_module, "SEASONS_DIR", seasons_dir)

    live_response = {
        "MRData": {
            "RaceTable": {
                "Races": [
                    {
                        "season": "2026",
                        "round": "1",
                        "raceName": "Australian Grand Prix",
                        "Circuit": {
                            "circuitId": "albert_park",
                            "circuitName": "Albert Park Grand Prix Circuit",
                            "Location": {
                                "locality": "Melbourne",
                                "country": "Australia",
                            },
                        },
                        "date": "2026-03-08",
                        "time": "04:00:00Z",
                    }
                ]
            }
        }
    }

    service = F1Service()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=MockResponse(live_response))
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance

        races = await service.get_season_races(2026)

    assert [race["race_name"] for race in races] == [
        "Australian Grand Prix",
        "Bahrain Grand Prix",
        "Saudi Arabian Grand Prix",
    ]
    assert races[1]["is_cancelled"] is True
    assert races[2]["is_cancelled"] is True


def test_convert_race_times_includes_sprint_qualifying_session():
    service = F1Service()
    race = Race(
        season="2026",
        round="2",
        raceName="Chinese Grand Prix",
        Circuit=Circuit(
            circuitId="shanghai",
            circuitName="Shanghai International Circuit",
            Location=Location(locality="Shanghai", country="China"),
        ),
        date="2026-03-15",
        time="07:00:00Z",
        FirstPractice=RaceSession(date="2026-03-13", time="03:30:00Z"),
        SprintQualifying=RaceSession(date="2026-03-13", time="07:30:00Z"),
        Sprint=RaceSession(date="2026-03-14", time="03:00:00Z"),
        Qualifying=RaceSession(date="2026-03-14", time="07:00:00Z"),
    )

    result = service._convert_race_times(race)
    session_names = [event["name"] for event in result["schedule"]]

    assert session_names == ["FP1", "SprintQualifying", "Sprint", "Qualifying", "Race"]
