"""HTTP contracts for optional, offline teams panels."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, AsyncHTTPTransport

from app.models import TeamEntry, TeamsData
from app.routes import images, previews
from app.services.teams_extra import TeamsExtra, build_teams_extra_context, teams_extra_cache_token
from app.services.weather_service import WeatherData, WeatherService
from app.state import get_bmp_cache
from app.utils.etag import strong_etag

_NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


@pytest.fixture
def routes(monkeypatch):
    """Mount isolated routes with deterministic data and no external fetching."""
    get_bmp_cache().clear()
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
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", MagicMock(return_value=None))
    monkeypatch.setattr(previews, "_cached_preview_response", AsyncMock(return_value=None))
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    yield app
    get_bmp_cache().clear()
    images._singleflight_tasks.clear()


@pytest.mark.asyncio
async def test_enum_validation_and_none_identity(routes):
    """Invalid panels fail validation while explicit none preserves the response."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        for path in ("/teams.bmp", "/preview/configure/teams.png"):
            assert (await client.get(path, params={"extra": "bogus"})).status_code == 422
        first = await client.get("/teams.bmp")
        explicit = await client.get("/teams.bmp?extra=none")
    assert first.status_code == explicit.status_code == 200
    assert first.content == explicit.content
    assert first.headers["etag"] == explicit.headers["etag"] == strong_etag(first.content)
    assert first.headers["cache-control"] == explicit.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [extra for extra in TeamsExtra if extra != TeamsExtra.NONE])
async def test_panels_render_unique_token_and_ignore_pregeneration(routes, extra):
    """Each panel renders distinct BMP bytes and caches using its snapshot token."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        plain = await client.get("/teams.bmp")
        images._get_pregenerated_teams_path.reset_mock()
        response = await client.get("/teams.bmp", params={"extra": extra})
        repeated = await client.get("/teams.bmp", params={"extra": extra})
    assert response.status_code == 200
    assert response.content[:2] == b"BM"
    assert response.content != plain.content
    assert response.headers["etag"] == strong_etag(response.content)
    assert repeated.content == response.content
    assert repeated.headers["x-cache"] == "HIT"
    images._get_pregenerated_teams_path.assert_not_called()
    ctx = build_teams_extra_context(extra, 2026, now=_NOW)
    token = teams_extra_cache_token(extra, ctx)
    assert token and any(token in key for key in get_bmp_cache())
    # no-cache requires revalidation even across the next local midnight.
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_historical_and_finished_seasons_share_none(routes, monkeypatch):
    """Unavailable snapshots share the none key, bytes, ETag, and file lookup."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        for year in (2025, 2026):
            if year == 2026:
                monkeypatch.setattr(images, "build_teams_extra_context", lambda extra, year: None)
            plain = await client.get("/teams.bmp", params={"year": year})
            extra = await client.get("/teams.bmp", params={"year": year, "extra": "timeline"})
            assert extra.content == plain.content
            assert extra.headers["etag"] == plain.headers["etag"]
            assert extra.headers["x-cache"] == "HIT"
    assert all("_x" not in key for key in get_bmp_cache())


@pytest.mark.asyncio
async def test_weather_panel_never_fetches(routes, monkeypatch):
    """Weather rendering reads memory only and cannot initiate weather HTTP requests."""
    monkeypatch.setattr(
        AsyncHTTPTransport,
        "handle_async_request",
        AsyncMock(side_effect=AssertionError("HTTP network forbidden")),
    )
    for name in ("_fetch_current_weather", "_fetch_weather", "_fetch_historical_weather"):
        monkeypatch.setattr(
            WeatherService, name, AsyncMock(side_effect=AssertionError("network forbidden"))
        )
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get("/teams.bmp?extra=weather")
    assert response.status_code == 200
    assert b"BM" == response.content[:2]
    AsyncHTTPTransport.handle_async_request.assert_not_called()
    for name in ("_fetch_current_weather", "_fetch_weather", "_fetch_historical_weather"):
        getattr(WeatherService, name).assert_not_called()


@pytest.mark.asyncio
async def test_extra_preview_cache_and_conditional_request(routes, monkeypatch):
    """Extra previews bypass disk, render once, and support cached conditional requests."""
    render = MagicMock(return_value=b"panel-png")
    monkeypatch.setattr(previews, "_build_teams_preview_png", render)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get("/preview/configure/teams.png?extra=countdown")
        repeated = await client.get("/preview/configure/teams.png?extra=countdown")
        conditional = await client.get(
            "/preview/configure/teams.png?extra=countdown",
            headers={"If-None-Match": response.headers["etag"]},
        )
    assert response.content == repeated.content == b"panel-png"
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert response.headers["cache-control"] == "no-cache"
    render.assert_called_once()
    assert render.call_args.kwargs["extra"] == TeamsExtra.COUNTDOWN
    assert render.call_args.kwargs["extra_context"].days_left >= 0
    previews._cached_preview_response.assert_not_called()
    assert any("preview:" in key and "_xcountdown_" in key for key in get_bmp_cache())


@pytest.mark.asyncio
async def test_preview_unavailable_context_uses_none_path(routes, monkeypatch):
    """An unavailable panel preserves the ordinary preview file response."""
    monkeypatch.setattr(previews, "build_teams_extra_context", lambda extra, year: None)
    render = AsyncMock(return_value=previews.Response(b"ordinary"))
    monkeypatch.setattr(previews, "_cached_preview_response", render)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get("/preview/configure/teams.png?extra=countdown")
    assert response.content == b"ordinary"
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_incomplete_data_is_not_cached(routes, monkeypatch):
    """Incomplete standings cannot poison the preview cache."""
    monkeypatch.setattr(previews, "is_teams_data_cacheable", lambda data: False)
    monkeypatch.setattr(previews, "_build_teams_preview_png", MagicMock(return_value=b"png"))
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get("/preview/configure/teams.png?extra=flags")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert not get_bmp_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["none", "track"])
async def test_license_headers(routes, extra, monkeypatch):
    """Keep license links on fresh and conditional BMP and configure responses."""
    monkeypatch.setattr(images.config, "SITE_URL", "https://f1.inkycloud.click/")
    expected = '<https://f1.inkycloud.click/credits>; rel="license"'
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        for path in ("/teams.bmp", "/preview/configure/teams.png"):
            response = await client.get(path, params={"extra": extra})
            assert response.status_code == 200
            assert response.headers["Link"] == expected
            if path == "/teams.bmp" or extra == "track":
                conditional = await client.get(
                    path,
                    params={"extra": extra},
                    headers={"If-None-Match": response.headers["etag"]},
                )
                assert conditional.status_code == 304
                assert conditional.headers["Link"] == expected
        monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
        monkeypatch.setattr(
            images,
            "_render_calendar_artifact",
            AsyncMock(return_value=(b"calendar", '"calendar"')),
        )
        calendar = await client.get("/calendar.bmp")
        assert 'rel="license"' not in calendar.headers.get("Link", "")
