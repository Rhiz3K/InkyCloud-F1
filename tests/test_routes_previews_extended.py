"""Behavioral coverage for preview generation and static fallback routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models import TeamEntry, TeamsData
from app.routes import previews


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/preview/test.png",
            "raw_path": b"/preview/test.png",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        }
    )


def test_build_preview_helpers_render_and_preserve_color():
    renderer = SimpleNamespace(
        render_calendar=MagicMock(return_value=b"calendar-bmp"),
        render_teams_drivers=MagicMock(return_value=b"teams-bmp"),
    )
    with (
        patch("app.routes.previews.get_translator", return_value="translator"),
        patch("app.routes.previews.create_renderer", return_value=renderer),
        patch(
            "app.routes.previews.bmp_to_png", side_effect=[b"calendar-png", b"teams-png"]
        ) as convert,
    ):
        assert previews._build_calendar_preview_png("en", "bwr", {}, None, True) == b"calendar-png"
        assert previews._build_teams_preview_png("en", "1bit", {}, False) == b"teams-png"

    assert convert.call_args_list[0].kwargs == {
        "width": 400,
        "full_size": True,
        "preserve_color": True,
    }
    assert convert.call_args_list[1].kwargs["preserve_color"] is False


@pytest.mark.asyncio
async def test_render_calendar_preview_requires_race_and_returns_png():
    f1_service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=None))
    with (
        patch("app.routes.previews.F1Service", return_value=f1_service),
        pytest.raises(RuntimeError, match="No race data"),
    ):
        await previews._render_calendar_preview("en", full_size=False)

    race = {"circuit": {"circuitId": "monza"}}
    f1_service.get_next_race_from_static.return_value = race
    with (
        patch("app.routes.previews.F1Service", return_value=f1_service),
        patch("app.routes.previews.F1Service.get_historical_from_static", return_value={}),
        patch("app.routes.previews.run_render", new=AsyncMock(return_value=b"png")),
    ):
        response = await previews._render_calendar_preview("en", "bwr", full_size=True)

    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
async def test_render_teams_preview_requires_data_and_returns_png():
    service = SimpleNamespace(
        get_teams_and_drivers=AsyncMock(return_value=TeamsData(season=2026, teams=[]))
    )
    with (
        patch("app.routes.previews.get_default_teams_year", return_value=2026),
        patch("app.routes.previews.TeamsService", return_value=service),
        pytest.raises(RuntimeError, match="No teams data"),
    ):
        await previews._render_teams_preview("en", full_size=False)

    service.get_teams_and_drivers.return_value = TeamsData(
        season=2026, teams=[TeamEntry(constructor_name="Team")]
    )
    with (
        patch("app.routes.previews.get_default_teams_year", return_value=2026),
        patch("app.routes.previews.TeamsService", return_value=service),
        patch("app.routes.previews.run_render", new=AsyncMock(return_value=b"png")),
    ):
        response = await previews._render_teams_preview("en", "bwr", full_size=True)

    assert response.media_type == "image/png"


@pytest.mark.asyncio
async def test_dynamic_preview_dispatches_calendar_and_teams():
    calendar = AsyncMock(return_value="calendar")
    teams = AsyncMock(return_value="teams")
    with (
        patch("app.routes.previews._render_calendar_preview", new=calendar),
        patch("app.routes.previews._render_teams_preview", new=teams),
    ):
        assert (
            await previews._render_dynamic_preview("calendar", "en", full_size=False) == "calendar"
        )
        assert await previews._render_dynamic_preview("teams", "en", full_size=True) == "teams"


@pytest.mark.asyncio
async def test_home_preview_route_covers_file_dynamic_invalid_and_failed_paths(tmp_path):
    request = _request()
    (tmp_path / "preview_calendar_en.png").write_bytes(b"png")
    with patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)):
        response = await previews.get_preview_png("calendar", request, "invalid")
    assert response.path == tmp_path / "preview_calendar_en.png"

    with pytest.raises(HTTPException) as error:
        await previews.get_preview_png("unknown", request, "en")
    assert error.value.status_code == 404

    (tmp_path / "preview_calendar_en.png").unlink()
    dynamic = AsyncMock(return_value=SimpleNamespace(media_type="image/png"))
    with (
        patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)),
        patch("app.routes.previews.enforce_rate_limit"),
        patch("app.routes.previews._render_dynamic_preview", new=dynamic),
    ):
        assert (await previews.get_preview_png("calendar", request, "en")).media_type == "image/png"

    dynamic.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)),
        patch("app.routes.previews.enforce_rate_limit"),
        patch("app.routes.previews._render_dynamic_preview", new=dynamic),
        pytest.raises(HTTPException) as error,
    ):
        await previews.get_preview_png("calendar", request, "en")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_configure_preview_route_covers_files_fallback_and_dynamic_teams(tmp_path):
    request = _request()
    with pytest.raises(HTTPException) as error:
        await previews.get_configure_preview_png("unknown", request)
    assert error.value.status_code == 404

    exact = tmp_path / "configure_calendar_en_bwr_weather_race.png"
    exact.write_bytes(b"png")
    with patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)):
        response = await previews.get_configure_preview_png(
            "calendar", request, lang="invalid", weather_type="race_day", display="bwr"
        )
    assert response.path == exact

    exact.unlink()
    fallback = tmp_path / "configure_calendar_en.png"
    fallback.write_bytes(b"png")
    with patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)):
        response = await previews.get_configure_preview_png(
            "calendar", request, lang="en", weather_type="invalid", display="bwr"
        )
    assert response.path == fallback

    fallback.unlink()
    dynamic = AsyncMock(return_value=SimpleNamespace(media_type="image/png"))
    with (
        patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)),
        patch("app.routes.previews.enforce_rate_limit"),
        patch("app.routes.previews._render_teams_preview", new=dynamic),
    ):
        response = await previews.get_configure_preview_png("teams", request, display="bwr")
    assert response.media_type == "image/png"

    dynamic.side_effect = RuntimeError("failed")
    with (
        patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)),
        patch("app.routes.previews.enforce_rate_limit"),
        patch("app.routes.previews._render_teams_preview", new=dynamic),
        pytest.raises(HTTPException) as error,
    ):
        await previews.get_configure_preview_png("teams", request)
    assert error.value.status_code == 404

    with (
        patch("app.routes.previews.config.IMAGES_PATH", str(tmp_path)),
        pytest.raises(HTTPException) as error,
    ):
        await previews.get_configure_preview_png("calendar", request)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_service_worker_response_uses_no_cache():
    response = await previews.service_worker()

    assert response.media_type == "application/javascript"
    assert response.headers["cache-control"] == "no-cache"
