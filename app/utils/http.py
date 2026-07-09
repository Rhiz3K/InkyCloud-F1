"""Shared HTTP helpers for outbound API calls."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
    retry_base_delay: float = RETRY_BASE_DELAY,
    logger: logging.Logger | None = None,
    **request_kwargs: Any,
) -> httpx.Response:
    """Fetch a URL with exponential backoff for rate limits and transient transport failures."""
    last_exception: httpx.HTTPError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {429, 502, 503, 504}:
                last_exception = exc
                if attempt < max_retries:
                    delay = retry_base_delay * (2**attempt)
                    if logger is not None:
                        logger.warning(
                            "Transient HTTP %d, retry %d/%d in %ss",
                            exc.response.status_code,
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                    await asyncio.sleep(delay)
                    continue
            raise
        except httpx.TransportError as exc:
            last_exception = exc
            if attempt < max_retries:
                delay = retry_base_delay * (2**attempt)
                if logger is not None:
                    logger.warning(
                        "Transport error, retry %d/%d in %ss: %s",
                        attempt + 1,
                        max_retries,
                        delay,
                        exc,
                    )
                await asyncio.sleep(delay)
                continue
            raise

    assert last_exception is not None
    raise last_exception
