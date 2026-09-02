"""Tests for F1 service cancelled-race handling."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Circuit, Location, Race, RaceSession
from app.services import f1_service as f1
from app.services.f1_service import F1Service
from app.services.http_client import _reset_shared_http_clients_for_tests


@pytest.fixture(autouse=True)
def reset_shared_http_clients():
    _reset_shared_http_clients_for_tests()
    f1._reset_remote_caches_for_tests()
    yield
    _reset_shared_http_clients_for_tests()
    f1._reset_remote_caches_for_tests()


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

    monkeypatch.setattr(f1, "SEASONS_DIR", seasons_dir)

    races = F1Service().get_all_races_from_static(2026)

    assert len(races) == 2
    assert races[0]["is_cancelled"] is False
    assert races[1]["is_cancelled"] is True
    assert races[1]["round"] is None
    assert races[1]["race_key"] == "2026-cancelled-jeddah-2026-04-19"


def test_static_season_loader_rejects_symlinked_files(tmp_path, monkeypatch):
    """Season discovery must not follow an allowlisted filename outside the assets directory."""
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()
    outside_file = tmp_path / "2026.json"
    outside_file.write_text('{"races": []}', encoding="utf-8")
    (seasons_dir / "2026.json").symlink_to(outside_file)
    monkeypatch.setattr(f1, "SEASONS_DIR", seasons_dir)

    assert F1Service.get_season_from_static(2026) == []


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

    monkeypatch.setattr(f1, "SEASONS_DIR", seasons_dir)

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


def test_next_race_static_uses_last_completed_race_for_empty_offseason(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 15, 12, tzinfo=tz or timezone.utc)

    last_race = Race(
        season="2026",
        round="24",
        raceName="Abu Dhabi Grand Prix",
        Circuit=Circuit(
            circuitId="yas_marina",
            circuitName="Yas Marina Circuit",
            Location=Location(locality="Abu Dhabi", country="UAE"),
        ),
        date="2026-12-06",
        time="13:00:00Z",
    )

    monkeypatch.setattr(f1, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        F1Service,
        "get_season_from_static",
        staticmethod(lambda year: [last_race] if year == 2026 else []),
    )

    result = F1Service().get_next_race_from_static()

    assert result is not None
    assert result["race_name"] == "Abu Dhabi Grand Prix"
    assert result["season"] == "2026"


@pytest.mark.asyncio
async def test_get_season_races_uses_configured_api_base_url(monkeypatch):
    service = F1Service()
    service.api_base_url = "https://mirror.example.com/custom/f1"
    pacer = object()
    pacer_urls: list[str] = []
    mock_fetch = AsyncMock(return_value=MockResponse({"MRData": {"RaceTable": {"Races": []}}}))

    def mock_get_jolpica_pacer(api_url: str) -> object:
        pacer_urls.append(api_url)
        return pacer

    monkeypatch.setattr(f1, "fetch_with_retry", mock_fetch)
    monkeypatch.setattr(f1, "get_jolpica_pacer", mock_get_jolpica_pacer)
    monkeypatch.setattr(
        f1,
        "get_shared_http_client",
        lambda *args, **kwargs: object(),
    )

    await service.get_season_races(2026)
    assert mock_fetch.await_args.args[1] == "https://mirror.example.com/custom/f1/2026.json"
    assert mock_fetch.await_args.kwargs["pacer"] is pacer

    await service.get_race_by_round(2026, 3)
    assert (
        mock_fetch.await_args_list[1].args[1] == "https://mirror.example.com/custom/f1/2026/3.json"
    )
    assert mock_fetch.await_args_list[1].kwargs["pacer"] is pacer
    assert pacer_urls == [
        "https://mirror.example.com/custom/f1",
        "https://mirror.example.com/custom/f1",
    ]


@pytest.mark.asyncio
async def test_get_season_races_handles_null_circuit_and_missing_time(monkeypatch):
    service = F1Service(timezone_name="UTC")
    mock_response = MockResponse(
        {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "season": "2026",
                            "round": "1",
                            "raceName": "Null Circuit Grand Prix",
                            "Circuit": None,
                            "date": "2026-03-08",
                        }
                    ]
                }
            }
        }
    )

    monkeypatch.setattr(
        f1,
        "fetch_with_retry",
        AsyncMock(return_value=mock_response),
    )
    monkeypatch.setattr(
        f1,
        "get_shared_http_client",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(F1Service, "get_all_races_from_static", lambda self, year: [])

    races = await service.get_season_races(2026)

    assert races[0]["circuit_id"] == ""
    assert races[0]["circuit_name"] == ""
    assert races[0]["country"] == ""
    assert races[0]["datetime"] == "2026-03-08T12:00:00+00:00"


# Extended validation and fallback coverage for the F1 data service.


def _race(
    *,
    name: str = "Test Grand Prix",
    race_round: str | None = "1",
    date: str = "2026-07-12",
    race_time: str | None = "14:00:00Z",
    session: RaceSession | None = None,
) -> Race:
    return Race(
        season="2026",
        round=race_round,
        raceName=name,
        Circuit=Circuit(
            circuitId="test",
            circuitName="Test Circuit",
            Location=Location(locality="Test City", country="Testland"),
        ),
        date=date,
        time=race_time,
        FirstPractice=session,
    )


def test_static_parser_skips_invalid_race_entries():
    payload = json.dumps(
        {
            "races": [
                {"raceName": "Invalid"},
                _race().model_dump(),
            ]
        }
    )

    result = f1._parse_static_season(payload)

    assert len(result) == 1
    assert result[0].raceName == "Test Grand Prix"


def test_service_falls_back_to_utc_for_unknown_timezone():
    with patch(
        "app.services.f1_service.get_timezone",
        side_effect=f1.ZoneInfoNotFoundError("unknown"),
    ):
        service = f1.F1Service(timezone_name="Bad/Zone")

    assert service.timezone_str == "UTC"
    assert service.target_tz is f1.UTC


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.example/current/next.json", "https://api.example"),
        ("https://api.example/current.json", "https://api.example"),
        ("https://api.example/custom.json", "https://api.example/custom"),
        ("https://api.example/root", "https://api.example/root"),
    ],
)
def test_derive_api_base_url_variants(url, expected):
    assert f1.F1Service._derive_api_base_url(url) == expected


def test_round_helpers_reject_non_numeric_and_non_positive_values():
    assert f1.F1Service._has_scheduled_round({"round": {}}) is False
    assert f1.F1Service._has_scheduled_round({"round": "bad"}) is False
    assert f1.F1Service._extract_round_number({"round": {}}) is None
    assert f1.F1Service._extract_round_number({"round": "bad"}) is None
    assert f1.F1Service._extract_round_number({"round": "0"}) is None


def test_race_key_handles_cancelled_and_missing_date():
    assert (
        f1.F1Service._build_race_key(
            season=None,
            round_value=None,
            race_name="Grand Prix",
            circuit_id="",
            race_date="",
        )
        == "race-cancelled-grand-prix"
    )


def test_merge_static_cancelled_races_skips_active_duplicates_and_handles_loader_failure():
    service = f1.F1Service()
    live = [{"race_key": "duplicate", "round": 1}]
    static = [
        {"race_key": "active", "is_cancelled": False},
        {"race_key": "duplicate", "is_cancelled": True},
        {"race_key": "cancelled", "is_cancelled": True, "date": "2026-07-01"},
    ]
    with patch.object(service, "get_all_races_from_static", return_value=static):
        merged = service._merge_static_cancelled_races(2026, live)
    assert {item["race_key"] for item in merged} == {"duplicate", "cancelled"}

    with patch.object(
        service, "get_all_races_from_static", side_effect=RuntimeError("load failed")
    ):
        assert service._merge_static_cancelled_races(2026, live) == live


def test_convert_race_times_uses_default_time_and_skips_invalid_datetimes():
    service = f1.F1Service(timezone_name="UTC")
    default_time = service._convert_race_times(_race(race_time=None))
    assert default_time["schedule"][-1]["name"] == "Race"
    assert default_time["datetime"].endswith("+00:00")

    invalid = service._convert_race_times(
        _race(
            date="invalid",
            race_time="invalid",
            session=RaceSession(date="invalid", time="invalid"),
        )
    )
    assert invalid["schedule"] == []
    assert invalid["datetime"] is None
    assert invalid["is_past"] is False


@pytest.mark.asyncio
async def test_get_season_races_skips_malformed_rows_and_handles_fetch_failure():
    service = f1.F1Service()
    response = SimpleNamespace(
        json=lambda: {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "season": "2026",
                            "round": "1",
                            "raceName": "Broken",
                            "date": "invalid",
                            "Circuit": None,
                        }
                    ]
                }
            }
        }
    )
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(return_value=response)),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        assert await service.get_season_races(2026) == []

    f1._reset_remote_caches_for_tests()
    with (
        patch("app.services.f1_service.get_shared_http_client", side_effect=RuntimeError("failed")),
    ):
        assert await service.get_season_races(2026) == []

    f1._reset_remote_caches_for_tests()
    undated = SimpleNamespace(
        json=lambda: {
            "MRData": {
                "RaceTable": {
                    "Races": [
                        {
                            "season": "2026",
                            "round": "1",
                            "raceName": "Undated",
                            "date": "",
                            "Circuit": {},
                        }
                    ]
                }
            }
        }
    )
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(return_value=undated)),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        assert (await service.get_season_races(2026))[0]["datetime"] is None


@pytest.mark.asyncio
async def test_get_race_by_round_handles_success_empty_and_failure():
    service = f1.F1Service()
    responses = [
        SimpleNamespace(json=lambda: {"MRData": {"RaceTable": {"Races": [_race().model_dump()]}}}),
        SimpleNamespace(json=lambda: {}),
    ]
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(side_effect=responses)),
    ):
        assert (await service.get_race_by_round(2026, 1))["race_name"] == "Test Grand Prix"
        assert await service.get_race_by_round(2026, 2) is None

    f1._reset_remote_caches_for_tests()
    with patch(
        "app.services.f1_service.get_shared_http_client", side_effect=RuntimeError("failed")
    ):
        assert await service.get_race_by_round(2026, 1) is None


@pytest.mark.asyncio
async def test_get_season_races_caches_remote_payload_and_coalesces_misses():
    service = f1.F1Service()
    fetch = AsyncMock(return_value=SimpleNamespace(json=lambda: MOCK_SEASON_RESPONSE))
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=fetch),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        first, second = await asyncio.gather(
            service.get_season_races(2026), service.get_season_races(2026)
        )
        third = await service.get_season_races(2026)

    assert first == second == third
    assert first[0]["race_name"] == "Australian Grand Prix"
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_get_season_races_remembers_upstream_failure_briefly():
    service = f1.F1Service()
    fetch = AsyncMock(side_effect=RuntimeError("down"))
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=fetch),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        assert await service.get_season_races(2026) == []
        assert await service.get_season_races(2026) == []

    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_get_season_races_tolerates_non_list_and_non_dict_payloads():
    service = f1.F1Service()
    non_list = SimpleNamespace(json=lambda: {"MRData": {"RaceTable": {"Races": "nope"}}})
    non_dict = SimpleNamespace(json=lambda: {"MRData": {"RaceTable": {"Races": ["oops"]}}})
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(return_value=non_list)),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        assert await service.get_season_races(2026) == []

    f1._reset_remote_caches_for_tests()
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(return_value=non_dict)),
        patch.object(service, "get_all_races_from_static", return_value=[]),
    ):
        assert await service.get_season_races(2026) == []


@pytest.mark.asyncio
async def test_get_race_by_round_caches_payloads_and_missing_rounds():
    service = f1.F1Service()
    responses = [
        SimpleNamespace(json=lambda: {"MRData": {"RaceTable": {"Races": [_race().model_dump()]}}}),
        SimpleNamespace(json=lambda: {"MRData": {"RaceTable": {"Races": []}}}),
    ]
    fetch = AsyncMock(side_effect=responses)
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=fetch),
    ):
        assert (await service.get_race_by_round(2026, 1))["race_name"] == "Test Grand Prix"
        assert (await service.get_race_by_round(2026, 1))["race_name"] == "Test Grand Prix"
        assert await service.get_race_by_round(2026, 2) is None
        assert await service.get_race_by_round(2026, 2) is None

    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_get_race_by_round_returns_none_for_invalid_upstream_race():
    service = f1.F1Service()
    broken = SimpleNamespace(
        json=lambda: {"MRData": {"RaceTable": {"Races": [{"raceName": "Broken"}]}}}
    )
    with (
        patch("app.services.f1_service.get_shared_http_client", return_value=object()),
        patch("app.services.f1_service.fetch_with_retry", new=AsyncMock(return_value=broken)),
    ):
        assert await service.get_race_by_round(2026, 3) is None


def test_static_season_loader_reads_each_file_version_once(tmp_path, monkeypatch):
    path = tmp_path / "2026.json"
    path.write_text(json.dumps({"races": []}), encoding="utf-8")
    monkeypatch.setattr(f1, "SEASONS_DIR", tmp_path)
    reads = 0
    original_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        nonlocal reads
        reads += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    f1._load_static_season_file.cache_clear()

    assert F1Service.get_season_from_static(2026) == []
    assert F1Service.get_season_from_static(2026) == []
    assert reads == 1

    path.write_text(json.dumps({"races": [], "generated_at": "later"}), encoding="utf-8")
    assert F1Service.get_season_from_static(2026) == []
    assert reads == 2


def test_get_season_from_static_rejects_invalid_year_and_read_failure(tmp_path):
    assert f1.F1Service.get_season_from_static(1999) == []
    path = tmp_path / "2026.json"
    path.write_text("{}", encoding="utf-8")
    with (
        patch("app.services.f1_service._find_static_season_path", return_value=path),
        patch.object(path.__class__, "read_text", side_effect=OSError("failed")),
    ):
        assert f1.F1Service.get_season_from_static(2026) == []


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 10, 12, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)


def test_next_race_selection_handles_cancelled_naive_invalid_and_no_races():
    service = f1.F1Service()
    with (
        patch("app.services.f1_service.datetime", _FixedDatetime),
        patch.object(
            f1.F1Service,
            "get_season_from_static",
            side_effect=[[_race(name="Future", date="2026-07-12")], []],
        ),
        patch.object(service, "_convert_race_times", return_value={"race": "future"}),
    ):
        assert service.get_next_race_from_static() == {"race": "future"}

    current = [
        _race(name="Cancelled", race_round=None),
        _race(name="Completed", date="2026-07-01", race_time="12:00:00"),
        _race(name="Older", date="2026-06-01", race_time="12:00:00"),
        _race(name="Invalid", date="invalid", race_time="bad"),
    ]
    with (
        patch("app.services.f1_service.datetime", _FixedDatetime),
        patch.object(
            f1.F1Service,
            "get_season_from_static",
            side_effect=[current, []],
        ),
        patch.object(service, "_convert_race_times", return_value={"race": "completed"}),
    ):
        assert service.get_next_race_from_static() == {"race": "completed"}

    previous = [
        _race(name="Cancelled", race_round=None),
        _race(name="Previous", date="2025-12-01", race_time="12:00:00"),
        _race(name="Older", date="2025-11-01", race_time="12:00:00"),
        _race(name="Invalid", date="invalid", race_time="bad"),
    ]
    with (
        patch("app.services.f1_service.datetime", _FixedDatetime),
        patch.object(
            f1.F1Service,
            "get_season_from_static",
            side_effect=[[], [], previous],
        ),
        patch.object(service, "_convert_race_times", return_value={"race": "previous"}),
    ):
        assert service.get_next_race_from_static() == {"race": "previous"}

    with (
        patch("app.services.f1_service.datetime", _FixedDatetime),
        patch.object(f1.F1Service, "get_season_from_static", return_value=[]),
    ):
        assert service.get_next_race_from_static() is None


def test_get_all_races_from_static_isolates_conversion_failures():
    service = f1.F1Service()
    races = [_race(name="Good"), _race(name="Bad")]
    with (
        patch.object(f1.F1Service, "get_season_from_static", return_value=races),
        patch.object(
            service, "_convert_race_times", side_effect=[{"name": "Good"}, RuntimeError("bad")]
        ),
    ):
        assert service.get_all_races_from_static(2026) == [{"name": "Good"}]


def test_historical_loader_handles_missing_malformed_and_file_errors():
    payload = {
        "test": {
            "historical": {
                "season": 2025,
                "qualifying": [
                    {"pos": 1, "code": "DRV", "name": "Driver", "team": "Team"},
                    {"pos": "bad"},
                ],
                "race": [
                    {"pos": 1, "code": "DRV", "name": "Driver", "team": "Team"},
                    {"pos": "bad"},
                ],
            }
        }
    }
    with patch("app.services.f1_service.load_circuits_data", return_value={}):
        assert f1.F1Service.get_historical_from_static("missing").is_new_track is True
    with patch("app.services.f1_service.load_circuits_data", return_value=payload):
        result = f1.F1Service.get_historical_from_static("test")
    assert len(result.qualifying_results) == 1
    assert len(result.race_results) == 1

    with (
        patch("app.services.f1_service.load_circuits_data", side_effect=FileNotFoundError),
        patch("app.services.f1_service.get_circuits_data_path", return_value="missing.json"),
    ):
        assert f1.F1Service.get_historical_from_static("test").is_new_track is True
    with patch("app.services.f1_service.load_circuits_data", side_effect=RuntimeError("failed")):
        assert f1.F1Service.get_historical_from_static("test").is_new_track is True
