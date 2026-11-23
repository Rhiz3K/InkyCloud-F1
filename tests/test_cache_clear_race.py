"""A render that straddles an image-cache clear is retried once instead of served no-store.

The scheduler clears the caches twice per generation run (startup and hourly). A request
whose render was in flight across a clear gets complete bytes that must not repopulate the
fresh cache; the route retries once with re-resolved inputs, so it normally answers with the
usual headers. Only a second race falls back to ``no-cache``; incomplete data keeps
``no-store``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models import TeamEntry, TeamsData
from app.routes import images, previews
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts
from app.services.weather_service import WeatherData
from app.state import clear_bmp_cache, get_bmp_cache, get_bmp_variant_cache
from app.utils.etag import strong_etag

_RACE = {"season": 2026, "round": 1, "race_name": "Test GP", "circuit": {"circuitId": "monza"}}
_DEFAULT_CALENDAR = images.CALENDAR_BMP_CACHE_CONTROL


class _Renders:
    """Count renders and clear the image caches during the configured ones."""

    def __init__(self, *, clear_on: tuple[int, ...] = (), complete: bool = True) -> None:
        self.count = 0
        self.clear_on = clear_on
        self.complete = complete

    def next(self) -> int:
        """Start one render and clear the caches mid-render when configured."""
        self.count += 1
        if self.count in self.clear_on:
            clear_bmp_cache()  # the scheduler's generation run lands while this render runs
        return self.count


@pytest.fixture
def race_app(monkeypatch):
    """Mount the image and preview routes with a fixed race and fake renderers."""
    clear_bmp_cache()
    bmp_artifacts._singleflight_tasks.clear()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=_RACE),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    monkeypatch.setattr(images, "record_api_call", MagicMock())
    for module in (images, previews):
        monkeypatch.setattr(module, "enforce_rate_limit", MagicMock())
        monkeypatch.setattr(module, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(previews, "_cached_preview_response", AsyncMock(return_value=None))
    monkeypatch.setattr(previews.F1Service, "get_next_race_from_static", lambda self: _RACE)
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    yield app
    clear_bmp_cache()
    bmp_artifacts._singleflight_tasks.clear()


def _calendar_renders(monkeypatch, renders: _Renders) -> None:
    """Replace the calendar renderer with one that numbers its bytes."""

    async def render_calendar(**kwargs):
        number = renders.next()
        return f"calendar-{number}".encode(), kwargs["race_data"], renders.complete

    monkeypatch.setattr(bmp_artifacts, "render_calendar", render_calendar)


def _teams_renders(monkeypatch, renders: _Renders, module=bmp_artifacts) -> None:
    """Replace teams data loading (where the clear lands) and the cacheability check."""

    async def load(_self, season):
        number = renders.next()
        return TeamsData(season=season, teams=[TeamEntry(constructor_name=f"Team {number}")])

    monkeypatch.setattr(module.TeamsService, "get_teams_and_drivers", load)
    monkeypatch.setattr(module, "is_teams_data_cacheable", lambda data: renders.complete)
    if module is bmp_artifacts:
        monkeypatch.setattr(
            bmp_artifacts,
            "render_teams_bytes",
            lambda display, lang, data, **kwargs: data.teams[0].constructor_name.encode(),
        )


async def _get(app: FastAPI, path: str, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params)


# --- Calendar ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "cache"),
    [({}, get_bmp_cache), ({"theme": "dark"}, get_bmp_variant_cache)],
    ids=["default", "variant"],
)
async def test_calendar_retry_after_a_clear_is_cached_with_normal_headers(
    race_app, monkeypatch, params, cache
):
    renders = _Renders(clear_on=(1,))
    _calendar_renders(monkeypatch, renders)

    response = await _get(race_app, "/calendar.bmp", **params)

    assert renders.count == 2
    assert response.status_code == 200
    assert response.content == b"calendar-2"
    assert response.headers["cache-control"] == _DEFAULT_CALENDAR
    assert response.headers["x-cache"] == "MISS"
    assert response.headers["etag"] == strong_etag(b"calendar-2")
    assert [artifact[0] for artifact in cache().values()] == [b"calendar-2"]


@pytest.mark.asyncio
async def test_calendar_second_race_falls_back_to_no_cache(race_app, monkeypatch):
    renders = _Renders(clear_on=(1, 2))
    _calendar_renders(monkeypatch, renders)

    response = await _get(race_app, "/calendar.bmp")

    assert renders.count == 2  # one retry, never a loop
    assert response.content == b"calendar-2"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-cache"] == "MISS"
    assert not get_bmp_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_on", [(), (1,)], ids=["no-clear", "with-clear"])
async def test_calendar_incomplete_data_stays_no_store_without_retry(
    race_app, monkeypatch, clear_on
):
    renders = _Renders(clear_on=clear_on, complete=False)
    _calendar_renders(monkeypatch, renders)

    response = await _get(race_app, "/calendar.bmp")

    assert renders.count == 1
    assert response.headers["cache-control"] == "no-store"
    assert not get_bmp_cache()


@pytest.mark.asyncio
async def test_calendar_without_a_clear_renders_once_with_normal_headers(race_app, monkeypatch):
    renders = _Renders()
    _calendar_renders(monkeypatch, renders)

    response = await _get(race_app, "/calendar.bmp")

    assert renders.count == 1
    assert response.headers["cache-control"] == _DEFAULT_CALENDAR
    assert list(get_bmp_cache().values()) == [(b"calendar-1", strong_etag(b"calendar-1"))]


@pytest.mark.asyncio
async def test_calendar_retry_serves_the_freshly_pregenerated_file(race_app, monkeypatch):
    renders = _Renders(clear_on=(1,))
    _calendar_renders(monkeypatch, renders)
    path = Path("calendar_en.bmp")
    lookup = MagicMock(side_effect=[None, path])
    fresh = (b"pregenerated", strong_etag(b"pregenerated"))
    read = AsyncMock(return_value=(fresh, None))
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", lookup)
    monkeypatch.setattr(images, "_read_pregenerated_artifact", read)

    response = await _get(race_app, "/calendar.bmp")

    assert renders.count == 1
    assert lookup.call_count == 2
    assert read.await_args.args[0] == path
    assert response.content == b"pregenerated"
    assert response.headers["cache-control"] == _DEFAULT_CALENDAR
    assert response.headers["x-cache"] == images.PREGENERATED_CACHE_STATUS


@pytest.mark.asyncio
async def test_calendar_retry_uses_an_entry_cached_meanwhile(race_app, monkeypatch):
    """The retry checks the in-process cache before rendering again."""
    renders = _Renders(clear_on=(1,))

    async def render_calendar(**kwargs):
        renders.next()
        # A newer request rendered and cached fresh bytes while this render was running.
        cache_key = capture.call_args_list[0].kwargs["cache_key"]
        get_bmp_cache()[cache_key] = (b"fresh", strong_etag(b"fresh"))
        return b"stale", kwargs["race_data"], True

    capture = AsyncMock(wraps=bmp_artifacts.render_calendar_artifact)
    monkeypatch.setattr(bmp_artifacts, "render_calendar", render_calendar)
    monkeypatch.setattr(bmp_artifacts, "render_calendar_artifact", capture)

    response = await _get(race_app, "/calendar.bmp")

    assert renders.count == 1
    assert response.content == b"fresh"
    assert response.headers["cache-control"] == _DEFAULT_CALENDAR
    assert response.headers["x-cache"] == "HIT"


@pytest.mark.asyncio
async def test_waiter_that_joined_a_pre_clear_flight_learns_it_was_stale(race_app, monkeypatch):
    """A request arriving after the clear but joining the old flight must not trust it."""
    started, release = asyncio.Event(), asyncio.Event()
    renders = 0
    joined = 0
    both_joined = asyncio.Event()
    original = bmp_artifacts.singleflight

    async def render_calendar(**kwargs):
        nonlocal renders
        renders += 1
        if renders == 1:
            started.set()
            await release.wait()
        return f"calendar-{renders}".encode(), kwargs["race_data"], True

    async def counting(key, producer):
        nonlocal joined
        joined += 1
        if joined == 2:
            both_joined.set()
        return await original(key, producer)

    monkeypatch.setattr(bmp_artifacts, "render_calendar", render_calendar)
    monkeypatch.setattr(bmp_artifacts, "singleflight", counting)

    async with AsyncClient(transport=ASGITransport(app=race_app), base_url="http://test") as client:
        first = asyncio.create_task(client.get("/calendar.bmp"))
        await started.wait()
        clear_bmp_cache()
        second = asyncio.create_task(client.get("/calendar.bmp"))
        await asyncio.wait_for(both_joined.wait(), timeout=1)
        release.set()
        responses = await asyncio.gather(first, second)

    assert renders == 2  # the original flight plus one shared retry
    for response in responses:
        assert response.content == b"calendar-2"
        assert response.headers["cache-control"] == _DEFAULT_CALENDAR
    assert list(get_bmp_cache().values()) == [(b"calendar-2", strong_etag(b"calendar-2"))]
    assert not bmp_artifacts._singleflight_tasks


@pytest.mark.asyncio
async def test_retry_starts_a_new_flight_for_the_same_key():
    """A finished flight has released its key before its waiters resume."""
    calls = []

    async def produce():
        calls.append(len(calls) + 1)
        return calls[-1]

    assert await bmp_artifacts.singleflight("same", produce) == 1
    assert await bmp_artifacts.singleflight("same", produce) == 2


def test_rendered_artifact_verdicts():
    generation = bmp_artifacts.bmp_cache_generation()
    artifact = (b"BM", '"etag"')
    complete = bmp_artifacts.RenderedArtifact(artifact, True, generation)
    incomplete = bmp_artifacts.RenderedArtifact(artifact, False, generation)
    assert complete.verdict() is bmp_artifacts.CacheVerdict.CACHED
    assert incomplete.verdict() is bmp_artifacts.CacheVerdict.INCOMPLETE
    clear_bmp_cache()
    assert complete.verdict() is bmp_artifacts.CacheVerdict.RACED
    assert incomplete.verdict() is bmp_artifacts.CacheVerdict.INCOMPLETE


@pytest.mark.asyncio
async def test_error_image_keeps_no_store(race_app, monkeypatch):
    monkeypatch.setattr(
        bmp_artifacts, "render_calendar_artifact", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(
        bmp_artifacts,
        "shared_error_artifact",
        AsyncMock(return_value=(b"error", strong_etag(b"error"))),
    )

    response = await _get(race_app, "/calendar.bmp")

    assert response.content == b"error"
    assert response.headers["cache-control"] == "no-store"


# --- Teams ---------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "cache"),
    [({}, get_bmp_cache), ({"theme": "dark"}, get_bmp_variant_cache)],
    ids=["default", "variant"],
)
async def test_teams_retry_after_a_clear_is_cached(race_app, monkeypatch, params, cache):
    renders = _Renders(clear_on=(1,))
    _teams_renders(monkeypatch, renders)

    response = await _get(race_app, "/teams.bmp", **params)

    assert renders.count == 2
    assert response.content == b"Team 2"
    assert response.headers["cache-control"] == images.TEAMS_BMP_CACHE_CONTROL
    assert response.headers["x-cache"] == "MISS"
    assert [artifact[0] for artifact in cache().values()] == [b"Team 2"]


@pytest.mark.asyncio
async def test_teams_extra_panel_retry_after_a_clear_is_cached(race_app, monkeypatch):
    renders = _Renders(clear_on=(1,))
    _teams_renders(monkeypatch, renders)
    monkeypatch.setattr(
        images, "build_teams_extra_context", MagicMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(images, "get_teams_image_key", lambda *args, **kwargs: "teams-extra")
    monkeypatch.setattr(images, "points_session_starts", lambda year: [])

    response = await _get(race_app, "/teams.bmp", extra="countdown")

    assert renders.count == 2
    assert response.headers["cache-control"] == images.TEAMS_BMP_CACHE_CONTROL
    assert get_bmp_variant_cache()["teams-extra"][0] == b"Team 2"


@pytest.mark.asyncio
async def test_teams_second_race_and_incomplete_data_are_not_cached(race_app, monkeypatch):
    renders = _Renders(clear_on=(1, 2))
    _teams_renders(monkeypatch, renders)

    raced = await _get(race_app, "/teams.bmp")

    assert renders.count == 2
    assert raced.content == b"Team 2"
    assert raced.headers["cache-control"] == "no-cache"
    assert not get_bmp_cache()

    renders.count, renders.clear_on, renders.complete = 0, (1,), False
    incomplete = await _get(race_app, "/teams.bmp")

    assert renders.count == 1
    assert incomplete.headers["cache-control"] == "no-cache"
    assert not get_bmp_cache()


@pytest.mark.asyncio
async def test_teams_retry_serves_the_freshly_pregenerated_file(race_app, monkeypatch):
    renders = _Renders(clear_on=(1,))
    _teams_renders(monkeypatch, renders)
    lookup = MagicMock(side_effect=[None, Path("teams_en.bmp")])
    fresh = (b"pregenerated", strong_etag(b"pregenerated"))
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", lookup)
    monkeypatch.setattr(
        images, "_read_pregenerated_artifact", AsyncMock(return_value=(fresh, None))
    )

    response = await _get(race_app, "/teams.bmp")

    assert renders.count == 1
    assert response.content == b"pregenerated"
    assert response.headers["x-cache"] == images.PREGENERATED_CACHE_STATUS


@pytest.mark.asyncio
async def test_teams_retry_re_resolves_the_default_season(race_app, monkeypatch):
    renders = _Renders(clear_on=(1,))
    _teams_renders(monkeypatch, renders)
    seasons = MagicMock(side_effect=[2026, 2027])
    monkeypatch.setattr(images, "get_default_teams_year", seasons)

    response = await _get(race_app, "/teams.bmp")

    assert seasons.call_count == 2
    assert response.status_code == 200
    assert any("2027" in key for key in get_bmp_cache())


# --- /auto.bmp -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", [images.AutoScreen.CALENDAR, images.AutoScreen.TEAMS])
async def test_auto_retries_through_the_shared_handlers(race_app, monkeypatch, screen):
    renders = _Renders(clear_on=(1,))
    _calendar_renders(monkeypatch, renders)
    _teams_renders(monkeypatch, renders)
    monkeypatch.setattr(
        images, "_choose_auto_screen", lambda *args, **kwargs: images.ScreenChoice(screen)
    )
    monkeypatch.setattr(
        images, "build_teams_extra_context", MagicMock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(images, "get_teams_image_key", lambda *args, **kwargs: "teams-extra")
    monkeypatch.setattr(images, "points_session_starts", lambda year: [])

    response = await _get(race_app, "/auto.bmp")

    assert renders.count == 2
    assert response.headers["x-active-screen"] == screen.value
    expected = {
        images.AutoScreen.CALENDAR: (_DEFAULT_CALENDAR, b"calendar-2"),
        images.AutoScreen.TEAMS: (images.TEAMS_BMP_CACHE_CONTROL, b"Team 2"),
    }[screen]
    assert (response.headers["cache-control"], response.content) == expected


@pytest.mark.asyncio
async def test_auto_second_race_falls_back_to_no_cache(race_app, monkeypatch):
    renders = _Renders(clear_on=(1, 2))
    _calendar_renders(monkeypatch, renders)
    monkeypatch.setattr(
        images,
        "_choose_auto_screen",
        lambda *args, **kwargs: images.ScreenChoice(images.AutoScreen.CALENDAR),
    )

    response = await _get(race_app, "/auto.bmp")

    assert renders.count == 2
    assert response.headers["cache-control"] == "no-cache"


# --- Configure previews --------------------------------------------------------------------


async def _bmp_as_png(func):
    """Skip PNG encoding: the preview body is the calendar BMP it was built from."""
    return func.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clear_on", "complete", "renders_expected", "cache_control"),
    [
        ((), True, 1, "public, max-age=300"),
        ((1,), True, 2, "public, max-age=300"),
        ((1, 2), True, 2, "no-cache"),
        ((1,), False, 1, "no-store"),
    ],
    ids=["no-clear", "retry", "second-race", "incomplete"],
)
async def test_configure_calendar_preview_retries_once(
    race_app, monkeypatch, clear_on, complete, renders_expected, cache_control
):
    renders = _Renders(clear_on=clear_on, complete=complete)
    _calendar_renders(monkeypatch, renders)
    monkeypatch.setattr(previews, "run_render", _bmp_as_png)

    response = await _get(race_app, "/preview/configure/calendar.png", theme="dark")

    assert renders.count == renders_expected
    assert response.content == f"calendar-{renders_expected}".encode()
    assert response.headers["cache-control"] == cache_control
    assert bool(get_bmp_variant_cache()) is (cache_control == "public, max-age=300")


@pytest.mark.asyncio
async def test_configure_calendar_preview_reuses_a_cached_calendar(race_app, monkeypatch):
    renders = _Renders()
    _calendar_renders(monkeypatch, renders)
    monkeypatch.setattr(previews, "run_render", _bmp_as_png)

    await _get(race_app, "/calendar.bmp", weather="false")
    response = await _get(race_app, "/preview/configure/calendar.png")

    assert renders.count == 1
    assert response.headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("clear_on", "complete", "renders_expected", "cache_control", "cached"),
    [
        ((1,), True, 2, "no-cache", True),
        ((1, 2), True, 2, "no-cache", False),
        ((1,), False, 1, "no-store", False),
    ],
    ids=["retry", "second-race", "incomplete"],
)
async def test_teams_preview_retries_once(
    race_app, monkeypatch, clear_on, complete, renders_expected, cache_control, cached
):
    renders = _Renders(clear_on=clear_on, complete=complete)
    _teams_renders(monkeypatch, renders, module=previews)

    async def png(func):
        return func.args[2].teams[0].constructor_name.encode()

    monkeypatch.setattr(previews, "run_render", png)

    response = await _get(race_app, "/preview/configure/teams.png", theme="dark")

    assert renders.count == renders_expected
    assert response.content == f"Team {renders_expected}".encode()
    assert response.headers["cache-control"] == cache_control
    assert bool(get_bmp_variant_cache()) is cached

    again = await _get(race_app, "/preview/configure/teams.png", theme="dark")
    assert again.headers["cache-control"] == ("no-cache" if cached else cache_control)


@pytest.mark.asyncio
async def test_teams_panel_preview_retry_rebuilds_its_context(race_app, monkeypatch):
    """The retry draws (and caches) the panel as it is after the clear, not before it."""
    renders = _Renders(clear_on=(1,))
    _teams_renders(monkeypatch, renders, module=previews)
    contexts = []

    def build(extra, season, *, timezone_name=None, temp_unit=None):
        # Each build sees the weather that is current at that moment: it changes with the
        # cache clear, as the scheduler refreshes weather in the same run.
        weather = WeatherData(20.0 + renders.count, 1, 10)
        ctx = SimpleNamespace(season=season, weather=weather, tz=timezone_name, unit=temp_unit)
        contexts.append(ctx)
        return ctx

    drawn = []

    async def png(func):
        drawn.append(func.keywords["extra_context"])
        return f"panel-{func.keywords['extra_context'].weather.temperature_c}".encode()

    monkeypatch.setattr(previews, "build_teams_extra_context", build)
    monkeypatch.setattr(
        previews,
        "get_teams_image_key",
        lambda lang, season, *, display, extra, extra_context: (
            f"teams_{lang}_{extra}_{extra_context.weather.temperature_c}"
        ),
    )
    monkeypatch.setattr(previews, "run_render", png)

    response = await _get(
        race_app,
        "/preview/configure/teams.png",
        extra="weather",
        tz="America/New_York",
        temp_unit="f",
    )

    assert response.status_code == 200
    assert renders.count == 2
    assert len(contexts) == 2 and drawn == contexts
    assert response.content == b"panel-21.0"
    # The rebuilt context keeps the requested timezone and unit.
    assert contexts[1].tz == "America/New_York" and str(contexts[1].unit) == "f"
    assert list(get_bmp_variant_cache()) == ["preview:teams_en_weather_21.0"]
