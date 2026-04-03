"""Test database service."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.services.database import Database


class TestCircuitWeatherDatabase:
    """Tests for circuit weather database methods."""

    @pytest_asyncio.fixture
    async def db(self):
        """Create a fresh database instance for each test."""
        db = Database()
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
        assert isinstance(result, dict)

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
                        f"SELECT COUNT(*) AS count FROM {table_name}"
                    ) as cursor:
                        row = await cursor.fetchone()
                        assert row["count"] == 1
        finally:
            await db.close()
