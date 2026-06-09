"""Tests for scheduler helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.image_keys import get_teams_image_key
from app.services.scheduler import _get_image_key, collect_and_generate


def test_get_image_key_uses_bwr_suffix():
    assert _get_image_key("en", display="bwr") == "calendar_en_bwr"


def test_get_image_key_uses_bwry_suffix():
    assert _get_image_key("en", display="bwry") == "calendar_en_bwry"


def test_get_image_key_uses_bwr_suffix_with_timezone_and_weather():
    assert (
        _get_image_key("cs", tz="America/New_York", display="bwr", weather="race")
        == "calendar_cs_America_New_York_bwr_weather_race"
    )


def test_get_image_key_uses_bwry_suffix_with_timezone_and_weather():
    assert (
        _get_image_key("cs", tz="America/New_York", display="bwry", weather="race")
        == "calendar_cs_America_New_York_bwry_weather_race"
    )


def test_get_teams_image_key_includes_year():
    assert get_teams_image_key("en", 2026, display="bwr") == "teams_2026_en_bwr"


def test_get_teams_image_key_rejects_unknown_display():
    with pytest.raises(ValueError, match="Unsupported display mode: invalid"):
        get_teams_image_key("en", 2026, display="invalid")


@pytest.mark.asyncio
async def test_collect_and_generate_uses_configured_stats_retention(tmp_path):
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(),
        cleanup_old_stats=AsyncMock(),
        close=AsyncMock(),
    )
    race_data = {"race_name": "Test Grand Prix"}

    with (
        patch("app.services.scheduler.Database", return_value=db),
        patch("app.services.scheduler.F1Service") as f1_service_cls,
        patch("app.services.scheduler._delete_stale_bmps", return_value=0),
        patch("app.services.scheduler._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler._load_weather_context",
            new=AsyncMock(return_value=(None, None, {"off": None})),
        ),
        patch(
            "app.services.scheduler._generate_base_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler.generate_preview_pngs", new=AsyncMock()),
        patch(
            "app.services.scheduler._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler.clear_bmp_cache"),
        patch("app.services.scheduler.config.IMAGES_PATH", str(tmp_path / "images")),
        patch("app.services.scheduler.config.STATS_RETENTION_DAYS", 400),
    ):
        f1_service_cls.return_value.get_next_race_from_static.return_value = race_data
        await collect_and_generate()

    db.cleanup_old_stats.assert_awaited_once_with(days=400)


@pytest.mark.asyncio
async def test_collect_and_generate_skips_stats_cleanup_when_retention_disabled(tmp_path):
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(),
        cleanup_old_stats=AsyncMock(),
        close=AsyncMock(),
    )
    race_data = {"race_name": "Test Grand Prix"}

    with (
        patch("app.services.scheduler.Database", return_value=db),
        patch("app.services.scheduler.F1Service") as f1_service_cls,
        patch("app.services.scheduler._delete_stale_bmps", return_value=0),
        patch("app.services.scheduler._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler._load_weather_context",
            new=AsyncMock(return_value=(None, None, {"off": None})),
        ),
        patch(
            "app.services.scheduler._generate_base_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler.generate_preview_pngs", new=AsyncMock()),
        patch(
            "app.services.scheduler._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler.clear_bmp_cache"),
        patch("app.services.scheduler.config.IMAGES_PATH", str(tmp_path / "images")),
        patch("app.services.scheduler.config.STATS_RETENTION_DAYS", 0),
    ):
        f1_service_cls.return_value.get_next_race_from_static.return_value = race_data
        await collect_and_generate()

    db.cleanup_old_stats.assert_not_awaited()
