"""Weekend weather forecast and the ``temp_unit=c|f`` switch (#185 F6)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageChops, ImageDraw

import app.services.renderer_base as renderer_base_module
from app import config as config_module
from app.config import LANGUAGE_CODES, Config
from app.routes import images, previews
from app.routes.deps import get_f1_service
from app.routes.params import canonical_weather_type, temp_unit_cache_suffix
from app.services import bmp_artifacts, renderer_calendar, scheduler_generation
from app.services import weather_service as ws
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderers import create_renderer
from app.services.teams_extra import TeamsExtra, build_teams_extra_context, teams_extra_cache_token
from app.services.teams_service import TeamsService
from app.services.weather_service import (
    SessionWeather,
    TempUnit,
    WeatherData,
    WeekendWeather,
    convert_temperature,
    forecast_window,
    weekend_session_starts,
)
from app.services.weekend_phase import weekend_phase
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils import refresh_hint as rh
from app.utils.refresh_hint import WEEKEND_FORECAST_LEAD_DAYS, CountdownMode

DISPLAYS = ("1bit", "bwr", "bwry", "spectra6")
# Qatar 2026 (bundled, normal weekend) and Singapore 2026 (sprint weekend), UTC.
QA_Q = datetime(2026, 11, 28, 18, 0, tzinfo=UTC)
QA_RACE = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
SG_SPRINT = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
SG_Q = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)
SG_RACE = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 22, 18, 30, tzinfo=UTC)
PRAGUE = ZoneInfo("Europe/Prague")


def _race(name: str) -> dict:
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    race = next((race for race in races if race["race_name"] == name), None)
    assert race is not None, name
    return race


def _qatar() -> dict:
    return _race("Qatar Grand Prix")


def _singapore() -> dict:
    return _race("Singapore Grand Prix")


def _weekend(*items) -> WeekendWeather:
    return WeekendWeather(
        tuple(
            SessionWeather(kind, WeatherData(temp, code, rain)) for kind, temp, code, rain in items
        )
    )


QR = _weekend(("qualifying", 19.2, 61, 60), ("race", 38.9, 1, 100))


@pytest.fixture(autouse=True)
def _clean_weather_caches():
    ws.clear_weather_cache()
    ws.clear_circuit_weather_cache()
    yield
    ws.clear_weather_cache()
    ws.clear_circuit_weather_cache()


class _Clock:
    now = NOW


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def frozen(monkeypatch):
    """Freeze the renderer and route clocks at the Qatar race week."""
    _Clock.now = NOW
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    monkeypatch.setattr(previews, "datetime", _FixedDatetime)
    return _Clock


# --- Conversion ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("celsius", "fahrenheit"),
    [(-1, 30), (20, 68), (38, 100), (38.9, 102), (-20, -4), (-40, -40), (0, 32), (40.2, 104)],
)
def test_fahrenheit_conversion_rounds_after_converting(celsius, fahrenheit):
    assert convert_temperature(celsius, TempUnit.F) == fahrenheit
    assert WeatherData(celsius, 0, 0).format_temp(TempUnit.F) == f"{fahrenheit}°"


@pytest.mark.parametrize("celsius", [-20.6, -0.4, 0, 25.4, 25.7, 38.9])
def test_celsius_formatting_is_the_previous_display(celsius):
    data = WeatherData(celsius, 0, 0)
    assert data.format_temp() == data.format_temp(TempUnit.C) == data.temp_display
    assert data.temp_display == f"{int(round(celsius))}°"


def test_weekend_summary_for_logs():
    assert QR.temp_display == "qualifying 19° race 39°"


# --- Forecast window and sessions ---------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (5, (3, 5)),  # Friday to Sunday
        (1, (0, 1)),  # clipped to today
        (0, (0, 0)),
        (15, (13, 15)),  # race on the last forecast day
        (16, (14, 15)),  # race beyond the horizon, qualifying still inside
        (17, (15, 15)),
        (18, None),
        (-1, None),
    ],
)
def test_forecast_window_is_clipped_to_today_and_the_horizon(offset, expected):
    today = date(2026, 11, 22)
    window = forecast_window(today + timedelta(days=offset), today)
    if expected is None:
        assert window is None
    else:
        assert window == (today + timedelta(days=expected[0]), today + timedelta(days=expected[1]))


def test_weekend_sessions_are_parsed_in_weekend_order():
    race_date, sessions = weekend_session_starts(_singapore())
    assert race_date == SG_RACE.date()
    assert sessions == [("sprint", SG_SPRINT), ("qualifying", SG_Q), ("race", SG_RACE)]
    race_date, sessions = weekend_session_starts(_qatar())
    assert [kind for kind, _ in sessions] == ["qualifying", "race"]


def test_weekend_sessions_skip_unknown_malformed_and_date_only_entries():
    race = {
        "schedule": [
            {"name": "Race", "datetime": "2026-12-06"},
            {"name": "Qualifying", "datetime": "2026-12-05T14:00:00+00:00"},
            {"name": "Sprint", "datetime": "2026-12-05"},
            {"name": "Practice 1", "datetime": "2026-12-04T10:00:00+00:00"},
            {"name": "Sprint", "datetime": "2026-12-05Tlate"},
            {"name": "Qualifying", "datetime": None},
        ]
    }
    race_date, sessions = weekend_session_starts(race)
    assert race_date == date(2026, 12, 6)
    assert sessions == [("qualifying", datetime(2026, 12, 5, 14, tzinfo=UTC))]
    assert weekend_session_starts({}) == (None, [])


# --- Open-Meteo requests (mocked HTTP) ----------------------------------------------------


def _hourly(start: date, days: int, *, temp=20.0, rain=30) -> dict:
    times = [f"{start + timedelta(days=d)}T{h:02d}:00" for d in range(days) for h in range(24)]
    return {
        "time": times,
        "temperature_2m": [temp + index / 100 for index in range(len(times))],
        "weather_code": [61] * len(times),
        "precipitation_probability": [rain] * len(times),
    }


class _OpenMeteo:
    """Fake shared HTTP client answering forecast requests and counting quota reservations."""

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.requests: list[dict] = []
        self.reserved = 0

    async def wait(self) -> None:
        self.reserved += 1

    async def get(self, url, params=None):
        self.requests.append(dict(params))
        if self.error is not None:
            raise self.error
        start = date.fromisoformat(params["start_date"])
        end = date.fromisoformat(params["end_date"])
        hourly = self.payload or _hourly(start, (end - start).days + 1)
        return httpx.Response(200, json={"hourly": hourly}, request=httpx.Request("GET", url))


@pytest.fixture
def open_meteo(monkeypatch):
    fake = _OpenMeteo()
    monkeypatch.setattr(ws, "get_shared_http_client", lambda *args, **kwargs: fake)
    monkeypatch.setattr(ws, "WEATHER_BUDGET", fake)
    return fake


def _future_weekend(days_to_race: int, *, sprint: bool = False):
    today = datetime.now(UTC).date()
    race = datetime.combine(today + timedelta(days=days_to_race), datetime.min.time(), UTC)
    race = race.replace(hour=14)
    sessions = [("qualifying", race - timedelta(hours=22)), ("race", race)]
    if sprint:
        sessions.insert(0, ("sprint", race - timedelta(hours=27)))
    return race, sessions


@pytest.mark.asyncio
async def test_race_day_and_weekend_share_one_request_and_one_quota_unit(open_meteo):
    race, sessions = _future_weekend(5, sprint=True)
    service = ws.WeatherService()
    race_weather = await service.get_race_weather(1.0, 2.0, race)
    weekend = await service.get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert len(open_meteo.requests) == 1 and open_meteo.reserved == 1
    params = open_meteo.requests[0]
    assert params["start_date"] == (race.date() - timedelta(days=2)).isoformat()
    assert params["end_date"] == race.date().isoformat()
    assert params["hourly"] == "temperature_2m,weather_code,precipitation_probability"
    assert params["timezone"] == "UTC" and "forecast_days" not in params
    # 3 days and 3 variables stay within one Open-Meteo call unit (<= 14 days, <= 10 vars).
    assert len(params["hourly"].split(",")) <= 10
    assert [item.session for item in weekend.sessions] == ["sprint", "qualifying", "race"]
    assert weekend.sessions[-1].weather == race_weather
    # Each session is matched to its own hour of the one payload.
    temps = [item.weather.temperature_c for item in weekend.sessions]
    assert len(set(temps)) == 3
    # Cached afterwards: neither lookup requests again.
    assert await service.get_weekend_weather(1.0, 2.0, race.date(), sessions) == weekend
    assert await service.get_race_weather(1.0, 2.0, race) == race_weather
    assert len(open_meteo.requests) == 1


@pytest.mark.asyncio
async def test_weekend_alone_makes_one_request(open_meteo):
    race, sessions = _future_weekend(2)
    weekend = await ws.WeatherService().get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert [item.session for item in weekend.sessions] == ["qualifying", "race"]
    assert len(open_meteo.requests) == 1


@pytest.mark.asyncio
async def test_sessions_beyond_the_horizon_are_left_out(open_meteo):
    race, sessions = _future_weekend(16)
    service = ws.WeatherService()
    assert await service.get_race_weather(1.0, 2.0, race) is None
    assert open_meteo.requests == []
    weekend = await service.get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert [item.session for item in weekend.sessions] == ["qualifying"]
    params = open_meteo.requests[0]
    today = datetime.now(UTC).date()
    assert params["end_date"] == (today + timedelta(days=15)).isoformat()
    assert params["start_date"] == (today + timedelta(days=14)).isoformat()


@pytest.mark.asyncio
async def test_weekend_entirely_beyond_the_horizon_needs_no_request(open_meteo):
    race, sessions = _future_weekend(20)
    assert await ws.WeatherService().get_weekend_weather(1.0, 2.0, race.date(), sessions) is None
    assert open_meteo.requests == []


@pytest.mark.asyncio
async def test_past_sessions_are_left_out(open_meteo):
    race, sessions = _future_weekend(1)
    sessions[0] = ("qualifying", race - timedelta(days=2))
    weekend = await ws.WeatherService().get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert [item.session for item in weekend.sessions] == ["race"]
    assert open_meteo.requests[0]["start_date"] == datetime.now(UTC).date().isoformat()


@pytest.mark.asyncio
async def test_sessions_that_started_today_are_left_out_even_when_cached(open_meteo, monkeypatch):
    qualifying = datetime(2026, 11, 28, 15, 0, tzinfo=UTC)
    race = datetime(2026, 11, 29, 14, 0, tzinfo=UTC)
    sessions = [("qualifying", qualifying), ("race", race)]
    clock = {"now": datetime(2026, 11, 28, 10, 0, tzinfo=UTC)}

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"] if tz is not None else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(ws, "datetime", _Frozen)
    service = ws.WeatherService()
    morning = await service.get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert [item.session for item in morning.sessions] == ["qualifying", "race"]

    # Saturday evening: qualifying is over although its forecast is cached and it is still
    # the same UTC day, so only the race forecast is drawn.
    clock["now"] = datetime(2026, 11, 28, 20, 0, tzinfo=UTC)
    evening = await service.get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert [item.session for item in evening.sessions] == ["race"]
    assert evening.sessions[0].weather == morning.sessions[1].weather


@pytest.mark.asyncio
async def test_missing_hours_and_temperatures_show_what_is_available(open_meteo):
    race, sessions = _future_weekend(3)
    payload = _hourly(race.date() - timedelta(days=2), 3)
    race_index = payload["time"].index(race.strftime("%Y-%m-%dT%H:00"))
    payload["temperature_2m"][race_index] = None
    # Qualifying day has no hourly rows at all.
    qualifying_day = sessions[0][1].strftime("%Y-%m-%d")
    for key in list(payload):
        payload[key] = [
            value
            for value, stamp in zip(
                payload[key], _hourly(race.date() - timedelta(days=2), 3)["time"]
            )
            if not stamp.startswith(qualifying_day)
        ]
    open_meteo.payload = payload
    service = ws.WeatherService()
    assert await service.get_weekend_weather(1.0, 2.0, race.date(), sessions) is None


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_cached(open_meteo):
    race, sessions = _future_weekend(3)
    open_meteo.payload = {"time": []}
    service = ws.WeatherService()
    assert await service.get_weekend_weather(1.0, 2.0, race.date(), sessions) is None
    assert await service.get_weekend_weather(1.0, 2.0, race.date(), sessions) is None
    assert len(open_meteo.requests) == 2


@pytest.mark.asyncio
async def test_expired_hourly_forecast_is_fetched_again(open_meteo):
    race, sessions = _future_weekend(3)
    service = ws.WeatherService()
    await service.get_race_weather(1.0, 2.0, race)
    (key,) = ws._hourly_forecast_cache
    hourly, _ = ws._hourly_forecast_cache[key]
    ws._hourly_forecast_cache[key] = (hourly, datetime.now(UTC) - timedelta(seconds=1))
    ws._weather_cache.clear()
    await service.get_weekend_weather(1.0, 2.0, race.date(), sessions)
    assert len(open_meteo.requests) == 2


@pytest.mark.asyncio
async def test_a_failed_request_never_fails_the_calendar(open_meteo):
    open_meteo.error = httpx.ConnectTimeout("slow")
    race, sessions = _future_weekend(3)
    service = ws.WeatherService(timeout=1)
    assert await service.get_weekend_weather(1.0, 2.0, race.date(), sessions) is None


@pytest.mark.asyncio
async def test_quota_exhaustion_shows_no_weekend_weather(monkeypatch):
    race, sessions = _future_weekend(3)
    monkeypatch.setattr(ws.WEATHER_BUDGET, "wait", AsyncMock(side_effect=RuntimeError("quota")))
    client = MagicMock()
    monkeypatch.setattr(ws, "get_shared_http_client", lambda *a, **k: client)
    assert await ws.WeatherService().get_weekend_weather(1.0, 2.0, race.date(), sessions) is None
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_input_and_finished_races(monkeypatch, open_meteo):
    race, sessions = _future_weekend(3)
    service = ws.WeatherService()
    assert await service.get_weekend_weather(1.0, 2.0, None, sessions) is None
    assert await service.get_weekend_weather(100.0, 2.0, race.date(), sessions) is None
    assert await service.get_weekend_weather(1.0, 200.0, race.date(), sessions) is None
    past = datetime.now(UTC) - timedelta(hours=5)
    archived = WeatherData(12.0, 61, 0, "1.6 mm")
    monkeypatch.setattr(service, "get_race_weather", AsyncMock(return_value=archived))
    weekend = await service.get_weekend_weather(
        1.0, 2.0, past.date(), [("qualifying", past - timedelta(days=1)), ("race", past)]
    )
    assert weekend == WeekendWeather((SessionWeather("race", archived),))
    service.get_race_weather.return_value = None
    assert await service.get_weekend_weather(1.0, 2.0, past.date(), [("race", past)]) is None
    assert open_meteo.requests == []


@pytest.mark.asyncio
async def test_fetch_weather_outside_the_window_returns_none(open_meteo):
    far = datetime.now(UTC) + timedelta(days=30)
    assert await ws.WeatherService()._fetch_weather(1.0, 2.0, far) is None
    assert open_meteo.requests == []


def test_match_session_hour_without_rows():
    assert ws._match_session_hour({}, NOW) is None


@pytest.mark.asyncio
async def test_weather_context_adds_the_weekend_only_on_request(monkeypatch):
    monkeypatch.setattr(ws.config, "WEATHER_ENABLED", True)
    current = WeatherData(20.0, 1, 5)
    race_weather = WeatherData(25.0, 2, 10)
    weekend = _weekend(("qualifying", 21.0, 3, 20), ("race", 25.0, 2, 10))
    monkeypatch.setattr(ws, "get_cached_circuit_weather", lambda _cid: current)
    monkeypatch.setattr(ws.WeatherService, "get_race_weather", AsyncMock(return_value=race_weather))
    get_weekend = AsyncMock(return_value=weekend)
    monkeypatch.setattr(ws.WeatherService, "get_weekend_weather", get_weekend)
    _, _, without = await ws.get_weather_context(_qatar())
    assert "weekend" not in without
    get_weekend.assert_not_awaited()
    _, race, with_weekend = await ws.get_weather_context(_qatar(), include_weekend=True)
    assert race == race_weather and with_weekend["weekend"] == weekend
    args = get_weekend.await_args.args
    assert args[2] == QA_RACE.date() and [kind for kind, _ in args[3]] == ["qualifying", "race"]
    get_weekend.return_value = None
    _, _, empty = await ws.get_weather_context(_qatar(), include_weekend=True)
    assert "weekend" not in empty


@pytest.mark.asyncio
async def test_weather_context_timeout_keeps_resolved_values(monkeypatch):
    import asyncio

    current = WeatherData(21.0, 2, 20)

    async def partial(_race, weather_by_type, **_kwargs):
        weather_by_type["current"] = current
        weather_by_type["weekend"] = QR
        await asyncio.Event().wait()

    monkeypatch.setattr(ws, "_get_weather_context_unbounded", partial)
    monkeypatch.setattr(ws.config, "WEATHER_ENRICHMENT_TIMEOUT_SECONDS", 0.01)
    result = await ws.get_weather_context({}, include_weekend=True)
    assert result == (current, None, {"off": None, "current": current, "weekend": QR})


# --- Hourly prefetch per pregeneration profile -------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_persists_weekend_sessions_only_with_the_weekend_profile(monkeypatch):
    race, sessions = _future_weekend(3)
    monkeypatch.setattr(ws, "_get_next_race_details", lambda: (1.0, 2.0, race))
    monkeypatch.setattr(ws, "_next_weekend_sessions", lambda: (race.date(), sessions))
    monkeypatch.setattr(ws.WeatherService, "get_current_weather", AsyncMock(return_value=None))
    race_weather = WeatherData(25.0, 2, 10)
    monkeypatch.setattr(ws.WeatherService, "get_race_weather", AsyncMock(return_value=race_weather))
    weekend = WeekendWeather(
        (
            SessionWeather("qualifying", WeatherData(21.0, 3, 20)),
            SessionWeather("race", race_weather),
        )
    )
    get_weekend = AsyncMock(return_value=weekend)
    monkeypatch.setattr(ws.WeatherService, "get_weekend_weather", get_weekend)
    db = SimpleNamespace(save_weather_cache=AsyncMock())

    monkeypatch.setattr(ws.config, "PREGENERATE_WEATHER_TYPES", "")
    assert await ws.prefetch_weather_for_next_race(db) == race_weather
    get_weekend.assert_not_awaited()
    assert db.save_weather_cache.await_count == 1

    monkeypatch.setattr(ws.config, "PREGENERATE_WEATHER_TYPES", "race_day,weekend")
    db.save_weather_cache.reset_mock()
    await ws.prefetch_weather_for_next_race(db)
    keys = [call.kwargs["cache_key"] for call in db.save_weather_cache.await_args_list]
    assert keys == [
        ws._race_weather_cache_key(1.0, 2.0, race),
        ws._race_weather_cache_key(1.0, 2.0, sessions[0][1]),
    ]

    get_weekend.return_value = None
    db.save_weather_cache.reset_mock()
    await ws.prefetch_weather_for_next_race(db)
    assert db.save_weather_cache.await_count == 1


@pytest.mark.asyncio
async def test_warm_load_restores_weekend_sessions_with_the_weekend_profile(monkeypatch):
    race, sessions = _future_weekend(3)
    monkeypatch.setattr(ws, "_get_next_race_details", lambda: (1.0, 2.0, race))
    monkeypatch.setattr(ws, "_next_weekend_sessions", lambda: (race.date(), sessions))
    stamp = datetime.now(UTC).isoformat()
    rows = {
        ws._race_weather_cache_key(1.0, 2.0, sessions[0][1]): (21.0, 3, 20, stamp, None),
    }
    db = SimpleNamespace(get_weather_cache=AsyncMock(side_effect=rows.get))
    monkeypatch.setattr(ws.config, "PREGENERATE_WEATHER_TYPES", "")
    assert await ws.load_prefetched_weather_from_db(db) == 0
    monkeypatch.setattr(ws.config, "PREGENERATE_WEATHER_TYPES", "weekend")
    assert await ws.load_prefetched_weather_from_db(db) == 1
    cached = ws.WeatherService.get_cached_value(
        ws._race_weather_cache_key(1.0, 2.0, sessions[0][1])
    )
    assert cached == WeatherData(21.0, 3, 20)


def test_next_weekend_sessions_reads_the_static_next_race(monkeypatch):
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _qatar())
    assert ws._next_weekend_sessions() == (
        QA_RACE.date(),
        [("qualifying", QA_Q), ("race", QA_RACE)],
    )
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: None)
    assert ws._next_weekend_sessions() == (None, [])


# --- Pregeneration profile ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ("current", "race_day")),
        ("weekend", ("weekend",)),
        ("race,WEEKEND", ("race_day", "weekend")),
        ("off", ()),
    ],
)
def test_weather_profile_accepts_weekend_and_keeps_the_default(raw, expected):
    assert (
        Config(_env_file=None, PREGENERATE_WEATHER_TYPES=raw).pregenerate_weather_types == expected
    )


def test_default_pregeneration_keeps_the_shipped_weather_variants():
    assert config_module.DEFAULT_PREGENERATE_WEATHER_CODES == ("current", "race_day")
    assert "weekend" in config_module.PREGENERATE_WEATHER_CODES


@pytest.mark.asyncio
async def test_generation_requests_the_weekend_only_with_the_profile(monkeypatch):
    context = AsyncMock(return_value=(None, None, {"off": None, "weekend": QR}))
    monkeypatch.setattr(scheduler_generation, "get_weather_context", context)
    monkeypatch.setattr(scheduler_generation.config, "PREGENERATE_WEATHER_TYPES", "")
    await scheduler_generation._load_weather_context(_qatar())
    assert context.await_args.kwargs == {"include_weekend": False}
    assert scheduler_generation._profile_weather_types({"off": None, "weekend": QR}) == ["off"]
    monkeypatch.setattr(scheduler_generation.config, "PREGENERATE_WEATHER_TYPES", "weekend")
    await scheduler_generation._load_weather_context(_qatar())
    assert context.await_args.kwargs == {"include_weekend": True}
    assert scheduler_generation._profile_weather_types({"off": None, "weekend": QR}) == [
        "off",
        "weekend",
    ]


# --- Refresh hint ------------------------------------------------------------------------


def _hint(race, now, weather_type):
    return rh.calendar_refresh_hint(
        race,
        now=now,
        timezone_name=None,
        countdown=CountdownMode.HOURS,
        weather_type=weather_type,
        weather_enabled=True,
        selection_grace=None,
    )


def test_weekend_weather_wakes_hourly_from_the_qualifying_forecast():
    race = _qatar()
    race_day_from = datetime(2026, 11, 14, tzinfo=UTC)
    weekend_from = datetime(2026, 11, 12, tzinfo=UTC)
    early = datetime(2026, 11, 1, tzinfo=UTC)
    assert race_day_from in _hint(race, early, "race_day").changes
    assert weekend_from in _hint(race, early, "weekend").changes
    between = datetime(2026, 11, 13, tzinfo=UTC)
    assert not _hint(race, between, "race_day").hourly
    assert _hint(race, between, "weekend").hourly
    assert _hint(race, NOW, "weekend").seconds() <= rh.HOURLY_REFRESH_SECONDS + rh.SETTLE_SECONDS


def test_date_only_race_has_a_weekend_forecast_but_no_race_day_forecast():
    race = {"schedule": [{"name": "Race", "datetime": "2026-12-06", "date": "2026-12-06"}]}
    now = datetime(2026, 12, 1, tzinfo=UTC)
    assert not _hint(race, now, "race_day").hourly
    assert _hint(race, now, "weekend").hourly


# --- Rendering ---------------------------------------------------------------------------


def _render(race, weather, weather_type, *, display="1bit", lang="en", **kwargs) -> bytes:
    renderer = create_renderer(display, get_translator(lang), lang)
    return renderer.render_calendar(race, None, weather, weather_type, **kwargs)


def _box_texts(race, weather, weather_type, monkeypatch, *, lang="en", phase=None, **kwargs):
    renderer = create_renderer("1bit", get_translator(lang), lang)
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    texts: list[str] = []
    original = draw.text

    def spy(xy, text, *args, **kw):
        texts.append(text)
        return original(xy, text, *args, **kw)

    monkeypatch.setattr(draw, "text", spy)
    bottom = renderer._draw_countdown_box(
        draw,
        race,
        kwargs.pop("schedule_bottom", 247),
        weather,
        weather_type,
        weekend=phase,
        **kwargs,
    )
    return texts, bottom


@pytest.mark.parametrize("display", DISPLAYS)
def test_celsius_is_the_default_render(frozen, display):
    race = _qatar()
    weather = WeatherData(27.4, 1, 10)
    default = _render(race, weather, "race_day", display=display)
    assert default == _render(race, weather, "race_day", display=display, temp_unit=TempUnit.C)
    assert default == bmp_artifacts.render_calendar_bytes(
        display, "en", race, None, weather, "race_day"
    )
    assert default != _render(race, weather, "race_day", display=display, temp_unit=TempUnit.F)


def test_fahrenheit_draws_the_converted_temperature(frozen, monkeypatch):
    texts, _ = _box_texts(
        _qatar(), WeatherData(38.9, 1, 100), "race_day", monkeypatch, temp_unit=TempUnit.F
    )
    assert "102° " in texts and "100%" in texts


@pytest.mark.parametrize("lang", LANGUAGE_CODES)
def test_three_digit_fahrenheit_fits_beside_every_countdown(frozen, monkeypatch, lang):
    race = _singapore()
    moments = (
        datetime(2026, 10, 2, 10, tzinfo=UTC),
        datetime(2026, 10, 8, 23, tzinfo=UTC),
        datetime(2026, 10, 9, 10, tzinfo=UTC),
        datetime(2026, 10, 9, 20, tzinfo=UTC),
        datetime(2026, 10, 11, 12, 30, tzinfo=UTC),
    )
    for now in moments:
        frozen.now = now
        for phase in (None, weekend_phase(race, now=now, zone=PRAGUE)):
            for mode in CountdownMode:
                for weather in (WeatherData(40.2, 1, 100), WeatherData(-40.4, 71, 100)):
                    texts, _ = _box_texts(
                        race,
                        weather,
                        "race_day",
                        monkeypatch,
                        lang=lang,
                        phase=phase,
                        countdown_mode=mode,
                        temp_unit=TempUnit.F,
                    )
                    assert not any("..." in text for text in texts), (lang, now, mode, texts)


def test_fahrenheit_falls_back_to_an_ellipsis_when_nothing_else_fits(frozen, monkeypatch):
    race = {**_qatar(), "is_cancelled": False}
    translator = {**get_translator("en"), "race_completed": "COMPLETED " * 6}
    frozen.now = QA_RACE + timedelta(hours=5)
    renderer = create_renderer("1bit", translator, "en")
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    texts: list[str] = []
    original = draw.text
    monkeypatch.setattr(
        draw, "text", lambda xy, text, *a, **k: (texts.append(text), original(xy, text, *a, **k))
    )
    renderer._draw_countdown_box(
        draw, race, 247, WeatherData(40.2, 1, 100), "race_day", temp_unit=TempUnit.F
    )
    assert any(text.endswith("...") for text in texts)
    assert "104° " in texts


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("lang", ["en", "de", "ja"])
@pytest.mark.parametrize("unit", list(TempUnit))
def test_weekend_weather_adds_a_second_line_inside_the_box(frozen, display, lang, unit):
    race = _qatar()
    plain = Image.open(BytesIO(_render(race, None, "off", display=display, lang=lang)))
    weekend = Image.open(
        BytesIO(_render(race, QR, "weekend", display=display, lang=lang, temp_unit=unit))
    )
    diff = ImageChops.difference(plain.convert("RGB"), weekend.convert("RGB")).getbbox()
    assert diff is not None
    left, top, right, bottom = diff
    # Only the status box between the schedule and the circuit facts changes.
    assert left >= 510 and right <= 796 and top >= 247 and bottom <= 310


@pytest.mark.parametrize("unit", list(TempUnit))
def test_weekend_line_texts(frozen, monkeypatch, unit):
    texts, _ = _box_texts(_qatar(), QR, "weekend", monkeypatch, temp_unit=unit)
    expected = ["19°", "39°"] if unit == TempUnit.C else ["67°", "102°"]
    assert "Q" in texts and "R" in texts and "60%" in texts and "100%" in texts
    assert all(value in texts for value in expected)
    # The countdown keeps its full labels on its own line.
    assert "6 days 21 hours" in texts


@pytest.mark.parametrize("lang", LANGUAGE_CODES)
def test_weekend_line_fits_every_locale_without_clamping(frozen, monkeypatch, lang):
    layouts = []
    original = renderer_calendar._weekend_line_layout

    def spy(draw, weekend, **kwargs):
        result = original(draw, weekend, **kwargs)
        layouts.append((result, kwargs["max_width"]))
        return result

    monkeypatch.setattr(renderer_calendar, "_weekend_line_layout", spy)
    hot = _weekend(
        ("sprint", 40.2, 95, 100), ("qualifying", 40.2, 95, 100), ("race", 40.2, 95, 100)
    )
    cold = _weekend(("qualifying", -40.4, 71, 100), ("race", -18, 71, 100))
    for weekend in (hot, cold):
        for unit in TempUnit:
            texts, _ = _box_texts(
                _singapore(), weekend, "weekend", monkeypatch, lang=lang, temp_unit=unit
            )
            assert not any("..." in text for text in texts)
    for (segments, font, width), max_width in layouts:
        assert width <= max_width and font.size >= 14 and len(segments) >= 2


def test_sprint_is_shown_when_it_fits(frozen, monkeypatch):
    translator = {
        **get_translator("en"),
        "weather_session_sprint": "S",
        "weather_session_qualifying": "Q",
        "weather_session_race": "R",
    }
    weekend = _weekend(("sprint", 1, 0, 1), ("qualifying", 2, 0, 2), ("race", 3, 0, 3))
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    renderer = create_renderer("1bit", translator, "en")
    segments, _, _ = renderer_calendar._weekend_line_layout(
        draw,
        weekend,
        max_width=400,
        translator=translator,
        temp_unit=TempUnit.C,
        rain_icon=ws.RAINDROP_ICON,
        text_font=renderer.fonts["schedule_row_bold"],
        weather_icon_font=renderer.fonts["weather_icon_font"],
    )
    assert [segment[0][0] for segment in segments] == ["S", "Q", "R"]
    only_sprint = _weekend(("sprint", 1, 0, 1))
    segments, _, _ = renderer_calendar._weekend_line_layout(
        draw,
        only_sprint,
        max_width=400,
        translator={},
        temp_unit=TempUnit.C,
        rain_icon=ws.RAINDROP_ICON,
        text_font=renderer.fonts["schedule_row_bold"],
        weather_icon_font=renderer.fonts["weather_icon_font"],
    )
    assert segments[0][0][0] == "S"


def test_a_very_long_label_is_clamped(frozen, monkeypatch):
    translator = {**get_translator("en"), "weather_session_qualifying": "QUALIFYING " * 8}
    renderer = create_renderer("1bit", translator, "en")
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    texts: list[str] = []
    original = draw.text
    monkeypatch.setattr(
        draw, "text", lambda xy, text, *a, **k: (texts.append(text), original(xy, text, *a, **k))
    )
    renderer._draw_countdown_box(draw, _qatar(), 247, QR, "weekend")
    assert any(text.startswith("QUALIFYING") and text.endswith("...") for text in texts)


def test_weekend_without_room_for_two_lines_keeps_the_race_forecast(frozen, monkeypatch):
    texts, _ = _box_texts(_qatar(), QR, "weekend", monkeypatch, schedule_bottom=280)
    assert "39° " in texts and "Q" not in texts
    qualifying_only = _weekend(("qualifying", 19.2, 61, 60))
    texts, _ = _box_texts(_qatar(), qualifying_only, "weekend", monkeypatch, schedule_bottom=280)
    assert not any("°" in text for text in texts)


def test_cancelled_race_shows_no_weekend_weather(frozen, monkeypatch):
    texts, _ = _box_texts({**_qatar(), "is_cancelled": True}, QR, "weekend", monkeypatch)
    assert texts == ["CANCELLED"]


def test_weekend_weather_with_the_weekend_phase(frozen, monkeypatch):
    race = _singapore()
    frozen.now = datetime(2026, 10, 9, 10, tzinfo=UTC)
    phase = weekend_phase(race, now=frozen.now, zone=PRAGUE)
    texts, _ = _box_texts(race, QR, "weekend", monkeypatch, phase=phase, temp_unit=TempUnit.F)
    assert "SQ: 2 hours" in texts and "102°" in texts


def test_font_size_keeps_non_truetype_fonts():
    font = object()
    assert renderer_calendar._font_size(font, 12) is font


# --- Teams weather panel -----------------------------------------------------------------


TEAMS_NOW = datetime(2026, 3, 6, 12, tzinfo=UTC)


def test_teams_panel_token_and_render_follow_the_unit(monkeypatch):
    weather = WeatherData(38.9, 1, 40)
    monkeypatch.setattr(ws.WeatherService, "get_cached_value", staticmethod(lambda key: weather))
    celsius = build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=TEAMS_NOW)
    fahrenheit = build_teams_extra_context(
        TeamsExtra.WEATHER, 2026, now=TEAMS_NOW, temp_unit=TempUnit.F
    )
    assert celsius.temp_unit == TempUnit.C and fahrenheit.temp_unit == TempUnit.F
    c_token = teams_extra_cache_token(TeamsExtra.WEATHER, celsius)
    f_token = teams_extra_cache_token(TeamsExtra.WEATHER, fahrenheit)
    assert not c_token.endswith("_f") and f_token.endswith("_f")
    assert c_token.rsplit("_", 1)[0] == f_token.rsplit("_", 2)[0]  # same day, round, panel
    # The hash follows the drawn temperature: 20.2 °C and 20.4 °C are both 20 °C (one Celsius
    # image) but 68 °F and 69 °F, so the Fahrenheit panel must not reuse the cached image.
    warm, warmer = WeatherData(20.2, 1, 40), WeatherData(20.4, 1, 40)
    assert teams_extra_cache_token(
        TeamsExtra.WEATHER, replace(celsius, weather=warm)
    ) == teams_extra_cache_token(TeamsExtra.WEATHER, replace(celsius, weather=warmer))
    assert teams_extra_cache_token(
        TeamsExtra.WEATHER, replace(fahrenheit, weather=warm)
    ) != teams_extra_cache_token(TeamsExtra.WEATHER, replace(fahrenheit, weather=warmer))
    # Without weather or on another panel the unit changes nothing.
    assert teams_extra_cache_token(
        TeamsExtra.WEATHER, replace(fahrenheit, weather=None)
    ) == teams_extra_cache_token(TeamsExtra.WEATHER, replace(celsius, weather=None))
    countdown = replace(fahrenheit, weather=None)
    assert teams_extra_cache_token(TeamsExtra.COUNTDOWN, countdown) == teams_extra_cache_token(
        TeamsExtra.COUNTDOWN, replace(celsius, weather=None)
    )

    teams = TeamsService()._load_from_json(2026)
    for display in DISPLAYS:
        renderer = create_renderer(display, get_translator("en"), "en")
        c_image = renderer.render_teams_drivers(teams, TeamsExtra.WEATHER, celsius)
        f_image = renderer.render_teams_drivers(teams, TeamsExtra.WEATHER, fahrenheit)
        assert c_image != f_image


def test_teams_panel_draws_three_digit_fahrenheit(monkeypatch):
    from app.services import renderer_teams_extras as panels

    weather = WeatherData(40.2, 95, 100)
    monkeypatch.setattr(ws.WeatherService, "get_cached_value", staticmethod(lambda key: weather))
    ctx = build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=TEAMS_NOW, temp_unit=TempUnit.F)
    renderer = create_renderer("bwry", get_translator("de"), "de")
    panel = Image.new("P", (394, 61))
    panel.putpalette(Image.open(BytesIO(renderer.render_error("x"))).getpalette() or [])
    draw = ImageDraw.Draw(panel)
    texts: list[str] = []
    original = draw.text
    monkeypatch.setattr(
        draw, "text", lambda xy, text, *a, **k: (texts.append(text), original(xy, text, *a, **k))
    )
    panels._draw_weather(
        panel, draw, renderer.theme, ctx, translator=get_translator("de"), lang="de"
    )
    assert "104°" in texts


# --- Routes ------------------------------------------------------------------------------


@pytest.fixture
def client_app(frozen, monkeypatch):
    """Mount the image and preview routes with Qatar 2026 and fake weather."""
    race = _qatar()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    bmp_artifacts._singleflight_tasks.clear()
    weather = {"off": None, "current": WeatherData(20.0, 1, 5), "race": WeatherData(38.9, 1, 100)}

    async def context(race_data, *, include_weekend=False):
        return None, None, {**weather, **({"weekend": QR} if include_weekend else {})}

    monkeypatch.setattr(bmp_artifacts, "get_weather_context", context)
    monkeypatch.setattr(bmp_artifacts.config, "WEATHER_ENABLED", True)
    monkeypatch.setattr(images, "record_api_call", MagicMock())
    pregenerated = MagicMock(return_value=None)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", pregenerated)
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _qatar())
    for module in (images, previews):
        monkeypatch.setattr(module, "enforce_rate_limit", MagicMock())
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    app.state.pregenerated = pregenerated
    yield app
    bmp_artifacts._singleflight_tasks.clear()


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/calendar.bmp?temp_unit=k",
        "/calendar.bmp?temp_unit=F",
        "/teams.bmp?temp_unit=kelvin",
        "/auto.bmp?temp_unit=celsius",
        "/preview/configure/calendar.png?temp_unit=x",
    ],
)
async def test_invalid_temp_unit_returns_422(client_app, url):
    assert (await _get(client_app, url)).status_code == 422


def test_weather_type_weekend_is_canonical_and_unknown_values_keep_the_fallback():
    assert canonical_weather_type("weekend") == "weekend"
    assert canonical_weather_type("weekend", weather=False) == "off"
    # Unknown weather types predate this feature and keep falling back (backward compatible).
    assert canonical_weather_type("bogus") == "race_day"
    assert temp_unit_cache_suffix(TempUnit.C) == ""
    assert temp_unit_cache_suffix(TempUnit.F) == ":temp=f"


@pytest.mark.asyncio
async def test_fahrenheit_uses_its_own_variant_cache_entry(client_app):
    default = await _get(client_app, "/calendar.bmp")
    explicit = await _get(client_app, "/calendar.bmp?temp_unit=c")
    fahrenheit = await _get(client_app, "/calendar.bmp?temp_unit=f")
    assert default.content == explicit.content and explicit.headers["x-cache"] == "HIT"
    assert fahrenheit.status_code == 200 and fahrenheit.headers["x-cache"] == "MISS"
    assert fahrenheit.content != default.content
    assert fahrenheit.headers["etag"] != default.headers["etag"]
    assert all(":temp=" not in key for key in get_bmp_cache())
    (key,) = get_bmp_variant_cache()
    assert key.endswith(":temp=f")
    # Pregenerated files only exist in Celsius.
    lookups = client_app.state.pregenerated.call_count
    again = await _get(
        client_app, "/calendar.bmp?temp_unit=f", {"If-None-Match": fahrenheit.headers["etag"]}
    )
    assert again.status_code == 304
    assert client_app.state.pregenerated.call_count == lookups
    assert fahrenheit.headers["x-next-refresh-seconds"] == default.headers["x-next-refresh-seconds"]


@pytest.mark.asyncio
async def test_weekend_weather_renders_on_demand_into_the_variant_cache(client_app):
    response = await _get(client_app, "/calendar.bmp?weather_type=weekend")
    assert response.status_code == 200
    (key,) = get_bmp_variant_cache()
    assert ":weekend:" in key
    assert not get_bmp_cache()
    assert int(response.headers["x-next-refresh-seconds"]) <= (
        rh.HOURLY_REFRESH_SECONDS + rh.SETTLE_SECONDS
    )
    both = await _get(client_app, "/calendar.bmp?weather_type=weekend&temp_unit=f")
    assert both.content != response.content
    assert len(get_bmp_variant_cache()) == 2
    # The pregenerated lookup is still asked for Celsius weekend calendars.
    kwargs = client_app.state.pregenerated.call_args.kwargs
    assert kwargs["weather_type"] == "weekend"


@pytest.mark.asyncio
async def test_auto_and_teams_forward_the_unit(client_app, monkeypatch):
    calendar = await _get(client_app, "/calendar.bmp?temp_unit=f&weather_type=weekend")
    monkeypatch.setattr(
        images,
        "_choose_auto_screen",
        lambda *a, **k: images.ScreenChoice(images.AutoScreen.CALENDAR),
    )
    auto = await _get(client_app, "/auto.bmp?temp_unit=f&weather_type=weekend")
    assert auto.headers["x-active-screen"] == "calendar" and auto.content == calendar.content

    seen = []

    def build(extra, year, *, timezone_name=None, temp_unit=TempUnit.C):
        seen.append(temp_unit)

    monkeypatch.setattr(images, "build_teams_extra_context", build)
    monkeypatch.setattr(
        images.bmp_artifacts,
        "render_teams_artifact",
        AsyncMock(return_value=(b"BM", '"etag"')),
    )
    await _get(client_app, "/teams.bmp?extra=weather&temp_unit=f")
    monkeypatch.setattr(
        images,
        "_choose_auto_screen",
        lambda *a, **k: images.ScreenChoice(images.AutoScreen.TEAMS),
    )
    await _get(client_app, "/auto.bmp?extra=weather&temp_unit=f")
    assert seen == [TempUnit.F, TempUnit.F]


@pytest.mark.asyncio
async def test_preview_forwards_the_unit_and_weekend(client_app, monkeypatch):
    cached = AsyncMock(return_value=None)
    monkeypatch.setattr(previews, "_cached_preview_response", cached)
    preview = await _get(
        client_app, "/preview/configure/calendar.png?weather_type=weekend&temp_unit=f"
    )
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    cached.assert_not_awaited()
    (key,) = get_bmp_variant_cache()
    assert ":weekend:" in key and key.endswith(":temp=f")
    calendar = await _get(client_app, "/calendar.bmp?weather_type=weekend&temp_unit=f")
    assert calendar.headers["x-cache"] == "HIT"
    plain = await _get(client_app, "/preview/configure/calendar.png?weather_type=weekend")
    assert plain.status_code == 200
    cached.assert_awaited_once()

    seen = []

    def build(extra, year, *, timezone_name=None, temp_unit=TempUnit.C):
        seen.append(temp_unit)

    monkeypatch.setattr(previews, "build_teams_extra_context", build)
    await _get(client_app, "/preview/configure/teams.png?extra=weather&temp_unit=f")
    assert seen == [TempUnit.F]


@pytest.mark.asyncio
async def test_render_calendar_picks_the_weekend_weather(monkeypatch):
    seen = {}

    async def context(race_data, *, include_weekend=False):
        seen["include_weekend"] = include_weekend
        return None, None, {"off": None, "race": WeatherData(1, 0, 0), "weekend": QR}

    monkeypatch.setattr(bmp_artifacts, "get_weather_context", context)
    monkeypatch.setattr(bmp_artifacts.config, "WEATHER_ENABLED", True)
    rendered = MagicMock(return_value=b"BM")
    monkeypatch.setattr(bmp_artifacts, "render_calendar_bytes", rendered)

    async def run_render(job):
        return job()

    monkeypatch.setattr(bmp_artifacts, "run_render", run_render)
    await bmp_artifacts.render_calendar(
        race_data=_qatar(),
        lang="en",
        target_tz="Europe/Prague",
        weather_type="weekend",
        display="1bit",
        temp_unit=TempUnit.F,
    )
    assert seen["include_weekend"] is True
    args = rendered.call_args.args
    assert args[4] is QR and args[5] == "weekend" and args[-1] == TempUnit.F


def test_variant_classification():
    assert not bmp_artifacts.is_calendar_variant(CountdownMode.HOURS)
    assert not bmp_artifacts.is_calendar_variant(CountdownMode.HOURS, weather_type="race_day")
    assert bmp_artifacts.is_calendar_variant(CountdownMode.HOURS, temp_unit=TempUnit.F)
    assert bmp_artifacts.is_calendar_variant(CountdownMode.HOURS, weather_type="weekend")
    assert bmp_artifacts.calendar_image_cache(CountdownMode.HOURS) is get_bmp_cache()
    assert (
        bmp_artifacts.calendar_image_cache(CountdownMode.HOURS, temp_unit=TempUnit.F)
        is get_bmp_variant_cache()
    )


@pytest.mark.asyncio
async def test_api_docs_list_the_unit_and_the_weekend_weather():
    from app.routes.api import api_info

    endpoints = (await api_info())["endpoints"]
    for path in (
        "/calendar.bmp",
        "/teams.bmp",
        "/auto.bmp",
        "/preview/configure/{screen_type}.png",
    ):
        parameter = endpoints[path]["parameters"]["temp_unit"]
        assert parameter["values"] == ["c", "f"] and parameter["default"] == "c"
    for path in ("/calendar.bmp", "/auto.bmp", "/preview/configure/{screen_type}.png"):
        assert "weekend" in endpoints[path]["parameters"]["weather_type"]["values"]


def test_configurator_weekend_lead_matches_the_server():
    import re

    template = (Path(__file__).resolve().parents[1] / "app/templates/configure.html").read_text()
    match = re.search(r"const WEEKEND_FORECAST_LEAD_DAYS = (\d+);", template)
    assert match and int(match.group(1)) == WEEKEND_FORECAST_LEAD_DAYS
