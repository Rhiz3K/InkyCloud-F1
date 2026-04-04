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
    """Fetch a URL with exponential backoff on HTTP 429 responses."""
    last_exception: httpx.HTTPStatusError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                last_exception = exc
                if attempt < max_retries:
                    delay = retry_base_delay * (2**attempt)
                    if logger is not None:
                        logger.warning(
                            "Rate limited (429), retry %d/%d in %ss",
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                    await asyncio.sleep(delay)
                    continue
            raise

    assert last_exception is not None
    raise last_exception
