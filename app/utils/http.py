"""Shared HTTP helpers for outbound API calls."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
MAX_RETRY_DELAY = 300.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_DELAY)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        return min(max(seconds, 0.0), MAX_RETRY_DELAY)


def _retry_delay(response: httpx.Response | None, attempt: int, base_delay: float) -> float:
    exponential = base_delay * (2**attempt)
    retry_after = _retry_after_seconds(response) if response is not None else None
    base = max(exponential, retry_after or 0.0)
    return min(base + random.uniform(0.0, min(exponential * 0.25, 1.0)), MAX_RETRY_DELAY)


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
                    delay = _retry_delay(exc.response, attempt, retry_base_delay)
                    if logger is not None:
                        logger.warning(
                            "Transient HTTP %d, retry %d/%d in %.2fs",
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
                delay = _retry_delay(None, attempt, retry_base_delay)
                if logger is not None:
                    logger.warning(
                        "Transport error, retry %d/%d in %.2fs: %s",
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
