"""One URL that switches between calendar and teams: ``GET /auto.bmp`` (#185 F2)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import DEFAULT, AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.services.renderer_base as renderer_base_module
from app.models import TeamEntry, TeamsData
from app.routes import images
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts, private_stats, teams_extra
from app.services.auto_screen import (
    AutoMode,
    AutoScreen,
    ScreenChoice,
    choose_screen,
    race_week_window,
)
from app.services.f1_service import NEXT_RACE_GRACE, F1Service
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils import refresh_hint as rh

PRAGUE = ZoneInfo("Europe/Prague")
LOS_ANGELES = ZoneInfo("America/Los_Angeles")
# Qatar 2026 (bundled schedule): FP1 2026-11-27 13:30 UTC, lights out 2026-11-29 16:00 UTC.
FP1 = datetime(2026, 11, 27, 13, 30, tzinfo=UTC)
RACE_START = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
LEAD = timedelta(hours=48)
WINDOW_START = FP1 - LEAD
WINDOW_END = RACE_START + NEXT_RACE_GRACE
# 2026-11-22 is an day with an even date.toordinal(): daily mode shows the calendar.
NOW = datetime(2026, 11, 22, 9, 30, tzinfo=UTC)
SECOND = timedelta(seconds=1)


def _race(**overrides) -> dict:
    """Return the bundled Qatar 2026 weekend converted to Europe/Prague."""
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    race = next((r for r in races if r["race_name"] == "Qatar Grand Prix"), None)
    assert race is not None
    return {**race, **overrides}


def _date_only_race(fp1: str, race_day: str) -> dict:
    """Return a weekend whose session hours are not published (TBA)."""
    return {
        "season": "2026",
        "round": "22",
        "is_cancelled": False,
        "schedule": [
            {"name": "FP1", "datetime": fp1, "date": fp1, "time": None},
            {"name": "Race", "datetime": race_day, "date": race_day, "time": None},
        ],
    }


def _choose(now, race=None, *, mode=AutoMode.RACE_WEEK, lead_hours=48, zone=PRAGUE):
    return choose_screen(
        mode, now=now, race_data=_race() if race is None else race, lead_hours=lead_hours, zone=zone
    )


# --- Screen choice -------------------------------------------------------------------------


def test_race_week_window_runs_from_lead_before_fp1_to_the_next_race_grace():
    assert race_week_window(_race(), lead=LEAD, zone=PRAGUE) == (WINDOW_START, WINDOW_END)
    assert race_week_window(_race(), lead=timedelta(0), zone=PRAGUE) == (FP1, WINDOW_END)


@pytest.mark.parametrize(
    "now, expected",
    [
        (WINDOW_START - SECOND, ScreenChoice(AutoScreen.TEAMS, WINDOW_START)),
        (WINDOW_START, ScreenChoice(AutoScreen.CALENDAR, WINDOW_END)),
        (RACE_START, ScreenChoice(AutoScreen.CALENDAR, WINDOW_END)),
        (WINDOW_END - SECOND, ScreenChoice(AutoScreen.CALENDAR, WINDOW_END)),
        (WINDOW_END, ScreenChoice(AutoScreen.TEAMS)),
    ],
)
def test_race_week_boundaries(now, expected):
    assert _choose(now) == expected


def test_lead_hours_moves_the_start_of_the_window():
    assert _choose(FP1 - SECOND, lead_hours=0) == ScreenChoice(AutoScreen.TEAMS, FP1)
    assert _choose(FP1, lead_hours=0).screen == AutoScreen.CALENDAR
    week = FP1 - timedelta(hours=168)
    assert _choose(week, lead_hours=168).screen == AutoScreen.CALENDAR
    assert _choose(week - SECOND, lead_hours=168).screen == AutoScreen.TEAMS


def test_date_only_race_uses_local_midnights_of_the_requested_timezone():
    race = _date_only_race("2026-11-27", "2026-11-29")
    start = datetime(2026, 11, 27, tzinfo=LOS_ANGELES).astimezone(UTC) - LEAD
    end = datetime(2026, 11, 30, tzinfo=LOS_ANGELES).astimezone(UTC)
    assert race_week_window(race, lead=LEAD, zone=LOS_ANGELES) == (start, end)
    assert _choose(start - SECOND, race, zone=LOS_ANGELES).screen == AutoScreen.TEAMS
    assert _choose(end - SECOND, race, zone=LOS_ANGELES) == ScreenChoice(AutoScreen.CALENDAR, end)
    assert _choose(end, race, zone=LOS_ANGELES) == ScreenChoice(AutoScreen.TEAMS)


def test_tba_race_time_with_timed_practice_ends_after_the_local_race_day():
    race = _race()
    schedule = [dict(event) for event in race["schedule"]]
    schedule[-1] = {"name": "Race", "datetime": "2026-11-29", "date": "2026-11-29", "time": None}
    window = race_week_window({**race, "schedule": schedule}, lead=LEAD, zone=PRAGUE)
    assert window == (WINDOW_START, datetime(2026, 11, 29, 23, 0, tzinfo=UTC))


@pytest.mark.parametrize(
    "race",
    [
        {},
        {**_race(), "is_cancelled": True},
        {**_race(), "schedule": [{"name": "FP1", "datetime": "2026-11-27T13:30:00+00:00"}]},
        {**_race(), "schedule": [{"name": "Race", "datetime": "not-a-date"}]},
        {**_race(), "schedule": [{"name": "Race", "datetime": None}]},
    ],
)
def test_cancelled_or_unscheduled_races_show_teams_without_a_switch(race):
    assert race_week_window(race, lead=LEAD, zone=PRAGUE) is None
    assert _choose(WINDOW_START, race) == ScreenChoice(AutoScreen.TEAMS)


def test_bundled_cancelled_races_have_no_window():
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    cancelled = [race for race in races if race["is_cancelled"]]
    assert cancelled
    assert all(race_week_window(race, lead=LEAD, zone=PRAGUE) is None for race in cancelled)


def test_malformed_practice_entries_are_ignored():
    race = _race()
    schedule = [{"name": "FP1", "datetime": "garbage"}, {"name": "FP2"}, *race["schedule"][1:]]
    start, _end = race_week_window({**race, "schedule": schedule}, lead=LEAD, zone=PRAGUE)
    assert start == datetime(2026, 11, 27, 17, 0, tzinfo=UTC) - LEAD


def test_off_season_last_completed_race_and_no_race_show_teams():
    assert _choose(datetime(2026, 12, 20, tzinfo=UTC)) == ScreenChoice(AutoScreen.TEAMS)
    assert choose_screen(
        AutoMode.RACE_WEEK, now=NOW, race_data=None, lead_hours=48, zone=PRAGUE
    ) == ScreenChoice(AutoScreen.TEAMS)


def test_daily_alternates_at_local_midnight_in_the_requested_timezone():
    prague_midnight = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    before = _choose(prague_midnight - SECOND, mode=AutoMode.DAILY)
    after = _choose(prague_midnight, mode=AutoMode.DAILY)
    assert before == ScreenChoice(AutoScreen.CALENDAR, prague_midnight)
    assert after == ScreenChoice(AutoScreen.TEAMS, prague_midnight + timedelta(days=1))
    # The same instant is still 2026-11-22 in Los Angeles (until 08:00 UTC the next day).
    la = _choose(prague_midnight, mode=AutoMode.DAILY, zone=LOS_ANGELES)
    assert la == ScreenChoice(AutoScreen.CALENDAR, datetime(2026, 11, 23, 8, 0, tzinfo=UTC))


def test_daily_switch_follows_a_dst_change():
    # Europe/Prague leaves summer time on 2026-10-25: that local day lasts 25 hours.
    day = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    choice = _choose(day, mode=AutoMode.DAILY)
    assert choice.next_switch == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert choice.screen == (
        AutoScreen.CALENDAR if day.astimezone(PRAGUE).toordinal() % 2 == 0 else AutoScreen.TEAMS
    )


def test_auto_endpoint_is_an_aggregate_stats_endpoint():
    assert private_stats.canonical_page("/auto.bmp?lang=cs", endpoint=True) == "/auto.bmp"
    assert private_stats.canonical_page("/auto.bmp") == "/other"


def test_clamp_max_age_only_lowers_existing_directives():
    assert images._clamp_max_age("public, max-age=3600", 90) == "public, max-age=90"
    assert images._clamp_max_age("public, max-age=60", 90) == "public, max-age=60"
    assert images._clamp_max_age("no-cache", 0) == "no-cache"
    assert images._clamp_max_age("no-store", 0) == "no-store"


# --- Route ---------------------------------------------------------------------------------


class _Clock:
    now = NOW


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def auto_app(monkeypatch):
    """Mount the image routes with a fixed next race, clock and teams data."""
    _Clock.now = NOW
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    race = _race()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()
    record = MagicMock()
    monkeypatch.setattr(images, "record_api_call", record)
    monkeypatch.setattr(images, "enforce_rate_limit", MagicMock())
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(images, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(images, "points_session_starts", lambda year: [RACE_START])
    monkeypatch.setattr(
        images,
        "build_teams_extra_context",
        lambda extra, year, **kwargs: teams_extra.build_teams_extra_context(
            extra, year, now=_Clock.now, **kwargs
        ),
    )
    monkeypatch.setattr(bmp_artifacts, "is_teams_data_cacheable", lambda data: True)
    monkeypatch.setattr(
        bmp_artifacts.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(
            return_value=TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
        ),
    )
    app = FastAPI()
    app.include_router(images.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    app.state.service = service
    app.state.record = record
    yield app
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


@pytest.mark.asyncio
async def test_outside_race_week_serves_the_teams_countdown_bytes(auto_app):
    _Clock.now = WINDOW_START - timedelta(hours=30)
    auto = await _get(auto_app, "/auto.bmp?tz=Europe/Prague")
    direct = await _get(auto_app, "/teams.bmp?extra=countdown&tz=Europe/Prague")
    assert auto.status_code == direct.status_code == 200
    assert auto.headers["x-active-screen"] == "teams"
    assert auto.headers["content-type"] == "image/bmp"
    assert auto.content == direct.content
    assert auto.headers["etag"] == direct.headers["etag"]
    assert direct.headers["x-cache"] == "HIT"
    assert auto.headers["cache-control"] == "no-cache"
    assert auto.headers["link"] == direct.headers["link"]
    assert 'rel="license"' in auto.headers["link"]
    # The day panel changes at local midnight, before the switch 30 hours later.
    assert auto.headers["x-next-refresh-seconds"] == direct.headers["x-next-refresh-seconds"]
    assert auto_app.state.record.call_args_list[0].args[0] == "/auto.bmp"
    assert auto_app.state.record.call_args_list[1].args[0] == "/teams.bmp"


@pytest.mark.asyncio
async def test_explicit_extra_and_year_reach_the_teams_screen(auto_app):
    auto = await _get(auto_app, "/auto.bmp?extra=none&year=2026&display=bwr")
    direct = await _get(auto_app, "/teams.bmp?year=2026&display=bwr")
    assert auto.headers["x-active-screen"] == "teams"
    assert auto.content == direct.content
    legacy = await _get(auto_app, "/auto.bmp?extra=timeline")
    countdown = await _get(auto_app, "/teams.bmp?extra=countdown")
    assert legacy.content == countdown.content


@pytest.mark.asyncio
async def test_race_week_serves_the_calendar_bytes_from_the_same_cache_entry(auto_app):
    _Clock.now = RACE_START - timedelta(hours=20)
    params = "lang=cs&tz=Europe/Prague&display=bwry&weather=false&countdown=days"
    auto = await _get(auto_app, f"/auto.bmp?{params}&track_style=contours")
    direct = await _get(auto_app, f"/calendar.bmp?{params}&track_style=contours")
    assert auto.headers["x-active-screen"] == "calendar"
    assert auto.headers["x-cache"] == "MISS"
    assert direct.headers["x-cache"] == "HIT"
    assert auto.content == direct.content
    assert auto.headers["etag"] == direct.headers["etag"]
    assert auto.headers["link"] == direct.headers["link"]
    assert auto.headers["cache-control"] == direct.headers["cache-control"]
    assert auto.headers["cache-control"] == "public, max-age=3600"
    assert auto.headers["x-next-refresh-seconds"] == direct.headers["x-next-refresh-seconds"]
    record = auto_app.state.record.call_args_list[0]
    assert record.args[0] == "/auto.bmp"
    assert record.kwargs["display_type"] == "bwry"


@pytest.mark.asyncio
async def test_default_auto_calendar_matches_the_default_calendar(auto_app):
    _Clock.now = WINDOW_START + timedelta(hours=1)
    auto = await _get(auto_app, "/auto.bmp?weather=false")
    direct = await _get(auto_app, "/calendar.bmp?weather=false")
    assert auto.headers["x-active-screen"] == "calendar"
    assert auto.content == direct.content
    assert direct.headers["x-cache"] == "HIT"
    assert len(get_bmp_cache()) == 1
    assert not get_bmp_variant_cache()


@pytest.mark.asyncio
async def test_hint_and_max_age_stop_at_the_switch(auto_app):
    # Twenty minutes before the end of the window the calendar may be cached 20 minutes only.
    _Clock.now = WINDOW_END - timedelta(minutes=20)
    calendar = await _get(auto_app, "/auto.bmp?weather=false&countdown=off")
    assert calendar.headers["x-active-screen"] == "calendar"
    assert calendar.headers["cache-control"] == "public, max-age=1200"
    assert calendar.headers["x-next-refresh-seconds"] == "1200"

    # Ten minutes before the window opens the teams hint shrinks to the switch.
    _Clock.now = WINDOW_START - timedelta(minutes=10)
    teams = await _get(auto_app, "/auto.bmp?extra=none")
    assert teams.headers["x-active-screen"] == "teams"
    assert teams.headers["cache-control"] == "no-cache"
    assert teams.headers["x-next-refresh-seconds"] == "600"
    direct = await _get(auto_app, "/teams.bmp")
    assert int(direct.headers["x-next-refresh-seconds"]) > 600


@pytest.mark.asyncio
async def test_slow_render_does_not_stretch_max_age_or_hint_past_the_switch(auto_app, monkeypatch):
    _Clock.now = WINDOW_END - timedelta(minutes=20)

    def take_five_minutes(*_args, **_kwargs):
        _Clock.now += timedelta(minutes=5)  # the render (or a queue) takes five minutes
        return DEFAULT

    slow_serve = AsyncMock(wraps=images._serve_calendar_bmp, side_effect=take_five_minutes)
    monkeypatch.setattr(images, "_serve_calendar_bmp", slow_serve)
    calendar = await _get(auto_app, "/auto.bmp?weather=false&countdown=off")
    assert calendar.headers["x-active-screen"] == "calendar"
    assert calendar.headers["cache-control"] == "public, max-age=900"
    assert calendar.headers["x-next-refresh-seconds"] == "900"


@pytest.mark.asyncio
async def test_304_keeps_headers_and_never_serves_a_stale_screen_across_the_switch(auto_app):
    _Clock.now = WINDOW_START - timedelta(minutes=30)
    teams = await _get(auto_app, "/auto.bmp?weather=false")
    etag = teams.headers["etag"]
    revalidated = await _get(auto_app, "/auto.bmp?weather=false", {"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["x-active-screen"] == "teams"
    assert revalidated.headers["etag"] == etag
    assert revalidated.headers["x-next-refresh-seconds"] == "1800"

    _Clock.now = WINDOW_START
    switched = await _get(auto_app, "/auto.bmp?weather=false", {"If-None-Match": etag})
    assert switched.status_code == 200
    assert switched.headers["x-active-screen"] == "calendar"
    assert switched.headers["etag"] != etag
    calendar_etag = switched.headers["etag"]
    again = await _get(auto_app, "/auto.bmp?weather=false", {"If-None-Match": calendar_etag})
    assert again.status_code == 304
    assert again.headers["x-active-screen"] == "calendar"
    assert again.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.asyncio
async def test_daily_mode_alternates_at_local_midnight_on_the_route(auto_app):
    midnight_la = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    _Clock.now = midnight_la - timedelta(minutes=5)
    url = "/auto.bmp?mode=daily&tz=America/Los_Angeles&weather=false"
    calendar = await _get(auto_app, url)
    assert calendar.headers["x-active-screen"] == "calendar"
    assert calendar.headers["cache-control"] == "public, max-age=300"
    assert calendar.headers["x-next-refresh-seconds"] == "300"
    _Clock.now = midnight_la
    teams = await _get(auto_app, url)
    assert teams.headers["x-active-screen"] == "teams"
    assert teams.headers["etag"] != calendar.headers["etag"]
    # Daily mode does not look up the next race to choose a screen.
    auto_app.state.service.get_next_race_from_static.reset_mock()
    await _get(auto_app, url)
    auto_app.state.service.get_next_race_from_static.assert_not_called()


@pytest.mark.asyncio
async def test_off_season_without_a_known_switch_keeps_the_teams_hint(auto_app):
    _Clock.now = datetime(2026, 12, 20, 12, tzinfo=UTC)
    # Off-season: the selection keeps the last completed race.
    auto_app.state.service.get_next_race_from_static.return_value = _race()
    auto = await _get(auto_app, "/auto.bmp?extra=none")
    direct = await _get(auto_app, "/teams.bmp")
    assert auto.headers["x-active-screen"] == "teams"
    assert auto.headers["x-next-refresh-seconds"] == direct.headers["x-next-refresh-seconds"]
    auto_app.state.service.get_next_race_from_static.return_value = None
    empty = await _get(auto_app, "/auto.bmp?extra=none")
    assert empty.headers["x-active-screen"] == "teams"


@pytest.mark.asyncio
async def test_failing_schedule_lookup_falls_back_to_teams(auto_app):
    auto_app.state.service.get_next_race_from_static.side_effect = RuntimeError("boom")
    response = await _get(auto_app, "/auto.bmp?extra=none")
    assert response.status_code == 200
    assert response.headers["x-active-screen"] == "teams"


@pytest.mark.asyncio
async def test_error_images_keep_no_store_and_the_quick_retry(auto_app, monkeypatch):
    monkeypatch.setattr(
        bmp_artifacts, "render_calendar_artifact", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(
        bmp_artifacts, "shared_error_artifact", AsyncMock(return_value=(b"err", '"e"'))
    )
    _Clock.now = WINDOW_END - timedelta(minutes=20)
    calendar = await _get(auto_app, "/auto.bmp?weather=false&countdown=auto")
    assert calendar.headers["x-active-screen"] == "calendar"
    assert calendar.headers["cache-control"] == "no-store"
    assert calendar.headers["x-next-refresh-seconds"] == str(rh.ERROR_REFRESH_SECONDS)

    monkeypatch.setattr(images, "points_session_starts", MagicMock(side_effect=RuntimeError))
    _Clock.now = NOW
    teams = await _get(auto_app, "/auto.bmp?display=spectra6")
    assert teams.headers["x-active-screen"] == "teams"
    assert teams.headers["cache-control"] == "no-store"
    assert teams.headers["x-next-refresh-seconds"] == str(rh.ERROR_REFRESH_SECONDS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "mode=weekly",
        "mode=",
        "lead_hours=-1",
        "lead_hours=169",
        "lead_hours=abc",
        "countdown=weekly",
        "extra=bogus",
        "track_style=bogus",
    ],
)
async def test_invalid_parameters_return_422(auto_app, query):
    response = await _get(auto_app, f"/auto.bmp?{query}")
    assert response.status_code == 422
    assert "x-active-screen" not in response.headers


@pytest.mark.asyncio
async def test_invalid_timezone_returns_400(auto_app):
    response = await _get(auto_app, "/auto.bmp?tz=Mars/Base")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_lead_hours_bounds_are_accepted(auto_app):
    _Clock.now = FP1 - timedelta(hours=100)
    week = await _get(auto_app, "/auto.bmp?lead_hours=168&weather=false")
    assert week.headers["x-active-screen"] == "calendar"
    zero = await _get(auto_app, "/auto.bmp?lead_hours=0&extra=none")
    assert zero.headers["x-active-screen"] == "teams"


def test_auto_route_is_rate_limited_in_its_own_bucket(monkeypatch):
    from fastapi.testclient import TestClient

    limiter = MagicMock(side_effect=images.HTTPException(status_code=429, detail="slow down"))
    monkeypatch.setattr(images, "enforce_rate_limit", limiter)
    app = FastAPI()
    app.include_router(images.router)
    response = TestClient(app).get("/auto.bmp")
    assert response.status_code == 429
    assert limiter.call_args.kwargs["bucket"] == "auto_bmp"


# --- Documentation and configurator ---------------------------------------------------------


@pytest.mark.asyncio
async def test_json_api_docs_describe_the_auto_endpoint():
    from app.routes.api import api_info

    auto = (await api_info())["endpoints"]["/auto.bmp"]
    assert auto["parameters"]["mode"]["values"] == ["race_week", "daily"]
    assert auto["parameters"]["mode"]["default"] == "race_week"
    lead = auto["parameters"]["lead_hours"]
    assert (lead["minimum"], lead["maximum"], lead["default"]) == (0, 168, 48)
    assert auto["parameters"]["extra"]["default"] == "countdown"
    assert "4 h after the start" in auto["parameters"]["mode"]["description"]
    assert "X-Active-Screen" in auto["response"]["active_screen"]


@pytest.mark.parametrize("lang, prefix", [("en", ""), ("cs", "/cs")])
def test_html_docs_and_configurator_offer_the_auto_url(lang, prefix):
    import html
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app import main

    texts = json.loads(
        (Path(__file__).resolve().parents[1] / "translations" / f"{lang}.json").read_text(
            encoding="utf-8"
        )
    )
    client = TestClient(main.app)
    docs = client.get(f"{prefix}/api/docs/html")
    assert docs.status_code == 200
    assert 'id="auto-bmp"' in docs.text
    assert "lead_hours" in docs.text
    assert "/auto.bmp?lang=cs&amp;tz=Europe/Prague" in docs.text
    for screen in ("calendar", "teams"):
        page = client.get(f"{prefix}/configure/{screen}", follow_redirects=True)
        assert page.status_code == 200
        assert 'id="autoScreenSelect"' in page.text
        assert 'data-on-change="selectAutoScreen"' in page.text
        assert texts["configure"]["autoScreen"]["label"] in page.text
        # The teams page keeps its chosen panel on /auto.bmp, so its race-week hint says so
        # instead of promising the countdown that the calendar page's auto URL gets.
        hints = texts["configure"]["autoScreen"]["hints"]
        shown, hidden = (
            ("race_week_teams", "race_week")
            if screen == "teams"
            else ("race_week", "race_week_teams")
        )
        assert html.escape(hints[shown]) in page.text
        assert html.escape(hints[hidden]) not in page.text
