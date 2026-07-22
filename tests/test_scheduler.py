"""Tests for scheduler helpers."""

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from PIL import Image

from app.models import TeamEntry, TeamsData
from app.services import scheduler, scheduler_generation, scheduler_maintenance
from app.services import scheduler_weather as weather_jobs
from app.services.historical_refresh import HistoricalRefreshResult
from app.services.image_keys import get_teams_image_key
from app.services.scheduler_generation import (
    _atomic_write_bytes,
    _generate_teams_bmp_variants,
    _get_image_key,
    collect_and_generate,
    generate_preview_pngs,
)
from app.services.weather_service import WeatherData
from app.utils.etag import etag_sidecar_path, read_etag_sidecar, strong_etag


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
async def test_base_generation_uses_bounded_render_pool_concurrency(tmp_path, monkeypatch):
    active = 0
    max_active = 0
    calls = 0
    first_pair_started = asyncio.Event()

    async def fake_generate(*_args):
        nonlocal active, max_active, calls
        calls += 1
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            first_pair_started.set()
        await first_pair_started.wait()
        await asyncio.sleep(0)
        active -= 1
        return tmp_path / f"variant-{calls}.bmp"

    monkeypatch.setattr(scheduler_generation, "SUPPORTED_LANGUAGES", ["en", "cs"])
    monkeypatch.setattr(scheduler_generation, "RENDER_WORKER_COUNT", 2)
    monkeypatch.setattr(scheduler_generation, "_generate_variant", fake_generate)

    _paths, failures = await scheduler_generation._generate_base_variants(
        images_dir=tmp_path,
        db=object(),
        race_data={},
        historical_data=None,
        display_types=("1bit", "bwr"),
        weather_by_type={"off": None},
    )

    assert calls == 4
    assert max_active == 2
    assert failures == 0


@pytest.mark.asyncio
async def test_generate_preview_pngs_converts_existing_bmps_without_rendering(
    tmp_path, monkeypatch
):
    buffer = BytesIO()
    Image.new("1", (800, 480), 1).save(buffer, format="BMP")
    bmp_data = buffer.getvalue()

    monkeypatch.setattr(scheduler_generation, "SUPPORTED_LANGUAGES", ["en"])
    monkeypatch.setattr(scheduler_generation.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(
        scheduler_generation,
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
    monkeypatch.setattr(scheduler_generation, "SUPPORTED_LANGUAGES", ["en"])
    monkeypatch.setattr(scheduler_generation.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(scheduler_generation, "run_render", run_render)

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

    monkeypatch.setattr(scheduler, "scheduler", None)
    monkeypatch.setattr(scheduler.config, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", lambda **_kwargs: FakeScheduler())

    scheduler.start_scheduler()

    historical_job = next(
        (job for job in added_jobs if job["id"] == "historical_results_refresh"),
        None,
    )
    assert historical_job is not None
    assert historical_job["func"] is scheduler_maintenance.refresh_historical_results
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

    monkeypatch.setattr(scheduler_maintenance, "update_historical_results", fake_update_historical)
    monkeypatch.setattr(scheduler_maintenance, "get_database", lambda: db)

    await scheduler_maintenance.refresh_historical_results()

    assert calls == ["update"]
    db.set_cache_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_historical_results_does_not_stamp_incomplete_run(monkeypatch):
    db = SimpleNamespace(set_cache_meta=AsyncMock(), close=AsyncMock())

    async def fake_update_historical():
        return HistoricalRefreshResult((), ("monza",), 1)

    monkeypatch.setattr(scheduler_maintenance, "update_historical_results", fake_update_historical)
    monkeypatch.setattr(scheduler_maintenance, "get_database", lambda: db)

    await scheduler_maintenance.refresh_historical_results()

    db.set_cache_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_historical_refresh_due_when_timestamp_is_missing(monkeypatch):
    db = SimpleNamespace(get_cache_meta=AsyncMock(return_value=None), close=AsyncMock())
    monkeypatch.setattr(scheduler_maintenance, "get_database", lambda: db)

    assert await scheduler_maintenance._historical_refresh_is_due() is True


@pytest.mark.asyncio
async def test_historical_refresh_not_due_for_recent_timestamp(monkeypatch):
    timestamp = datetime.now(timezone.utc).isoformat()
    db = SimpleNamespace(get_cache_meta=AsyncMock(return_value=timestamp), close=AsyncMock())
    monkeypatch.setattr(scheduler_maintenance, "get_database", lambda: db)

    assert await scheduler_maintenance._historical_refresh_is_due() is False


@pytest.mark.asyncio
async def test_collect_and_generate_uses_configured_stats_retention(tmp_path):
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(),
        cleanup_old_stats=AsyncMock(),
        close=AsyncMock(),
    )
    race_data = {"race_name": "Test Grand Prix"}

    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service_cls,
        patch("app.services.scheduler_generation._delete_stale_bmps", return_value=0),
        patch("app.services.scheduler_generation._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(return_value=(None, None, {"off": None})),
        ),
        patch(
            "app.services.scheduler_generation._generate_base_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=AsyncMock()),
        patch(
            "app.services.scheduler_generation._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path / "images")),
        patch("app.services.scheduler_generation.config.STATS_RETENTION_DAYS", 400),
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
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service_cls,
        patch("app.services.scheduler_generation._delete_stale_bmps", return_value=0),
        patch("app.services.scheduler_generation._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(return_value=(None, None, {"off": None})),
        ),
        patch(
            "app.services.scheduler_generation._generate_base_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=AsyncMock()),
        patch(
            "app.services.scheduler_generation._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path / "images")),
        patch("app.services.scheduler_generation.config.STATS_RETENTION_DAYS", 0),
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
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service_cls,
        patch("app.services.scheduler_generation._delete_stale_bmps", prune),
        patch("app.services.scheduler_generation._load_historical_data", return_value={}),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(return_value=(object(), None, {"off": None, "current": object()})),
        ),
        patch(
            "app.services.scheduler_generation._generate_base_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=AsyncMock()),
        patch(
            "app.services.scheduler_generation._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path / "images")),
        patch("app.services.scheduler_generation.config.STATS_RETENTION_DAYS", 0),
        patch("app.services.scheduler_generation.config.WEATHER_ENABLED", True),
    ):
        f1_service_cls.return_value.get_next_race_from_static.return_value = race_data
        await collect_and_generate()

    prune.assert_not_called()
    db.set_cache_meta.assert_not_awaited()


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
        patch("app.services.scheduler_generation.run_render", new=AsyncMock(return_value=b"BM")),
    ):
        teams_service_cls.return_value.get_teams_and_drivers = AsyncMock(
            return_value=degraded_teams
        )

        generated, failures = await _generate_teams_bmp_variants(images_dir=tmp_path, db=db)

    assert generated == set()
    assert failures == 1
    db.save_generated_image.assert_not_awaited()


# Extended behavioral coverage for scheduler jobs and helpers.


@pytest.mark.asyncio
async def test_scheduler_locks_are_reused_within_event_loop():
    from app.utils.async_locks import get_loop_lock

    assert get_loop_lock(scheduler_generation._generation_locks) is get_loop_lock(
        scheduler_generation._generation_locks
    )
    assert get_loop_lock(weather_jobs._weather_fetch_locks) is get_loop_lock(
        weather_jobs._weather_fetch_locks
    )
    assert get_loop_lock(scheduler_maintenance._historical_refresh_locks) is get_loop_lock(
        scheduler_maintenance._historical_refresh_locks
    )


def test_render_variant_helpers_construct_renderer_in_calling_thread():
    renderer = SimpleNamespace(
        render_calendar=Mock(return_value=b"calendar"),
        render_teams_drivers=Mock(return_value=b"teams"),
    )
    with (
        patch(
            "app.services.scheduler_generation.get_translator", return_value="translator"
        ) as translator,
        patch("app.services.scheduler_generation.create_renderer", return_value=renderer) as create,
    ):
        assert (
            scheduler_generation._render_calendar_variant_bytes(
                "en", "1bit", {"race": 1}, {"history": 1}, None, "off"
            )
            == b"calendar"
        )
        assert (
            scheduler_generation._render_teams_variant_bytes("cs", "bwr", {"teams": 1}) == b"teams"
        )

    assert translator.call_args_list == [call("en"), call("cs")]
    assert create.call_args_list == [
        call("1bit", "translator", "en"),
        call("bwr", "translator", "cs"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", ["calendar", "teams"])
async def test_preview_generation_isolates_conversion_failures(tmp_path, screen):
    source = tmp_path / ("calendar_en.bmp" if screen == "calendar" else "teams_2026_en.bmp")
    source.write_bytes(b"not-used")
    with (
        patch("app.services.scheduler_generation.SUPPORTED_LANGUAGES", ["en"]),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path)),
        patch(
            "app.services.scheduler_generation.run_render",
            new=AsyncMock(side_effect=RuntimeError("bad bmp")),
        ),
        patch("app.services.scheduler_generation._atomic_write_bytes", new=AsyncMock()) as write,
    ):
        await scheduler_generation.generate_preview_pngs(["off"], 2026)

    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_variant_persists_success_and_returns_none_on_failure(tmp_path):
    db = SimpleNamespace(save_generated_image=AsyncMock())
    with (
        patch("app.services.scheduler_generation.run_render", new=AsyncMock(return_value=b"bmp")),
        patch("app.services.scheduler_generation._write_bmp_artifact", new=AsyncMock()) as write,
    ):
        image_path = await scheduler_generation._generate_variant(
            tmp_path,
            db,
            {"race": 1},
            None,
            WeatherData(20.0, 1, 0),
            "en",
            None,
            "1bit",
            "off",
        )

    assert image_path == tmp_path / "calendar_en.bmp"
    write.assert_awaited_once_with(image_path, b"bmp")
    db.save_generated_image.assert_awaited_once()

    with patch(
        "app.services.scheduler_generation.run_render",
        new=AsyncMock(side_effect=RuntimeError("render failed")),
    ):
        assert (
            await scheduler_generation._generate_variant(
                tmp_path, db, {}, None, None, "en", None, "1bit", "off"
            )
            is None
        )


def test_delete_stale_bmps_keeps_only_current_outputs(tmp_path):
    keep = tmp_path / "keep.bmp"
    stale = tmp_path / "stale.bmp"
    other = tmp_path / "not-an-image.txt"
    for path in (keep, stale, other):
        path.touch()
    keep_sidecar = etag_sidecar_path(keep)
    orphan_sidecar = etag_sidecar_path(tmp_path / "orphan.bmp")
    keep_sidecar.touch()
    orphan_sidecar.touch()

    assert scheduler_generation._delete_stale_bmps(tmp_path, keep={keep}) == 1
    assert keep.exists()
    assert keep_sidecar.exists()
    assert not orphan_sidecar.exists()
    assert not stale.exists()
    assert other.exists()


@pytest.mark.parametrize(
    ("race_data", "historical", "expected"),
    [
        ({}, None, None),
        ({"circuit": {"circuitId": "missing"}}, None, None),
    ],
)
def test_load_historical_data_handles_missing_values(race_data, historical, expected):
    with patch(
        "app.services.scheduler_generation.F1Service.get_historical_from_static",
        return_value=historical,
    ):
        assert scheduler_generation._load_historical_data(race_data) is expected


@pytest.mark.parametrize("is_new_track", [True, False])
def test_load_historical_data_returns_available_result(is_new_track):
    historical = SimpleNamespace(is_new_track=is_new_track, season=2024)
    with patch(
        "app.services.scheduler_generation.F1Service.get_historical_from_static",
        return_value=historical,
    ):
        assert (
            scheduler_generation._load_historical_data({"circuit": {"circuitId": "monza"}})
            is historical
        )


@pytest.mark.asyncio
async def test_load_weather_context_returns_service_result():
    current = WeatherData(20.0, 1, 0)
    race = WeatherData(22.0, 2, 10)
    result = (current, race, {"off": None, "current": current, "race": race})
    with patch(
        "app.services.scheduler_generation.get_weather_context", new=AsyncMock(return_value=result)
    ):
        assert (
            await scheduler_generation._load_weather_context({"circuit": {"circuitId": "monza"}})
            == result
        )

    empty_result = (None, None, {"off": None})
    with patch(
        "app.services.scheduler_generation.get_weather_context",
        new=AsyncMock(return_value=empty_result),
    ):
        assert await scheduler_generation._load_weather_context({"circuit": {}}) == empty_result


@pytest.mark.parametrize(
    ("circuit", "expected"),
    [
        ({"lat": "45", "long": "9"}, True),
        ({"lat": "45", "lon": "9"}, True),
        ({"lat": "91", "long": "9"}, False),
        ({"lat": "45", "long": "181"}, False),
        ({"lat": "invalid", "long": "9"}, False),
    ],
)
def test_has_weather_coordinates_validates_bounds_and_alias(circuit, expected):
    assert scheduler_generation._has_weather_coordinates({"circuit": circuit}) is expected


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        ([], None),
        ([{"name": "Race"}], None),
        ([{"name": "Race", "datetime": "invalid"}], None),
        (
            [{"name": "Race", "datetime": "2026-07-12T14:00:00"}],
            datetime(2026, 7, 12, 14, tzinfo=timezone.utc),
        ),
        (
            [{"name": "race", "datetime": "2026-07-12T16:00:00+02:00"}],
            datetime(2026, 7, 12, 14, tzinfo=timezone.utc),
        ),
    ],
)
def test_parse_race_datetime_utc(schedule, expected):
    assert scheduler_generation._parse_race_datetime_utc({"schedule": schedule}) == expected


def test_race_weather_expected_for_past_near_and_far_dates():
    now = datetime.now(timezone.utc)

    def race_at(value):
        return {"schedule": [{"name": "Race", "datetime": value.isoformat()}]}

    assert scheduler_generation._race_weather_expected({}) is False
    assert scheduler_generation._race_weather_expected(race_at(now - timedelta(days=1))) is True
    assert scheduler_generation._race_weather_expected(race_at(now + timedelta(days=2))) is True
    assert scheduler_generation._race_weather_expected(race_at(now + timedelta(days=20))) is False


def test_weather_context_degradation_requires_enabled_expected_weather():
    race_data = {
        "circuit": {"lat": "45", "long": "9"},
        "schedule": [
            {
                "name": "Race",
                "datetime": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            }
        ],
    }
    current = WeatherData(20.0, 1, 0)
    race = WeatherData(21.0, 2, 0)
    with patch("app.services.scheduler_generation.config.WEATHER_ENABLED", False):
        assert scheduler_generation._weather_context_degraded(race_data, {}) is False
    with patch("app.services.scheduler_generation.config.WEATHER_ENABLED", True):
        assert scheduler_generation._weather_context_degraded({}, {}) is False
        assert (
            scheduler_generation._weather_context_degraded(race_data, {"current": current}) is True
        )
        assert (
            scheduler_generation._weather_context_degraded(
                race_data, {"current": current, "race": race}
            )
            is False
        )
        far_race = {
            **race_data,
            "schedule": [
                {
                    "name": "Race",
                    "datetime": (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(),
                }
            ],
        }
        assert (
            scheduler_generation._weather_context_degraded(far_race, {"current": current}) is False
        )


@pytest.mark.asyncio
async def test_generate_base_variants_counts_successes_and_failures(tmp_path):
    success = tmp_path / "ok.bmp"
    with (
        patch("app.services.scheduler_generation.SUPPORTED_LANGUAGES", ["en"]),
        patch(
            "app.services.scheduler_generation._generate_variant",
            new=AsyncMock(side_effect=[success, None]),
        ) as generate,
    ):
        paths, failures = await scheduler_generation._generate_base_variants(
            images_dir=tmp_path,
            db=object(),
            race_data={},
            historical_data=None,
            display_types=["1bit"],
            weather_by_type={"off": None, "current": WeatherData(20.0, 1, 0)},
        )

    assert paths == {success}
    assert failures == 1
    assert generate.await_count == 2


@pytest.mark.asyncio
async def test_generate_popular_timezone_variants_handles_all_input_classes(tmp_path):
    db = SimpleNamespace(
        get_popular_tz_variants=AsyncMock(
            return_value=[
                {"lang": "xx", "tz": "UTC"},
                {"lang": "en", "tz": "Bad/Zone"},
                {"lang": "en", "tz": "Europe/Prague"},
            ]
        )
    )
    success = tmp_path / "ok.bmp"

    def convert(data, timezone_name):
        if timezone_name == "Bad/Zone":
            raise ValueError("invalid zone")
        return {**data, "tz": timezone_name}

    with (
        patch("app.services.scheduler_generation.SUPPORTED_LANGUAGES", ["en"]),
        patch(
            "app.services.scheduler_generation.convert_race_times_to_timezone", side_effect=convert
        ),
        patch(
            "app.services.scheduler_generation._generate_variant",
            new=AsyncMock(side_effect=[success, None]),
        ),
    ):
        paths, failures = await scheduler_generation._generate_popular_tz_variants(
            images_dir=tmp_path,
            db=db,
            race_data={"race": 1},
            historical_data=None,
            display_types=["1bit"],
            weather_by_type={"off": None, "current": WeatherData(20.0, 1, 0)},
        )

    assert paths == {success}
    assert failures == 2


@pytest.mark.asyncio
async def test_generate_popular_timezone_variants_returns_empty_without_demand(tmp_path):
    db = SimpleNamespace(get_popular_tz_variants=AsyncMock(return_value=[]))

    assert await scheduler_generation._generate_popular_tz_variants(
        images_dir=tmp_path,
        db=db,
        race_data={},
        historical_data=None,
        display_types=["1bit"],
        weather_by_type={"off": None},
    ) == (set(), 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("teams_data", [None, TeamsData(season=2026, teams=[])])
async def test_generate_teams_variants_handles_fetch_failure_and_empty_data(tmp_path, teams_data):
    service = MagicMock()
    if teams_data is None:
        service.get_teams_and_drivers = AsyncMock(side_effect=RuntimeError("upstream failed"))
    else:
        service.get_teams_and_drivers = AsyncMock(return_value=teams_data)
    with (
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.teams_service.TeamsService", return_value=service),
    ):
        assert await scheduler_generation._generate_teams_bmp_variants(
            images_dir=tmp_path, db=object()
        ) == (
            set(),
            1,
        )


@pytest.mark.asyncio
async def test_generate_teams_variants_persists_success_and_counts_render_failure(tmp_path):
    teams_data = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Test Team")],
        standings_complete=True,
    )
    service = SimpleNamespace(get_teams_and_drivers=AsyncMock(return_value=teams_data))
    db = SimpleNamespace(save_generated_image=AsyncMock())
    write = AsyncMock()
    with (
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.teams_service.TeamsService", return_value=service),
        patch("app.services.teams_service.is_teams_data_cacheable", return_value=True),
        patch("app.services.scheduler_generation.SUPPORTED_LANGUAGES", ["en"]),
        patch(
            "app.services.scheduler_generation.run_render",
            new=AsyncMock(side_effect=[b"1", RuntimeError("render"), b"3", b"4"]),
        ),
        patch("app.services.scheduler_generation._write_bmp_artifact", new=write),
    ):
        paths, failures = await scheduler_generation._generate_teams_bmp_variants(
            images_dir=tmp_path, db=db
        )

    assert failures == 1
    assert len(paths) == 3
    assert write.await_count == 3
    assert db.save_generated_image.await_count == 3


@pytest.mark.asyncio
async def test_write_bmp_artifact_writes_matching_etag_sidecar(tmp_path):
    image_path = tmp_path / "calendar_en.bmp"

    etag = await scheduler_generation._write_bmp_artifact(image_path, b"bmp")

    assert image_path.read_bytes() == b"bmp"
    assert etag == strong_etag(b"bmp")
    assert etag_sidecar_path(image_path).exists()
    assert read_etag_sidecar(image_path) == etag


@pytest.mark.asyncio
async def test_collect_and_generate_handles_no_race_and_database_failure(tmp_path):
    db = SimpleNamespace(close=AsyncMock())
    f1 = SimpleNamespace(get_next_race_from_static=Mock(return_value=None))
    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service", return_value=f1),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path)),
    ):
        await scheduler_generation._collect_and_generate_unlocked()

    with patch(
        "app.services.scheduler_generation.get_database", side_effect=RuntimeError("db failed")
    ):
        await scheduler_generation._collect_and_generate_unlocked()


@pytest.mark.asyncio
async def test_collect_and_generate_prunes_after_fully_successful_run(tmp_path):
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(), cleanup_old_stats=AsyncMock(), close=AsyncMock()
    )
    keep = tmp_path / "keep.bmp"
    stale = tmp_path / "stale.bmp"
    keep.touch()
    stale.touch()
    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service,
        patch("app.services.scheduler_generation._load_historical_data", return_value=None),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(return_value=(None, None, {"off": None})),
        ),
        patch("app.services.scheduler_generation._weather_context_degraded", return_value=False),
        patch(
            "app.services.scheduler_generation._generate_base_variants",
            new=AsyncMock(return_value=({keep}, 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=AsyncMock()),
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path)),
        patch("app.services.scheduler_generation.config.STATS_RETENTION_DAYS", 0),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
    ):
        f1_service.return_value.get_next_race_from_static.return_value = {"race_name": "Test"}
        await scheduler_generation._collect_and_generate_unlocked()

    assert keep.exists()
    assert not stale.exists()
    db.set_cache_meta.assert_awaited_once()


@pytest.mark.asyncio
async def test_flush_api_calls_handles_empty_success_failure_and_cancellation():
    with patch(
        "app.services.scheduler_maintenance.get_and_clear_api_calls_buffer", return_value=[]
    ):
        await scheduler_maintenance.flush_api_calls_to_db()

    calls = [{"path": "/"}]
    db = SimpleNamespace(save_api_calls_batch=AsyncMock(return_value=1), close=AsyncMock())
    with (
        patch(
            "app.services.scheduler_maintenance.get_and_clear_api_calls_buffer", return_value=calls
        ),
        patch("app.services.scheduler_maintenance.get_database", return_value=db),
    ):
        await scheduler_maintenance.flush_api_calls_to_db()

    requeue = Mock()
    db = SimpleNamespace(
        save_api_calls_batch=AsyncMock(side_effect=RuntimeError("write failed")),
        close=AsyncMock(side_effect=RuntimeError("close failed")),
    )
    with (
        patch(
            "app.services.scheduler_maintenance.get_and_clear_api_calls_buffer", return_value=calls
        ),
        patch("app.services.scheduler_maintenance.get_database", return_value=db),
        patch("app.services.scheduler_maintenance.requeue_api_calls", requeue),
    ):
        await scheduler_maintenance.flush_api_calls_to_db()
    requeue.assert_called_once_with(calls)

    with (
        patch(
            "app.services.scheduler_maintenance.get_and_clear_api_calls_buffer", return_value=calls
        ),
        patch(
            "app.services.scheduler_maintenance.get_database",
            side_effect=RuntimeError("open failed"),
        ),
        patch("app.services.scheduler_maintenance.requeue_api_calls", requeue),
    ):
        await scheduler_maintenance.flush_api_calls_to_db()

    db = SimpleNamespace(
        save_api_calls_batch=AsyncMock(side_effect=asyncio.CancelledError), close=AsyncMock()
    )
    with (
        patch(
            "app.services.scheduler_maintenance.get_and_clear_api_calls_buffer", return_value=calls
        ),
        patch("app.services.scheduler_maintenance.get_database", return_value=db),
        patch("app.services.scheduler_maintenance.requeue_api_calls", requeue),
        pytest.raises(asyncio.CancelledError),
    ):
        await scheduler_maintenance.flush_api_calls_to_db()


@pytest.mark.asyncio
async def test_fetch_single_circuit_weather_returns_data_or_none():
    weather_data = WeatherData(20.0, 1, 0)
    service = SimpleNamespace(get_current_weather=AsyncMock(return_value=weather_data))
    assert await weather_jobs._fetch_single_circuit_weather(service, 1.0, 2.0) is weather_data

    service.get_current_weather.side_effect = RuntimeError("weather failed")
    assert await weather_jobs._fetch_single_circuit_weather(service, 1.0, 2.0) is None


@pytest.mark.asyncio
async def test_fetch_all_circuit_weather_disabled_empty_and_outer_failure():
    with patch("app.services.scheduler_weather.config.WEATHER_ENABLED", False):
        await weather_jobs._fetch_all_circuits_weather_unlocked()

    db = SimpleNamespace(close=AsyncMock())
    f1 = SimpleNamespace(get_all_races_from_static=Mock(side_effect=[[], []]))
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch("app.services.scheduler_weather.F1Service", return_value=f1),
        patch("app.services.scheduler_weather.WeatherService"),
    ):
        await weather_jobs._fetch_all_circuits_weather_unlocked()

    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", side_effect=RuntimeError("db failed")),
    ):
        await weather_jobs._fetch_all_circuits_weather_unlocked()


@pytest.mark.asyncio
async def test_fetch_all_circuit_weather_retries_and_persists_results():
    weather = WeatherData(20.0, 1, 25)
    races = [
        {"circuit": {}},
        {"circuit": {"circuitId": "missing", "name": "Missing"}},
        {"circuit": {"circuitId": "bad-lat", "lat": "north", "long": "2"}},
        {"circuit": {"circuitId": "bad-lon", "lat": "1", "long": object()}},
        {"circuit": {"circuitId": "a", "name": "A", "lat": "1", "long": "2"}},
        {"circuit": {"circuitId": "a", "name": "Duplicate", "lat": "1", "long": "2"}},
        {"circuit": {"circuitId": "b", "name": "B", "lat": "3", "long": "4"}},
    ]
    db = SimpleNamespace(save_circuit_weather=AsyncMock(), close=AsyncMock())
    f1 = SimpleNamespace(get_all_races_from_static=Mock(return_value=races))
    fetch = AsyncMock(side_effect=[weather, None, None, None])
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch("app.services.scheduler_weather.F1Service", return_value=f1),
        patch("app.services.scheduler_weather.WeatherService"),
        patch("app.services.scheduler_weather._fetch_single_circuit_weather", new=fetch),
        patch("app.services.scheduler_weather.set_cached_circuit_weather") as set_cached,
        patch("app.services.scheduler_weather.asyncio.sleep", new=AsyncMock()),
    ):
        await weather_jobs._fetch_all_circuits_weather_unlocked()

    assert fetch.await_count == 4
    set_cached.assert_called_once_with("a", weather)
    db.save_circuit_weather.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_all_circuit_weather_retry_can_recover():
    weather = WeatherData(20.0, 1, 25)
    race = {"circuit": {"circuitId": "a", "name": "A", "lat": "1", "long": "2"}}
    db = SimpleNamespace(save_circuit_weather=AsyncMock(), close=AsyncMock())
    f1 = SimpleNamespace(get_all_races_from_static=Mock(return_value=[race]))
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch("app.services.scheduler_weather.F1Service", return_value=f1),
        patch("app.services.scheduler_weather.WeatherService"),
        patch(
            "app.services.scheduler_weather._fetch_single_circuit_weather",
            new=AsyncMock(side_effect=[None, weather]),
        ),
        patch("app.services.scheduler_weather.set_cached_circuit_weather"),
        patch("app.services.scheduler_weather.asyncio.sleep", new=AsyncMock()),
    ):
        await weather_jobs._fetch_all_circuits_weather_unlocked()

    db.save_circuit_weather.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_all_wrapper_respects_disabled_locked_and_unlocked_states():
    run = AsyncMock()
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", False),
        patch("app.services.scheduler_weather._fetch_all_circuits_weather_unlocked", new=run),
    ):
        await weather_jobs.fetch_all_circuits_weather()
    run.assert_not_awaited()

    lock = asyncio.Lock()
    await lock.acquire()
    try:
        with (
            patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
            patch("app.services.scheduler_weather.get_loop_lock", return_value=lock),
            patch("app.services.scheduler_weather._fetch_all_circuits_weather_unlocked", new=run),
        ):
            await weather_jobs.fetch_all_circuits_weather()
    finally:
        lock.release()
    run.assert_not_awaited()

    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_loop_lock", return_value=lock),
        patch("app.services.scheduler_weather._fetch_all_circuits_weather_unlocked", new=run),
    ):
        await weather_jobs.fetch_all_circuits_weather()
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_weather_from_db_covers_disabled_empty_loaded_and_error_paths():
    with patch("app.services.scheduler_weather.config.WEATHER_ENABLED", False):
        await weather_jobs.load_weather_from_db()

    db = SimpleNamespace(load_all_circuit_weather=AsyncMock(return_value={}), close=AsyncMock())
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch(
            "app.services.scheduler_weather.load_prefetched_weather_from_db",
            new=AsyncMock(return_value=0),
        ),
    ):
        await weather_jobs.load_weather_from_db()

    db = SimpleNamespace(
        load_all_circuit_weather=AsyncMock(return_value={"monza": {"temperature_c": 20}}),
        close=AsyncMock(),
    )
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch("app.services.scheduler_weather.load_circuit_weather_to_cache", return_value=1),
        patch(
            "app.services.scheduler_weather.load_prefetched_weather_from_db",
            new=AsyncMock(return_value=2),
        ),
    ):
        await weather_jobs.load_weather_from_db()

    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", side_effect=RuntimeError("db failed")),
    ):
        await weather_jobs.load_weather_from_db()


@pytest.mark.asyncio
async def test_prefetch_weather_covers_disabled_result_empty_cleanup_and_error():
    with patch("app.services.scheduler_weather.config.WEATHER_ENABLED", False):
        await weather_jobs.prefetch_weather()

    weather_data = WeatherData(20.0, 1, 0)
    for value, deleted in ((weather_data, 2), (None, 0)):
        db = SimpleNamespace(
            cleanup_expired_weather_cache=AsyncMock(return_value=deleted), close=AsyncMock()
        )
        with (
            patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
            patch("app.services.scheduler_weather.get_database", return_value=db),
            patch(
                "app.services.scheduler_weather.prefetch_weather_for_next_race",
                new=AsyncMock(return_value=value),
            ),
        ):
            await weather_jobs.prefetch_weather()

    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.get_database", side_effect=RuntimeError("db failed")),
    ):
        await weather_jobs.prefetch_weather()


def test_run_backup_honors_configuration():
    with (
        patch("app.services.backup.is_backup_configured", return_value=False),
        patch("app.services.backup.perform_backup") as perform,
    ):
        scheduler._run_backup()
    perform.assert_not_called()

    with (
        patch("app.services.backup.is_backup_configured", return_value=True),
        patch("app.services.backup.perform_backup") as perform,
    ):
        scheduler._run_backup()
    perform.assert_called_once()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1-5", "mon,tue,wed,thu,fri"),
        ("*/2", "sun,tue,thu,sat"),
        ("mon-fri", "mon,tue,wed,thu,fri"),
        ("1,3,7", "mon,wed,sun"),
        ("1-5/2", "mon,wed,fri"),
        ("0-4", "sun,mon,tue,wed,thu"),
        ("0-6", "sun,mon,tue,wed,thu,fri,sat"),
        ("5-0", "fri,sat,sun"),
        ("0", "sun"),
        ("0-6/2", "sun,tue,thu,sat"),
        ("0/2", "sun,tue,thu,sat"),
        ("monday", "mon"),
        ("1,1-2", "mon,tue"),
    ],
)
def test_normalize_cron_day_of_week(value, expected):
    assert scheduler._normalize_cron_day_of_week(value) == expected


def test_normalize_cron_day_of_week_rejects_invalid_value():
    for value in ("", "8", "1/0", "1-2-3", "mondayjunk"):
        with pytest.raises(ValueError, match="invalid day-of-week"):
            scheduler._normalize_cron_day_of_week(value)


def test_register_backup_job_handles_disabled_invalid_and_valid_configuration():
    sched = SimpleNamespace(add_job=Mock())
    with patch("app.services.backup.is_backup_configured", return_value=False):
        scheduler._register_backup_job(sched)
    sched.add_job.assert_not_called()

    with (
        patch("app.services.backup.is_backup_configured", return_value=True),
        patch("app.services.scheduler.config.BACKUP_CRON", "invalid"),
        pytest.raises(ValueError, match="Invalid BACKUP_CRON"),
    ):
        scheduler._register_backup_job(sched)
    sched.add_job.assert_not_called()

    with (
        patch("app.services.backup.is_backup_configured", return_value=True),
        patch("app.services.scheduler.config.BACKUP_CRON", "0 3 * * 1"),
    ):
        scheduler._register_backup_job(sched)
    sched.add_job.assert_called_once()
    assert sched.add_job.call_args.kwargs["id"] == "s3_backup"


@pytest.mark.asyncio
async def test_refresh_historical_results_handles_lock_no_changes_update_and_persist_errors():
    lock = asyncio.Lock()
    await lock.acquire()
    try:
        with patch("app.services.scheduler_maintenance.get_loop_lock", return_value=lock):
            await scheduler_maintenance.refresh_historical_results()
    finally:
        lock.release()

    completed = HistoricalRefreshResult((), (), 1)
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(side_effect=RuntimeError("write")), close=AsyncMock()
    )
    with (
        patch("app.services.scheduler_maintenance.get_loop_lock", return_value=lock),
        patch(
            "app.services.scheduler_maintenance.update_historical_results",
            new=AsyncMock(return_value=completed),
        ),
        patch("app.services.scheduler_maintenance.get_database", return_value=db),
    ):
        await scheduler_maintenance.refresh_historical_results()

    with (
        patch("app.services.scheduler_maintenance.get_loop_lock", return_value=lock),
        patch(
            "app.services.scheduler_maintenance.update_historical_results",
            new=AsyncMock(return_value=completed),
        ),
        patch(
            "app.services.scheduler_maintenance.get_database",
            side_effect=RuntimeError("open failed"),
        ),
    ):
        await scheduler_maintenance.refresh_historical_results()

    with (
        patch("app.services.scheduler_maintenance.get_loop_lock", return_value=lock),
        patch(
            "app.services.scheduler_maintenance.update_historical_results",
            new=AsyncMock(side_effect=RuntimeError("upstream")),
        ),
    ):
        await scheduler_maintenance.refresh_historical_results()


@pytest.mark.asyncio
async def test_historical_refresh_age_handles_naive_old_and_database_error():
    old = (datetime.now() - timedelta(days=2)).isoformat()
    db = SimpleNamespace(get_cache_meta=AsyncMock(return_value=old), close=AsyncMock())
    with patch("app.services.scheduler_maintenance.get_database", return_value=db):
        assert await scheduler_maintenance._historical_refresh_is_due() is True

    with patch(
        "app.services.scheduler_maintenance.get_database", side_effect=RuntimeError("db failed")
    ):
        assert await scheduler_maintenance._historical_refresh_is_due() is True


def test_start_and_stop_scheduler_cover_disabled_existing_and_weather_jobs():
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", False),
        patch.object(scheduler, "scheduler", None),
    ):
        scheduler.start_scheduler()
        assert scheduler.scheduler is None

    existing = MagicMock()
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", True),
        patch.object(scheduler, "scheduler", existing),
    ):
        scheduler.start_scheduler()
        existing.add_job.assert_not_called()

    created = MagicMock()
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", True),
        patch("app.services.scheduler.config.WEATHER_ENABLED", True),
        patch.object(scheduler, "scheduler", None),
        patch("app.services.scheduler.AsyncIOScheduler", return_value=created),
        patch("app.services.scheduler._register_backup_job"),
    ):
        scheduler.start_scheduler()
        assert {call.kwargs["id"] for call in created.add_job.call_args_list} == {
            "weather_prefetch",
            "hourly_generation",
            "historical_results_refresh",
            "flush_api_calls",
            "fetch_circuit_weather",
            "refresh_version_info",
        }
        scheduler.stop_scheduler()
        created.shutdown.assert_called_once_with(wait=True)
        assert scheduler.scheduler is None

    scheduler.stop_scheduler()


@pytest.mark.asyncio
async def test_run_initial_generation_runs_enabled_steps_and_isolates_failures():
    due = AsyncMock(return_value=True)
    historical = AsyncMock()
    load = AsyncMock()
    fetch = AsyncMock()
    collect = AsyncMock()
    version = AsyncMock()
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", True),
        patch("app.services.scheduler.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler._historical_refresh_is_due", new=due),
        patch("app.services.scheduler._refresh_historical_results", new=historical),
        patch("app.services.scheduler._load_weather_from_db", new=load),
        patch("app.services.scheduler._fetch_all_circuits_weather", new=fetch),
        patch("app.services.scheduler._collect_and_generate", new=collect),
        patch("app.services.scheduler.refresh_version_info", new=version),
    ):
        await scheduler.run_initial_generation()

    historical.assert_awaited_once()
    load.assert_awaited_once()
    fetch.assert_awaited_once()
    collect.assert_awaited_once()
    version.assert_awaited_once()

    collect_disabled = AsyncMock()
    version_disabled = AsyncMock()
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", False),
        patch("app.services.scheduler.config.WEATHER_ENABLED", False),
        patch("app.services.scheduler._collect_and_generate", new=collect_disabled),
        patch("app.services.scheduler.refresh_version_info", new=version_disabled),
    ):
        await scheduler.run_initial_generation()
    collect_disabled.assert_awaited_once()
    version_disabled.assert_awaited_once()

    due_false = AsyncMock(return_value=False)
    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", True),
        patch("app.services.scheduler.config.WEATHER_ENABLED", False),
        patch("app.services.scheduler._historical_refresh_is_due", new=due_false),
        patch("app.services.scheduler._collect_and_generate", new=AsyncMock()),
        patch("app.services.scheduler.refresh_version_info", new=AsyncMock()),
    ):
        await scheduler.run_initial_generation()
    due_false.assert_awaited_once()

    with (
        patch("app.services.scheduler.config.SCHEDULER_ENABLED", True),
        patch("app.services.scheduler.config.WEATHER_ENABLED", True),
        patch(
            "app.services.scheduler._historical_refresh_is_due",
            new=AsyncMock(side_effect=RuntimeError("age")),
        ),
        patch(
            "app.services.scheduler._load_weather_from_db",
            new=AsyncMock(side_effect=RuntimeError("load")),
        ),
        patch(
            "app.services.scheduler._fetch_all_circuits_weather",
            new=AsyncMock(side_effect=RuntimeError("fetch")),
        ),
        patch(
            "app.services.scheduler._collect_and_generate",
            new=AsyncMock(side_effect=RuntimeError("generate")),
        ),
        patch(
            "app.services.scheduler.refresh_version_info",
            new=AsyncMock(side_effect=RuntimeError("version")),
        ),
    ):
        await scheduler.run_initial_generation()
