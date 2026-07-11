"""Tests for scheduler helpers."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from app.models import TeamEntry, TeamsData
from app.services import scheduler as scheduler_module
from app.services.historical_refresh import HistoricalRefreshResult
from app.services.image_keys import get_teams_image_key
from app.services.scheduler import (
    _atomic_write_bytes,
    _generate_teams_bmp_variants,
    _get_image_key,
    collect_and_generate,
    generate_preview_pngs,
)


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
async def test_generate_preview_pngs_converts_existing_bmps_without_rendering(
    tmp_path, monkeypatch
):
    buffer = BytesIO()
    Image.new("1", (800, 480), 1).save(buffer, format="BMP")
    bmp_data = buffer.getvalue()

    monkeypatch.setattr(scheduler_module, "SUPPORTED_LANGUAGES", ["en"])
    monkeypatch.setattr(scheduler_module.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(
        scheduler_module,
        "create_renderer",
        Mock(side_effect=AssertionError("preview generation must not render again")),
    )

    for display in ("1bit", "spectra6", "bwr", "bwry"):
        (tmp_path / f"{_get_image_key('en', display=display)}.bmp").write_bytes(bmp_data)
        teams_key = get_teams_image_key("en", 2026, display=display)
        (tmp_path / f"{teams_key}.bmp").write_bytes(bmp_data)

    await generate_preview_pngs(["off"], 2026)

    assert (tmp_path / "preview_calendar_en.png").is_file()
    assert (tmp_path / "configure_calendar_en_spectra6.png").is_file()
    assert (tmp_path / "preview_teams_en.png").is_file()
    assert (tmp_path / "configure_teams_en_bwry.png").is_file()


@pytest.mark.asyncio
async def test_generate_preview_pngs_skips_missing_bmp_sources(tmp_path, monkeypatch):
    run_render = AsyncMock()
    monkeypatch.setattr(scheduler_module, "SUPPORTED_LANGUAGES", ["en"])
    monkeypatch.setattr(scheduler_module.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(scheduler_module, "run_render", run_render)

    await generate_preview_pngs(["off"], 2026)

    run_render.assert_not_awaited()


def test_start_scheduler_registers_daily_historical_refresh(monkeypatch):
    added_jobs = []

    class FakeScheduler:
        @staticmethod
        def add_job(func, *, trigger, id, name, replace_existing):
            added_jobs.append(
                {
                    "func": func,
                    "trigger": trigger,
                    "id": id,
                    "name": name,
                    "replace_existing": replace_existing,
                }
            )

        @staticmethod
        def start():
            return None

    monkeypatch.setattr(scheduler_module, "scheduler", None)
    monkeypatch.setattr(scheduler_module.config, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler_module.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(scheduler_module, "AsyncIOScheduler", lambda **_kwargs: FakeScheduler())

    scheduler_module.start_scheduler()

    historical_job = next(
        (job for job in added_jobs if job["id"] == "historical_results_refresh"),
        None,
    )
    assert historical_job is not None
    assert historical_job["func"] is scheduler_module.refresh_historical_results
    assert historical_job["replace_existing"] is True


@pytest.mark.asyncio
async def test_atomic_write_bytes_uses_unique_temp_path_per_call(tmp_path, monkeypatch):
    """Concurrent writers to one target must not share the same temp filename."""
    replace_sources: list[str] = []

    def fake_replace(source, _target):
        replace_sources.append(source.name)
        source.unlink()

    monkeypatch.setattr("app.utils.atomic_io.os.replace", fake_replace)

    image_path = tmp_path / "calendar_en.bmp"
    await _atomic_write_bytes(image_path, b"first")
    await _atomic_write_bytes(image_path, b"second")

    assert len(set(replace_sources)) == 2


def test_directory_fsync_failure_is_tolerated(monkeypatch, tmp_path):
    """Filesystems without directory fsync support must still close the descriptor."""
    from app.utils import atomic_io

    closed: list[int] = []
    monkeypatch.setattr(atomic_io.os, "open", lambda *_args: 17)
    monkeypatch.setattr(
        atomic_io.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("unsupported"))
    )
    monkeypatch.setattr(atomic_io.os, "close", closed.append)

    atomic_io._fsync_directory(tmp_path)

    assert closed == [17]


@pytest.mark.asyncio
async def test_refresh_historical_results_defers_rendering_to_hourly_job(monkeypatch):
    calls = []
    db = SimpleNamespace(set_cache_meta=AsyncMock(), close=AsyncMock())

    async def fake_update_historical():
        calls.append("update")
        return HistoricalRefreshResult(("red_bull_ring",), (), 1)

    async def fake_collect_and_generate():
        calls.append("generate")

    monkeypatch.setattr(scheduler_module, "update_historical_results", fake_update_historical)
    monkeypatch.setattr(scheduler_module, "Database", lambda: db)
    monkeypatch.setattr(
        scheduler_module,
        "_collect_and_generate_unlocked",
        fake_collect_and_generate,
    )

    await scheduler_module.refresh_historical_results()

    assert calls == ["update"]
    db.set_cache_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_historical_results_does_not_stamp_incomplete_run(monkeypatch):
    db = SimpleNamespace(set_cache_meta=AsyncMock(), close=AsyncMock())

    async def fake_update_historical():
        return HistoricalRefreshResult((), ("monza",), 1)

    monkeypatch.setattr(scheduler_module, "update_historical_results", fake_update_historical)
    monkeypatch.setattr(scheduler_module, "Database", lambda: db)

    await scheduler_module.refresh_historical_results()

    db.set_cache_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_refresh_due_when_timestamp_is_missing(monkeypatch):
    db = SimpleNamespace(get_cache_meta=AsyncMock(return_value=None), close=AsyncMock())
    monkeypatch.setattr(scheduler_module, "Database", lambda: db)

    assert await scheduler_module._historical_refresh_is_due() is True
    db.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_historical_refresh_not_due_for_recent_timestamp(monkeypatch):
    timestamp = datetime.now(timezone.utc).isoformat()
    db = SimpleNamespace(get_cache_meta=AsyncMock(return_value=timestamp), close=AsyncMock())
    monkeypatch.setattr(scheduler_module, "Database", lambda: db)

    assert await scheduler_module._historical_refresh_is_due() is False


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


@pytest.mark.asyncio
async def test_collect_and_generate_skips_stale_prune_when_race_weather_missing(tmp_path):
    """A partial weather outage must not prune previously-good race-day BMP variants."""
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(),
        cleanup_old_stats=AsyncMock(),
        close=AsyncMock(),
    )
    race_dt = datetime.now(timezone.utc) + timedelta(days=2)
    race_data = {
        "race_name": "Test Grand Prix",
        "circuit": {"circuitId": "test", "lat": "50.0", "long": "14.0"},
        "schedule": [{"name": "Race", "datetime": race_dt.isoformat()}],
    }
    prune = Mock(return_value=0)

    with (
        patch("app.services.scheduler.Database", return_value=db),
        patch("app.services.scheduler.F1Service") as f1_service_cls,
        patch("app.services.scheduler._delete_stale_bmps", prune),
        patch("app.services.scheduler._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler._load_weather_context",
            new=AsyncMock(return_value=(object(), None, {"off": None, "current": object()})),
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
        patch("app.services.scheduler.config.WEATHER_ENABLED", True),
    ):
        f1_service_cls.return_value.get_next_race_from_static.return_value = race_data
        await collect_and_generate()

    prune.assert_not_called()


@pytest.mark.asyncio
async def test_generate_teams_bmp_variants_treats_incomplete_standings_as_failure(tmp_path):
    """Scheduler should keep old teams BMPs when standings enrichment was degraded."""
    db = SimpleNamespace(save_generated_image=AsyncMock())
    degraded_teams = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Test Team")],
        standings_complete=False,
    )

    with (
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.teams_service.TeamsService") as teams_service_cls,
        patch("app.services.scheduler.run_render", new=AsyncMock(return_value=b"BM")),
    ):
        teams_service_cls.return_value.get_teams_and_drivers = AsyncMock(
            return_value=degraded_teams
        )

        generated, failures = await _generate_teams_bmp_variants(images_dir=tmp_path, db=db)

    assert generated == set()
    assert failures == 1
    db.save_generated_image.assert_not_awaited()
