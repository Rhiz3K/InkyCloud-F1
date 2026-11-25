"""Keep an image request's selection and shared producer in one cache generation."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from functools import wraps

from app.state import bmp_cache_generation

_selection: ContextVar[int | None] = ContextVar("image_selection", default=None)
_retrying: ContextVar[bool] = ContextVar("retrying_image_selection", default=False)


class StaleSelection(Exception):
    """A queued producer acquired its slot after its inputs expired."""


def selected_generation() -> int:
    """Return the generation that supplied this request's inputs."""
    selected = _selection.get()
    return bmp_cache_generation() if selected is None else selected


def require_current_selection(generation: int) -> None:
    """Release admission and restart selection before drawing obsolete inputs."""
    if generation != bmp_cache_generation():
        raise StaleSelection


def retrying_selection() -> bool:
    """Return whether this request already passed route admission."""
    return _retrying.get()


def with_current_selection[**P, T](
    operation: Callable[P, Awaitable[T]],
) -> Callable[P, Awaitable[T]]:
    """Re-resolve all inputs and share the current flight when a queued selection expires."""

    @wraps(operation)
    async def resolve(*args: P.args, **kwargs: P.kwargs) -> T:
        """Capture selection before the first lookup and retry outside the render slot."""
        retry = False
        while True:
            with _selection.set(bmp_cache_generation()), _retrying.set(retry):
                try:
                    return await operation(*args, **kwargs)
                except StaleSelection:
                    retry = True

    return resolve
