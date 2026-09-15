"""Regression checks for cache identity, telemetry and sporting data refresh."""

import asyncio
import sqlite3
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models import PerfMetricsPayload
from app.routes import images, previews
from app.services import scheduler_generation as generation
from app.services import teams_service, weather_service
from app.services.database import Database
from app.state import clear_bmp_cache
from tests.test_routes_images import _request


@pytest.mark.asyncio
async def test_weather_total_budget_and_caller_cancellation(monkeypatch):
    async def stalled(_):
        await asyncio.Event().wait()

    monkeypatch.setattr(weather_service, "_get_weather_context_unbounded", stalled)
    monkeypatch.setattr(weather_service.config, "WEATHER_ENRICHMENT_TIMEOUT_SECONDS", 0.01)
    assert await asyncio.wait_for(weather_service.get_weather_context({}), 0.5) == (
        None,
        None,
        {"off": None},
    )
    monkeypatch.setattr(weather_service.config, "WEATHER_ENRICHMENT_TIMEOUT_SECONDS", 60)
    task = asyncio.create_task(weather_service.get_weather_context({}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_teams_total_budget_covers_scheduler_and_both_dynamic_routes(tmp_path, monkeypatch):
    async def stalled(*_):
        await asyncio.Event().wait()

    monkeypatch.setattr(teams_service.TeamsService, "get_teams_and_drivers", stalled)
    monkeypatch.setattr(generation.config, "TEAMS_ENRICHMENT_TIMEOUT_SECONDS", 0.01)
    assert await asyncio.wait_for(
        generation._generate_teams_bmp_variants(images_dir=tmp_path, db=Mock()), 0.5
    ) == (set(), 1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(previews._render_teams_preview("en", full_size=False), 0.5)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            images._render_teams_artifact(cache_key="teams", lang="en", year=2026, display="1bit"),
            0.5,
        )


@pytest.mark.asyncio
async def test_core_artifacts_and_readiness_precede_blocked_weather(tmp_path, monkeypatch):
    db = SimpleNamespace(set_cache_meta=AsyncMock())
    core = tmp_path / "calendar_en.bmp"
    monkeypatch.setattr(generation, "get_database", lambda: db)
    monkeypatch.setattr(
        generation,
        "F1Service",
        lambda: SimpleNamespace(get_next_race_from_static=lambda: {"race_key": "test"}),
    )
    monkeypatch.setattr(generation, "_load_historical_data", lambda _: None)
    monkeypatch.setattr(generation.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(generation, "_generate_base_variants", AsyncMock(return_value=({core}, 0)))
    preview = AsyncMock()
    monkeypatch.setattr(generation, "generate_preview_pngs", preview)
    entered = asyncio.Event()

    async def blocked_weather(_):
        assert (
            db.set_cache_meta.await_args_list[1].args[0] == generation.GENERATION_SUCCESS_META_KEY
        )
        assert preview.await_args.args[0] == ["off"]
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(generation, "_load_weather_context", blocked_weather)
    task = asyncio.create_task(generation._collect_and_generate_unlocked())
    await asyncio.wait_for(entered.wait(), 0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_perf_snapshots_upsert_ordered_values_and_keep_legacy_data(tmp_path):
    path = tmp_path / "legacy.db"
    now = datetime.now(timezone.utc).isoformat()
    with ExitStack() as contexts:
        conn = contexts.enter_context(closing(sqlite3.connect(path)))
        contexts.enter_context(conn)
        conn.execute(
            "CREATE TABLE perf_metrics (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "page_path TEXT, lcp_ms REAL, cls REAL, fcp_ms REAL, ttfb_ms REAL, "
            "inp_ms REAL, user_agent TEXT, connection_type TEXT, device_memory REAL)"
        )
        conn.execute(
            "INSERT INTO perf_metrics(timestamp, page_path, inp_ms) VALUES (?, '/', 9999)", (now,)
        )
    db = Database(str(path))
    visit = str(uuid4())
    try:
        await db.save_perf_metric("/configure", visit_id=visit, report_seq=1, fcp_ms=150, cls=0)
        await db.save_perf_metric(
            "/configure", visit_id=visit, report_seq=3, fcp_ms=150, cls=0, inp_ms=400
        )
        await db.save_perf_metric("/configure", visit_id=visit, report_seq=2, inp_ms=900)
        await db.save_perf_metric("/configure", visit_id=visit, report_seq=3, inp_ms=999)
        await db.save_perf_metric(
            "/configure", visit_id=visit, report_seq=4, fcp_ms=150, cls=0, inp_ms=200
        )
        stats = await db.get_perf_stats()
        assert stats["sample_count"] == 1
        assert stats["inp"]["p75"] == 200
        assert stats["fcp"]["avg"] == 150
        assert stats["cls"]["avg"] == 0
        assert (await db.get_perf_stats_by_page())[0]["samples"] == 1
        assert (await db.get_perf_trends())["samples"] == [1]
        async with (
            db._get_connection() as conn,
            conn.execute(
                "SELECT measurement_version, inp_ms FROM perf_metrics ORDER BY id"
            ) as cursor,
        ):
            assert [tuple(row) for row in await cursor.fetchall()] == [(1, 9999), (2, 200)]
    finally:
        await db.close()


@pytest.mark.parametrize(
    "values",
    [
        {"measurement_version": 2},
        {"measurement_version": 2, "visit_id": str(uuid4())},
        {"measurement_version": 2, "report_seq": 1},
        {"visit_id": "invalid"},
        {"report_seq": -1},
    ],
)
def test_perf_payload_discards_obsolete_visit_fields(values):
    payload = PerfMetricsPayload(page_path="/", **values).model_dump()
    assert "visit_id" not in payload
    assert "report_seq" not in payload


@pytest.mark.asyncio
async def test_auto_calendar_memory_cache_changes_with_next_race(monkeypatch):
    clear_bmp_cache()
    race = {"race_key": "monza", "season": 2026}
    service = SimpleNamespace(get_next_race_from_static=lambda: race)
    monkeypatch.setattr(images, "enforce_rate_limit", Mock())
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", lambda **_: None)
    monkeypatch.setattr(images, "_schedule_calendar_analytics", Mock())
    render = AsyncMock(side_effect=[(b"monza", race, True), (b"madring", race, True)])
    monkeypatch.setattr(images, "_render_calendar", render)
    kwargs = dict(
        lang="en",
        year=None,
        race_round=None,
        race_key=None,
        tz=None,
        weather=False,
        weather_type="off",
        display="1bit",
        f1_service=service,
    )
    try:
        assert (await images.get_calendar_bmp(_request(), **kwargs)).body == b"monza"
        race = {"race_key": "madring", "season": 2026}
        assert (await images.get_calendar_bmp(_request(), **kwargs)).body == b"madring"
        assert render.await_count == 2
    finally:
        clear_bmp_cache()
