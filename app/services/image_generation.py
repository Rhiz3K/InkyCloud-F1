"""Keep an image request's selection and shared producer in one cache generation."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps

from app.state import bmp_cache_generation

_selection: ContextVar[int | None] = ContextVar("image_selection", default=None)
_attempt: ContextVar[int] = ContextVar("image_selection_attempt", default=0)
_MAX_RESELECTIONS = 2


class StaleSelection(Exception):
    """A queued producer acquired its slot after its inputs expired."""


def selected_generation() -> int:
    """Return the generation that supplied this request's inputs."""
    selected = _selection.get()
    return bmp_cache_generation() if selected is None else selected


def require_current_selection(generation: int) -> None:
    """Re-select outdated inputs while the request has retries left."""
    if generation != bmp_cache_generation() and _attempt.get() < _MAX_RESELECTIONS:
        raise StaleSelection


def retrying_selection() -> bool:
    """Return whether this request already passed route admission."""
    return _attempt.get() > 0


def selection_flight_key(key: str) -> str:
    """Keep the final attempt separate from producers that can still reject stale inputs."""
    return f"final-selection:{key}" if _attempt.get() == _MAX_RESELECTIONS else key


def with_current_selection[**P, T](
    operation: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Re-resolve all inputs and share the current flight when a queued selection expires."""

    @wraps(operation)
    async def resolve(*args: P.args, **kwargs: P.kwargs) -> T:
        """Capture selection before the first lookup and retry outside the render slot."""
        for attempt in range(_MAX_RESELECTIONS):
            with _selection.set(bmp_cache_generation()), _attempt.set(attempt):
                try:
                    return await operation(*args, **kwargs)
                except StaleSelection:
                    continue
        # Re-resolve once more, then let the producer's generation guard decide cacheability.
        with _selection.set(bmp_cache_generation()), _attempt.set(_MAX_RESELECTIONS):
            return await operation(*args, **kwargs)

    return resolve
