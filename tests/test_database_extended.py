"""Defensive-path coverage for the asynchronous database service."""

from __future__ import annotations

import weakref
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from app.services import database
from app.services.database import Database


class _EmptyCursor:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def fetchone(self):
        return None

    async def fetchall(self):
        return []


class _EmptyConnection:
    def execute(self, *_args, **_kwargs):
        return _EmptyCursor()


def _empty_connection_context():
    @asynccontextmanager
    async def context():
        yield _EmptyConnection()

    return context


@pytest.mark.asyncio
async def test_stale_connection_close_failure_does_not_block_reconnect(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "reconnect.db"))
    stale = MagicMock(close=AsyncMock(side_effect=RuntimeError("already closed")))
    replacement = MagicMock(close=AsyncMock())
    db._connection = stale
    db._connection_loop = None
    monkeypatch.setattr(database.aiosqlite, "connect", AsyncMock(return_value=replacement))
    monkeypatch.setattr(db, "_configure_connection", AsyncMock())

    assert await db._ensure_connection() is replacement
    stale.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_all_closes_every_live_instance(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "close-all.db"))
    close = AsyncMock()
    monkeypatch.setattr(db, "close", close)
    monkeypatch.setattr(Database, "_instances", weakref.WeakSet([db]))

    await Database.close_all()

    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_schema_initialization_observes_path_initialized_inside_lock(tmp_path, monkeypatch):
    class AppearingPathSet:
        def __init__(self):
            self.calls = 0

        def __contains__(self, _path):
            self.calls += 1
            return self.calls == 2

    db = Database(str(tmp_path / "already-initialized.db"))
    paths = AppearingPathSet()
    monkeypatch.setattr(Database, "initialized_paths", paths)

    await db._init_db_if_needed()

    assert paths.calls == 2


@pytest.mark.asyncio
async def test_run_migrations_adds_every_missing_api_call_column():
    conn = await aiosqlite.connect(":memory:")
    try:
        await conn.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY)")

        await Database._run_migrations(conn)

        async with conn.execute("PRAGMA table_info(api_calls)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        assert {"year", "round", "display_type", "race_name", "is_auto_selected"} <= columns
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cleanup_old_stats_handles_empty_database_and_rolls_back_failures(
    tmp_path, monkeypatch
):
    db = Database(str(tmp_path / "cleanup-edges.db"))
    try:
        assert await db.cleanup_old_stats() == 0

        monkeypatch.setattr(
            database,
            "STATS_CLEANUP_QUERIES",
            {"missing": "DELETE FROM missing_table WHERE timestamp < ?"},
        )
        with pytest.raises(aiosqlite.OperationalError):
            await db.cleanup_old_stats()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_save_api_calls_batch_accepts_empty_input(tmp_path):
    db = Database(str(tmp_path / "empty-batch.db"))

    assert await db.save_api_calls_batch([]) == 0


@pytest.mark.asyncio
async def test_stats_24h_handles_missing_aggregate_row(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "missing-aggregate.db"))
    monkeypatch.setattr(db, "_init_db_if_needed", AsyncMock())
    monkeypatch.setattr(db, "_get_connection", _empty_connection_context())

    assert await db.get_api_calls_stats_24h() == {
        "count_24h": 0,
        "avg_response_ms": None,
        "total_bytes_24h": 0,
    }


@pytest.mark.asyncio
async def test_stats_range_handles_missing_aggregate_row(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "missing-range-aggregate.db"))
    monkeypatch.setattr(db, "_init_db_if_needed", AsyncMock())
    monkeypatch.setattr(db, "_get_connection", _empty_connection_context())

    result = await db.get_stats_for_range(24)

    assert result == {
        "total_requests": 0,
        "min_response_ms": 0,
        "avg_response_ms": 0,
        "max_response_ms": 0,
        "total_bytes": 0,
        "endpoints": [],
        "languages": [],
        "display_types": [],
        "teams_display_types": [],
        "timezones": [],
        "hourly": [],
        "races": [],
    }


def test_percentile_helpers_accept_empty_samples():
    assert Database._calculate_percentile([], 75) is None
    assert Database._calculate_percentile_fine([], 75) is None
