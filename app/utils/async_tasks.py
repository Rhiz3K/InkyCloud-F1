"""Helpers for supervised background asyncio tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

import sentry_sdk

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


async def _run_supervised(coro: Awaitable[Any], name: str) -> Any:
    """Run a background coroutine and surface failures through logging and Sentry."""
    try:
        return await coro
    except asyncio.CancelledError:
        logger.debug("Background task cancelled: %s", name)
        raise
    except Exception as exc:
        logger.error("Background task failed: %s", name, exc_info=True)
        sentry_sdk.capture_exception(exc)
        return None


def create_supervised_task(coro: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
    """Create a tracked background task so exceptions are never dropped silently."""
    task = asyncio.create_task(_run_supervised(coro, name), name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
