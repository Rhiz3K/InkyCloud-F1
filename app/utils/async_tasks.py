"""Helpers for supervised background asyncio tasks and render offloading."""

from __future__ import annotations

import asyncio
import atexit
import functools
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import sentry_sdk

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()

T = TypeVar("T")


@functools.lru_cache(maxsize=1)
def _get_render_executor() -> ThreadPoolExecutor:
    """Lazily create the dedicated executor for CPU-bound Pillow rendering.

    Two workers bound the number of per-thread font caches (each CJK face costs ~2 MB heap)
    while still keeping renders off the event loop; renders queue beyond that, which matches
    the pre-offload serialization.
    """
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="render")


async def run_render(func: Callable[[], T]) -> T:
    """Run a zero-arg render callable on the dedicated render executor.

    The callable should CONSTRUCT the renderer inside itself, not just call render_*:
    renderer __init__ loads fonts into a per-thread cache, so building it on the event
    loop and rendering in a worker would share FreeTypeFont objects across threads.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_render_executor(), func)


def shutdown_render_executor(*, wait: bool = True) -> None:
    """Shut down the dedicated render executor if it has been initialized."""
    if _get_render_executor.cache_info().currsize == 0:
        return

    executor = _get_render_executor()
    _get_render_executor.cache_clear()
    executor.shutdown(wait=wait)


atexit.register(shutdown_render_executor, wait=False)


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
