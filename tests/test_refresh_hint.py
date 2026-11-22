"""Battery-friendly countdown modes and the X-Next-Refresh-Seconds hint (#185 F1)."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

import app.services.renderer_base as renderer_base_module
from app.models import TeamEntry, TeamsData
from app.routes import images, previews
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts, teams_extra
from app.services.f1_service import NEXT_RACE_GRACE, F1Service
from app.services.i18n import get_translator
from app.services.renderers import create_renderer
from app.services.weather_service import WeatherData
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils import refresh_hint as rh
from app.utils.race_times import today_in_timezone
from app.utils.refresh_hint import CountdownMode, RefreshHint

PRAGUE = ZoneInfo("Europe/Prague")
# Qatar 2026: lights out 2026-11-29 16:00 UTC (17:00 in Prague).
RACE_START = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 22, 9, 30, tzinfo=UTC)


def _race(**overrides) -> dict:
    """Return the bundled Qatar 2026 weekend converted to Europe/Prague."""
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    race = next(r for r in races if r["race_name"] == "Qatar Grand Prix")
    return {**race, **overrides}


def _date_only_race(day: str) -> dict:
    """Return a minimal weekend whose race start hour is not published."""
    return {
        "season": "2026",
        "round": "22",
        "timezone": "Europe/Prague",
        "schedule": [{"name": "Race", "datetime": day, "date": day, "time": None}],
    }


def _hint(race, now=NOW, **kwargs) -> RefreshHint:
    options = {
        "timezone_name": "Europe/Prague",
        "countdown": CountdownMode.HOURS,
        "weather_type": "off",
        "weather_enabled": True,
        "selection_grace": None,
    }
    options.update(kwargs)
    return rh.calendar_refresh_hint(race, now=now, **options)


class _Clock:
    """A mutable clock shared by the renderer and the route seam."""

    now = NOW


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def frozen(monkeypatch):
    """Freeze the renderer and the hint at NOW; tests move _Clock.now."""
    _Clock.now = NOW
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    return _Clock


def _render(race, mode=None, *, lang="en", display="1bit", weather=None) -> bytes:
    renderer = create_renderer(display, get_translator(lang), lang)
    kwargs = {} if mode is None else {"countdown_mode": mode}
    return renderer.render_calendar(race, None, weather, "race_day" if weather else "off", **kwargs)


def _box_texts(race, mode, monkeypatch, *, lang="en") -> list[str]:
    """Capture the strings drawn into the countdown box."""
    renderer = create_renderer("1bit", get_translator(lang), lang)
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    texts: list[str] = []
    original = draw.text

    def spy(xy, text, *args, **kwargs):
        texts.append(text)
        return original(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy)
    renderer._draw_countdown_box(draw, race, 220, countdown_mode=mode)
    return texts


# --- Rendering ----------------------------------------------------------------------------


def test_default_render_is_byte_identical_to_explicit_hours(frozen):
    race = _race()
    assert _render(race) == _render(race, CountdownMode.HOURS)


@pytest.mark.parametrize("mode", [CountdownMode.DAYS, CountdownMode.AUTO, CountdownMode.OFF])
@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
def test_battery_modes_render_identical_bytes_three_hours_apart(frozen, mode, display):
    race = _race()
    first = _render(race, mode, display=display)
    frozen.now = NOW + timedelta(hours=3)
    assert _render(race, mode, display=display) == first


def test_hours_output_changes_within_three_hours(frozen):
    race = _race()
    first = _render(race)
    frozen.now = NOW + timedelta(hours=3)
    assert _render(race) != first


def test_countdown_texts_per_mode(frozen, monkeypatch):
    race = _race()
    assert "7 days 6 hours" in _box_texts(race, CountdownMode.HOURS, monkeypatch)
    assert "7 days" in _box_texts(race, CountdownMode.DAYS, monkeypatch)
    assert "7 days" in _box_texts(race, CountdownMode.AUTO, monkeypatch)
    assert "Sun 29.11. 17:00" in _box_texts(race, CountdownMode.OFF, monkeypatch)
    assert "7 dní" in _box_texts(race, CountdownMode.DAYS, monkeypatch, lang="cs")
    assert "Ne 29.11. 17:00" in _box_texts(race, CountdownMode.OFF, monkeypatch, lang="cs")


def test_days_count_calendar_days_in_the_requested_timezone(frozen, monkeypatch):
    race = _race()
    # 23:30 in Prague on the 28th: the race is tomorrow, although fewer than 24 h remain.
    frozen.now = datetime(2026, 11, 28, 22, 30, tzinfo=UTC)
    assert "1 day" in _box_texts(race, CountdownMode.DAYS, monkeypatch)
    assert "1 den" in _box_texts(race, CountdownMode.DAYS, monkeypatch, lang="cs")


def test_auto_switches_to_days_and_hours_inside_48_hours(frozen, monkeypatch):
    race = _race()
    frozen.now = RACE_START - timedelta(hours=47, minutes=30)
    assert "1 day 23 hours" in _box_texts(race, CountdownMode.AUTO, monkeypatch)
    # Friday 12:30 in Prague: more than 48 h left, and Sunday is two calendar days away.
    frozen.now = RACE_START - timedelta(hours=48, minutes=30)
    assert "2 days" in _box_texts(race, CountdownMode.AUTO, monkeypatch)


def test_days_mode_on_race_day_and_statuses(frozen, monkeypatch):
    race = _race()
    frozen.now = RACE_START - timedelta(hours=5)
    assert "Today" in _box_texts(race, CountdownMode.DAYS, monkeypatch)
    assert "Dnes" in _box_texts(race, CountdownMode.DAYS, monkeypatch, lang="cs")
    frozen.now = RACE_START + timedelta(hours=1)
    for mode in CountdownMode:
        assert "IN PROGRESS" in _box_texts(race, mode, monkeypatch)
    frozen.now = RACE_START + timedelta(hours=3, minutes=1)
    for mode in CountdownMode:
        assert "COMPLETED" in _box_texts(race, mode, monkeypatch)


def test_date_only_race_modes(frozen, monkeypatch):
    race_day = today_in_timezone("Europe/Prague") + timedelta(days=3)
    race = _date_only_race(race_day.isoformat())
    for mode in (CountdownMode.HOURS, CountdownMode.DAYS, CountdownMode.AUTO):
        assert "3 days" in _box_texts(race, mode, monkeypatch)
    label = _box_texts(race, CountdownMode.OFF, monkeypatch)
    expected_day = race_day.strftime("%d.%m.")
    assert any(text.endswith(expected_day) for text in label)


def test_off_mode_keeps_weather_in_the_box(frozen):
    race = _race()
    weather = WeatherData(27.0, 1, 10)
    with_weather = _render(race, CountdownMode.OFF, weather=weather, lang="de")
    without = _render(race, CountdownMode.OFF, lang="de")
    assert with_weather != without
    image = Image.open(BytesIO(with_weather))
    assert image.size == (800, 480)


# --- Hint computation ---------------------------------------------------------------------


def test_next_local_midnight_counts_dst_days_correctly():
    zone = PRAGUE
    # 2026-10-25 is 25 hours long in Prague (CEST -> CET).
    autumn = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # 00:00 CEST on the 25th
    assert rh.next_local_midnight(autumn, zone) - autumn == timedelta(hours=25)
    # 2026-03-29 is 23 hours long (CET -> CEST).
    spring = datetime(2026, 3, 28, 23, 0, tzinfo=UTC)  # 00:00 CET on the 29th
    assert rh.next_local_midnight(spring, zone) - spring == timedelta(hours=23)


def test_days_hint_targets_dst_midnight_in_prague():
    race = _race()  # weeks after both DST changes
    autumn = _hint(race, now=datetime(2026, 10, 24, 22, 0, tzinfo=UTC), countdown="days")
    assert autumn.next_change == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)  # 25 h day
    assert autumn.seconds() == rh.MAX_REFRESH_SECONDS
    late_autumn = _hint(race, now=datetime(2026, 10, 25, 1, 30, tzinfo=UTC), countdown="days")
    assert late_autumn.seconds() == 21 * 3600 + 1800 + rh.SETTLE_SECONDS
    spring = _hint(race, now=datetime(2026, 3, 28, 23, 0, tzinfo=UTC), countdown="days")
    assert spring.seconds() == 23 * 3600 + rh.SETTLE_SECONDS
    assert spring.next_change == datetime(2026, 3, 29, 22, 0, tzinfo=UTC)


def test_hours_hint_targets_the_next_whole_hour_before_start():
    hint = _hint(_race())
    assert hint.next_change == NOW + timedelta(minutes=30)
    assert hint.seconds() == 1800 + rh.SETTLE_SECONDS
    exact = _hint(_race(), now=datetime(2026, 11, 22, 10, 0, tzinfo=UTC))
    assert exact.seconds() == rh.SETTLE_SECONDS
    assert exact.next_change == datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def test_auto_hint_uses_midnight_then_the_48_hour_switch_then_hours():
    far = _hint(_race(), countdown=CountdownMode.AUTO)
    assert far.next_change == datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    near_switch = _hint(
        _race(), now=RACE_START - timedelta(hours=48, minutes=10), countdown=CountdownMode.AUTO
    )
    assert near_switch.next_change == RACE_START - timedelta(hours=48)
    inside = _hint(
        _race(), now=RACE_START - timedelta(hours=10, minutes=15), countdown=CountdownMode.AUTO
    )
    assert inside.next_change == RACE_START - timedelta(hours=10)


def test_off_hint_waits_for_the_race_start_and_status_changes():
    off = _hint(_race(), countdown=CountdownMode.OFF)
    assert off.next_change == RACE_START
    assert off.seconds() == rh.MAX_REFRESH_SECONDS
    live = _hint(_race(), now=RACE_START + timedelta(hours=1), countdown=CountdownMode.OFF)
    assert live.next_change == RACE_START + rh.RACE_LIVE_WINDOW
    done = _hint(_race(), now=RACE_START + timedelta(hours=4), countdown=CountdownMode.OFF)
    assert done.next_change is None
    assert done.cache_token() == "none"
    assert done.seconds() == rh.MAX_REFRESH_SECONDS


def test_selection_grace_moves_the_automatic_race_on():
    hint = _hint(
        _race(), now=RACE_START + timedelta(hours=3, minutes=30), selection_grace=NEXT_RACE_GRACE
    )
    assert hint.next_change == RACE_START + NEXT_RACE_GRACE
    assert hint.seconds() == 1800 + rh.SETTLE_SECONDS
    dated = _hint(
        _date_only_race("2026-11-29"),
        now=datetime(2026, 11, 29, 10, tzinfo=UTC),
        selection_grace=NEXT_RACE_GRACE,
    )
    assert dated.next_change == datetime(2026, 11, 29, 23, 0, tzinfo=UTC)


def test_date_only_hints_follow_local_midnights():
    race = _date_only_race("2026-11-29")
    before = _hint(race, countdown=CountdownMode.DAYS)
    assert before.next_change == datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    off = _hint(race, countdown=CountdownMode.OFF)
    assert off.next_change == datetime(2026, 11, 28, 23, 0, tzinfo=UTC)
    race_day = _hint(race, now=datetime(2026, 11, 29, 10, tzinfo=UTC), countdown="off")
    assert race_day.next_change == datetime(2026, 11, 29, 23, 0, tzinfo=UTC)
    after = _hint(race, now=datetime(2026, 12, 1, tzinfo=UTC))
    assert after.next_change is None


def test_malformed_or_missing_race_sessions_have_no_countdown_changes():
    assert _hint(_date_only_race("2026-13-40")).next_change is None
    broken = {"schedule": [{"name": "Race", "datetime": "not-a-real-datetime"}]}
    assert _hint(broken).next_change is None
    assert _hint({"schedule": [{"name": "FP1", "datetime": "2026-11-27"}]}).next_change is None
    cancelled = _hint(_race(is_cancelled=True), weather_type="current")
    assert cancelled.next_change is None
    assert cancelled.hourly is False


def test_missing_race_data_retries_hourly():
    hint = _hint(None)
    assert hint.hourly is True
    assert hint.seconds() <= rh.HOURLY_REFRESH_SECONDS


def test_current_weather_caps_the_hint_at_one_hour():
    hint = _hint(_race(), countdown=CountdownMode.DAYS, weather_type="current")
    assert hint.hourly is True
    # Next hourly generation at 10:00 UTC plus the settle margin.
    assert hint.seconds() == 1800 + rh.SETTLE_SECONDS
    late = _hint(
        _race(),
        now=datetime(2026, 11, 22, 9, 59, 30, tzinfo=UTC),
        countdown=CountdownMode.OFF,
        weather_type="current",
    )
    assert late.seconds() == 30 + rh.SETTLE_SECONDS
    early = _hint(
        _race(),
        now=datetime(2026, 11, 22, 9, 0, 30, tzinfo=UTC),
        countdown=CountdownMode.OFF,
        weather_type="current",
    )
    assert early.seconds() == rh.HOURLY_REFRESH_SECONDS
    disabled = _hint(_race(), countdown="off", weather_type="current", weather_enabled=False)
    assert disabled.hourly is False


def test_race_day_forecast_appears_at_the_forecast_range_then_refreshes_hourly():
    far = _hint(
        _race(), now=datetime(2026, 11, 1, 12, tzinfo=UTC), countdown="off", weather_type="race_day"
    )
    assert far.hourly is False
    assert far.next_change == datetime(2026, 11, 14, tzinfo=UTC)
    near = _hint(_race(), countdown=CountdownMode.OFF, weather_type="race_day")
    assert near.hourly is True
    date_only = _hint(_date_only_race("2026-11-29"), countdown="off", weather_type="race_day")
    assert date_only.hourly is False


def test_hint_floor_and_cache_token():
    hint = RefreshHint(NOW, (NOW - timedelta(seconds=5), NOW + timedelta(seconds=1)))
    assert hint.next_change == NOW + timedelta(seconds=1)
    assert hint.cache_token() == str(int((NOW + timedelta(seconds=1)).timestamp()))
    assert RefreshHint(NOW, (NOW,)).seconds() == max(rh.MIN_REFRESH_SECONDS, rh.SETTLE_SECONDS)
    assert rh.countdown_shows_days(CountdownMode.HOURS, timedelta(days=9)) is False
    assert rh.countdown_shows_days(CountdownMode.AUTO, timedelta(hours=48)) is False


def test_teams_hint_follows_points_sessions_midnight_and_weather():
    sessions = [RACE_START - timedelta(days=1), RACE_START, RACE_START + timedelta(days=7)]
    quiet = rh.teams_refresh_hint(now=NOW, points_sessions=sessions)
    assert quiet.next_change == sessions[0]
    assert quiet.seconds() == rh.MAX_REFRESH_SECONDS
    results = rh.teams_refresh_hint(now=RACE_START + timedelta(hours=5), points_sessions=sessions)
    assert results.hourly is True
    assert results.seconds() <= rh.HOURLY_REFRESH_SECONDS
    panel = rh.teams_refresh_hint(
        now=NOW, points_sessions=sessions, timezone_name="America/New_York", day_panel=True
    )
    assert panel.next_change == datetime(2026, 11, 23, 5, 0, tzinfo=UTC)
    weather = rh.teams_refresh_hint(now=NOW, points_sessions=(), hourly=True)
    assert weather.next_change is None
    assert weather.seconds() == 1800 + rh.SETTLE_SECONDS


def test_points_session_starts_include_sprints_and_skip_malformed(monkeypatch):
    session = SimpleNamespace
    races = [
        session(date="2026-05-03", time="20:00:00Z", Sprint=session(date="2026-05-02", time=None)),
        session(date="bad-date", time=None, Sprint=None),
    ]
    monkeypatch.setattr(F1Service, "get_season_from_static", staticmethod(lambda season: races))
    assert teams_extra.points_session_starts(2026) == [
        datetime(2026, 5, 2, tzinfo=UTC),
        datetime(2026, 5, 3, 20, tzinfo=UTC),
    ]


# --- Routes -------------------------------------------------------------------------------


@pytest.fixture
def client_app(frozen, monkeypatch):
    """Mount the image and preview routes with a fixed next race and clock."""
    race = _race()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()
    monkeypatch.setattr(images, "record_api_call", MagicMock())
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
    for module in (images, previews):
        monkeypatch.setattr(module, "enforce_rate_limit", MagicMock())
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    yield app
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


@pytest.mark.asyncio
async def test_calendar_default_url_is_unchanged_and_hints_the_next_hour(client_app):
    response = await _get(client_app, "/calendar.bmp?weather=false")
    assert response.status_code == 200
    race = _race()
    historical = F1Service.get_historical_from_static(race["circuit"]["circuitId"])
    assert response.content == bmp_artifacts.render_calendar_bytes(
        "1bit", "en", race, historical, None, "off"
    )
    assert response.headers["x-next-refresh-seconds"] == str(1800 + rh.SETTLE_SECONDS)
    assert not get_bmp_variant_cache()
    assert all(":countdown=" not in key for key in get_bmp_cache())
    explicit = await _get(client_app, "/calendar.bmp?weather=false&countdown=hours")
    assert explicit.content == response.content
    assert explicit.headers["etag"] == response.headers["etag"]
    assert explicit.headers["x-cache"] == "HIT"


@pytest.mark.asyncio
async def test_calendar_days_returns_304_with_hint_across_hours(client_app, frozen):
    first = await _get(client_app, "/calendar.bmp?weather=false&countdown=days")
    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    midnight = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    assert first.headers["x-next-refresh-seconds"] == str(
        int((midnight - NOW).total_seconds()) + rh.SETTLE_SECONDS
    )
    assert not get_bmp_cache()
    assert len(get_bmp_variant_cache()) == 1

    frozen.now = NOW + timedelta(hours=3)
    etag = first.headers["etag"]
    revalidated = await _get(
        client_app, "/calendar.bmp?weather=false&countdown=days", {"If-None-Match": etag}
    )
    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers["x-next-refresh-seconds"] == str(
        int((midnight - frozen.now).total_seconds()) + rh.SETTLE_SECONDS
    )

    # A fresh render three hours later produces the same bytes and ETag.
    get_bmp_variant_cache().clear()
    rerendered = await _get(client_app, "/calendar.bmp?weather=false&countdown=days")
    assert rerendered.headers["x-cache"] == "MISS"
    assert rerendered.content == first.content
    assert rerendered.headers["etag"] == etag

    # After local midnight the cache key moves on and the day count is rendered again.
    frozen.now = midnight + timedelta(minutes=1)
    next_day = await _get(
        client_app, "/calendar.bmp?weather=false&countdown=days", {"If-None-Match": etag}
    )
    assert next_day.status_code == 200
    assert next_day.headers["etag"] != etag


@pytest.mark.asyncio
async def test_calendar_current_weather_caps_hint(client_app, monkeypatch):
    monkeypatch.setattr(
        bmp_artifacts,
        "get_weather_context",
        AsyncMock(return_value=(None, None, {"current": WeatherData(20.0, 1, 5)})),
    )
    response = await _get(client_app, "/calendar.bmp?weather_type=current&countdown=off")
    assert response.status_code == 200
    assert int(response.headers["x-next-refresh-seconds"]) <= rh.HOURLY_REFRESH_SECONDS


@pytest.mark.asyncio
async def test_invalid_countdown_returns_422(client_app):
    for url in ("/calendar.bmp?countdown=weekly", "/preview/configure/calendar.png?countdown=x"):
        response = await _get(client_app, url)
        assert response.status_code == 422
        assert "x-next-refresh-seconds" not in response.headers


@pytest.mark.asyncio
async def test_calendar_error_image_hints_a_quick_retry(client_app, monkeypatch):
    monkeypatch.setattr(
        bmp_artifacts, "render_calendar_artifact", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(
        bmp_artifacts, "shared_error_artifact", AsyncMock(return_value=(b"err", '"e"'))
    )
    response = await _get(client_app, "/calendar.bmp?weather=false&countdown=auto")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-next-refresh-seconds"] == str(rh.ERROR_REFRESH_SECONDS)


@pytest.mark.asyncio
async def test_configure_preview_renders_countdown_variants(client_app, monkeypatch):
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _race())
    cached = AsyncMock(return_value=None)
    monkeypatch.setattr(previews, "_cached_preview_response", cached)
    response = await _get(
        client_app, "/preview/configure/calendar.png?weather_type=off&countdown=days"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    cached.assert_not_awaited()
    assert any(":countdown=days:" in key for key in get_bmp_variant_cache())
    plain = await _get(client_app, "/preview/configure/calendar.png?weather_type=off")
    assert plain.status_code == 200
    cached.assert_awaited_once()
    assert plain.content != response.content


@pytest.mark.asyncio
async def test_teams_hint_on_200_304_and_error(client_app, monkeypatch):
    monkeypatch.setattr(images, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(bmp_artifacts, "is_teams_data_cacheable", lambda data: True)
    monkeypatch.setattr(
        bmp_artifacts.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(
            return_value=TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
        ),
    )
    monkeypatch.setattr(images, "points_session_starts", lambda year: [RACE_START])
    first = await _get(client_app, "/teams.bmp")
    assert first.status_code == 200
    assert first.headers["x-next-refresh-seconds"] == str(rh.MAX_REFRESH_SECONDS)
    again = await _get(client_app, "/teams.bmp", {"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["x-next-refresh-seconds"] == str(rh.MAX_REFRESH_SECONDS)

    monkeypatch.setattr(
        images,
        "build_teams_extra_context",
        lambda extra, year, **kwargs: teams_extra.build_teams_extra_context(
            extra, year, now=NOW, **kwargs
        ),
    )
    panel = await _get(client_app, "/teams.bmp?extra=countdown&tz=Europe/Prague")
    midnight = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    assert panel.headers["x-next-refresh-seconds"] == str(
        int((midnight - NOW).total_seconds()) + rh.SETTLE_SECONDS
    )

    monkeypatch.setattr(images, "points_session_starts", MagicMock(side_effect=RuntimeError))
    monkeypatch.setattr(
        bmp_artifacts, "shared_error_artifact", AsyncMock(return_value=(b"err", '"e"'))
    )
    failed = await _get(client_app, "/teams.bmp?display=bwr")
    assert failed.headers["x-next-refresh-seconds"] == str(rh.ERROR_REFRESH_SECONDS)
