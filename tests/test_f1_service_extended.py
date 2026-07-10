"""Extended validation and fallback coverage for the F1 data service."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Circuit, Location, Race, RaceSession
from app.services import f1_service as f1


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

    with (
        patch("app.services.f1_service.get_shared_http_client", side_effect=RuntimeError("failed")),
    ):
        assert await service.get_season_races(2026) == []

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

    with patch(
        "app.services.f1_service.get_shared_http_client", side_effect=RuntimeError("failed")
    ):
        assert await service.get_race_by_round(2026, 1) is None


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
