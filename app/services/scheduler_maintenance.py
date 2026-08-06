"""Scheduled persistence and historical-data maintenance jobs."""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timedelta, timezone
from time import monotonic

from app.config import config
from app.services.database import get_database
from app.services.historical_refresh import HistoricalRefreshResult
from app.services.historical_refresh import main as update_historical_results
from app.state import get_and_clear_api_calls_buffer, requeue_api_calls
from app.utils.async_locks import LoopLockRegistry, get_loop_lock

logger = logging.getLogger(__name__)

_historical_refresh_locks: LoopLockRegistry = weakref.WeakKeyDictionary()
_HISTORICAL_REFRESH_META_KEY = "last_historical_refresh"
_HISTORICAL_REFRESH_STREAK_META_KEY = "historical_refresh_incomplete_streak"
_HISTORICAL_REFRESH_MAX_AGE = timedelta(days=1)
_HISTORICAL_REFRESH_ALERT_AFTER_RUNS = 3


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


async def _read_incomplete_streak() -> int:
    """Return how many consecutive refresh runs ended without completing."""
    try:
        raw_streak = await get_database().get_cache_meta(_HISTORICAL_REFRESH_STREAK_META_KEY)
        return max(int(raw_streak or 0), 0)
    except TypeError, ValueError:
        return 0
    except Exception as e:
        logger.warning("Could not read historical refresh failure streak: %s", e)
        return 0


async def _store_incomplete_streak(streak: int) -> None:
    """Persist the consecutive incomplete-run counter without failing the job."""
    try:
        await get_database().set_cache_meta(_HISTORICAL_REFRESH_STREAK_META_KEY, str(streak))
    except Exception as e:
        logger.warning("Could not persist historical refresh failure streak: %s", e)


async def _report_incomplete_run(
    result: HistoricalRefreshResult,
    *,
    failure_reason: str | None = None,
    timeout_seconds: float | None = None,
) -> None:
    """Log an incomplete run, escalating to error once it recurs across runs.

    The messages stay static so the error tracker groups every recurrence into one
    issue; the varying circuit lists travel as structured record attributes instead.
    """
    streak = await _read_incomplete_streak() + 1
    await _store_incomplete_streak(streak)
    log_context: dict[str, object] = {
        "failed_circuits": list(result.failed_circuits),
        "transient_failed_circuits": list(result.transient_failed_circuits),
        "consecutive_incomplete_runs": streak,
        "advanced_freshness": result.can_advance_freshness,
        "failure_reason": failure_reason,
    }
    if timeout_seconds is not None:
        log_context["timeout_seconds"] = timeout_seconds
    if streak >= _HISTORICAL_REFRESH_ALERT_AFTER_RUNS:
        logger.error(
            "Historical refresh has not completed for several consecutive runs",
            extra=log_context,
        )
    else:
        logger.warning("Historical refresh incomplete", extra=log_context)


async def _run_historical_refresh() -> None:
    """Bound upstream refresh work, then persist its completion metadata."""
    try:
        async with asyncio.timeout(config.HISTORICAL_REFRESH_TIMEOUT_SECONDS):
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
        if result.completed:
            await _store_incomplete_streak(0)
        else:
            await _report_incomplete_run(result)
            if not result.can_advance_freshness:
                return
    except TimeoutError:
        await _report_incomplete_run(
            HistoricalRefreshResult((), (), 0),
            failure_reason="timeout",
            timeout_seconds=config.HISTORICAL_REFRESH_TIMEOUT_SECONDS,
        )
        return
    except Exception as e:
        logger.error("Error refreshing historical results: %s", e, exc_info=True)
        return

    try:
        await get_database().set_cache_meta(
            _HISTORICAL_REFRESH_META_KEY, datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:  # noqa: BLE001 - timestamp persistence is best effort
        logger.warning("Could not persist historical refresh timestamp: %s", e)


async def refresh_historical_results() -> None:
    """Run the static historical refresh once with overlap and runtime protection."""
    lock = get_loop_lock(_historical_refresh_locks)
    if lock.locked():
        logger.info("Historical results refresh already running; skipping overlapping trigger")
        return

    async with lock:
        started_at = monotonic()
        try:
            await _run_historical_refresh()
        finally:
            duration_seconds = monotonic() - started_at
            logger.info(
                "Historical refresh finished in %.2f seconds",
                duration_seconds,
                extra={"duration_seconds": duration_seconds},
            )


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
