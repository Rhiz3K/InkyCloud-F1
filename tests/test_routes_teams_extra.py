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
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils.etag import strong_etag

_NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


@pytest.fixture
def routes(monkeypatch):
    """Mount isolated routes with deterministic data and no external fetching."""
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
            lambda extra, year, **kwargs: build_teams_extra_context(
                extra, year, now=_NOW, **kwargs
            ),
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
    get_bmp_variant_cache().clear()
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
    assert token and any(token in key for key in get_bmp_variant_cache())
    assert all(token not in key for key in get_bmp_cache())
    # no-cache requires revalidation even across the next local midnight.
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.asyncio
async def test_historical_and_finished_seasons_share_none(routes, monkeypatch):
    """Unavailable snapshots share the none key, bytes, ETag, and file lookup."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        for year in (2025, 2026):
            if year == 2026:
                monkeypatch.setattr(
                    images, "build_teams_extra_context", lambda extra, year, **kwargs: None
                )
            plain = await client.get("/teams.bmp", params={"year": year})
            extra = await client.get("/teams.bmp", params={"year": year, "extra": "timeline"})
            assert extra.content == plain.content
            assert extra.headers["etag"] == plain.headers["etag"]
            assert extra.headers["x-cache"] == "HIT"
    assert all("_x" not in key for key in get_bmp_cache())
    assert not get_bmp_variant_cache()


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
    assert any("preview:" in key and "_xcountdown_" in key for key in get_bmp_variant_cache())
    assert not any(key.startswith("preview:") for key in get_bmp_cache())


@pytest.mark.asyncio
async def test_preview_unavailable_context_uses_none_path(routes, monkeypatch):
    """An unavailable panel preserves the ordinary preview file response."""
    monkeypatch.setattr(previews, "build_teams_extra_context", lambda extra, year, **kwargs: None)
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
    assert not get_bmp_variant_cache()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["timeline", "combo"])
@pytest.mark.parametrize("path", ["/teams.bmp", "/preview/configure/teams.png"])
async def test_legacy_panel_urls_share_countdown_output_and_cache(routes, legacy, path):
    """Old links select the new countdown without duplicate cache entries or 422s."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        canonical = await client.get(path, params={"extra": "countdown"})
        old = await client.get(path, params={"extra": legacy})
    assert canonical.status_code == old.status_code == 200
    assert canonical.content == old.content
    assert canonical.headers["etag"] == old.headers["etag"]


@pytest.mark.parametrize(
    "lang", ["cs", "de", "en", "es", "fr", "it", "ja", "nl", "pl", "pt-BR", "sk", "tr", "zh-CN"]
)
def test_configurator_exposes_only_five_localized_choices(lang):
    """Both real selects offer the same five translated labels and hints."""
    from bs4 import BeautifulSoup

    from app.services.i18n import get_translator
    from app.web.templates import templates

    rendered = templates.env.get_template("configure/_teams_sidebar.html").render(
        t=get_translator(lang), lang_url=lambda path: path
    )
    soup = BeautifulSoup(rendered, "html.parser")
    selects = soup.select(".teams-extra-select")
    assert len(selects) == 2
    for select in selects:
        options = select.select("option")
        assert [option["value"] for option in options] == [extra.value for extra in TeamsExtra]
        assert all(
            option.get_text(strip=True) and option["data-hint"].strip() for option in options
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "timeout", "service", "render"])
@pytest.mark.parametrize("fallback", ["cached", "dynamic", "unavailable"])
async def test_extra_preview_failures_use_ordinary_fallback(
    routes, monkeypatch, caplog, failure, fallback
):
    """Reject empty/failed panel output and fall back without double rate charging."""
    data = TeamsData(season=2026, teams=[])
    service = AsyncMock(return_value=data)
    if failure == "timeout":
        service.side_effect = TimeoutError("teams enrichment timed out")
    elif failure == "service":
        service.side_effect = RuntimeError("teams service unavailable")
    elif failure == "render":
        service.return_value = TeamsData(season=2026, teams=[TeamEntry(constructor_name="Ferrari")])
    monkeypatch.setattr(previews.TeamsService, "get_teams_and_drivers", service)
    render = MagicMock(side_effect=ValueError("panel renderer failed"))
    monkeypatch.setattr(previews, "_build_teams_preview_png", render)
    cached = AsyncMock(
        return_value=previews.Response(b"cached-teams", media_type="image/png")
        if fallback == "cached"
        else None
    )
    monkeypatch.setattr(previews, "_cached_preview_response", cached)
    ordinary = AsyncMock(return_value=previews.Response(b"ordinary-teams", media_type="image/png"))
    if fallback == "unavailable":
        ordinary.side_effect = RuntimeError("ordinary preview unavailable")
    monkeypatch.setattr(previews, "_render_teams_preview", ordinary)

    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get("/preview/configure/teams.png?extra=countdown")

    if fallback == "unavailable":
        assert response.status_code == 404
        assert response.json()["detail"] == "Configure preview not generated yet"
    else:
        assert response.status_code == 200
        assert response.content == (b"cached-teams" if fallback == "cached" else b"ordinary-teams")
        assert response.headers["content-type"] == "image/png"
        assert response.headers["Link"] == (
            f'<{previews.config.SITE_URL.rstrip("/")}/credits>; rel="license"'
        )
    if fallback == "cached":
        ordinary.assert_not_awaited()
    else:
        ordinary.assert_awaited_once_with("en", "1bit", full_size=True)
    service.assert_awaited_once_with(2026)
    cached.assert_awaited_once()
    if failure == "render":
        render.assert_called_once()
    else:
        render.assert_not_called()
    assert "Dynamic teams extra preview failed" in caplog.text
    if failure == "empty":
        assert "No teams data available for preview season 2026" in caplog.text
    previews.enforce_rate_limit.assert_called_once()
    assert not get_bmp_cache()
    assert not get_bmp_variant_cache()
    assert not images._singleflight_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/teams.bmp", "/preview/configure/teams.png"])
@pytest.mark.parametrize("extra", ["none", "countdown"])
async def test_invalid_timezone_rejected_even_without_panel(routes, path, extra):
    """Both image endpoints validate timezone input before rendering any panel."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get(path, params={"extra": extra, "tz": "Invalid/Zone"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid timezone: Invalid/Zone"}
    assert not get_bmp_cache()
    assert not get_bmp_variant_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["none", "countdown"])
async def test_timezone_ignored_for_plain_and_unavailable_context(routes, monkeypatch, extra):
    """Plain dashboards share bytes, headers, disk lookup and main-cache keys with tz."""
    if extra == "countdown":
        monkeypatch.setattr(images, "build_teams_extra_context", lambda *args, **kwargs: None)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        await client.get("/teams.bmp", params={"extra": extra})
        baseline = await client.get("/teams.bmp", params={"extra": extra})
        shifted = await client.get(
            "/teams.bmp", params={"extra": extra, "tz": "America/Los_Angeles"}
        )
        conditional = await client.get(
            "/teams.bmp",
            params={"extra": extra, "tz": "America/Los_Angeles"},
            headers={"If-None-Match": baseline.headers["etag"]},
        )
    assert baseline.status_code == shifted.status_code == 200
    assert baseline.content == shifted.content
    for header in ("etag", "cache-control", "x-cache", "link", "content-type", "content-length"):
        assert baseline.headers[header] == shifted.headers[header]
    assert conditional.status_code == 304
    assert conditional.headers["etag"] == baseline.headers["etag"]
    assert set(get_bmp_cache()) == {"teams_2026_en"}
    assert not get_bmp_variant_cache()
    images._get_pregenerated_teams_path.assert_called_once_with(
        lang="en", year=2026, display="1bit"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["countdown", "flags", "track", "weather"])
async def test_timezone_panels_render_and_cache_separately(routes, monkeypatch, extra):
    """Each visible dated panel uses local pixels and a separate variant cache key."""
    from app.config import config
    from app.services.f1_service import F1Service
    from app.services.image_keys import get_teams_image_key

    race = F1Service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.date, race.time = "2026-05-03", "23:00:00Z"
    monkeypatch.setattr(
        F1Service, "get_season_from_static", lambda year: [race] if year == 2026 else []
    )
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        default = await client.get("/teams.bmp", params={"extra": extra})
        explicit = await client.get(
            "/teams.bmp", params={"extra": extra, "tz": config.DEFAULT_TIMEZONE}
        )
        shifted = await client.get(
            "/teams.bmp", params={"extra": extra, "tz": "America/Los_Angeles"}
        )
        repeated = await client.get(
            "/teams.bmp", params={"extra": extra, "tz": "America/Los_Angeles"}
        )
    assert default.status_code == explicit.status_code == shifted.status_code == 200
    assert default.content == explicit.content
    assert default.headers["etag"] == explicit.headers["etag"]
    assert explicit.headers["x-cache"] == repeated.headers["x-cache"] == "HIT"
    assert default.content != shifted.content == repeated.content
    assert default.headers["etag"] != shifted.headers["etag"]
    for zone, response in ((None, default), ("America/Los_Angeles", shifted)):
        context = build_teams_extra_context(TeamsExtra(extra), 2026, now=_NOW, timezone_name=zone)
        key = get_teams_image_key("en", 2026, extra=TeamsExtra(extra), extra_context=context)
        assert get_bmp_variant_cache()[key] == (response.content, response.headers["etag"])
    assert len(get_bmp_variant_cache()) == 2
    assert not get_bmp_cache()
    images._get_pregenerated_teams_path.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/teams.bmp", "/preview/configure/teams.png"])
async def test_timezone_aliases_share_route_cache(routes, path):
    """Both routes normalize aliases before constructing the panel snapshot and key."""
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        canonical = await client.get(path, params={"extra": "track", "tz": "Asia/Kolkata"})
        alias = await client.get(path, params={"extra": "track", "tz": "Asia/Calcutta"})
    assert canonical.status_code == alias.status_code == 200
    assert canonical.content == alias.content
    assert canonical.headers["etag"] == alias.headers["etag"]
    assert len(get_bmp_variant_cache()) == 1
    assert "Asia_Kolkata" in next(iter(get_bmp_variant_cache()))


@pytest.mark.asyncio
async def test_preview_timezone_reaches_renderer_and_cache(routes, monkeypatch):
    """PNG rendering receives the requested local start and stores a timezone variant."""
    render = MagicMock(return_value=b"panel-png")
    monkeypatch.setattr(previews, "_build_teams_preview_png", render)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        response = await client.get(
            "/preview/configure/teams.png",
            params={"extra": "countdown", "tz": "America/Los_Angeles"},
        )
    assert response.status_code == 200
    context = render.call_args.kwargs["extra_context"]
    assert context.timezone_name == "America/Los_Angeles"
    assert context == build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=_NOW, timezone_name="America/Los_Angeles"
    )
    assert len(get_bmp_variant_cache()) == 1
    assert "America_Los_Angeles" in next(iter(get_bmp_variant_cache()))
    previews._cached_preview_response.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["none", "countdown"])
async def test_preview_timezone_ignored_for_plain_fallback(routes, monkeypatch, extra):
    """A valid timezone preserves plain preview filenames, content and headers."""
    monkeypatch.setattr(previews, "build_teams_extra_context", lambda *args, **kwargs: None)
    cached = AsyncMock(return_value=previews.Response(b"plain", headers={"ETag": '"plain"'}))
    monkeypatch.setattr(previews, "_cached_preview_response", cached)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        default = await client.get("/preview/configure/teams.png", params={"extra": extra})
        shifted = await client.get(
            "/preview/configure/teams.png",
            params={"extra": extra, "tz": "America/Los_Angeles"},
        )
    assert default.content == shifted.content == b"plain"
    assert default.headers == shifted.headers
    assert cached.await_args_list[0].args[0] == cached.await_args_list[1].args[0]
    assert not get_bmp_variant_cache()


@pytest.mark.asyncio
async def test_plain_timezone_uses_pregenerated_file(routes, monkeypatch, tmp_path):
    """Timezone input preserves disk serving and conditional headers for plain teams."""
    from app.services.artifact_metadata import write_artifact_metadata

    content = b"pregenerated-teams"
    path = tmp_path / "teams_2026_en.bmp"
    path.write_bytes(content)
    await write_artifact_metadata(path, content, "teams:2026")
    lookup = MagicMock(return_value=path)
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", lookup)
    async with AsyncClient(transport=ASGITransport(app=routes), base_url="http://test") as client:
        default = await client.get("/teams.bmp?extra=none")
        shifted = await client.get("/teams.bmp?extra=none&tz=America/Los_Angeles")
        default_conditional = await client.get(
            "/teams.bmp?extra=none",
            headers={"If-None-Match": default.headers["etag"]},
        )
        conditional = await client.get(
            "/teams.bmp?extra=none&tz=America/Los_Angeles",
            headers={"If-None-Match": default.headers["etag"]},
        )
    assert default.content == shifted.content == content
    assert default.headers == shifted.headers
    assert default.headers["x-cache"] == "MISS"
    assert conditional.status_code == default_conditional.status_code == 304
    assert conditional.content == default_conditional.content == b""
    assert conditional.headers["etag"] == default.headers["etag"]
    assert conditional.headers == default_conditional.headers
    assert lookup.call_count == 4
    assert all(
        call.kwargs == {"lang": "en", "year": 2026, "display": "1bit"}
        for call in lookup.call_args_list
    )
    assert not get_bmp_cache()
    assert not get_bmp_variant_cache()
