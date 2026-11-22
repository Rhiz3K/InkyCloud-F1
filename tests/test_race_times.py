"""Shared race time contract (issue #185, D7 and B9)."""

import time
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models import Circuit, Location, Race, RaceSession
from app.services import weather_service
from app.services.f1_service import F1Service
from app.utils.race_times import (
    convert_race_times_to_timezone,
    local_session_start,
    parse_session_start,
    parse_utc_instant,
    race_session_start,
    schedule_sort_key,
    session_over,
    to_utc,
)

NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
def prague_host(monkeypatch):
    """Run with a host clock in Europe/Prague, where naive-as-local would shift by 1–2 h."""
    monkeypatch.setenv("TZ", "Europe/Prague")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-12T14:00:00Z", datetime(2026, 7, 12, 14, tzinfo=UTC)),
        ("2026-07-12T14:00:00", datetime(2026, 7, 12, 14, tzinfo=UTC)),
        ("2026-07-12T16:00:00+02:00", datetime(2026, 7, 12, 14, tzinfo=UTC)),
    ],
)
def test_parse_utc_instant_reads_naive_as_utc_and_honours_offsets(value, expected):
    parsed = parse_utc_instant(value)
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


def test_to_utc_converts_aware_values():
    aware = datetime(2026, 7, 12, 10, tzinfo=NEW_YORK)
    assert to_utc(aware) == datetime(2026, 7, 12, 14, tzinfo=UTC)
    assert to_utc(aware).tzinfo is UTC


def test_parse_session_start_keeps_tba_unknown_and_rejects_garbage():
    assert parse_session_start("2026-07-12", None) is None
    assert parse_session_start("2026-07-12", "") is None
    assert parse_session_start("2026-07-12", "14:00:00Z") == datetime(2026, 7, 12, 14, tzinfo=UTC)
    with pytest.raises(ValueError):
        parse_session_start("2026-07-12", "lunchtime")


def test_local_session_start_keeps_the_calendar_date_for_tba():
    assert local_session_start("2026-03-14", None, NEW_YORK) == datetime(
        2026, 3, 14, tzinfo=NEW_YORK
    )
    assert local_session_start("2026-03-14", "00:00:00Z", NEW_YORK) == datetime(
        2026, 3, 13, 20, tzinfo=NEW_YORK
    )


def test_session_over_for_timed_and_date_only_sessions():
    start = datetime(2026, 7, 12, 14, tzinfo=UTC)
    assert not session_over("2026-07-12", "14:00:00Z", UTC, start - timedelta(seconds=1))
    assert session_over("2026-07-12", "14:00:00Z", UTC, start)
    assert not session_over("2026-07-12", "14:00:00Z", UTC, start, grace=timedelta(hours=3))
    # A date-only race lasts until its local day ends in the viewer's zone.
    late_evening_ny = datetime(2026, 7, 13, 3, tzinfo=UTC)
    assert not session_over("2026-07-12", None, NEW_YORK, late_evening_ny)
    assert session_over("2026-07-12", None, NEW_YORK, datetime(2026, 7, 13, 4, tzinfo=UTC))


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        (None, None),
        ([], None),
        ([{"name": "Race"}], None),
        ([{"name": "Race", "datetime": "2026-07-12"}], None),
        ([{"name": "Race", "datetime": "invalid"}], None),
        ([{"name": "Race", "datetime": datetime(2026, 7, 12)}], None),
        (
            [{"name": "Race", "datetime": "2026-07-10T14:00:00Z"}],
            datetime(2026, 7, 10, 14, tzinfo=timezone.utc),
        ),
        (
            [{"name": "Race", "datetime": "2026-07-12T14:00:00"}],
            datetime(2026, 7, 12, 14, tzinfo=timezone.utc),
        ),
        (
            [
                {"name": "Qualifying", "datetime": "2026-07-11T14:00:00Z"},
                {"name": "race", "datetime": "2026-07-12T16:00:00+02:00"},
            ],
            datetime(2026, 7, 12, 14, tzinfo=timezone.utc),
        ),
    ],
)
def test_race_session_start(schedule, expected):
    assert race_session_start({"schedule": schedule}) == expected


def test_schedule_sort_key_orders_by_instant_with_unknown_hours_last():
    events = [
        {"name": "TBA", "date": "2026-10-25", "time": None, "datetime": "2026-10-25"},
        {
            "name": "Race",
            "date": "2026-10-25",
            "time": "02:15",
            "datetime": "2026-10-25T02:15:00+01:00",
        },
        {
            "name": "Qualifying",
            "date": "2026-10-25",
            "time": "02:30",
            "datetime": "2026-10-25T02:30:00+02:00",
        },
        {"name": "Broken", "date": "2026-10-25", "time": "09:00", "datetime": "2026-10-25Tnope"},
        {"name": "Earlier", "time": "10:00", "datetime": "2026-10-24T10:00:00+02:00"},
    ]
    assert [e["name"] for e in sorted(events, key=schedule_sort_key)] == [
        "Earlier",
        "Qualifying",
        "Race",
        "TBA",
        "Broken",
    ]


def _fold_race(race_time: str = "01:15:00Z") -> Race:
    """A weekend whose qualifying and race straddle the Prague autumn DST fold."""
    return Race(
        season="2026",
        round="20",
        raceName="Fold Grand Prix",
        Circuit=Circuit(
            circuitId="test",
            circuitName="Test Circuit",
            Location=Location(locality="Test City", country="Testland"),
        ),
        date="2026-10-25",
        time=race_time,
        Qualifying=RaceSession(date="2026-10-25", time="00:30:00Z"),
    )


def test_f1_service_orders_sessions_by_instant_across_a_dst_fold():
    """B9: 00:30Z is 02:30+02:00 and 01:15Z is 02:15+01:00; text order would swap them."""
    schedule = F1Service(timezone="Europe/Prague")._convert_race_times(_fold_race())["schedule"]
    assert [event["name"] for event in schedule] == ["Qualifying", "Race"]
    assert [event["time"] for event in schedule] == ["02:30", "02:15"]


def test_converting_a_schedule_keeps_instant_order_across_a_dst_fold():
    data = F1Service(timezone="UTC")._convert_race_times(_fold_race())
    converted = convert_race_times_to_timezone(data, "Europe/Prague")
    assert [event["name"] for event in converted["schedule"]] == ["Qualifying", "Race"]


def test_f1_service_reads_naive_times_as_utc_on_any_host(prague_host):
    """B9: a time without 'Z' was converted from the host's local time, not UTC."""
    data = F1Service(timezone="UTC")._convert_race_times(_fold_race("13:00:00"))
    race = next(event for event in data["schedule"] if event["name"] == "Race")
    assert race["datetime"] == "2026-10-25T13:00:00+00:00"


@pytest.mark.asyncio
async def test_season_races_read_naive_times_as_utc_on_any_host(prague_host):
    race = _fold_race("13:00:00").model_dump()
    service = F1Service(timezone="UTC")
    with patch.object(service, "_get_remote_season_payload", return_value=[race]):
        races = await service.get_season_races(2026)
    assert races[0]["datetime"] == "2026-10-25T13:00:00+00:00"


def test_weather_prefetch_uses_the_shared_race_lookup():
    """The prefetch path matched only 'Race' and kept the local offset; now it is UTC."""
    race_data = {
        "circuit": {"lat": "1", "long": "2"},
        "schedule": [{"name": "race", "datetime": "2026-07-12T16:00:00+02:00"}],
    }
    f1_service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=race_data))
    with patch("app.services.f1_service.F1Service", return_value=f1_service):
        lat, lon, race_dt = weather_service._get_next_race_details()
    assert (lat, lon) == (1.0, 2.0)
    assert race_dt == datetime(2026, 7, 12, 14, tzinfo=UTC)
    assert race_dt.utcoffset() == timedelta(0)
