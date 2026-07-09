"""Simple in-memory rate limiting helpers."""

from __future__ import annotations

import math
import time

from cachetools import TTLCache
from fastapi import HTTPException, Request

from app.config import config

RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_BUCKETS: TTLCache[str, tuple[float, int]] = TTLCache(
    maxsize=10_000, ttl=RATE_LIMIT_WINDOW_SECONDS * 2
)


def _get_client_identifier(request: Request) -> str:
    # uvicorn runs with --proxy-headers/--forwarded-allow-ips, so request.client.host is
    # already the proxy-validated client IP. We must NOT parse X-Forwarded-For ourselves:
    # the leftmost entry is attacker-supplied, letting a client forge a fresh rate-limit
    # bucket on every request and bypass the quota entirely.
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, *, bucket: str, limit: int) -> None:
    """Raise HTTP 429 when a client exceeds the configured per-minute quota."""
    if not config.RATE_LIMIT_ENABLED or limit <= 0:
        return

    client_id = _get_client_identifier(request)
    cache_key = f"{bucket}:{client_id}"
    now = time.monotonic()
    window_started_at, current_count = _RATE_LIMIT_BUCKETS.get(cache_key, (now, 0))
    elapsed = now - window_started_at
    if elapsed >= RATE_LIMIT_WINDOW_SECONDS:
        window_started_at, current_count, elapsed = now, 0, 0.0

    if current_count >= limit:
        retry_after = max(1, math.ceil(RATE_LIMIT_WINDOW_SECONDS - elapsed))
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    _RATE_LIMIT_BUCKETS[cache_key] = (window_started_at, current_count + 1)


def _reset_rate_limit_state_for_tests() -> None:
    _RATE_LIMIT_BUCKETS.clear()
