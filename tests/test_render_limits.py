"""Cache isolation and render-slot limits for calendar, teams, and preview images."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.models import TeamEntry, TeamsData
from app.routes import images, previews, tracks
from app.services.renderers import DISPLAY_TYPES
from app.services.teams_extra import TeamsExtra, build_teams_extra_context
from app.services.weather_service import WeatherData, WeatherService
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils import render_limit
from app.utils.etag import strong_etag
from app.utils.rate_limit import _reset_rate_limit_state_for_tests
from app.utils.render_limit import render_slot, reset_render_limits_for_tests

_NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


@pytest.fixture
def routes(monkeypatch):
    """Mount image routes with deterministic teams data and no external fetching."""
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    images._singleflight_tasks.clear()
    monkeypatch.setattr(WeatherService, "get_cached_value", lambda key: WeatherData(20.0, 1, 10))
    monkeypatch.setattr(images, "record_api_call", MagicMock())
    for module in (images, previews):
        monkeypatch.setattr(module, "enforce_rate_limit", MagicMock())
        monkeypatch.setattr(module, "get_default_teams_year", lambda: 2026)
        monkeypatch.setattr(
            module,
            "build_teams_extra_context",
            lambda extra, year: build_teams_extra_context(extra, year, now=_NOW),
        )
        monkeypatch.setattr(module, "is_teams_data_cacheable", lambda data: True)
    monkeypatch.setattr(
        images.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(
            return_value=TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
        ),
    )
    monkeypatch.setattr(
        previews.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(
            return_value=TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
        ),
    )
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(previews, "_cached_preview_response", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    yield app
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    images._singleflight_tasks.clear()


def _configure_limits(monkeypatch, *, global_limit: int, variant_limit: int, wait: float) -> None:
    """Apply render limits and drop semaphores created under the previous values."""
    monkeypatch.setattr(render_limit.config, "RENDER_CONCURRENCY", global_limit)
    monkeypatch.setattr(render_limit.config, "RENDER_VARIANT_CONCURRENCY", variant_limit)
    monkeypatch.setattr(render_limit.config, "RENDER_SLOT_WAIT_SECONDS", wait)
    reset_render_limits_for_tests()


@asynccontextmanager
async def _occupy_slot(*, variant: bool = False):
    """Hold one render permit until the caller exits."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def occupy() -> None:
        async with render_slot(variant=variant):
            started.set()
            await release.wait()

    task = asyncio.create_task(occupy())
    await started.wait()
    try:
        yield
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_render_slots_follow_the_loop_and_the_configured_limit(monkeypatch):
    """A gate is reused on one loop and rebuilt when the limit or the loop changes."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=10, wait=0.05)

    async with render_slot():
        first = render_limit._gates["global"][2]
    async with render_slot():
        assert render_limit._gates["global"][2] is first

    monkeypatch.setattr(render_limit.config, "RENDER_CONCURRENCY", 3)
    async with render_slot():
        rebuilt = render_limit._gates["global"][2]
    assert rebuilt is not first
    assert render_limit._gates["global"][0] == 3

    async with render_slot(variant=True):
        pass
    # The extra pool cannot exceed the global pool even when configured higher.
    assert render_limit._gates["variant"][0] == 3


def test_render_slot_is_rebuilt_for_a_new_event_loop(monkeypatch):
    """Semaphores belong to the loop that created them."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=0.05)
    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()

    async def grab():
        async with render_slot():
            return render_limit._gates["global"][2]

    try:
        first = first_loop.run_until_complete(grab())
        second = second_loop.run_until_complete(grab())
    finally:
        first_loop.close()
        second_loop.close()

    assert first is not second


@pytest.mark.asyncio
async def test_nested_slots_do_not_deadlock_and_timeouts_release_permits(monkeypatch):
    """Reentering a slot does not take a second permit, and a timeout does not leak one."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=0.05)

    async with render_slot():
        async with render_slot():
            async with render_slot(variant=True):
                pass

    async with render_slot(variant=True):
        async with render_slot(variant=True):
            async with render_slot():
                pass

    with pytest.raises(RuntimeError, match="render failed"):
        async with render_slot():
            raise RuntimeError("render failed")

    async with _occupy_slot(variant=False):
        with pytest.raises(HTTPException) as blocked:
            async with render_slot(variant=True):
                pass
        assert blocked.value.status_code == 503
        assert blocked.value.headers is not None
        assert blocked.value.headers["Retry-After"] == "1"

    async with render_slot(variant=True):
        pass


@pytest.mark.asyncio
async def test_variant_pool_rejects_extras_while_plain_renders_continue(monkeypatch):
    """A full extra pool returns 503 without consuming the last global slot."""
    _configure_limits(monkeypatch, global_limit=2, variant_limit=1, wait=0.05)

    async with _occupy_slot(variant=True):
        with pytest.raises(HTTPException) as extra:
            async with render_slot(variant=True):
                pass
        assert extra.value.status_code == 503
        async with render_slot():
            pass


@pytest.mark.asyncio
async def test_route_bytes_match_the_renderer_and_land_in_separate_caches(routes, monkeypatch):
    """Every display and extra panel keeps the renderer bytes, headers, and cache split."""
    rendered: list[bytes] = []
    original = images.run_render

    async def spy(func):
        data = await original(func)
        rendered.append(data)
        return data

    monkeypatch.setattr(images, "run_render", spy)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        for display in DISPLAY_TYPES:
            rendered.clear()
            plain = await client.get("/teams.bmp", params={"display": display})
            explicit = await client.get("/teams.bmp", params={"display": display, "extra": "none"})
            assert plain.status_code == explicit.status_code == 200
            assert plain.content == explicit.content == rendered[0]
            assert explicit.headers["x-cache"] == "HIT"
            assert plain.headers["cache-control"] == "no-cache"
            assert plain.headers["etag"] == strong_etag(plain.content)
            assert 'rel="license"' in plain.headers["link"]
            assert len(rendered) == 1
            with Image.open(BytesIO(plain.content)) as image:
                assert image.format == "BMP"
                assert image.size == (800, 480)
            assert plain.content in {item[0] for item in get_bmp_cache().values()}
            assert plain.content not in {item[0] for item in get_bmp_variant_cache().values()}

            for extra in TeamsExtra:
                if extra is TeamsExtra.NONE:
                    continue
                before = len(rendered)
                response = await client.get(
                    "/teams.bmp", params={"display": display, "extra": extra.value}
                )
                again = await client.get(
                    "/teams.bmp", params={"display": display, "extra": extra.value}
                )
                assert response.status_code == again.status_code == 200
                assert response.content == again.content == rendered[before]
                assert response.content != plain.content
                assert again.headers["x-cache"] == "HIT"
                assert response.headers["cache-control"] == "no-cache"
                assert response.headers["etag"] == strong_etag(response.content)
                assert response.headers["link"] == plain.headers["link"]
                assert len(rendered) == before + 1
                with Image.open(BytesIO(response.content)) as image:
                    assert image.format == "BMP"
                    assert image.size == (800, 480)
                assert response.content in {item[0] for item in get_bmp_variant_cache().values()}
                assert response.content not in {item[0] for item in get_bmp_cache().values()}

            before = len(rendered)
            calendar = await client.get(
                "/calendar.bmp", params={"display": display, "weather": "false"}
            )
            calendar_again = await client.get(
                "/calendar.bmp", params={"display": display, "weather": "false"}
            )
            assert calendar.status_code == calendar_again.status_code == 200
            assert calendar.content == calendar_again.content == rendered[before]
            assert calendar_again.headers["x-cache"] == "HIT"
            assert calendar.headers["cache-control"] == images.CALENDAR_BMP_CACHE_CONTROL
            assert calendar.headers["etag"] == strong_etag(calendar.content)
            assert 'rel="license"' not in calendar.headers.get("link", "")
            assert len(rendered) == before + 1
            with Image.open(BytesIO(calendar.content)) as image:
                assert image.format == "BMP"
                assert image.size == (800, 480)
            assert calendar.content in {item[0] for item in get_bmp_cache().values()}
            assert calendar.content not in {item[0] for item in get_bmp_variant_cache().values()}


@pytest.mark.asyncio
async def test_cached_images_skip_a_full_render_pool(routes, monkeypatch):
    """A cache hit returns while every render slot is occupied."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=5)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        first = await client.get("/teams.bmp", params={"extra": "countdown"})
        assert first.status_code == 200
        async with _occupy_slot(variant=True):
            hit = await asyncio.wait_for(
                client.get("/teams.bmp", params={"extra": "countdown"}), timeout=1
            )
        plain = await client.get("/teams.bmp")
        async with _occupy_slot():
            plain_hit = await asyncio.wait_for(client.get("/teams.bmp"), timeout=1)

    assert hit.status_code == plain_hit.status_code == 200
    assert hit.content == first.content
    assert hit.headers["x-cache"] == plain_hit.headers["x-cache"] == "HIT"
    assert hit.headers["link"] == plain.headers["link"]
    assert 'rel="license"' in hit.headers["link"]


@pytest.mark.asyncio
async def test_same_key_shares_one_slot_and_distinct_keys_receive_503(routes, monkeypatch):
    """Singleflight keeps one admission, and a second uncached key is told to retry."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=0.05)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow(func):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return b"BMshared"

    entries = 0
    joined = asyncio.Event()
    original_singleflight = images._singleflight

    async def counting(key, producer):
        nonlocal entries
        entries += 1
        if entries == 2:
            joined.set()
        return await original_singleflight(key, producer)

    monkeypatch.setattr(images, "run_render", slow)
    monkeypatch.setattr(images, "_singleflight", counting)

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        first_task = asyncio.create_task(client.get("/teams.bmp"))
        await started.wait()
        second_task = asyncio.create_task(client.get("/teams.bmp"))
        await asyncio.wait_for(joined.wait(), timeout=1)
        other = await client.get("/teams.bmp", params={"lang": "cs"})
        release.set()
        first, second = await asyncio.gather(first_task, second_task)

    assert other.status_code == 503
    assert other.headers["retry-after"] == "1"
    assert other.json()["detail"] == "Rendering is busy; retry shortly"
    assert calls == 1
    assert entries == 3
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == b"BMshared"
    assert first.headers["link"] == second.headers["link"]
    assert 'rel="license"' in first.headers["link"]


@pytest.mark.asyncio
async def test_busy_global_pool_returns_503_without_replacing_image_routes(routes, monkeypatch):
    """Calendar, teams, and preview misses report 503 instead of an error image or a fallback."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=0.05)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        async with _occupy_slot():
            responses = {
                "calendar": await client.get("/calendar.bmp", params={"weather": "false"}),
                "teams": await client.get("/teams.bmp"),
                "home": await client.get("/preview/calendar.png"),
                "configure-calendar": await client.get("/preview/configure/calendar.png"),
                "configure-teams": await client.get("/preview/configure/teams.png"),
            }

    for name, response in responses.items():
        assert response.status_code == 503, name
        assert response.headers["retry-after"] == "1"
        assert response.json()["detail"] == "Rendering is busy; retry shortly"
        assert response.content[:2] != b"BM"


@pytest.mark.asyncio
async def test_busy_variant_pool_rejects_extras_and_still_serves_calendar(routes, monkeypatch):
    """Extra BMP and preview floods get 503 while a calendar miss can still render."""
    _configure_limits(monkeypatch, global_limit=2, variant_limit=1, wait=0.05)
    monkeypatch.setattr(images, "run_render", AsyncMock(return_value=b"BMcalendar"))
    monkeypatch.setattr(previews, "run_render", AsyncMock(return_value=b"panel-png"))
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        async with _occupy_slot(variant=True):
            extra = await client.get("/teams.bmp", params={"extra": "flags", "lang": "de"})
            preview = await client.get("/preview/configure/teams.png", params={"extra": "track"})
            calendar = await client.get("/calendar.bmp", params={"weather": "false"})

    assert extra.status_code == preview.status_code == 503
    assert extra.headers["retry-after"] == preview.headers["retry-after"] == "1"
    assert calendar.status_code == 200
    assert calendar.content == b"BMcalendar"
    assert calendar.headers["cache-control"] == images.CALENDAR_BMP_CACHE_CONTROL
    images.run_render.assert_awaited()


@pytest.mark.asyncio
async def test_extra_preview_overload_does_not_render_the_ordinary_fallback(routes, monkeypatch):
    """A busy extra preview must not spend another render on the plain teams PNG."""
    _configure_limits(monkeypatch, global_limit=2, variant_limit=1, wait=0.05)
    ordinary = AsyncMock(return_value=previews.Response(b"ordinary", media_type="image/png"))
    monkeypatch.setattr(previews, "_render_teams_preview", ordinary)

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        async with _occupy_slot(variant=True):
            response = await client.get("/preview/configure/teams.png", params={"extra": "weather"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    ordinary.assert_not_awaited()
    assert not get_bmp_variant_cache()


@pytest.mark.asyncio
async def test_track_export_returns_503_when_render_slots_are_full(monkeypatch):
    """Uncached circuit exports share the global render pool."""
    _configure_limits(monkeypatch, global_limit=1, variant_limit=1, wait=0.05)
    monkeypatch.setattr(tracks.config, "RATE_LIMIT_ENABLED", False)
    _reset_rate_limit_state_for_tests()
    app = FastAPI()
    app.include_router(tracks.router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with _occupy_slot():
            response = await client.get("/api/tracks/albert_park.png")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["detail"] == "Rendering is busy; retry shortly"


@pytest.mark.asyncio
async def test_image_rate_limit_still_returns_429_before_rendering(monkeypatch):
    """The existing per-minute limit is unchanged and still wins over render admission."""
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(images.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(images.config, "IMAGE_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(images.config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(images.config, "AGGREGATE_STATS_ONLY", False)
    app = FastAPI()
    app.include_router(images.router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/teams.bmp", params={"extra": "none"})
        second = await client.get("/teams.bmp", params={"extra": "countdown"})

    assert first.status_code == 200
    assert first.content[:2] == b"BM"
    assert 'rel="license"' in first.headers["link"]
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert second.json()["detail"] == "Rate limit exceeded"
