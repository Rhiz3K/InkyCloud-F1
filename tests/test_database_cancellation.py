"""Integration checks for cancellation, retries, and existing SQLite databases."""

import asyncio
import sqlite3
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.services import scheduler_maintenance
from app.services.database import Database
from app.state import get_and_clear_api_calls_buffer, record_api_call


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_cancelled_flush_is_idempotent_and_serializes_other_writers(tmp_path, committed):
    db = Database(str(tmp_path / "events.db"))
    get_and_clear_api_calls_buffer()
    try:
        await db.ping()
        conn = await db._ensure_connection()
        original_commit = conn.commit
        entered = asyncio.Event()

        async def interrupted_commit():
            if committed:
                await original_commit()
            entered.set()
            await asyncio.Event().wait()

        record_api_call("/calendar.bmp", 1.0, 100)
        with patch.object(scheduler_maintenance, "get_database", return_value=db):
            with patch.object(conn, "commit", interrupted_commit):
                task = asyncio.create_task(scheduler_maintenance.flush_api_calls_to_db())
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            await asyncio.gather(
                scheduler_maintenance.flush_api_calls_to_db(),
                db.save_perf_metric("/", fcp_ms=100),
            )
        assert await db.get_api_calls_count(24) == 1
        assert (await db.get_perf_stats())["sample_count"] == 1
    finally:
        await db.close()
        get_and_clear_api_calls_buffer()


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_rollback_before_releasing_connection(tmp_path):
    db = Database(str(tmp_path / "rollback.db"))
    try:
        await db.ping()
        conn = await db._ensure_connection()
        original = conn.rollback
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_rollback():
            started.set()
            await release.wait()
            await original()

        async def failed_write():
            async with db._get_connection() as connection:
                await connection.execute(
                    "INSERT INTO cache_meta(key, updated_at) VALUES ('uncommitted', 'today')"
                )
                raise asyncio.CancelledError

        with patch.object(conn, "rollback", slow_rollback):
            task = asyncio.create_task(failed_write())
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            other = asyncio.create_task(db.set_cache_meta("good", "kept"))
            await asyncio.sleep(0)
            assert not other.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await other
        assert await db.get_cache_meta("uncommitted") is None
        assert await db.get_cache_meta("good") == "kept"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_schema_upgrade_preserves_legacy_rows_and_deduplicates_new_events(tmp_path):
    path = tmp_path / "legacy.db"
    with ExitStack() as contexts:
        connection = contexts.enter_context(closing(sqlite3.connect(path)))
        contexts.enter_context(connection)
        connection.execute(
            "CREATE TABLE api_calls (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, "
            "endpoint TEXT NOT NULL, response_time_ms REAL, response_size_bytes INTEGER, "
            "lang TEXT, tz TEXT)"
        )
        connection.execute(
            "INSERT INTO api_calls(timestamp, endpoint) VALUES (?, '/calendar.bmp')",
            (datetime.now(timezone.utc).isoformat(),),
        )
    db = Database(str(path))
    try:
        batch = [{"timestamp": datetime.now(timezone.utc).isoformat(), "endpoint": "/calendar.bmp"}]
        assert await db.save_api_calls_batch(batch) == 1
        assert await db.save_api_calls_batch(batch) == 0
        assert await db.get_api_calls_count(24) == 2
    finally:
        await db.close()
