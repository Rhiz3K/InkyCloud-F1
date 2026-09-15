"""Verify that the default public profile does not persist or export visitor usage."""

import asyncio
import logging
import runpy
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from cachetools import TTLCache
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main, state
from app.config import Config, config
from app.routes import api, pages
from app.services import backup, scheduler
from app.services.database import Database
from app.utils import async_tasks, rate_limit
from app.web.templates import detect_ui_language
from scripts.check_public_deployment import findings


@pytest.fixture(autouse=True)
def minimal_profile(monkeypatch):
    """Exercise the shipped profile without enabling external services."""
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", True)
    monkeypatch.setattr(config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(pages, "refresh_version_info", AsyncMock())
    state._api_calls_buffer.clear()
    rate_limit._reset_rate_limit_state_for_tests()
    yield
    state._api_calls_buffer.clear()
    rate_limit._reset_rate_limit_state_for_tests()


def test_aggregate_mode_is_default_without_environment(monkeypatch):
    monkeypatch.delenv("MINIMAL_DATA_MODE")
    monkeypatch.delenv("AGGREGATE_STATS_ONLY")
    settings = Config(_env_file=None)
    assert settings.MINIMAL_DATA_MODE is False
    assert settings.AGGREGATE_STATS_ONLY is True


def test_no_event_identifiers_or_pending_request_buffer():
    with patch("app.state.uuid4", side_effect=AssertionError("Identifier generated")):
        state.record_api_call("/calendar.bmp", 100, 1000, tz="Europe/Prague")
    assert not state._api_calls_buffer
    state._api_calls_buffer.append({"event_id": "old"})
    assert state.get_and_clear_api_calls_buffer() == []
    state._api_calls_buffer.append({"event_id": "old"})
    state.requeue_api_calls([{"event_id": "failed-flush"}])
    assert not state._api_calls_buffer


@pytest.mark.asyncio
async def test_usage_writes_do_not_open_database(tmp_path):
    path = tmp_path / "must-not-be-created.db"
    db = Database(str(path))
    with patch.object(db, "_init_db_if_needed", side_effect=AssertionError("DB opened")):
        assert await db.save_api_calls_batch([{"endpoint": "/", "timestamp": "now"}]) == 0
        assert await db.save_perf_metric("/", user_agent="private", visit_id="private") is None
    assert not path.exists()


@pytest.mark.asyncio
async def test_startup_erases_old_usage_but_preserves_content_and_provider_quota(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "migration.db"))
    marker = "retired-visitor-identifier-9ee8851"
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", False)
    try:
        await db.save_api_calls_batch([{"endpoint": marker, "timestamp": "2026-09-01"}])
        await db.save_perf_metric("/", user_agent=marker, visit_id=marker)
        await db.set_cache_meta("content-version", "licensed-data")
        assert await db.reserve_weather_request(now=100000)
        async with db._get_connection() as conn:
            await conn.execute(
                "INSERT INTO request_stats (timestamp, hour_count, day_count) VALUES (?, 1, 1)",
                (marker,),
            )
            await conn.execute(
                "INSERT INTO api_call_totals "
                "(timestamp, endpoint, response_count, sample_count, status_code) "
                "VALUES (?, '/', 1, 1, 200)",
                (marker,),
            )
            await conn.commit()
        await db.close()
        Database.initialized_paths.discard(db.db_path)
        monkeypatch.setattr(config, "MINIMAL_DATA_MODE", True)
        await db.ping()
        async with db._get_connection() as conn:
            for table in ("api_calls", "api_call_totals", "perf_metrics", "request_stats"):
                async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                    assert (await cursor.fetchone())[0] == 0
            async with conn.execute("SELECT timestamp FROM weather_api_requests") as cursor:
                assert (await cursor.fetchone())[0] == 100000
        assert await db.get_cache_meta("content-version") == "licensed-data"
        assert await db.cleanup_old_stats() == 0
    finally:
        await db.close()
    # This checks the active SQLite files, not external backups or disk forensics.
    for path in tmp_path.glob("migration.db*"):
        assert marker.encode() not in path.read_bytes()


@pytest.mark.asyncio
async def test_busy_wal_checkpoint_does_not_declare_cleanup_complete(tmp_path):
    db = Database(str(tmp_path / "locked.db"))
    try:
        await db.ping()
        async with db._get_connection() as conn:
            await conn.execute("PRAGMA busy_timeout=10")
            await conn.execute("INSERT INTO request_stats VALUES (NULL, 'old', 1, 1)")
            await conn.commit()
            with closing(sqlite3.connect(db.db_path)) as reader:
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM request_stats").fetchall()
                with pytest.raises(RuntimeError, match="exclusive database access"):
                    await Database._purge_usage_tables(conn)
                reader.rollback()
            await Database._purge_usage_tables(conn)
    finally:
        await db.close()


def test_rate_limiting_never_reads_ip_and_shares_one_expiring_counter(monkeypatch):
    now = [100.0]
    counters = TTLCache(maxsize=10000, ttl=60, timer=lambda: now[0])
    monkeypatch.setattr(rate_limit, "_RATE_LIMIT_BUCKETS", counters)
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    with patch.object(rate_limit, "_get_client_identifier", side_effect=AssertionError("IP read")):
        for address in ("192.0.2.1", "192.0.2.2"):
            request = Request({"type": "http", "client": (address, 123), "headers": []})
            rate_limit.enforce_rate_limit(request, bucket="images", limit=2)
        with pytest.raises(HTTPException) as exc:
            rate_limit.enforce_rate_limit(Mock(), bucket="images", limit=2)
        assert exc.value.status_code == 429
        assert set(counters) == {"images:shared"}
        assert counters.ttl == 60
        now[0] += 60
        assert not counters
        rate_limit.enforce_rate_limit(Mock(), bucket="images", limit=2)
        assert counters["images:shared"][1] == 1


@pytest.mark.parametrize("path", ["/api/stats", "/api/stats/history", "/api/perf-metrics"])
def test_statistics_apis_are_gone_without_any_database_access(path):
    with patch.object(api, "get_database", side_effect=AssertionError("DB read")):
        assert TestClient(main.app).get(path).status_code == 410


@pytest.mark.parametrize("lang", ["", "/cs", "/ja"])
def test_statistics_page_explains_disabled_collection_without_loading_usage(lang):
    with patch.object(pages, "get_database", side_effect=AssertionError("DB read")):
        response = TestClient(main.app).get(f"{lang}/stats")
    assert response.status_code == 410
    assert TestClient(main.app).head(f"{lang}/stats").status_code == 410
    assert f'href="{lang}/privacy"' in response.text
    assert "stats_disabled" not in response.text


def test_opt_out_keeps_language_preference_but_hides_statistics():
    client = TestClient(main.app)
    client.cookies.set("preferredLang", "cs")
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200
    home = client.get("/cs/")
    assert '<html lang="cs"' in home.text
    assert 'href="/stats"' not in home.text
    assert "window.MINIMAL_DATA_MODE = true" in home.text
    assert "set-cookie" not in home.headers
    assert "/stats" not in client.get("/sitemap.xml").text
    request = Mock()
    request.cookies = {"preferredLang": "cs"}
    assert detect_ui_language(request) == "cs"


@pytest.mark.parametrize(
    "lang",
    ["", "/cs", "/sk", "/de", "/es", "/fr", "/it", "/nl", "/pl", "/pt-BR", "/ja", "/zh-CN", "/tr"],
)
def test_minimal_privacy_notice_is_translated_and_shows_effective_services(lang, monkeypatch):
    monkeypatch.setattr(config, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "MONITORING_DETAILS", "must-not-be-shown-monitoring")
    monkeypatch.setattr(config, "BACKUP_DETAILS", "must-not-be-shown-backup")
    response = TestClient(main.app).get(f"{lang}/privacy")
    assert response.status_code == 200
    assert "privacy_minimal_" not in response.text
    assert "must-not-be-shown" not in response.text
    assert "{retention}" not in response.text
    assert "{sample}" not in response.text
    assert "2026-09-11" in response.text


def test_minimal_mode_overrides_old_backup_credentials_and_enabled_flag(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(config, "S3_ENDPOINT_URL", "https://unused.example.org")
    monkeypatch.setattr(config, "S3_BUCKET_NAME", "unused")
    with patch.object(backup, "_resolve_secret", side_effect=AssertionError("Credentials used")):
        assert backup.is_backup_configured() is False
        assert backup._get_s3_client() is None
        assert backup.perform_backup() is False
        assert backup.perform_backup_with_details()["success"] is False


def test_app_does_not_initialize_sentry_and_disables_library_logging(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_ENABLED", True)
    monkeypatch.setattr(config, "SENTRY_DSN", "https://public@unused.example.org/1")
    with (
        patch("app.config.config", config),
        patch.object(main.sentry_sdk, "init") as init,
        patch.object(logging, "disable") as disable,
    ):
        runpy.run_path("app/main.py", run_name="minimal_data_import_test")
    init.assert_not_called()
    disable.assert_called_once_with(logging.CRITICAL)


@pytest.mark.asyncio
async def test_background_failures_are_not_sent_to_sentry():
    async def broken():
        raise ValueError("Private request payload")

    with patch.object(async_tasks.sentry_sdk, "capture_exception") as capture:
        assert await async_tasks._run_supervised(broken(), "test") is None
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_startup_requires_cleanup_without_scheduler_and_does_not_report_errors(monkeypatch):
    database = SimpleNamespace(ping=AsyncMock())
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)
    with (
        patch.object(main, "get_database", return_value=database),
        patch.object(main, "_check_persistent_storage"),
        patch.object(main, "ensure_runtime_circuits_data", side_effect=ValueError("private")),
        patch.object(main, "run_render", AsyncMock(side_effect=ValueError("private"))),
        patch.object(main, "start_scheduler") as start,
        patch.object(main, "stop_scheduler"),
        patch.object(main, "run_initial_generation", AsyncMock()),
        patch.object(main, "shutdown_render_executor"),
        patch.object(main, "close_shared_http_clients", AsyncMock()),
        patch.object(main, "close_shared_database", AsyncMock()),
        patch.object(main.sentry_sdk, "capture_exception") as capture,
    ):
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            database.ping.assert_awaited_once()
        database.ping.side_effect = RuntimeError("cleanup failed")
        start.reset_mock()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            async with main.lifespan(main.app):
                pytest.fail("Serving before cleanup")
        start.assert_not_called()
    capture.assert_not_called()


def test_scheduler_omits_usage_and_backup_jobs_but_keeps_generation(monkeypatch):
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(config, "BACKUP_ENABLED", True)
    monkeypatch.setattr(scheduler, "scheduler", None)
    with patch.object(scheduler, "AsyncIOScheduler") as factory:
        scheduler.start_scheduler()
    jobs = {call.kwargs["id"] for call in factory.return_value.add_job.call_args_list}
    assert jobs == {"hourly_generation", "historical_results_refresh", "refresh_version_info"}
    factory.return_value.start.assert_called_once()


def test_public_check_does_not_claim_minimal_data_exempts_operator_obligations():
    settings = Config(
        _env_file=None,
        MINIMAL_DATA_MODE=True,
        SENTRY_ENABLED=True,
        BACKUP_ENABLED=True,
        SCHEDULER_ENABLED=False,
        OPERATOR_NAME="",
        PRIVACY_CONTACT_EMAIL="",
        MONITORING_DETAILS="",
        BACKUP_DETAILS="",
        HETZNER_DPA_CONFIRMED=False,
    )
    unresolved = "\n".join(findings(settings))
    assert "OPERATOR_NAME" in unresolved
    assert "PRIVACY_CONTACT_EMAIL" in unresolved
    assert "HETZNER_DPA_CONFIRMED" in unresolved
    for disabled_service in ("MONITORING_DETAILS", "BACKUP_DETAILS", "SCHEDULER_ENABLED"):
        assert disabled_service not in unresolved


@pytest.mark.parametrize(
    "path",
    [
        "/configure/calendar",
        "/cs/configure/calendar",
        "/configure/teams",
        "/cs/configure/teams",
        "/missing/page",
    ],
)
def test_opt_out_hides_statistics_but_keeps_timezone_detection(path):
    response = TestClient(main.app).get(path)
    assert response.status_code in (200, 404)
    assert 'href="/stats"' not in response.text
    assert 'href="/cs/stats"' not in response.text
    if "calendar" in path:
        assert 'data-continent="DETECTED"' in response.text
