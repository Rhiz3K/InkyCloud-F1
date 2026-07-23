"""Scheduled persistence and historical-data maintenance jobs."""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timedelta, timezone

from app.services.database import get_database
from app.services.historical_refresh import main as update_historical_results
from app.state import get_and_clear_api_calls_buffer, requeue_api_calls
from app.utils.async_locks import LoopLockRegistry, get_loop_lock

logger = logging.getLogger(__name__)

_historical_refresh_locks: LoopLockRegistry = weakref.WeakKeyDictionary()
_HISTORICAL_REFRESH_META_KEY = "last_historical_refresh"
_HISTORICAL_REFRESH_MAX_AGE = timedelta(days=1)


async def flush_api_calls_to_db() -> None:
    """
    Flush API calls buffer to SQLite.

    This job runs every minute to persist API call data from
    the in-memory buffer to the database.
    """
    calls = get_and_clear_api_calls_buffer()
    if not calls:
        return
    try:
        count = await get_database().save_api_calls_batch(calls)
    except asyncio.CancelledError:
        requeue_api_calls(calls)
        raise
    except Exception as e:
        requeue_api_calls(calls)
        logger.error("Error flushing API calls (re-queued %d): %s", len(calls), e, exc_info=True)
    else:
        logger.debug("Flushed %d API calls to database", count)


async def refresh_historical_results() -> None:
    """Refresh static historical results; hourly generation publishes any relevant change."""
    lock = get_loop_lock(_historical_refresh_locks)
    if lock.locked():
        logger.info("Historical results refresh already running; skipping overlapping trigger")
        return

    async with lock:
        try:
            result = await update_historical_results()
            if result.updated_circuits:
                logger.info(
                    "Historical results refreshed for %s circuits (%s); "
                    "hourly generation will publish relevant changes",
                    len(result.updated_circuits),
                    ", ".join(result.updated_circuits),
                )
            else:
                logger.info("Historical results refresh completed with no material changes")
            if not result.completed:
                logger.error(
                    "Historical refresh incomplete; not updating freshness timestamp (failed: %s)",
                    ", ".join(result.failed_circuits) or "no circuits attempted",
                )
                return
        except Exception as e:
            logger.error("Error refreshing historical results: %s", e, exc_info=True)
            return

        try:
            await get_database().set_cache_meta(
                _HISTORICAL_REFRESH_META_KEY, datetime.now(timezone.utc).isoformat()
            )
        except Exception as e:
            logger.warning("Could not persist historical refresh timestamp: %s", e)


async def _historical_refresh_is_due() -> bool:
    """Return whether startup should catch up a missed daily historical refresh."""
    try:
        raw_timestamp = await get_database().get_cache_meta(_HISTORICAL_REFRESH_META_KEY)
        if not raw_timestamp:
            return True
        refreshed_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if refreshed_at.tzinfo is None:
            refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc) >= (
            _HISTORICAL_REFRESH_MAX_AGE
        )
    except Exception as e:
        logger.warning("Could not determine historical refresh age: %s", e)
        return True
