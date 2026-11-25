"""Choose a completed image before applying conditional headers and recording its request."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Concatenate

from fastapi.responses import Response

from app.services.bmp_artifacts import CacheVerdict
from app.services.image_generation import with_current_selection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """The final representation and its own selection, ready to build an HTTP response."""

    verdict: CacheVerdict
    respond: Callable[[], Response]


def retry_render[**P](
    prepare: Callable[Concatenate[bool, P], Awaitable[PreparedImage]],
) -> Callable[P, Awaitable[Response]]:
    """Retry one complete raced image with fresh inputs, retaining it until a replacement exists."""
    attempt = with_current_selection(prepare)

    @wraps(prepare)
    async def serve(*args: P.args, **kwargs: P.kwargs) -> Response:
        """Finish only the chosen attempt, including its validator and call statistics."""
        image = await attempt(False, *args, **kwargs)
        if image.verdict is CacheVerdict.RACED:
            try:
                replacement = await attempt(True, *args, **kwargs)
            except Exception as exc:
                logger.warning("Image retry failed, serving the completed image: %s", exc)
            else:
                if replacement.verdict is not CacheVerdict.INCOMPLETE:
                    image = replacement
        return image.respond()

    return serve
