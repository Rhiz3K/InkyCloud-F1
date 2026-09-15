"""Privacy and correctness of the default aggregate collection profile."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from app import main, state
from app.config import config
from app.routes import api, pages
from app.services import private_stats
from app.services.database import Database
from app.utils import rate_limit


@pytest.fixture(autouse=True)
def aggregate_profile(monkeypatch):
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", True)
    monkeypatch.setattr(config, "UMAMI_ENABLED", False)
    monkeypatch.setattr(config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(pages, "refresh_version_info", AsyncMock())
    state._api_calls_buffer.clear()
    state._aggregate_calls.clear()
    rate_limit._reset_rate_limit_state_for_tests()
    yield
    state._api_calls_buffer.clear()
    state._aggregate_calls.clear()
    rate_limit._reset_rate_limit_state_for_tests()


@pytest.mark.asyncio
async def test_hourly_counts_merge_and_retry_without_individual_records(tmp_path):
    db = Database(str(tmp_path / "counts.db"))
    try:
        with patch("app.state.uuid4", side_effect=AssertionError("Per-request identifier")):
            for duration in (101, 199, None):
                state.record_api_call(
                    "/calendar.bmp?secret=private",
                    duration,
                    1000,
                    lang="cs",
                    tz="Europe/Prague",
                    year=2026,
                    round_num=1,
                    race_name="private-name",
                    display_type="bwry",
                )
        assert len(state._aggregate_calls) == 1
        batch = state.get_and_clear_api_calls_buffer()
        assert batch[0]["sample_count"] == 3
        assert "private" not in json.dumps(batch)
        assert "event_id" not in batch[0]
        assert await db.save_api_calls_batch(batch) == 3
        assert await db.save_api_calls_batch(batch) == 0
        state.record_api_call(
            "/calendar.bmp",
            300,
            1000,
            lang="cs",
            tz="Europe/Prague",
            year=2026,
            round_num=1,
            display_type="bwry",
        )
        assert await db.save_api_calls_batch(state.get_and_clear_api_calls_buffer()) == 1
        async with db._get_connection() as conn:
            assert (await (await conn.execute("SELECT COUNT(*) FROM api_calls")).fetchone())[0] == 0
            rows = await (await conn.execute("SELECT * FROM api_call_totals")).fetchall()
            assert len(rows) == 1
            row = dict(rows[0])
            assert row["sample_count"] == 4
            assert row["response_count"] == 3
            assert row["response_time_ms"] == 600
            assert row["response_min"] == 100
            assert row["response_max"] == 300
            assert row["response_size_bytes"] == 4000
        stats = await db.get_api_calls_stats_24h()
        assert stats["count_24h"] == 4
        assert stats["avg_response_ms"] == 200
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_histograms_do_not_store_user_metadata_and_keep_percentiles(tmp_path):
    db = Database(str(tmp_path / "histograms.db"))
    try:
        for value in (101, 121, 202, 399):
            await db.save_perf_metric(
                "/cs/configure/calendar?secret=marker-private",
                lcp_ms=value,
                cls=0.1234,
                fcp_ms=50,
                ttfb_ms=11,
                inp_ms=249,
                user_agent="marker-private",
                visit_id="marker-private",
                device_memory=16,
                connection_type="marker-private",
            )
        await db.save_perf_metric("/whatever-private", lcp_ms=800)
        await db.save_perf_metric("/", lcp_ms=None)
        stats = await db.get_perf_stats()
        assert stats["sample_count"] == 5
        assert stats["lcp"]["p50"] == 200
        assert stats["lcp"]["p95"] == 800
        assert stats["cls"]["p75"] == 0.12
        assert stats["ttfb"]["avg"] == 0
        by_page = await db.get_perf_stats_by_page()
        assert by_page[0]["page"] == "/configure/calendar"
        assert by_page[0]["samples"] == 4
        assert (await db.get_perf_trends())["samples"] == [5]
        async with db._get_connection() as conn:
            assert (await (await conn.execute("SELECT COUNT(*) FROM perf_metrics")).fetchone())[
                0
            ] == 0
            rows = await (await conn.execute("SELECT * FROM perf_buckets")).fetchall()
            assert "private" not in json.dumps([dict(row) for row in rows])
        await db.close()
        for path in tmp_path.glob("histograms.db*"):
            assert b"marker-private" not in path.read_bytes()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_migration_preserves_counts_and_erases_legacy_identifiers(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    db = Database(str(path))
    marker = "private-visitor-12345"
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", False)
    try:
        await db.save_api_calls_batch(
            [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "endpoint": "/calendar.bmp?" + marker,
                    "event_id": marker,
                    "response_time_ms": 150,
                }
            ]
        )
        await db.save_perf_metric("/cs/stats", lcp_ms=151, user_agent=marker, visit_id=marker)
        await db.set_cache_meta("sport", "keep")
        await db.close()
        Database.initialized_paths.discard(db.db_path)
        monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", True)
        await db.ping()
        assert (await db.get_api_calls_stats_24h())["count_24h"] == 1
        assert (await db.get_perf_stats())["lcp"]["avg"] == 150
        assert await db.get_cache_meta("sport") == "keep"
        await db.close()
        Database.initialized_paths.discard(db.db_path)
        await db.ping()
        assert (await db.get_api_calls_stats_24h())["count_24h"] == 1
        await db.close()
        for file in tmp_path.glob("legacy.db*"):
            assert marker.encode() not in file.read_bytes()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_histogram_and_retry_receipt_retention(tmp_path):
    db = Database(str(tmp_path / "retention.db"))
    try:
        await db.ping()
        async with db._get_connection() as conn:
            old = (datetime.now(timezone.utc) - timedelta(days=401)).isoformat()
            await private_stats.save_performance(conn, "/", {"lcp_ms": 500}, old)
            await conn.execute("INSERT INTO stats_batches VALUES ('old', ?)", (old,))
            await conn.commit()
        await db.cleanup_old_stats(400)
        async with db._get_connection() as conn:
            for table in ("perf_buckets", "stats_batches"):
                assert (await (await conn.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[
                    0
                ] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_batch_is_ignored_and_missing_season_cannot_store_free_text(tmp_path):
    db = Database(str(tmp_path / "invalid.db"))
    try:
        assert await db.save_api_calls_batch([{"timestamp": "", "endpoint": "/"}]) == 0
        normalized = private_stats.normalize_call(
            {"endpoint": "/calendar.bmp", "year": 2099, "round": 1, "race_name": "private"}
        )
        assert normalized["race_name"] is None
    finally:
        await db.close()


def test_aggregate_buffer_drops_oldest_group_at_capacity(monkeypatch):
    monkeypatch.setattr(state, "API_CALLS_BUFFER_MAXSIZE", 2)
    for lang in ("cs", "de", "fr"):
        state.record_api_call("/calendar.bmp", None, None, lang=lang)
    assert len(state._aggregate_calls) == 2
    assert [row["lang"] for row in state.get_and_clear_api_calls_buffer()] == ["de", "fr"]


@pytest.mark.asyncio
async def test_startup_without_scheduler_runs_and_stops_private_retention(monkeypatch):
    events = []

    async def retention():
        events.append("started")
        try:
            await asyncio.Event().wait()
        finally:
            events.append("stopped")

    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    with (
        patch.object(main, "_maintain_private_stats", retention),
        patch.object(main, "_check_persistent_storage"),
        patch.object(main, "ensure_runtime_circuits_data"),
        patch.object(main, "get_database", return_value=AsyncMock()),
        patch.object(main, "warm_teams_renderer_assets", lambda: None),
        patch.object(main, "start_scheduler"),
        patch.object(main, "stop_scheduler"),
        patch.object(main, "run_initial_generation", AsyncMock()),
        patch.object(main, "flush_api_calls_to_db", AsyncMock()),
        patch.object(main, "close_shared_database", AsyncMock()),
        patch.object(main, "close_shared_http_clients", AsyncMock()),
        patch.object(main, "shutdown_render_executor"),
    ):
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            assert events == ["started"]
        assert events == ["started", "stopped"]


@pytest.mark.asyncio
async def test_migration_requires_old_reader_to_release_wal(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "busy.db"))
    monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", False)
    try:
        await db.save_perf_metric("/stats", lcp_ms=200, visit_id="legacy-visitor")
        async with aiosqlite.connect(db.db_path) as reader:
            await reader.execute("BEGIN")
            await (await reader.execute("SELECT * FROM perf_metrics")).fetchall()
            Database.initialized_paths.discard(db.db_path)
            monkeypatch.setattr(config, "AGGREGATE_STATS_ONLY", True)
            with pytest.raises(RuntimeError, match="exclusive database access"):
                await db.ping()
            await reader.rollback()
        await db.ping()
        assert (await db.get_perf_stats())["sample_count"] == 1
    finally:
        await db.close()


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/cs/credits?token=private", "/credits"),
        ("/configure/", "/configure/calendar"),
        ("//private.example", "/other"),
        ("https://private.example", "/other"),
        ("/cs/", "/"),
        ("/unknown-private", "/other"),
        ("/", "/"),
    ],
)
def test_page_categories_cannot_contain_query_values_or_arbitrary_paths(path, expected):
    assert private_stats.canonical_page(path) == expected


@pytest.mark.parametrize("value", [None, "private", True, float("inf"), float("nan"), -1, 1001])
def test_non_numeric_and_out_of_range_metrics_are_discarded(value):
    assert private_stats.bounded_number(value, 1000) is None


@pytest.mark.parametrize("version", [1, 2, 3])
def test_ingestion_ignores_headers_and_legacy_payload_metadata(monkeypatch, version):
    save = AsyncMock()
    monkeypatch.setattr(api, "get_database", type("DB", (), {"save_perf_metric": save}))
    client = TestClient(main.app)
    response = client.post(
        "/api/perf-metrics",
        headers={"User-Agent": "private", "Referer": "private"},
        json={
            "page_path": "/cs/stats",
            "lcp_ms": 151,
            "measurement_version": version,
            "visit_id": "obsolete and not even a UUID",
            "report_seq": -1,
            "device_memory": "private",
            "connection_type": {"private": True},
            "user_agent": "private",
        },
    )
    assert response.status_code == 200
    assert save.await_args.kwargs == {
        "page_path": "/stats",
        "measurement_version": 3,
        "lcp_ms": 151,
        "cls": None,
        "fcp_ms": None,
        "ttfb_ms": None,
        "inp_ms": None,
    }
    assert client.get("/api").json()["statistics"]["enabled"] is True
    assert 'href="/stats"' in client.get("/").text
    assert "/stats" in client.get("/sitemap.xml").text


def test_perf_ingestion_schema_only_advertises_numeric_metrics_and_page():
    schema = api.PerfMetricsPayload.model_json_schema()
    assert set(schema["properties"]) == {
        "page_path",
        "measurement_version",
        "lcp_ms",
        "cls",
        "fcp_ms",
        "ttfb_ms",
        "inp_ms",
    }


def test_aggregate_rate_limit_does_not_read_or_store_ip(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    with patch.object(rate_limit, "_get_client_identifier", side_effect=AssertionError("IP read")):
        rate_limit.enforce_rate_limit(None, bucket="test", limit=2)
    assert set(rate_limit._RATE_LIMIT_BUCKETS) == {"test:shared"}


@pytest.mark.asyncio
@pytest.mark.parametrize("monitoring", [False, True])
async def test_retention_loop_recovers_after_storage_failure_and_allows_cancellation(
    monkeypatch, monitoring
):
    sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
    cleanup = AsyncMock(side_effect=[RuntimeError("private-value"), None])
    monkeypatch.setattr(config, "SENTRY_ENABLED", monitoring)
    monkeypatch.setattr(config, "SENTRY_DSN", "https://public@unused.example.org/1")
    with (
        patch.object(main.asyncio, "sleep", sleep),
        patch.object(main, "flush_api_calls_to_db", AsyncMock()) as flush,
        patch.object(
            main, "get_database", return_value=type("DB", (), {"cleanup_old_stats": cleanup})()
        ),
        patch.object(main.sentry_sdk, "capture_exception") as capture,
        pytest.raises(asyncio.CancelledError),
    ):
        await main._maintain_private_stats()
    assert cleanup.await_count == 2
    assert flush.await_count == 2
    assert capture.call_count == int(monitoring)


@pytest.mark.parametrize(
    "lang", ["en", "cs", "sk", "de", "es", "fr", "it", "nl", "pl", "pt-BR", "tr", "ja", "zh-CN"]
)
def test_all_locales_have_complete_map_credits_and_matching_mobile_rows(lang):
    from app.services.i18n import get_translator
    from app.services.track_catalog import TrackStyle, track_ui_options

    translations = get_translator(lang)
    assert translations["credits_page"].keys() == get_translator("en")["credits_page"].keys()
    assert translations["track_art"]["styles"].keys() == {str(style) for style in TrackStyle}
    options = track_ui_options(lang)
    assert len(options["accents"]) == 16
    assert options["styleLabel"] == translations["track_art"]["styleLabel"]
    prefix = "" if lang == "en" else "/" + lang
    client = TestClient(main.app)
    home = client.get(prefix + "/").text
    assert home.count('data-credit="tracks"') == 2
    for key in ("hosting", "analytics", "errors"):
        assert home.count(f'data-credit="{key}"') == 2
    credit_html = client.get(prefix + "/credits").text
    assert credit_html.count("<article id=") == 52
    assert translations["credits_page"]["intro"] in credit_html
    assert "None" not in credit_html
