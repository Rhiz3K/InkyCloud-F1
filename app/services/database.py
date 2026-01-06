"""Database service for caching metadata and statistics in SQLite."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from app.config import config

logger = logging.getLogger(__name__)


class Database:
    """
    Async SQLite database for metadata and statistics.

    Note: Race data and historical results are now stored in static JSON files.
    This database only stores:
    - Generated image metadata
    - Cache timestamps
    - Request statistics
    """

    def __init__(self, db_path: str | None = None):
        """
        Initialize database.

        Args:
            db_path: Path to SQLite database file. Defaults to config value.
        """
        self.db_path = db_path or config.DATABASE_PATH
        self._ensure_directory()
        self._initialized = False

    def _ensure_directory(self) -> None:
        """Ensure the database directory exists."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> aiosqlite.Connection:
        """Get a database connection context manager with WAL mode enabled."""
        return aiosqlite.connect(self.db_path)

    async def _configure_connection(self, conn: aiosqlite.Connection) -> None:
        """Configure connection settings after it's opened."""
        await conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = aiosqlite.Row

    async def _init_db_if_needed(self) -> None:
        """Initialize database schema if not already done."""
        if self._initialized:
            return

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
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

                -- Weather cache table
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
                CREATE INDEX IF NOT EXISTS idx_perf_metrics_timestamp ON perf_metrics(timestamp);
                CREATE INDEX IF NOT EXISTS idx_weather_cache_key ON weather_cache(cache_key);
                CREATE INDEX IF NOT EXISTS idx_weather_cache_expires ON weather_cache(expires_at);
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

            logger.info(f"Database initialized at {self.db_path}")
        self._initialized = True

    async def _run_migrations(self, conn: aiosqlite.Connection) -> None:
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
            ("race_name", "TEXT"),
            ("is_auto_selected", "INTEGER DEFAULT 0"),
        ]

        for column_name, column_type in migrations:
            if column_name not in existing_columns:
                logger.info(f"Migration: Adding column '{column_name}' to api_calls table")
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
            await self._configure_connection(conn)
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
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                "SELECT image_path FROM generated_images WHERE image_key = ?",
                (image_key,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row["image_path"]
                return None

    async def set_cache_meta(self, key: str, value: str) -> None:
        """Set a cache metadata value."""
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
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
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row["value"]
                return None

    async def save_request_stats(self, hour_count: int, day_count: int) -> None:
        """
        Save current request statistics snapshot.

        Args:
            hour_count: Number of requests in last hour
            day_count: Number of requests in last 24 hours
        """
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            await conn.execute(
                """
                INSERT INTO request_stats (timestamp, hour_count, day_count)
                VALUES (?, ?, ?)
                """,
                (datetime.now(timezone.utc).isoformat(), hour_count, day_count),
            )
            await conn.commit()
            logger.debug(f"Saved request stats: hour={hour_count}, day={day_count}")

    async def get_request_stats_history(self, limit: int = 168) -> list[dict]:
        """
        Get historical request statistics.

        Args:
            limit: Maximum number of records to return (default 168 = 7 days of hourly data)

        Returns:
            List of stats records with timestamp, hour_count, day_count
        """
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                """
                SELECT timestamp, hour_count, day_count
                FROM request_stats
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
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
        Remove request stats older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of deleted records
        """
        await self._init_db_if_needed()
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            cursor = await conn.execute(
                "DELETE FROM request_stats WHERE timestamp < ?", (cutoff_date,)
            )
            deleted = cursor.rowcount
            await conn.commit()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old stats records")
            return deleted

    async def save_api_calls_batch(self, calls: list[dict]) -> int:
        """
        Bulk insert API calls to database.

        Args:
            calls: List of call dictionaries with keys:
                   timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                   year, round, race_name, is_auto_selected

        Returns:
            Number of inserted records
        """
        if not calls:
            return 0

        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            await conn.executemany(
                """
                INSERT INTO api_calls
                    (timestamp, endpoint, response_time_ms, response_size_bytes, lang, tz,
                     year, round, race_name, is_auto_selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        call["timestamp"],
                        call["endpoint"],
                        call.get("response_time_ms"),
                        call.get("response_size_bytes"),
                        call.get("lang"),
                        call.get("tz"),
                        call.get("year"),
                        call.get("round"),
                        call.get("race_name"),
                        call.get("is_auto_selected", 0),
                    )
                    for call in calls
                ],
            )
            await conn.commit()
            logger.debug(f"Saved {len(calls)} API calls to database")
            return len(calls)

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

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                """
                SELECT
                    COUNT(*) as count,
                    AVG(response_time_ms) as avg_ms,
                    COALESCE(SUM(response_size_bytes), 0) as total_bytes
                FROM api_calls
                WHERE timestamp > ?
                """,
                (cutoff,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    avg_ms = round(row["avg_ms"], 1) if row["avg_ms"] else None
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
        Get comprehensive statistics for a given time range.

        Args:
            hours: Number of hours to look back (1, 24, 168 for 7d, 720 for 30d)

        Returns:
            Dictionary with all stats for the dashboard
        """
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)

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
                basic_stats = {
                    "total_requests": row["total_requests"] or 0,
                    "min_response_ms": round(row["min_ms"], 1) if row["min_ms"] else 0,
                    "avg_response_ms": round(row["avg_ms"], 1) if row["avg_ms"] else 0,
                    "max_response_ms": round(row["max_ms"], 1) if row["max_ms"] else 0,
                    "total_bytes": row["total_bytes"] or 0,
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
                    is_auto_selected,
                    COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                    AND endpoint = '/calendar.bmp'
                    AND race_name IS NOT NULL
                GROUP BY year, round, race_name, is_auto_selected
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
                        "is_auto_selected": bool(row["is_auto_selected"]),
                        "count": row["count"],
                    }
                    for row in rows
                ]

            return {
                **basic_stats,
                "endpoints": endpoint_stats,
                "languages": language_stats,
                "timezones": timezone_stats,
                "hourly": hourly_stats,
                "races": race_stats,
            }

    async def get_api_calls_count(self, hours: int) -> int:
        """Get count of API calls in the last N hours."""
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                "SELECT COUNT(*) as count FROM api_calls WHERE timestamp > ?",
                (cutoff,),
            ) as cursor:
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

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                """
                SELECT lang, tz, COUNT(*) as count
                FROM api_calls
                WHERE timestamp > ?
                  AND endpoint = '/calendar.bmp'
                  AND tz IS NOT NULL
                  AND tz != ''
                  AND tz != ?
                  AND year IS NULL
                  AND round IS NULL
                GROUP BY lang, tz
                HAVING COUNT(*) >= ?
                ORDER BY count DESC
                LIMIT ?
                """,
                (cutoff, exclude_tz, min_requests, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"lang": row["lang"], "tz": row["tz"], "count": row["count"]} for row in rows
                ]

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
        await self._init_db_if_needed()
        async with self._get_connection() as conn:
            await self._configure_connection(conn)
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
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)

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

            # Fetch raw values for percentile calculation (sorted)
            async with conn.execute(
                """SELECT lcp_ms FROM perf_metrics
                WHERE timestamp > ? AND lcp_ms IS NOT NULL ORDER BY lcp_ms""",
                (cutoff,),
            ) as cursor:
                lcp_values = [r["lcp_ms"] for r in await cursor.fetchall()]

            async with conn.execute(
                """SELECT cls FROM perf_metrics
                WHERE timestamp > ? AND cls IS NOT NULL ORDER BY cls""",
                (cutoff,),
            ) as cursor:
                cls_values = [r["cls"] for r in await cursor.fetchall()]

            async with conn.execute(
                """SELECT fcp_ms FROM perf_metrics
                WHERE timestamp > ? AND fcp_ms IS NOT NULL ORDER BY fcp_ms""",
                (cutoff,),
            ) as cursor:
                fcp_values = [r["fcp_ms"] for r in await cursor.fetchall()]

            async with conn.execute(
                """SELECT ttfb_ms FROM perf_metrics
                WHERE timestamp > ? AND ttfb_ms IS NOT NULL ORDER BY ttfb_ms""",
                (cutoff,),
            ) as cursor:
                ttfb_values = [r["ttfb_ms"] for r in await cursor.fetchall()]

            async with conn.execute(
                """SELECT inp_ms FROM perf_metrics
                WHERE timestamp > ? AND inp_ms IS NOT NULL ORDER BY inp_ms""",
                (cutoff,),
            ) as cursor:
                inp_values = [r["inp_ms"] for r in await cursor.fetchall()]

            return {
                "sample_count": row["sample_count"],
                "lcp": {
                    "avg": round(row["avg_lcp"], 0) if row["avg_lcp"] else None,
                    "min": round(row["min_lcp"], 0) if row["min_lcp"] else None,
                    "max": round(row["max_lcp"], 0) if row["max_lcp"] else None,
                    "p50": self._calculate_percentile(lcp_values, 50),
                    "p75": self._calculate_percentile(lcp_values, 75),
                    "p95": self._calculate_percentile(lcp_values, 95),
                },
                "cls": {
                    "avg": round(row["avg_cls"], 3) if row["avg_cls"] else None,
                    "p50": self._calculate_percentile_fine(cls_values, 50),
                    "p75": self._calculate_percentile_fine(cls_values, 75),
                    "p95": self._calculate_percentile_fine(cls_values, 95),
                },
                "fcp": {
                    "avg": round(row["avg_fcp"], 0) if row["avg_fcp"] else None,
                    "p50": self._calculate_percentile(fcp_values, 50),
                    "p75": self._calculate_percentile(fcp_values, 75),
                    "p95": self._calculate_percentile(fcp_values, 95),
                },
                "ttfb": {
                    "avg": round(row["avg_ttfb"], 0) if row["avg_ttfb"] else None,
                    "min": round(row["min_ttfb"], 0) if row["min_ttfb"] else None,
                    "max": round(row["max_ttfb"], 0) if row["max_ttfb"] else None,
                    "p50": self._calculate_percentile(ttfb_values, 50),
                    "p75": self._calculate_percentile(ttfb_values, 75),
                    "p95": self._calculate_percentile(ttfb_values, 95),
                },
                "inp": {
                    "avg": round(row["avg_inp"], 0) if row["avg_inp"] else None,
                    "p50": self._calculate_percentile(inp_values, 50),
                    "p75": self._calculate_percentile(inp_values, 75),
                    "p95": self._calculate_percentile(inp_values, 95),
                },
            }

    async def get_perf_stats_by_page(self, hours: int = 24) -> list[dict]:
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)

            async with conn.execute(
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
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "page": row["page_path"],
                        "samples": row["sample_count"],
                        "lcp": round(row["avg_lcp"], 0) if row["avg_lcp"] else None,
                        "cls": round(row["avg_cls"], 3) if row["avg_cls"] else None,
                        "fcp": round(row["avg_fcp"], 0) if row["avg_fcp"] else None,
                        "ttfb": round(row["avg_ttfb"], 0) if row["avg_ttfb"] else None,
                    }
                    for row in rows
                ]

    def _calculate_percentile(self, values: list[float], percentile: int) -> float | None:
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

    def _calculate_percentile_fine(self, values: list[float], percentile: int) -> float | None:
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
        await self._init_db_if_needed()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)

            async with conn.execute(
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
            ) as cursor:
                rows = await cursor.fetchall()
                return {
                    "hours": [row["hour"] for row in rows],
                    "lcp": [round(row["avg_lcp"], 0) if row["avg_lcp"] else None for row in rows],
                    "fcp": [round(row["avg_fcp"], 0) if row["avg_fcp"] else None for row in rows],
                    "ttfb": [
                        round(row["avg_ttfb"], 0) if row["avg_ttfb"] else None for row in rows
                    ],
                    "samples": [row["samples"] for row in rows],
                }

    async def save_weather_cache(
        self,
        cache_key: str,
        temperature_c: float,
        weather_code: int,
        precipitation_probability: int,
        ttl_minutes: int = 120,
    ) -> None:
        """
        Save weather data to database cache.

        Args:
            cache_key: Unique cache key (e.g., "current_-37.85_144.97")
            temperature_c: Temperature in Celsius
            weather_code: WMO weather code
            precipitation_probability: Precipitation probability percentage
            ttl_minutes: Cache TTL in minutes (default 120 = 2 hours)
        """
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=ttl_minutes)

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
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

    async def get_weather_cache(self, cache_key: str) -> Optional[tuple[float, int, int]]:
        """
        Get weather data from database cache if not expired.

        Args:
            cache_key: Cache key to lookup

        Returns:
            Tuple of (temperature_c, weather_code, precipitation_probability) or None
        """
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            async with conn.execute(
                """
                SELECT temperature_c, weather_code, precipitation_probability
                FROM weather_cache
                WHERE cache_key = ? AND expires_at > ?
                """,
                (cache_key, now),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return (
                        row["temperature_c"],
                        row["weather_code"],
                        row["precipitation_probability"],
                    )
                return None

    async def cleanup_expired_weather_cache(self) -> int:
        """
        Remove expired weather cache entries.

        Returns:
            Number of rows deleted
        """
        await self._init_db_if_needed()
        now = datetime.now(timezone.utc).isoformat()

        async with self._get_connection() as conn:
            await self._configure_connection(conn)
            cursor = await conn.execute(
                "DELETE FROM weather_cache WHERE expires_at <= ?",
                (now,),
            )
            await conn.commit()
            return cursor.rowcount
