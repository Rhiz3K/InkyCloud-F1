"""Simple in-memory rate limiting helpers."""

from __future__ import annotations

from cachetools import TTLCache
from fastapi import HTTPException, Request

from app.config import config

RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_BUCKETS: TTLCache[str, int] = TTLCache(maxsize=10_000, ttl=RATE_LIMIT_WINDOW_SECONDS)


def _get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, *, bucket: str, limit: int) -> None:
    """Raise HTTP 429 when a client exceeds the configured per-minute quota."""
    if not config.RATE_LIMIT_ENABLED or limit <= 0:
        return

    client_id = _get_client_identifier(request)
    cache_key = f"{bucket}:{client_id}"
    current_count = _RATE_LIMIT_BUCKETS.get(cache_key, 0) + 1
    _RATE_LIMIT_BUCKETS[cache_key] = current_count

    if current_count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


def _reset_rate_limit_state_for_tests() -> None:
    _RATE_LIMIT_BUCKETS.clear()
