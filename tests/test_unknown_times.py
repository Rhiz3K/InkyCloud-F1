"""Date-only schedules remain unknown throughout consumers."""

from datetime import date, datetime

import pytest

from app.config import LANGUAGE_CODES
from app.models import ScheduleEvent
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer_calendar import _find_race_datetime, _resolve_countdown_status
from app.services.renderers import create_renderer
from app.services.scheduler_generation import _parse_race_datetime_utc, _race_weather_expected
from app.services.weather_service import _extract_race_datetime
from app.utils.race_times import convert_race_times_to_timezone


def unknown_race():
    """Build an entirely date-only weekend from the current calendar."""
    service = F1Service(timezone="Pacific/Honolulu")
    race = service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.time = None
    for name in ("FirstPractice", "SecondPractice", "ThirdPractice", "Qualifying"):
        session = getattr(race, name)
        if session:
            session.time = None
    return service._convert_race_times(race)


def test_date_only_consumers():
    """Conversion, models, countdown and weather preserve date-only values."""
    data = unknown_race()
    converted = convert_race_times_to_timezone(data, "Pacific/Kiritimati")
    assert converted["schedule"] == data["schedule"]
    for event in data["schedule"]:
        assert event["time"] is None
        assert event["datetime"] == event["date"]
        assert event["display_time"].endswith(" –")
        assert ScheduleEvent(**event).time is None
    assert _extract_race_datetime(data) is None
    assert _parse_race_datetime_utc(data) is None
    assert not _race_weather_expected(data)
    race_day = _find_race_datetime(data["schedule"], datetime)
    assert type(race_day) is date
    status, delta = _resolve_countdown_status(
        is_cancelled=False, race_dt=race_day, datetime_cls=datetime, translator={}
    )
    assert status == "COMPLETED"
    assert delta is None


@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
@pytest.mark.parametrize("language", LANGUAGE_CODES)
def test_unknown_time_all_renderers(display, language):
    """All translations and palettes accept missing session times."""
    image = create_renderer(display, get_translator(language), language).render_calendar(
        unknown_race()
    )
    assert image.startswith(b"BM")


@pytest.mark.parametrize("offset", [-1, 0, 2])
def test_date_countdown(offset):
    """Unknown hours never imply a live race or an hourly countdown."""
    from datetime import timedelta

    day = date.today() + timedelta(days=offset)
    status, delta = _resolve_countdown_status(
        is_cancelled=False, race_dt=day, datetime_cls=datetime, translator={}
    )
    assert status == ("COMPLETED" if offset < 0 else "–" if offset == 0 else None)
    assert delta == (timedelta(days=offset) if offset > 0 else None)


def test_weather_prefetch_date_only():
    """Weather prefetch never assigns an hour to a date-only race."""
    from unittest.mock import patch

    from app.services.weather_service import _get_next_race_details

    with patch.object(F1Service, "get_next_race_from_static", return_value=unknown_race()):
        assert _get_next_race_details() is None


def test_image_and_preview_date_only():
    """Image cache conversion and browser previews accept date-only schedules."""
    from app.routes.images import _maybe_convert_timezone
    from app.routes.previews import _build_calendar_preview_png

    data = _maybe_convert_timezone(unknown_race(), "Pacific/Kiritimati")
    assert data["schedule"] == unknown_race()["schedule"]
    assert _build_calendar_preview_png("en", "1bit", data, None, True).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_api_null_times():
    """Both public API endpoints serialize unknown times as JSON null."""
    import json
    from unittest.mock import patch

    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from app.routes.api import get_race_detail, get_season_races

    data = unknown_race()
    service = F1Service()
    request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    with (
        patch.object(F1Service, "get_all_races_from_static", return_value=[data]),
        patch("app.routes.api.enforce_rate_limit"),
    ):
        detail = await get_race_detail(2026, int(data["round"]), request, service)
        season = await get_season_races(2026, request, service)
    for race in [detail, season["races"][0]]:
        payload = json.loads(JSONResponse(race).body)
        assert payload["time"] is None
        assert all(event["time"] is None for event in payload["schedule"])


@pytest.mark.parametrize("year_offset,days", [(0, 0), (0, -2), (-1, -370)])
def test_date_only_next_race(year_offset, days):
    """Selection keeps today's race and falls back to completed date-only races."""
    from datetime import timedelta
    from unittest.mock import patch

    service = F1Service(timezone="UTC")
    race = service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.time = None
    race.date = (date.today() + timedelta(days=days)).isoformat()
    target_year = date.today().year + year_offset
    with patch.object(
        F1Service,
        "get_season_from_static",
        side_effect=lambda year: [race] if year == target_year else [],
    ):
        selected = service.get_next_race_from_static()
    assert selected["datetime"] == race.date
    assert selected["time"] is None


def test_known_time_timezone_fields():
    """Known timestamps retain display formatting and expose local date/time."""
    service = F1Service(timezone="UTC")
    data = service._convert_race_times(service.get_season_from_static(2026)[0])
    converted = convert_race_times_to_timezone(data, "Pacific/Kiritimati")
    for event in converted["schedule"]:
        local = datetime.fromisoformat(event["datetime"])
        assert event["date"] == local.strftime("%Y-%m-%d")
        assert event["time"] == local.strftime("%H:%M")
        assert event["display_time"] == local.strftime("%a %H:%M")


def test_future_date_only_render():
    """A future date-only race renders a countdown without hours."""
    from datetime import timedelta

    data = unknown_race()
    data["schedule"][-1]["datetime"] = (date.today() + timedelta(days=2)).isoformat()
    assert (
        create_renderer("1bit", get_translator("en"), "en").render_calendar(data).startswith(b"BM")
    )


def test_mixed_schedule_stable_order():
    """Date-only sessions sort after known hours with original ties preserved."""
    service = F1Service(timezone="UTC")
    race = service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.time = None
    for name in ("FirstPractice", "SecondPractice", "ThirdPractice", "Qualifying"):
        session = getattr(race, name)
        if session:
            session.date = race.date
            session.time = "09:00:00Z" if name == "ThirdPractice" else None
    events = service._convert_race_times(race)["schedule"]
    assert [event["name"] for event in events] == ["FP3", "FP1", "FP2", "Qualifying", "Race"]
    assert [event["time"] for event in events] == ["09:00", None, None, None, None]


def test_schedule_event_date_type_and_serialization():
    import json

    event = ScheduleEvent(name="Race", datetime="2026-03-08", display_time="Sun –")
    assert type(event.event_datetime) is date
    assert event.event_datetime == date(2026, 3, 8)
    assert event.model_dump(by_alias=True)["datetime"] == date(2026, 3, 8)
    assert event.model_dump(mode="json", by_alias=True)["datetime"] == "2026-03-08"
    assert json.loads(event.model_dump_json(by_alias=True))["datetime"] == "2026-03-08"
    known = ScheduleEvent(name="Race", datetime="2026-03-08T13:00:00Z", display_time="Sun 13:00")
    assert isinstance(known.event_datetime, datetime)
    assert known.event_datetime.utcoffset() is not None


def test_local_schedule_order():
    events = [
        {"name": "FP1", "datetime": "2026-03-07", "date": "2026-03-07", "time": None},
        {"name": "Qualifying", "datetime": "2026-03-07T23:00:00Z", "time": "23:00"},
        {"name": "Race", "datetime": "2026-03-08", "date": "2026-03-08", "time": None},
        {"name": "FP2", "datetime": "2026-03-08", "date": "2026-03-08", "time": None},
    ]
    converted = convert_race_times_to_timezone(
        {"schedule": [events[1], events[0], events[2], events[3]]}, "Pacific/Kiritimati"
    )
    schedule = converted["schedule"]
    assert [e["name"] for e in schedule] == ["FP1", "Qualifying", "Race", "FP2"]
    assert [e["date"] for e in schedule] == ["2026-03-07"] + ["2026-03-08"] * 3
    assert schedule[1]["time"] == "13:00"
    for index in (0, 2, 3):
        assert schedule[index]["datetime"] == events[index]["datetime"]
        assert schedule[index]["time"] is None


def test_known_schedule_order_after_conversion():
    events = [
        {"name": str(hour), "datetime": f"2026-03-07T{hour}:00:00Z", "time": f"{hour}:00"}
        for hour in ("09", "10", "23")
    ]
    result = convert_race_times_to_timezone({"schedule": events}, "Pacific/Kiritimati")
    assert [e["name"] for e in result["schedule"]] == [e["name"] for e in events]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "now_iso,tz,past",
    [
        ("2026-03-08T01:00:00+00:00", "Pacific/Honolulu", False),
        ("2026-03-08T01:00:00+00:00", "Europe/Prague", True),
        ("2026-03-06T23:30:00+00:00", "Asia/Tokyo", False),
    ],
)
async def test_date_only_timezone_agreement(now_iso, tz, past):
    from unittest.mock import AsyncMock, patch

    from starlette.requests import Request

    from app.routes.api import get_season_races

    instant = datetime.fromisoformat(now_iso)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz)

    service = F1Service(timezone_name=tz)
    race = service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.date = "2026-03-07"
    race.time = None
    following = race.model_copy(update={"date": "2026-03-15", "round": "2"})
    request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    with (
        patch("app.utils.race_times.datetime", FrozenDatetime),
        patch("app.services.f1_service.datetime", FrozenDatetime),
        patch.object(
            F1Service,
            "get_season_from_static",
            side_effect=lambda year: [race, following] if year == 2026 else [],
        ),
        patch.object(
            service, "_get_remote_season_payload", AsyncMock(return_value=[race.model_dump()])
        ),
        patch("app.routes.api.enforce_rate_limit"),
    ):
        data = service._convert_race_times(race)
        api_data = await get_season_races(2026, request, service)
        remote_data = await service.get_season_races(2026)
        assert data["is_past"] is past
        assert api_data["races"][0]["is_past"] is past
        assert remote_data[0]["is_past"] is past
        status, delta = _resolve_countdown_status(
            is_cancelled=False,
            race_dt=date(2026, 3, 7),
            datetime_cls=datetime,
            translator={},
            timezone_name=tz,
        )
        assert status == ("COMPLETED" if past else "–")
        assert delta is None
        with patch(
            "app.services.renderer_calendar._resolve_countdown_status",
            wraps=_resolve_countdown_status,
        ) as resolve:
            create_renderer("1bit", get_translator("en"), "en").render_calendar(data)
        assert resolve.call_args.kwargs["timezone_name"] == tz
        selected = service.get_next_race_from_static()
        assert selected["date"] == (following.date if past else race.date)
