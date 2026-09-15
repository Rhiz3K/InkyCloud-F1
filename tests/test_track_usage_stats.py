"""Map usage must survive migration, cache responses and aggregate flush retries."""

import json
import sqlite3
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main, state
from app.config import LANGUAGE_CODES, config
from app.routes import api, images, pages
from app.routes.deps import get_f1_service
from app.services.database import DATABASE_SCHEMA_VERSION, Database
from app.services.i18n import get_translator
from app.services.private_stats import TRACK_DIMENSIONS, hour_stamp, normalize_track_options
from app.utils.etag import strong_etag


@pytest.fixture(autouse=True)
def usage_profile(monkeypatch):
    """Isolate collection and keep every test offline."""
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "ADMIN_API_TOKEN", None)
    monkeypatch.setattr(config, "UMAMI_ENABLED", False)
    monkeypatch.setattr(pages, "track_pageview", AsyncMock())
    monkeypatch.setattr(pages, "refresh_version_info", AsyncMock())
    state.get_and_clear_api_calls_buffer()
    state.get_bmp_cache().clear()
    yield
    state.get_and_clear_api_calls_buffer()
    state.get_bmp_cache().clear()


@pytest.fixture
def schema_five_database(tmp_path):
    """Build the pre-map-statistics schema with raw and weighted historic counts."""
    path = tmp_path / "schema-five.db"
    with ExitStack() as contexts:
        conn = contexts.enter_context(closing(sqlite3.connect(path)))
        contexts.enter_context(conn)
        conn.executescript("""
            CREATE TABLE api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
                timestamp TEXT NOT NULL, endpoint TEXT NOT NULL,
                response_time_ms REAL, response_size_bytes INTEGER,
                lang TEXT, tz TEXT, year INTEGER, round INTEGER, display_type TEXT,
                race_name TEXT, is_auto_selected INTEGER DEFAULT 0,
                status_code INTEGER NOT NULL DEFAULT 200
            );
            CREATE TABLE api_call_totals (
                timestamp TEXT NOT NULL, endpoint TEXT NOT NULL,
                response_time_ms REAL, response_count INTEGER NOT NULL,
                response_min REAL, response_max REAL,
                response_size_bytes INTEGER, sample_count INTEGER NOT NULL,
                lang TEXT, tz TEXT, year INTEGER, round INTEGER,
                display_type TEXT, race_name TEXT, is_auto_selected INTEGER,
                status_code INTEGER NOT NULL
            );
            CREATE VIEW api_calls_combined AS
                SELECT timestamp, endpoint, response_time_ms,
                    CASE WHEN response_time_ms IS NULL THEN 0 ELSE 1 END AS response_count,
                    response_time_ms AS response_min, response_time_ms AS response_max,
                    response_size_bytes, 1 AS sample_count, lang, tz, year, round,
                    display_type, race_name, is_auto_selected, status_code FROM api_calls
                UNION ALL
                SELECT timestamp, endpoint, response_time_ms, response_count,
                    response_min, response_max, response_size_bytes, sample_count,
                    lang, tz, year, round, display_type, race_name, is_auto_selected,
                    status_code FROM api_call_totals;
            PRAGMA user_version=5;
        """)
        stamp = hour_stamp()
        conn.executemany(
            "INSERT INTO api_calls (timestamp, endpoint, response_size_bytes) VALUES (?, ?, ?)",
            [(stamp, "/calendar.bmp", 100)] * 2,
        )
        old = hour_stamp((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        conn.executemany(
            "INSERT INTO api_call_totals (timestamp, endpoint, response_count, "
            "response_size_bytes, sample_count, is_auto_selected, status_code) "
            "VALUES (?, '/calendar.bmp', 0, ?, ?, 0, 200)",
            [(stamp, 700, 7), (old, 1100, 11)],
        )
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate", [True, False])
async def test_migration_backfills_legacy_and_preserves_counts_on_restart(
    schema_five_database, monkeypatch, aggregate
):
    """Both historic storage formats become legacy without losing or doubling counts."""
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", aggregate)
    db = Database(str(schema_five_database))
    try:
        recent = await db.get_stats_for_range(24)
        week = await db.get_stats_for_range(168)
        assert recent["total_requests"] == 9
        assert week["total_requests"] == 20
        for field in TRACK_DIMENSIONS:
            assert recent[f"{field}s"] == [{field: "legacy", "count": 9}]
            assert week[f"{field}s"] == [{field: "legacy", "count": 20}]
        async with db._get_connection() as conn:
            assert (await (await conn.execute("PRAGMA user_version")).fetchone())[
                0
            ] == DATABASE_SCHEMA_VERSION
            for table in ("api_calls", "api_call_totals"):
                row = await (
                    await conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE track_style IS NULL "
                        "OR track_source IS NULL OR track_accent IS NULL"
                    )
                ).fetchone()
                assert row[0] == 0
        calls = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "endpoint": "/calendar.bmp",
                "response_size_bytes": 100,
                "track_style": "relief",
                "track_source": "jules",
                "track_accent": "all",
            }
            for _ in range(3)
        ]
        assert await db.save_api_calls_batch(calls) == 3
        await db.close()
        Database.initialized_paths.discard(db.db_path)
        recent = await db.get_api_calls_stats_24h()
        assert recent["count_24h"] == 12
        assert recent["total_bytes_24h"] == 1200
        for field, choice in zip(TRACK_DIMENSIONS, ("relief", "jules", "all"), strict=True):
            assert recent[f"{field}s"] == [
                {field: "legacy", "count": 9},
                {field: choice, "count": 3},
            ]
        assert (await db.get_stats_for_range(168))["total_requests"] == 23
    finally:
        await db.close()


@pytest.mark.parametrize("value", [None, "", "private-token", ["private"], {"private": 1}])
def test_map_dimensions_reject_free_text_and_ignore_non_calendar_requests(value):
    """New dimensions accept catalogue identifiers, never arbitrary request data."""
    call = {field: value for field in TRACK_DIMENSIONS}
    assert normalize_track_options({"endpoint": "/calendar.bmp", **call}) == {
        field: "legacy" for field in TRACK_DIMENSIONS
    }
    assert normalize_track_options({"endpoint": "/teams.bmp", **call}) == {
        field: None for field in TRACK_DIMENSIONS
    }


@pytest.mark.asyncio
async def test_calendar_defaults_custom_choices_cache_and_304_reach_stats_api(
    tmp_path, monkeypatch
):
    """Every calendar response records resolved options; retries of a flush do not add usage."""
    db = Database(str(tmp_path / "usage.db"))
    race = {
        "season": 2026,
        "round": 16,
        "race_name": "Spanish Grand Prix",
        "circuit": {"circuitId": "madring"},
    }
    service = SimpleNamespace(get_next_race_from_static=lambda: race)
    monkeypatch.setattr(api, "get_database", lambda: db)
    monkeypatch.setattr(images, "_schedule_calendar_analytics", lambda **_: None)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", lambda **_: None)
    renders = []

    async def render(**kwargs):
        """Make response identity depend on options while excluding unrelated raster work."""
        renders.append(kwargs["track_options"])
        data = kwargs["track_options"].cache_key.encode()
        artifact = (data, strong_etag(data))
        state.get_bmp_cache()[kwargs["cache_key"]] = artifact
        return artifact

    monkeypatch.setattr(images, "_render_calendar_artifact", render)
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(api.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for first, second in (
                ({}, {"track_style": "relief", "track_source": "jules", "track_accent": "all"}),
                (
                    {
                        "track_style": "contours",
                        "track_source": "commons",
                        "track_accent": "red-blue",
                        "display": "bwr",
                    },
                    {
                        "track_style": "contours",
                        "track_source": "commons",
                        "track_accent": "red-blue",
                        "display": "bwr",
                    },
                ),
            ):
                generated = await client.get("/calendar.bmp", params=first)
                assert generated.status_code == 200
                assert generated.headers["x-cache"] == "MISS"
                cached = await client.get("/calendar.bmp", params=second)
                assert cached.status_code == 200
                assert cached.headers["x-cache"] == "HIT"
                revalidated = await client.get(
                    "/calendar.bmp",
                    params=second,
                    headers={"If-None-Match": cached.headers["etag"]},
                )
                assert revalidated.status_code == 304
            for field in TRACK_DIMENSIONS:
                assert (
                    await client.get("/calendar.bmp", params={field: "private-value"})
                ).status_code == 422
            assert len(renders) == 2
            batch = state.get_and_clear_api_calls_buffer()
            assert await db.save_api_calls_batch(batch) == 6
            assert await db.save_api_calls_batch(batch) == 0
            response = await client.get("/api/stats")
            assert response.status_code == 200
            requests = response.json()["requests"]
            assert requests["last_24h"] == 6
            assert requests["by_status"] == [
                {"status_code": 200, "count": 4},
                {"status_code": 304, "count": 2},
            ]
            for field, choices in zip(
                TRACK_DIMENSIONS,
                (("relief", "contours"), ("jules", "commons"), ("all", "red-blue")),
                strict=True,
            ):
                assert {
                    row[field]: row["count"] for row in requests[f"by_{field}"]
                } == dict.fromkeys(choices, 3)
            assert "legacy" not in json.dumps(requests)
            assert "private-value" not in json.dumps(batch)
    finally:
        await db.close()


@pytest.mark.parametrize("lang", LANGUAGE_CODES)
def test_dashboard_localizes_map_usage_and_includes_legacy_in_percentages(monkeypatch, lang):
    """Current choices and legacy share one denominator in every translated dashboard."""
    stats = pages._empty_stats()
    stats["total_requests"] = 100
    for field, choice in zip(TRACK_DIMENSIONS, ("relief", "jules", "red-blue"), strict=True):
        stats[f"{field}s"] = [{field: "legacy", "count": 6}, {field: choice, "count": 2}]
    db = SimpleNamespace(
        get_stats_for_range=AsyncMock(return_value=stats),
        get_perf_stats=AsyncMock(return_value={"sample_count": 0}),
    )
    monkeypatch.setattr(pages, "get_database", lambda: db)
    response = TestClient(main.app).get(("" if lang == "en" else f"/{lang}") + "/stats?range=7d")
    assert response.status_code == 200
    db.get_stats_for_range.assert_awaited_once_with(168)
    soup = BeautifulSoup(response.text, "html.parser")
    t = get_translator(lang)
    assert t["stats_track_usage"] in soup.get_text()
    assert t["stats_track_legacy_note"] in soup.get_text()
    for field in TRACK_DIMENSIONS:
        card = soup.find(attrs={"data-testid": f"stats-card-{field}"})
        assert card is not None
        assert card.h3.get_text(strip=True) == t[f"stats_by_{field}"]
        summaries = [
            node.get_text(" ", strip=True)
            for node in card.select('[data-testid="stats-track-summary"]')
        ]
        assert summaries == ["6 · 75.0%", "2 · 25.0%"]
    assert t["track_art"]["styles"]["relief"] in soup.get_text()


@pytest.mark.asyncio
async def test_non_calendar_requests_do_not_contribute_to_map_breakdowns(tmp_path):
    db = Database(str(tmp_path / "other-requests.db"))
    try:
        state.record_api_call(
            "/teams.bmp", 100, 1000, track_style="relief", track_source="jules", track_accent="all"
        )
        await db.save_api_calls_batch(state.get_and_clear_api_calls_buffer())
        stats = await db.get_stats_for_range(24)
        assert stats["total_requests"] == 1
        assert all(stats[f"{field}s"] == [] for field in TRACK_DIMENSIONS)
        async with db._get_connection() as conn:
            row = await (await conn.execute("SELECT * FROM api_call_totals")).fetchone()
            assert all(row[field] is None for field in TRACK_DIMENSIONS)
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate", [True, False])
async def test_each_map_dimension_survives_buffering_and_retention(
    tmp_path, monkeypatch, aggregate
):
    """Changing any one choice keeps a distinct group, including after raw data rolls up."""
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", aggregate)
    monkeypatch.setattr(config, "RAW_STATS_RETENTION_DAYS", 1)
    db = Database(str(tmp_path / "retention-map-choices.db"))
    defaults = {"track_style": "relief", "track_source": "jules", "track_accent": "all"}
    variants = [
        defaults,
        {**defaults, "track_style": "contours"},
        {**defaults, "track_source": "commons"},
        {**defaults, "track_accent": "red-blue"},
    ]
    for choices in variants:
        for _ in range(2):
            state.record_api_call("/calendar.bmp", 10, 100, "en", None, **choices)
    batch = state.get_and_clear_api_calls_buffer()
    assert len(batch) == (4 if aggregate else 8)
    old = hour_stamp((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    for call in batch:
        call["timestamp"] = old
    try:
        assert await db.save_api_calls_batch(batch) == 8
        assert await db.cleanup_old_stats(30) == (0 if aggregate else 8)
        assert await db.cleanup_old_stats(30) == 0
        week = await db.get_stats_for_range(168)
        assert week["total_requests"] == 8
        assert week["total_bytes"] == 800
        assert week["avg_response_ms"] == 10
        for field, alternative in zip(
            TRACK_DIMENSIONS, ("contours", "commons", "red-blue"), strict=True
        ):
            assert week[f"{field}s"] == [
                {field: defaults[field], "count": 6},
                {field: alternative, "count": 2},
            ]
        assert (await db.get_api_calls_stats_24h())["count_24h"] == 0
        async with db._get_connection() as conn:
            assert (await (await conn.execute("SELECT COUNT(*) FROM api_calls")).fetchone())[0] == 0
    finally:
        await db.close()
