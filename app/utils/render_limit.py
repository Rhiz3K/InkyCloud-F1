"""Bound concurrent uncached image renders.

Cache hits must not call :func:`render_slot`. The permit is taken inside the
singleflight producer, not by each waiting request, and it is held until that
render finishes. Nested calls on the same task reuse permits the task already
holds so a render cannot deadlock on its own slot.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import HTTPException

from app.config import config

logger = logging.getLogger(__name__)

_Gate = tuple[int, asyncio.AbstractEventLoop, asyncio.Semaphore]
_gates: dict[str, _Gate] = {}


@dataclass(frozen=True)
class _Reservation:
    """Render permits held by the current task."""

    global_semaphore: asyncio.Semaphore
    variant_semaphore: asyncio.Semaphore | None


_reservation: ContextVar[_Reservation | None] = ContextVar("render_reservation", default=None)


def reset_render_limits_for_tests() -> None:
    """Drop cached semaphores so later tests observe a fresh event loop and limit."""
    _gates.clear()


def _current_limits() -> tuple[int, int, float]:
    """Return the global cap, the extra/preview cap, and the wait in seconds."""
    global_limit = config.RENDER_CONCURRENCY
    variant_limit = min(global_limit, config.RENDER_VARIANT_CONCURRENCY)
    return global_limit, variant_limit, config.RENDER_SLOT_WAIT_SECONDS


def _semaphore(name: str, limit: int) -> asyncio.Semaphore:
    """Return the gate for this event loop, rebuilding it when the limit changes."""
    loop = asyncio.get_running_loop()
    cached = _gates.get(name)
    if cached is not None:
        cached_limit, cached_loop, semaphore = cached
        if cached_limit == limit and cached_loop is loop:
            return semaphore
    semaphore = asyncio.Semaphore(limit)
    _gates[name] = (limit, loop, semaphore)
    return semaphore


async def _acquire(semaphore: asyncio.Semaphore, timeout: float) -> None:
    """Take one permit, or reject the render when the wait expires."""
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout)
    except TimeoutError:
        logger.warning("Uncached image render waited %.1fs without a free slot", timeout)
        raise HTTPException(
            status_code=503,
            detail="Rendering is busy; retry shortly",
            headers={"Retry-After": str(max(1, math.ceil(timeout)))},
        ) from None


@asynccontextmanager
async def render_slot(*, variant: bool = False) -> AsyncIterator[None]:
    """Wait for permission to render one uncached image.

    ``variant`` also takes a permit from the smaller extra/preview pool. When
    that pool, or the global pool, does not open before
    ``RENDER_SLOT_WAIT_SECONDS``, this raises HTTP 503 with ``Retry-After``.
    """
    held = _reservation.get()
    if held is not None and (not variant or held.variant_semaphore is not None):
        yield
        return

    global_limit, variant_limit, timeout = _current_limits()
    acquired_variant: asyncio.Semaphore | None = None
    acquired_global: asyncio.Semaphore | None = None
    try:
        if variant:
            variant_semaphore = _semaphore("variant", variant_limit)
            await _acquire(variant_semaphore, timeout)
            acquired_variant = variant_semaphore
        if held is None:
            global_semaphore = _semaphore("global", global_limit)
            await _acquire(global_semaphore, timeout)
            acquired_global = global_semaphore
            reservation = _Reservation(global_semaphore, acquired_variant)
        else:
            reservation = _Reservation(held.global_semaphore, acquired_variant)
        token = _reservation.set(reservation)
        try:
            yield
        finally:
            _reservation.reset(token)
    finally:
        if acquired_global is not None:
            acquired_global.release()
        if acquired_variant is not None:
            acquired_variant.release()
