"""Test database service."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.services.database import Database


class TestCircuitWeatherDatabase:
    """Tests for circuit weather database methods."""

    @pytest_asyncio.fixture
    async def db(self, tmp_path):
        """Create a fresh database instance for each test."""
        db = Database(str(tmp_path / "circuit_weather.db"))
        yield db
        await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_save_and_get_circuit_weather(db):
        """Test saving and retrieving circuit weather."""
        await db.save_circuit_weather(
            circuit_id="albert_park",
            circuit_name="Albert Park",
            temperature_c=25.5,
            weather_code=1,
            precipitation_probability=15,
        )

        result = await db.get_circuit_weather("albert_park")
        assert result is not None
        assert result["temperature_c"] == 25.5
        assert result["weather_code"] == 1
        assert result["precipitation_probability"] == 15
        assert "fetched_at" in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_circuit_weather_not_found(db):
        """Test that missing circuit returns None."""
        result = await db.get_circuit_weather("nonexistent")
        assert result is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_save_circuit_weather_upsert(db):
        """Test that saving same circuit updates the data."""
        await db.save_circuit_weather(
            circuit_id="monaco",
            circuit_name="Monaco",
            temperature_c=20.0,
            weather_code=0,
            precipitation_probability=5,
        )

        await db.save_circuit_weather(
            circuit_id="monaco",
            circuit_name="Monaco Street Circuit",
            temperature_c=28.0,
            weather_code=61,
            precipitation_probability=80,
        )

        result = await db.get_circuit_weather("monaco")
        assert result is not None
        assert result["temperature_c"] == 28.0
        assert result["weather_code"] == 61
        assert result["precipitation_probability"] == 80

    @staticmethod
    @pytest.mark.asyncio
    async def test_load_all_circuit_weather_empty(db):
        """Test loading all weather when database is empty."""
        result = await db.load_all_circuit_weather()
        assert result == {}

    @staticmethod
    @pytest.mark.asyncio
    async def test_load_all_circuit_weather(db):
        """Test loading all circuit weather data."""
        await db.save_circuit_weather(
            circuit_id="silverstone",
            circuit_name="Silverstone",
            temperature_c=18.0,
            weather_code=3,
            precipitation_probability=40,
        )
        await db.save_circuit_weather(
            circuit_id="spa",
            circuit_name="Spa-Francorchamps",
            temperature_c=15.0,
            weather_code=61,
            precipitation_probability=70,
        )

        result = await db.load_all_circuit_weather()

        assert "silverstone" in result
        assert "spa" in result
        assert result["silverstone"]["temperature_c"] == 18.0
        assert result["spa"]["weather_code"] == 61


class TestDatabaseConnectionLifecycle:
    """Tests for persistent database connection reuse."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_database_reuses_connection_within_same_loop(tmp_path):
        db = Database(str(tmp_path / "reuse.db"))
        try:
            async with db._get_connection() as first_conn, db._get_connection() as second_conn:
                assert first_conn is second_conn
        finally:
            await db.close()

    @staticmethod
    def test_database_refreshes_connection_across_event_loops(tmp_path):
        db = Database(str(tmp_path / "loop-refresh.db"))
        connections = []

        async def capture_connection():
            async with db._get_connection() as conn:
                connections.append(conn)

        try:
            asyncio.run(capture_connection())
            asyncio.run(capture_connection())
        finally:
            asyncio.run(db.close())

        assert len(connections) == 2
        assert connections[0] is not connections[1]

    @staticmethod
    @pytest.mark.asyncio
    async def test_database_initialization_is_serialized_across_instances(tmp_path, monkeypatch):
        db_path = str(tmp_path / "shared.db")
        db1 = Database(db_path)
        db2 = Database(db_path)
        migration_calls = 0
        original_run_migrations = Database._run_migrations

        async def slow_run_migrations(conn):
            nonlocal migration_calls
            migration_calls += 1
            await asyncio.sleep(0.05)
            await original_run_migrations(conn)

        monkeypatch.setattr(Database, "_run_migrations", staticmethod(slow_run_migrations))

        try:
            await asyncio.gather(db1._init_db_if_needed(), db2._init_db_if_needed())
        finally:
            await db1.close()
            await db2.close()

        assert migration_calls == 1


class TestGeneratedImageAndCacheMetaDatabase:
    """Tests for generated image and cache metadata helpers."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_save_and_get_generated_image(tmp_path):
        db = Database(str(tmp_path / "generated-images.db"))
        try:
            await db.save_generated_image(
                "calendar_en",
                "/data/images/calendar_en.bmp",
                "en",
                season=2026,
                round_num=5,
            )
            assert await db.get_image_path("calendar_en") == "/data/images/calendar_en.bmp"

            await db.save_generated_image(
                "calendar_en",
                "/data/images/calendar_en_v2.bmp",
                "en",
            )
            assert await db.get_image_path("calendar_en") == "/data/images/calendar_en_v2.bmp"
            assert await db.get_image_path("nonexistent") is None
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_set_and_get_cache_meta(tmp_path):
        db = Database(str(tmp_path / "cache-meta.db"))
        try:
            await db.set_cache_meta("last_round", "5")
            assert await db.get_cache_meta("last_round") == "5"

            await db.set_cache_meta("last_round", "6")
            assert await db.get_cache_meta("last_round") == "6"
            assert await db.get_cache_meta("nonexistent") is None
        finally:
            await db.close()


class TestApiCallStatsDatabase:
    """Tests for API call statistics."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_stats_for_range_includes_display_breakdowns(tmp_path):
        """Stats include separate display type counts for calendar and teams."""
        db = Database(str(tmp_path / "stats.db"))
        try:
            now = datetime.now(timezone.utc).isoformat()

            await db.save_api_calls_batch(
                [
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 100.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 120.0,
                        "response_size_bytes": 2048,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "bwr",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 125.0,
                        "response_size_bytes": 2048,
                        "lang": "cs",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "bwr",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 1,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 110.0,
                        "response_size_bytes": 2048,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "bwry",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 80.0,
                        "response_size_bytes": 512,
                        "lang": "en",
                        "tz": None,
                        "year": 2026,
                        "round": None,
                        "display_type": "spectra6",
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 82.0,
                        "response_size_bytes": 600,
                        "lang": "cs",
                        "tz": None,
                        "year": 2026,
                        "round": None,
                        "display_type": "bwr",
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 81.0,
                        "response_size_bytes": 580,
                        "lang": "en",
                        "tz": None,
                        "year": 2025,
                        "round": None,
                        "display_type": None,
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 83.0,
                        "response_size_bytes": 590,
                        "lang": "cs",
                        "tz": None,
                        "year": 2025,
                        "round": None,
                        "display_type": "",
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                ]
            )

            stats = await db.get_stats_for_range(24)

            assert stats["total_requests"] == 8
            assert {tuple(item.items()) for item in stats["display_types"]} == {
                (("display_type", "bwr"), ("count", 2)),
                (("display_type", "1bit"), ("count", 1)),
                (("display_type", "bwry"), ("count", 1)),
            }
            assert {tuple(item.items()) for item in stats["teams_display_types"]} == {
                (("display_type", "1bit"), ("count", 2)),
                (("display_type", "spectra6"), ("count", 1)),
                (("display_type", "bwr"), ("count", 1)),
            }
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_api_calls_count(tmp_path):
        db = Database(str(tmp_path / "api-call-count.db"))
        try:
            now = datetime.now(timezone.utc)
            await db.save_api_calls_batch(
                [
                    {
                        "timestamp": now.isoformat(),
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 100.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": (now - timedelta(hours=2)).isoformat(),
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 110.0,
                        "response_size_bytes": 2048,
                        "lang": "cs",
                        "tz": None,
                        "year": 2026,
                        "round": None,
                        "display_type": "bwr",
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                ]
            )

            assert await db.get_api_calls_count(hours=1) == 1
            assert await db.get_api_calls_count(hours=3) == 2
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_stats_for_range_aggregates_race_rows_across_auto_selection(tmp_path):
        """Race breakdown aggregates one race even when requests mix auto/manual selection."""
        db = Database(str(tmp_path / "race-stats.db"))
        try:
            now = datetime.now(timezone.utc).isoformat()

            await db.save_api_calls_batch(
                [
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 100.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 1,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 110.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 120.0,
                        "response_size_bytes": 1024,
                        "lang": "cs",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "bwr",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": now,
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 130.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 2,
                        "display_type": "1bit",
                        "race_name": "Chinese Grand Prix",
                        "is_auto_selected": 0,
                    },
                ]
            )

            stats = await db.get_stats_for_range(24)

            assert len(stats["races"]) == 2
            assert stats["races"][0]["race_name"] == "Australian Grand Prix"
            assert stats["races"][0]["count"] == 3
            assert stats["races"][0]["is_auto_selected"] is True
            assert stats["races"][0]["auto_selected_count"] == 1
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_request_stats_history_uses_true_24h_window_across_gaps(tmp_path):
        """Rolling day_count should exclude populated buckets older than 24 clock hours."""
        db = Database(str(tmp_path / "history-gap.db"))
        try:
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            older = now - timedelta(hours=26)
            recent = now - timedelta(hours=2)
            await db.save_api_calls_batch(
                [
                    {
                        "timestamp": (older + timedelta(minutes=15)).isoformat(),
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 100.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": (recent + timedelta(minutes=5)).isoformat(),
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 120.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 2,
                        "display_type": "1bit",
                        "race_name": "Chinese Grand Prix",
                        "is_auto_selected": 0,
                    },
                ]
            )

            history = await db.get_request_stats_history(limit=10)

            assert history[0] == {
                "timestamp": recent.isoformat(),
                "hour_count": 1,
                "day_count": 1,
            }
            assert any(row["timestamp"] == older.isoformat() for row in history)
        finally:
            await db.close()


class TestDatabaseStatsCleanup:
    """Tests for retention cleanup across statistics tables."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_cleanup_old_stats_removes_all_stats_tables(tmp_path):
        db = Database(str(tmp_path / "cleanup.db"))
        try:
            await db._init_db_if_needed()

            old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
            recent_timestamp = datetime.now(timezone.utc).isoformat()

            async with db._get_connection() as conn:
                await db._configure_connection(conn)
                await conn.execute(
                    "INSERT INTO request_stats (timestamp, hour_count, day_count) VALUES (?, ?, ?)",
                    (old_timestamp, 1, 1),
                )
                await conn.execute(
                    "INSERT INTO request_stats (timestamp, hour_count, day_count) VALUES (?, ?, ?)",
                    (recent_timestamp, 2, 2),
                )
                await conn.execute(
                    """
                    INSERT INTO api_calls
                        (timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                         year, round, display_type, race_name, is_auto_selected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        old_timestamp,
                        "/calendar.bmp",
                        100.0,
                        1000,
                        "en",
                        None,
                        2026,
                        1,
                        "1bit",
                        "Test GP",
                        0,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO api_calls
                        (timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                         year, round, display_type, race_name, is_auto_selected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recent_timestamp,
                        "/calendar.bmp",
                        120.0,
                        1100,
                        "en",
                        None,
                        2026,
                        1,
                        "1bit",
                        "Test GP",
                        0,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO perf_metrics
                        (timestamp, page_path, lcp_ms, cls, fcp_ms, ttfb_ms, inp_ms,
                         user_agent, connection_type, device_memory)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (old_timestamp, "/stats", 1200.0, 0.01, 500.0, 100.0, 150.0, None, None, None),
                )
                await conn.execute(
                    """
                    INSERT INTO perf_metrics
                        (timestamp, page_path, lcp_ms, cls, fcp_ms, ttfb_ms, inp_ms,
                         user_agent, connection_type, device_memory)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recent_timestamp,
                        "/stats",
                        1000.0,
                        0.02,
                        450.0,
                        90.0,
                        140.0,
                        None,
                        None,
                        None,
                    ),
                )
                await conn.commit()

            deleted = await db.cleanup_old_stats(days=30)

            assert deleted == 3

            async with db._get_connection() as conn:
                await db._configure_connection(conn)
                for table_name in ("request_stats", "api_calls", "perf_metrics"):
                    async with conn.execute(
                        "SELECT COUNT(*) AS count, MAX(timestamp) AS remaining_timestamp "
                        f"FROM {table_name}"
                    ) as cursor:
                        row = await cursor.fetchone()
                        assert row["count"] == 1
                        assert row["remaining_timestamp"] == recent_timestamp
        finally:
            await db.close()


class TestRequestStatsHistory:
    """Tests for hourly stats history derived from API calls."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_get_request_stats_history_aggregates_api_calls_by_hour(tmp_path):
        db = Database(str(tmp_path / "history.db"))
        try:
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            hour_10 = now - timedelta(hours=2)
            hour_11 = now - timedelta(hours=1)
            await db.save_api_calls_batch(
                [
                    {
                        "timestamp": (hour_10 + timedelta(minutes=15)).isoformat(),
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 100.0,
                        "response_size_bytes": 1024,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": (hour_10 + timedelta(minutes=45)).isoformat(),
                        "endpoint": "/teams.bmp",
                        "response_time_ms": 120.0,
                        "response_size_bytes": 2048,
                        "lang": "cs",
                        "tz": None,
                        "year": 2026,
                        "round": None,
                        "display_type": "bwr",
                        "race_name": None,
                        "is_auto_selected": 0,
                    },
                    {
                        "timestamp": (hour_11 + timedelta(minutes=5)).isoformat(),
                        "endpoint": "/calendar.bmp",
                        "response_time_ms": 130.0,
                        "response_size_bytes": 4096,
                        "lang": "en",
                        "tz": "Europe/Prague",
                        "year": 2026,
                        "round": 1,
                        "display_type": "1bit",
                        "race_name": "Australian Grand Prix",
                        "is_auto_selected": 1,
                    },
                ]
            )

            history = await db.get_request_stats_history(limit=10)

            assert history == [
                {
                    "timestamp": hour_11.isoformat(),
                    "hour_count": 1,
                    "day_count": 3,
                },
                {
                    "timestamp": hour_10.isoformat(),
                    "hour_count": 2,
                    "day_count": 2,
                },
            ]
        finally:
            await db.close()


class TestPerfMetricsAggregation:
    """Tests for aggregate perf metrics serialization."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_perf_aggregates_preserve_zero_values(tmp_path):
        db = Database(str(tmp_path / "perf.db"))
        try:
            await db.save_perf_metric(
                page_path="/perfect",
                lcp_ms=0.0,
                cls=0.0,
                fcp_ms=0.0,
                ttfb_ms=0.0,
                inp_ms=0.0,
            )

            aggregate = await db.get_perf_stats(hours=24)
            by_page = await db.get_perf_stats_by_page(hours=24)
            trends = await db.get_perf_trends(hours=24)

            perfect_page = next(row for row in by_page if row["page"] == "/perfect")
            assert aggregate["lcp"]["avg"] == 0.0
            assert aggregate["cls"]["avg"] == 0.0
            assert aggregate["fcp"]["avg"] == 0.0
            assert aggregate["ttfb"]["avg"] == 0.0
            assert aggregate["inp"]["avg"] == 0.0
            assert perfect_page["lcp"] == 0.0
            assert perfect_page["cls"] == 0.0
            assert perfect_page["fcp"] == 0.0
            assert perfect_page["ttfb"] == 0.0
            assert 0.0 in trends["lcp"]
            assert 0.0 in trends["fcp"]
            assert 0.0 in trends["ttfb"]
        finally:
            await db.close()


class TestWeatherCacheDatabase:
    """Tests for key-based weather cache persistence."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_weather_cache_save_and_get(tmp_path):
        db = Database(str(tmp_path / "weather-cache.db"))
        try:
            await db.save_weather_cache("circuit_monaco_2026", 22.5, 1, 10, ttl_minutes=120)

            result = await db.get_weather_cache("circuit_monaco_2026")

            assert result is not None
            temp, code, precip = result
            assert temp == 22.5
            assert code == 1
            assert precip == 10
            assert await db.get_weather_cache("nonexistent") is None
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_weather_cache_expired_entries_not_returned(tmp_path):
        db = Database(str(tmp_path / "weather-cache-expired.db"))
        try:
            await db.save_weather_cache("expired_key", 15.0, 3, 50, ttl_minutes=0)
            assert await db.get_weather_cache("expired_key") is None
        finally:
            await db.close()

    @staticmethod
    @pytest.mark.asyncio
    async def test_cleanup_expired_weather_cache(tmp_path):
        db = Database(str(tmp_path / "weather-cache-cleanup.db"))
        try:
            await db.save_weather_cache("fresh", 20.0, 1, 5, ttl_minutes=120)
            await db.save_weather_cache("stale", 15.0, 3, 50, ttl_minutes=0)

            deleted = await db.cleanup_expired_weather_cache()

            assert deleted >= 1
            assert await db.get_weather_cache("fresh") is not None
            assert await db.get_weather_cache("stale") is None
        finally:
            await db.close()
