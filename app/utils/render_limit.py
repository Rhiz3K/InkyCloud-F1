"""Bound concurrent uncached image renders.

Cache hits must not call :func:`render_slot`. The permit is taken inside the
singleflight producer, not by each waiting request, and it is held until that
render finishes. Nested calls on the same task reuse permits that task already
holds. A child task does not inherit them: ``asyncio`` copies the context, so
the reservation records its owning task.

Every admission takes the global permit before a variant permit and releases
in the opposite order. Both waits share one deadline, so the total wait is at
most ``RENDER_SLOT_WAIT_SECONDS``. Cancelling the request does not free the
permit while its worker thread is still running.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException

from app.config import config

logger = logging.getLogger(__name__)

_Gate = tuple[int, asyncio.AbstractEventLoop, asyncio.Semaphore]
_gates: dict[str, _Gate] = {}


@dataclass(frozen=True)
class _Reservation:
    """Render permits held by one task."""

    owner: asyncio.Task[object]
    global_semaphore: asyncio.Semaphore
    variant_semaphore: asyncio.Semaphore | None


_reservation: ContextVar[_Reservation | None] = ContextVar("render_reservation", default=None)
_inflight: ContextVar[list[Future[Any]] | None] = ContextVar("render_inflight", default=None)
_inflight_owner: ContextVar[asyncio.Task[object] | None] = ContextVar(
    "render_inflight_owner", default=None
)


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


def render_jobs() -> list[Future[Any]] | None:
    """Return workers tracked for this task's render slot, ignoring a copied parent slot."""
    if _inflight_owner.get() is not asyncio.current_task():
        return None
    return _inflight.get()


def _attach_inflight() -> tuple[
    list[Future[Any]],
    Token[list[Future[Any]] | None] | None,
    Token[asyncio.Task[object] | None] | None,
]:
    """Reuse this task's in-flight list, or start one for the acquiring slot."""
    existing = render_jobs()
    if existing is not None:
        return existing, None, None
    created: list[Future[Any]] = []
    return created, _inflight.set(created), _inflight_owner.set(asyncio.current_task())


def _release_after(
    futures: list[Future[Any]],
    release: Callable[[], None],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Release permits now, or when every still-running worker finishes."""
    pending = [future for future in futures if not future.done()]
    if not pending:
        release()
        return
    remaining = len(pending)

    def _finished(_future: Future[Any]) -> None:
        """Count one worker from whichever thread completed it."""

        def _step() -> None:
            """Release once the last deferred worker has finished."""
            nonlocal remaining
            remaining -= 1
            if remaining == 0:
                release()

        loop.call_soon_threadsafe(_step)

    for future in pending:
        future.add_done_callback(_finished)


def _owned_reservation() -> _Reservation | None:
    """Return this task's reservation, ignoring one copied onto a child task."""
    held = _reservation.get()
    if held is None or held.owner is not asyncio.current_task():
        return None
    return held


def _retry_after(wait_seconds: float) -> str:
    """Return the whole configured wait as a positive Retry-After value."""
    return str(max(1, math.ceil(wait_seconds)))


def _busy(wait_seconds: float) -> HTTPException:
    """Build the 503 returned when the shared wait budget runs out."""
    logger.warning("Uncached image render waited %.1fs without a free slot", wait_seconds)
    return HTTPException(
        status_code=503,
        detail="Rendering is busy; retry shortly",
        headers={"Retry-After": _retry_after(wait_seconds)},
    )


async def _acquire(semaphore: asyncio.Semaphore, deadline: float, wait_seconds: float) -> None:
    """Take one permit before the shared deadline, or reject the render."""
    remaining = deadline - asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(semaphore.acquire(), max(0.0, remaining))
    except TimeoutError:
        raise _busy(wait_seconds) from None


@asynccontextmanager
async def render_slot(*, variant: bool = False) -> AsyncIterator[None]:
    """Wait for permission to render one uncached image.

    ``variant`` also takes a permit from the smaller extra/preview pool, and
    only after the global permit. When the pools do not open before
    ``RENDER_SLOT_WAIT_SECONDS``, this raises HTTP 503 with ``Retry-After``
    set to that whole budget.
    """
    held = _owned_reservation()
    needs_variant = variant and (held is None or held.variant_semaphore is None)
    if held is not None and not needs_variant:
        yield
        return

    global_limit, variant_limit, timeout = _current_limits()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    inflight, list_token, owner_token = _attach_inflight()
    acquired_global: asyncio.Semaphore | None = None
    acquired_variant: asyncio.Semaphore | None = None
    try:
        if held is None:
            global_semaphore = _semaphore("global", global_limit)
            await _acquire(global_semaphore, deadline, timeout)
            acquired_global = global_semaphore
            reserved_global = global_semaphore
        else:
            reserved_global = held.global_semaphore
        if needs_variant:
            variant_semaphore = _semaphore("variant", variant_limit)
            await _acquire(variant_semaphore, deadline, timeout)
            acquired_variant = variant_semaphore
        owner = cast(asyncio.Task[object], asyncio.current_task())
        reservation = _Reservation(owner, reserved_global, acquired_variant)
        token = _reservation.set(reservation)
        try:
            yield
        finally:
            _reservation.reset(token)
    finally:
        pending = list(inflight)
        if list_token is not None:
            _inflight.reset(list_token)
        if owner_token is not None:
            _inflight_owner.reset(owner_token)

        def release() -> None:
            """Return whichever permits this slot managed to acquire."""
            if acquired_variant is not None:
                acquired_variant.release()
            if acquired_global is not None:
                acquired_global.release()

        if acquired_global is not None or acquired_variant is not None:
            _release_after(pending, release, loop)
