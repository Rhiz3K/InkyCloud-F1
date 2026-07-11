"""Database service for caching metadata and statistics in SQLite."""

import asyncio
import logging
import threading
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, ClassVar, Optional

import aiosqlite

from app.config import config

logger = logging.getLogger(__name__)
PERF_STATS_SAMPLE_LIMIT = 10_000

STATS_CLEANUP_QUERIES = {
    "request_stats": "DELETE FROM request_stats WHERE timestamp < ?",
    "api_calls": "DELETE FROM api_calls WHERE timestamp < ?",
    "perf_metrics": "DELETE FROM perf_metrics WHERE timestamp < ?",
}


async def _acquire_thread_lock(lock: threading.Lock) -> None:
    """Poll a thread lock without blocking the event loop."""
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)


class Database:
    """Async SQLite database for metadata and statistics.

    Note: Race data and historical results are now stored in static JSON files.
    This database only stores:
    - Generated image metadata
    - Cache timestamps
    - Request statistics
    """

    _instances: ClassVar[weakref.WeakSet["Database"]] = weakref.WeakSet()
    initialized_paths: ClassVar[set[str]] = set()
    schema_init_locks: ClassVar[dict[str, threading.Lock]] = {}
    schema_state_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, db_path: str | None = None):
        """
        Initialize database.

        Args:
            db_path: Path to SQLite database file. Defaults to config value.
        """
        self.db_path = db_path or config.DATABASE_PATH
        self._ensure_directory()
        self._connection: aiosqlite.Connection | None = None
        self._connection_loop: asyncio.AbstractEventLoop | None = None
        self._connection_lock = asyncio.Lock()
        self._instances.add(self)

    def _ensure_directory(self) -> None:
        """Ensure the database directory exists."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    async def _ensure_connection(self) -> aiosqlite.Connection:
        """Return a persistent connection for this Database instance and event loop."""
        current_loop = asyncio.get_running_loop()

        async with self._connection_lock:
            if self._connection is not None and self._connection_loop is current_loop:
                return self._connection

            if self._connection is not None:
                try:
                    await self._connection.close()
                except Exception:
                    logger.debug("Failed to close stale database connection", exc_info=True)

            conn = await aiosqlite.connect(self.db_path)
            try:
                await self._configure_connection(conn)
            except BaseException:
                await conn.close()
                raise
            self._connection = conn
            self._connection_loop = current_loop
            return conn

    @asynccontextmanager
    async def _get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield the persistent database connection without recreating it per operation."""
        yield await self._ensure_connection()

    async def close(self) -> None:
        """Close this instance's persistent connection, if any."""
        async with self._connection_lock:
            if self._connection is None:
                return
            try:
                await self._connection.close()
            finally:
                self._connection = None
                self._connection_loop = None

    @classmethod
    async def close_all(cls) -> None:
        """Close all live Database instance connections."""
        for db in list(cls._instances):
            await db.close()

    @staticmethod
    async def _configure_connection(conn: aiosqlite.Connection) -> None:
        """Configure connection settings after it's opened."""
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout=30000;")
        conn.row_factory = aiosqlite.Row

    async def _init_db_if_needed(self) -> None:
        """Initialize database schema if not already done."""
        cls = type(self)

        if self.db_path in cls.initialized_paths:
            return

        with cls.schema_state_lock:
            if self.db_path in cls.initialized_paths:
                return
            path_lock = cls.schema_init_locks.setdefault(self.db_path, threading.Lock())

        await _acquire_thread_lock(path_lock)
        try:
            if self.db_path in cls.initialized_paths:
                return
            async with self._get_connection() as conn:
                await conn.executescript(
                    """
                    -- Generated images table
                    CREATE TABLE IF NOT EXISTS generated_images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        image_key TEXT UNIQUE NOT NULL,
                        image_path TEXT NOT NULL,
                        lang TEXT NOT NULL,
                        season INTEGER,
                        round INTEGER,
                        generated_at TEXT NOT NULL
                    );

                    -- Cache metadata table
                    CREATE TABLE IF NOT EXISTS cache_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at TEXT NOT NULL
                    );

                    -- Request statistics table (legacy hourly snapshots)
                    CREATE TABLE IF NOT EXISTS request_stats (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        hour_count INTEGER NOT NULL,
                        day_count INTEGER NOT NULL
                    );

                    -- API calls table (individual call logging)
                    CREATE TABLE IF NOT EXISTS api_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        response_time_ms REAL,
                        response_size_bytes INTEGER,
                        lang TEXT,
                        tz TEXT,
                        year INTEGER,
                        round INTEGER,
                        display_type TEXT,
                        race_name TEXT,
                        is_auto_selected INTEGER DEFAULT 0
                    );

                    -- Performance metrics table (Real User Monitoring)
                    CREATE TABLE IF NOT EXISTS perf_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        page_path TEXT NOT NULL,
                        lcp_ms REAL,
                        cls REAL,
                        fcp_ms REAL,
                        ttfb_ms REAL,
                        inp_ms REAL,
                        user_agent TEXT,
                        connection_type TEXT,
                        device_memory REAL
                    );

                    -- Circuit weather cache table (batch circuit caching)
                    CREATE TABLE IF NOT EXISTS circuit_weather (
                        circuit_id TEXT PRIMARY KEY,
                        circuit_name TEXT,
                        temperature_c REAL,
                        weather_code INTEGER,
                        precipitation_probability INTEGER,
                        fetched_at TEXT NOT NULL
                    );

                    -- Weather cache table (key-based caching with TTL)
                    CREATE TABLE IF NOT EXISTS weather_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cache_key TEXT UNIQUE NOT NULL,
                        temperature_c REAL NOT NULL,
                        weather_code INTEGER NOT NULL,
                        precipitation_probability INTEGER NOT NULL,
                        cached_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );

                    -- Create indexes (note: idx_api_calls_race created after migrations)
                    CREATE INDEX IF NOT EXISTS idx_images_key ON generated_images(image_key);
                    CREATE INDEX IF NOT EXISTS idx_stats_timestamp ON request_stats(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_perf_metrics_timestamp
                        ON perf_metrics(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_weather_cache_key ON weather_cache(cache_key);
                    CREATE INDEX IF NOT EXISTS idx_weather_cache_expires
                        ON weather_cache(expires_at);
                """
                )
                await conn.commit()

                # Run migrations for existing databases
                await self._run_migrations(conn)

                # Create index on year/round AFTER migrations ensure columns exist
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_api_calls_race ON api_calls(year, round)"
                )
                await conn.commit()

                logger.info("Database initialized at %s", self.db_path)
                with cls.schema_state_lock:
                    cls.initialized_paths.add(self.db_path)
        finally:
            path_lock.release()

    @staticmethod
    async def _run_migrations(conn: aiosqlite.Connection) -> None:
        """
        Run database migrations for existing databases.

        Adds missing columns to tables that may have been created before schema updates.
        """
        # Get existing columns in api_calls table
        async with conn.execute("PRAGMA table_info(api_calls)") as cursor:
            rows = await cursor.fetchall()
            existing_columns = {row[1] for row in rows}  # column name is at index 1

        # Define columns that should exist (added in later versions)
        migrations = [
            ("year", "INTEGER"),
            ("round", "INTEGER"),
            ("display_type", "TEXT"),
            ("race_name", "TEXT"),
            ("is_auto_selected", "INTEGER DEFAULT 0"),
        ]

        for column_name, column_type in migrations:
            if column_name not in existing_columns:
                logger.info("Migration: Adding column '%s' to api_calls table", column_name)
                await conn.execute(f"ALTER TABLE api_calls ADD COLUMN {column_name} {column_type}")

        await conn.commit()

    async def save_generated_image(
        self,
        image_key: str,
        image_path: str,
        lang: str,
        season: int | None = None,
        round_num: int | None = None,
    ) -> None:
        """
        Record a generated image in the database.

        Args:
            image_key: Unique key for the image (e.g., "calendar_en")
            image_path: Path to the generated image file
            lang: Language code
            season: Optional season year
            round_num: Optional round number
        """
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO generated_images
                    (image_key, image_path, lang, season, round, generated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(image_key) DO UPDATE SET
                    image_path = excluded.image_path,
                    generated_at = excluded.generated_at
            """,
                (
                    image_key,
                    image_path,
                    lang,
                    season,
                    round_num,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()

    async def get_image_path(self, image_key: str) -> Optional[str]:
        """
        Get the path to a generated image.

        Args:
            image_key: Unique key for the image

        Returns:
            Path to image file or None
        """
        await self._init_db_if_needed()
        async with (
            self._get_connection() as conn,
            conn.execute(
                "SELECT image_path FROM generated_images WHERE image_key = ?",
                (image_key,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return row["image_path"]
            return None

    async def set_cache_meta(self, key: str, value: str) -> None:
        """Set a cache metadata value."""
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO cache_meta (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """,
                (key, value, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()

    async def get_cache_meta(self, key: str) -> Optional[str]:
        """Get a cache metadata value."""
        await self._init_db_if_needed()
        async with (
            self._get_connection() as conn,
            conn.execute(
                "SELECT value FROM cache_meta WHERE key = ?",
                (key,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return row["value"]
            return None

    async def get_request_stats_history(self, limit: int = 168) -> list[dict]:
        """
        Get historical request statistics derived from stored API calls.

        Args:
            limit: Maximum number of records to return (default 168 = 7 days of hourly data)

        Returns:
            List of stats records with timestamp, hour_count, day_count
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=limit + 24)).isoformat()
        async with (
            self._get_connection() as conn,
            conn.execute(
                """
                WITH hourly AS (
                    SELECT
                        substr(timestamp, 1, 13) || ':00:00+00:00' AS timestamp,
                        COUNT(*) AS hour_count
                    FROM api_calls
                    WHERE timestamp > ?
                    GROUP BY substr(timestamp, 1, 13)
                ),
                annotated AS (
                    SELECT
                        timestamp,
                        hour_count,
                        (
                            SELECT COALESCE(SUM(h2.hour_count), 0)
                            FROM hourly h2
                            WHERE datetime(h2.timestamp)
                                BETWEEN datetime(hourly.timestamp, '-23 hours')
                                AND datetime(hourly.timestamp)
                        ) AS day_count
                    FROM hourly
                )
                SELECT timestamp, hour_count, day_count
                FROM annotated
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (cutoff, limit),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return [
                {
                    "timestamp": row["timestamp"],
                    "hour_count": row["hour_count"],
                    "day_count": row["day_count"],
                }
                for row in rows
            ]

    async def cleanup_old_stats(self, days: int = 30) -> int:
        """
        Remove stats rows older than specified days across all metrics tables.

        Args:
            days: Number of days to keep

        Returns:
            Number of deleted records across request_stats, api_calls, and perf_metrics
        """
        await self._init_db_if_needed()
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        deleted_counts: dict[str, int] = {}

        async with self._get_connection() as conn:
            try:
                for table_name, query in STATS_CLEANUP_QUERIES.items():
                    cursor = await conn.execute(query, (cutoff_date,))
                    deleted_counts[table_name] = cursor.rowcount
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        deleted_total = sum(deleted_counts.values())
        if deleted_total > 0:
            logger.info(
                "Cleaned up %s old stats records (request_stats=%s, api_calls=%s, perf_metrics=%s)",
                deleted_total,
                deleted_counts.get("request_stats", 0),
                deleted_counts.get("api_calls", 0),
                deleted_counts.get("perf_metrics", 0),
            )
        return deleted_total

    async def save_api_calls_batch(self, calls: list[dict]) -> int:
        """
        Bulk insert API calls to database.

        Args:
            calls: List of call dictionaries with keys:
                   timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                   year, round, display_type, race_name, is_auto_selected

        Returns:
            Number of inserted records
        """
        if not calls:
            return 0

        valid_calls = [
            call
            for call in calls
            if isinstance(call.get("timestamp"), str)
            and call["timestamp"]
            and isinstance(call.get("endpoint"), str)
            and call["endpoint"]
        ]
        skipped_count = len(calls) - len(valid_calls)
        if skipped_count:
            logger.warning("Skipping %s malformed API-call records", skipped_count)
        if not valid_calls:
            return 0

        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await conn.executemany(
                """
                INSERT INTO api_calls
                    (timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                     year, round, display_type, race_name, is_auto_selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        call.get("timestamp"),
                        call.get("endpoint"),
                        call.get("response_time_ms"),
                        call.get("response_size_bytes"),
                        call.get("lang"),
                        call.get("tz"),
                        call.get("year"),
                        call.get("round"),
                        call.get("display_type"),
                        call.get("race_name"),
                        call.get("is_auto_selected", 0),
                    )
                    for call in valid_calls
                ],
            )
            await conn.commit()
            logger.debug("Saved %s API calls to database", len(valid_calls))
            return len(valid_calls)

    async def get_api_calls_stats_24h(self) -> dict:
        """
        Get API call statistics for the last 24 hours.

        Returns:
            Dictionary with:
                - count_24h: Number of calls in last 24 hours
                - avg_response_ms: Average response time in ms (or None)
                - total_bytes_24h: Total bytes transferred (or 0)
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT
                COUNT(*) as count,
                AVG(response_time_ms) as avg_ms,
                COALESCE(SUM(response_size_bytes), 0) as total_bytes
            FROM api_calls
            WHERE timestamp > ?
            """,
                (cutoff,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                avg_ms = round(row["avg_ms"], 1) if row["avg_ms"] is not None else None
                return {
                    "count_24h": row["count"] or 0,
                    "avg_response_ms": avg_ms,
                    "total_bytes_24h": row["total_bytes"] or 0,
                }
            return {
                "count_24h": 0,
                "avg_response_ms": None,
                "total_bytes_24h": 0,
            }

    async def get_stats_for_range(self, hours: int) -> dict:
        """
        Return aggregated API/usage statistics for the past N hours.

        Parameters:
            hours: Number of hours to look back (e.g., 1, 24, 168, 720).

        Returns:
            dict with total_requests, min/avg/max_response_ms, total_bytes,
            endpoints, languages, calendar display_types, teams_display_types, timezones,
            hourly, and races breakdowns.
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            # Basic stats: count, response times, data transfer
            async with conn.execute(
                """
                SELECT
                    COUNT(*) as total_requests,
                    MIN(response_time_ms) as min_ms,
                    AVG(response_time_ms) as avg_ms,
                    MAX(response_time_ms) as max_ms,
                    COALESCE(SUM(response_size_bytes), 0) as total_bytes
                FROM api_calls
                WHERE timestamp > ?
                """,
                (cutoff,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    basic_stats = {
                        "total_requests": row["total_requests"] or 0,
                        "min_response_ms": round(row["min_ms"], 1) if row["min_ms"] else 0,
                        "avg_response_ms": round(row["avg_ms"], 1) if row["avg_ms"] else 0,
                        "max_response_ms": round(row["max_ms"], 1) if row["max_ms"] else 0,
                        "total_bytes": row["total_bytes"] or 0,
                    }
                else:
                    basic_stats = {
                        "total_requests": 0,
                        "min_response_ms": 0,
                        "avg_response_ms": 0,
                        "max_response_ms": 0,
                        "total_bytes": 0,
                    }

            # Endpoint breakdown
            async with conn.execute(
                """
                SELECT endpoint, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                GROUP BY endpoint
                ORDER BY count DESC
                LIMIT 10
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                endpoint_stats = [
                    {"endpoint": row["endpoint"], "count": row["count"]} for row in rows
                ]

            # Language breakdown
            async with conn.execute(
                """
                SELECT lang, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ? AND lang IS NOT NULL
                GROUP BY lang
                ORDER BY count DESC
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                language_stats = [{"lang": row["lang"], "count": row["count"]} for row in rows]

            # Display breakdown - calendar requests
            async with conn.execute(
                """
                SELECT display_type, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                    AND endpoint = '/calendar.bmp'
                    AND display_type IS NOT NULL
                    AND display_type != ''
                GROUP BY display_type
                ORDER BY count DESC
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                display_stats = [
                    {"display_type": row["display_type"], "count": row["count"]} for row in rows
                ]

            # Display breakdown - teams requests
            async with conn.execute(
                """
                SELECT COALESCE(NULLIF(display_type, ''), '1bit') as display_type, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                    AND endpoint = '/teams.bmp'
                GROUP BY COALESCE(NULLIF(display_type, ''), '1bit')
                ORDER BY count DESC
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                teams_display_stats = [
                    {"display_type": row["display_type"], "count": row["count"]} for row in rows
                ]

            # Timezone breakdown (top 10)
            async with conn.execute(
                """
                SELECT tz, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ? AND tz IS NOT NULL AND tz != ''
                GROUP BY tz
                ORDER BY count DESC
                LIMIT 10
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                timezone_stats = [{"tz": row["tz"], "count": row["count"]} for row in rows]

            # Hourly breakdown (for charts) - last 24 data points max
            chart_hours = min(hours, 24)
            chart_cutoff = (datetime.now(timezone.utc) - timedelta(hours=chart_hours)).isoformat()
            async with conn.execute(
                """
                SELECT
                    strftime('%Y-%m-%d %H:00', timestamp) as hour,
                    COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                GROUP BY hour
                ORDER BY hour ASC
                """,
                (chart_cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                hourly_stats = [{"hour": row["hour"], "count": row["count"]} for row in rows]

            # Race breakdown (top 10) - only for /calendar.bmp endpoint
            async with conn.execute(
                """
                SELECT
                    year,
                    round,
                    race_name,
                    SUM(CASE WHEN is_auto_selected = 1 THEN 1 ELSE 0 END) as auto_selected_count,
                    COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                    AND endpoint = '/calendar.bmp'
                    AND race_name IS NOT NULL
                GROUP BY year, round, race_name
                ORDER BY count DESC
                LIMIT 10
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
                race_stats = [
                    {
                        "year": row["year"],
                        "round": row["round"],
                        "race_name": row["race_name"],
                        "is_auto_selected": bool(row["auto_selected_count"]),
                        "auto_selected_count": row["auto_selected_count"],
                        "count": row["count"],
                    }
                    for row in rows
                ]

            return {
                **basic_stats,
                "endpoints": endpoint_stats,
                "languages": language_stats,
                "display_types": display_stats,
                "teams_display_types": teams_display_stats,
                "timezones": timezone_stats,
                "hourly": hourly_stats,
                "races": race_stats,
            }

    async def get_api_calls_count(self, hours: int) -> int:
        """Get count of API calls in the last N hours."""
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                "SELECT COUNT(*) as count FROM api_calls WHERE timestamp > ?",
                (cutoff,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            return row["count"] if row else 0

    async def get_popular_tz_variants(
        self,
        min_requests: int = 10,
        hours: int = 24,
        limit: int = 20,
        exclude_tz: str = "Europe/Prague",
    ) -> list[dict]:
        """
        Get popular (lang, tz) combinations for next race from last N hours.

        Used by scheduler to pre-generate popular timezone variants.
        Excludes the default timezone since those are always generated.

        Args:
            min_requests: Minimum number of requests to be considered popular
            hours: Number of hours to look back
            limit: Maximum number of variants to return
            exclude_tz: Timezone to exclude (default TZ, already generated)

        Returns:
            List of dicts: [{"lang": "en", "tz": "America/New_York", "count": 150}, ...]
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT lang, tz, COUNT(*) as count
            FROM api_calls
            WHERE timestamp > ?
              AND endpoint = '/calendar.bmp'
              AND tz IS NOT NULL
              AND tz != ''
              AND tz != ?
              AND is_auto_selected = 1
            GROUP BY lang, tz
            HAVING COUNT(*) >= ?
            ORDER BY count DESC
            LIMIT ?
            """,
                (cutoff, exclude_tz, min_requests, limit),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return [{"lang": row["lang"], "tz": row["tz"], "count": row["count"]} for row in rows]

    async def save_perf_metric(
        self,
        page_path: str,
        lcp_ms: float | None = None,
        cls: float | None = None,
        fcp_ms: float | None = None,
        ttfb_ms: float | None = None,
        inp_ms: float | None = None,
        user_agent: str | None = None,
        connection_type: str | None = None,
        device_memory: float | None = None,
    ) -> None:
        """Persist a single client-side performance metric payload."""
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO perf_metrics
                    (timestamp, page_path, lcp_ms, cls, fcp_ms, ttfb_ms, inp_ms,
                     user_agent, connection_type, device_memory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    page_path,
                    lcp_ms,
                    cls,
                    fcp_ms,
                    ttfb_ms,
                    inp_ms,
                    user_agent,
                    connection_type,
                    device_memory,
                ),
            )
            await conn.commit()

    async def get_perf_stats(self, hours: int = 24) -> dict:
        """Return aggregate Web Vitals statistics for the lookback window."""
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            # Get aggregate stats
            async with conn.execute(
                """
                SELECT
                    COUNT(*) as sample_count,
                    AVG(lcp_ms) as avg_lcp,
                    AVG(cls) as avg_cls,
                    AVG(fcp_ms) as avg_fcp,
                    AVG(ttfb_ms) as avg_ttfb,
                    AVG(inp_ms) as avg_inp,
                    MIN(lcp_ms) as min_lcp,
                    MAX(lcp_ms) as max_lcp,
                    MIN(ttfb_ms) as min_ttfb,
                    MAX(ttfb_ms) as max_ttfb
                FROM perf_metrics
                WHERE timestamp > ?
                """,
                (cutoff,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row or row["sample_count"] == 0:
                return {
                    "sample_count": 0,
                    "percentile_sample_count": 0,
                    "lcp": {
                        "avg": None,
                        "min": None,
                        "max": None,
                        "p50": None,
                        "p75": None,
                        "p95": None,
                    },
                    "cls": {"avg": None, "p50": None, "p75": None, "p95": None},
                    "fcp": {"avg": None, "p50": None, "p75": None, "p95": None},
                    "ttfb": {
                        "avg": None,
                        "min": None,
                        "max": None,
                        "p50": None,
                        "p75": None,
                        "p95": None,
                    },
                    "inp": {"avg": None, "p50": None, "p75": None, "p95": None},
                }

            # Bound percentile memory and collapse five full scans into one recent-sample query.
            async with conn.execute(
                """SELECT lcp_ms, cls, fcp_ms, ttfb_ms, inp_ms
                FROM perf_metrics
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT ?""",
                (cutoff, PERF_STATS_SAMPLE_LIMIT),
            ) as cursor:
                samples = list(await cursor.fetchall())
            lcp_values = sorted(r["lcp_ms"] for r in samples if r["lcp_ms"] is not None)
            cls_values = sorted(r["cls"] for r in samples if r["cls"] is not None)
            fcp_values = sorted(r["fcp_ms"] for r in samples if r["fcp_ms"] is not None)
            ttfb_values = sorted(r["ttfb_ms"] for r in samples if r["ttfb_ms"] is not None)
            inp_values = sorted(r["inp_ms"] for r in samples if r["inp_ms"] is not None)

            return {
                "sample_count": row["sample_count"],
                "percentile_sample_count": len(samples),
                "lcp": {
                    "avg": round(row["avg_lcp"], 0) if row["avg_lcp"] is not None else None,
                    "min": round(row["min_lcp"], 0) if row["min_lcp"] is not None else None,
                    "max": round(row["max_lcp"], 0) if row["max_lcp"] is not None else None,
                    "p50": self._calculate_percentile(lcp_values, 50),
                    "p75": self._calculate_percentile(lcp_values, 75),
                    "p95": self._calculate_percentile(lcp_values, 95),
                },
                "cls": {
                    "avg": round(row["avg_cls"], 3) if row["avg_cls"] is not None else None,
                    "p50": self._calculate_percentile_fine(cls_values, 50),
                    "p75": self._calculate_percentile_fine(cls_values, 75),
                    "p95": self._calculate_percentile_fine(cls_values, 95),
                },
                "fcp": {
                    "avg": round(row["avg_fcp"], 0) if row["avg_fcp"] is not None else None,
                    "p50": self._calculate_percentile(fcp_values, 50),
                    "p75": self._calculate_percentile(fcp_values, 75),
                    "p95": self._calculate_percentile(fcp_values, 95),
                },
                "ttfb": {
                    "avg": round(row["avg_ttfb"], 0) if row["avg_ttfb"] is not None else None,
                    "min": round(row["min_ttfb"], 0) if row["min_ttfb"] is not None else None,
                    "max": round(row["max_ttfb"], 0) if row["max_ttfb"] is not None else None,
                    "p50": self._calculate_percentile(ttfb_values, 50),
                    "p75": self._calculate_percentile(ttfb_values, 75),
                    "p95": self._calculate_percentile(ttfb_values, 95),
                },
                "inp": {
                    "avg": round(row["avg_inp"], 0) if row["avg_inp"] is not None else None,
                    "p50": self._calculate_percentile(inp_values, 50),
                    "p75": self._calculate_percentile(inp_values, 75),
                    "p95": self._calculate_percentile(inp_values, 95),
                },
            }

    async def get_perf_stats_by_page(self, hours: int = 24) -> list[dict]:
        """Return aggregate Web Vitals statistics grouped by page path."""
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT
                page_path,
                COUNT(*) as sample_count,
                AVG(lcp_ms) as avg_lcp,
                AVG(cls) as avg_cls,
                AVG(fcp_ms) as avg_fcp,
                AVG(ttfb_ms) as avg_ttfb
            FROM perf_metrics
            WHERE timestamp > ?
            GROUP BY page_path
            ORDER BY sample_count DESC
            LIMIT 10
            """,
                (cutoff,),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return [
                {
                    "page": row["page_path"],
                    "samples": row["sample_count"],
                    "lcp": round(row["avg_lcp"], 0) if row["avg_lcp"] is not None else None,
                    "cls": round(row["avg_cls"], 3) if row["avg_cls"] is not None else None,
                    "fcp": round(row["avg_fcp"], 0) if row["avg_fcp"] is not None else None,
                    "ttfb": round(row["avg_ttfb"], 0) if row["avg_ttfb"] is not None else None,
                }
                for row in rows
            ]

    @staticmethod
    def _calculate_percentile(values: list[float], percentile: int) -> float | None:
        """Calculate a rounded percentile for coarse timing metrics."""
        if not values:
            return None
        n = len(values)
        idx = (percentile / 100) * (n - 1)
        lower = int(idx)
        upper = lower + 1
        if upper >= n:
            return round(values[-1], 0)
        weight = idx - lower
        return round(values[lower] * (1 - weight) + values[upper] * weight, 0)

    @staticmethod
    def _calculate_percentile_fine(values: list[float], percentile: int) -> float | None:
        """Calculate a rounded percentile for fine-grained floating-point metrics."""
        if not values:
            return None
        n = len(values)
        idx = (percentile / 100) * (n - 1)
        lower = int(idx)
        upper = lower + 1
        if upper >= n:
            return round(values[-1], 3)
        weight = idx - lower
        return round(values[lower] * (1 - weight) + values[upper] * weight, 3)

    async def get_perf_trends(self, hours: int = 24) -> dict:
        """
        Retrieve hourly trends for page performance metrics.

        Parameters:
            hours: Lookback period in hours (default 24).

        Returns:
            dict with hours, lcp, fcp, ttfb (averages or None), and samples lists.
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT
                strftime('%Y-%m-%d %H:00', timestamp) as hour,
                AVG(lcp_ms) as avg_lcp,
                AVG(fcp_ms) as avg_fcp,
                AVG(ttfb_ms) as avg_ttfb,
                COUNT(*) as samples
            FROM perf_metrics
            WHERE timestamp > ?
            GROUP BY hour
            ORDER BY hour ASC
            """,
                (cutoff,),
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return {
                "hours": [row["hour"] for row in rows],
                "lcp": [
                    round(row["avg_lcp"], 0) if row["avg_lcp"] is not None else None for row in rows
                ],
                "fcp": [
                    round(row["avg_fcp"], 0) if row["avg_fcp"] is not None else None for row in rows
                ],
                "ttfb": [
                    round(row["avg_ttfb"], 0) if row["avg_ttfb"] is not None else None
                    for row in rows
                ],
                "samples": [row["samples"] for row in rows],
            }

    # =========================================================================
    # Circuit Weather Cache Methods (batch circuit caching)
    # =========================================================================

    async def save_circuit_weather(
        self,
        circuit_id: str,
        circuit_name: str,
        temperature_c: float,
        weather_code: int,
        precipitation_probability: int,
    ) -> None:
        """Store or update cached weather for a circuit (upsert)."""
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO circuit_weather
                    (circuit_id, circuit_name, temperature_c, weather_code,
                     precipitation_probability, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(circuit_id) DO UPDATE SET
                    circuit_name = excluded.circuit_name,
                    temperature_c = excluded.temperature_c,
                    weather_code = excluded.weather_code,
                    precipitation_probability = excluded.precipitation_probability,
                    fetched_at = excluded.fetched_at
                """,
                (
                    circuit_id,
                    circuit_name,
                    temperature_c,
                    weather_code,
                    precipitation_probability,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await conn.commit()

    async def get_circuit_weather(self, circuit_id: str) -> Optional[dict]:
        """Retrieve cached weather data for a circuit (may be stale)."""
        await self._init_db_if_needed()
        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT temperature_c, weather_code, precipitation_probability, fetched_at
            FROM circuit_weather
            WHERE circuit_id = ?
            """,
                (circuit_id,),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return {
                    "temperature_c": row["temperature_c"],
                    "weather_code": row["weather_code"],
                    "precipitation_probability": row["precipitation_probability"],
                    "fetched_at": row["fetched_at"],
                }
            return None

    async def load_all_circuit_weather(self) -> dict[str, dict]:
        """Load all cached circuit weather records from the database."""
        await self._init_db_if_needed()
        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT circuit_id, temperature_c, weather_code,
                   precipitation_probability, fetched_at
            FROM circuit_weather
            """
            ) as cursor,
        ):
            rows = await cursor.fetchall()
            return {
                row["circuit_id"]: {
                    "temperature_c": row["temperature_c"],
                    "weather_code": row["weather_code"],
                    "precipitation_probability": row["precipitation_probability"],
                    "fetched_at": row["fetched_at"],
                }
                for row in rows
            }

    # =========================================================================
    # Weather Cache Methods (key-based caching with TTL)
    # =========================================================================

    async def save_weather_cache(
        self,
        cache_key: str,
        temperature_c: float,
        weather_code: int,
        precipitation_probability: int,
        ttl_minutes: int = 120,
    ) -> None:
        """Save weather data to database cache with TTL."""
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)

        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO weather_cache
                    (cache_key, temperature_c, weather_code, precipitation_probability,
                     cached_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    temperature_c = excluded.temperature_c,
                    weather_code = excluded.weather_code,
                    precipitation_probability = excluded.precipitation_probability,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at
                """,
                (
                    cache_key,
                    temperature_c,
                    weather_code,
                    precipitation_probability,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            await conn.commit()

    async def get_weather_cache(self, cache_key: str) -> Optional[tuple[float, int, int, str]]:
        """Get non-expired weather data plus its original cached_at timestamp."""
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc).isoformat()

        async with (
            self._get_connection() as conn,
            conn.execute(
                """
            SELECT temperature_c, weather_code, precipitation_probability, cached_at
            FROM weather_cache
            WHERE cache_key = ? AND expires_at > ?
            """,
                (cache_key, now),
            ) as cursor,
        ):
            row = await cursor.fetchone()
            if row:
                return (
                    row["temperature_c"],
                    row["weather_code"],
                    row["precipitation_probability"],
                    row["cached_at"],
                )
            return None

    async def cleanup_expired_weather_cache(self) -> int:
        """Remove expired weather cache entries."""
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM weather_cache WHERE expires_at <= ?",
                (now,),
            )
            await conn.commit()
            return cursor.rowcount
