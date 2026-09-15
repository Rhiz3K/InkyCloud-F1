"""Optional Umami integration: hourly server totals, without visitor tracking."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import config
from app.services.http_client import get_shared_http_client
from app.services.private_stats import canonical_page, hour_stamp

_counts: Counter[tuple[str, str, str]] = Counter()
_EVENTS = frozenset({"calendar_download", "teams_download"})


def analytics_enabled() -> bool:
    """Require explicit Umami configuration and permit the global collection opt-out."""
    return bool(not config.MINIMAL_DATA_MODE and config.UMAMI_ENABLED and config.UMAMI_WEBSITE_ID)


async def _send_to_umami(*, page: str, kind: str, hour: str, count: int) -> None:
    """Export only an allowlisted server summary; never forward browser headers or IDs."""
    if not analytics_enabled():
        return
    client = get_shared_http_client(httpx.AsyncClient, timeout=5.0)
    response = await client.post(
        str(config.UMAMI_API_URL),
        headers={"User-Agent": "InkyCloud/1.3 (server usage summaries)"},
        json={
            "type": "event",
            "payload": {
                "website": config.UMAMI_WEBSITE_ID,
                "hostname": config.ANALYTICS_HOSTNAME or urlsplit(str(config.SITE_URL)).hostname,
                "url": page,
                "name": "usage_summary",
                "data": {"hour": hour, "kind": kind, "count": count},
            },
        },
    )
    response.raise_for_status()
    # Do not parse or persist the session/cache IDs returned by Umami.


async def track_pageview(url: str, **_ignored: Any) -> None:
    """Count known public page categories in memory; discard all other caller fields."""
    if analytics_enabled():
        _counts[(hour_stamp(), canonical_page(url, endpoint=True), "pageview")] += 1


async def track_event(url: str, event_name: str, **_ignored: Any) -> None:
    """Count only the finite set of supported download event names."""
    if analytics_enabled() and event_name in _EVENTS:
        _counts[(hour_stamp(), canonical_page(url, endpoint=True), event_name)] += 1


async def flush_analytics(*, force: bool = False) -> None:
    """Send completed hourly totals; keep failures for up to one day without logging payloads."""
    if not analytics_enabled():
        _counts.clear()
        return
    current = hour_stamp()
    cutoff = hour_stamp((datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    for key in list(_counts):
        hour, page, kind = key
        if hour < cutoff:
            del _counts[key]
            continue
        if not force and hour == current:
            continue
        count = _counts.pop(key)
        try:
            await _send_to_umami(page=page, kind=kind, hour=hour, count=count)
        except asyncio.CancelledError:
            _counts[key] += count
            raise
        except httpx.HTTPError:
            # A bounded set of hourly categories, never a queue of individual events.
            if len(_counts) < 500:
                _counts[key] += count


def get_umami_script_tag() -> str:
    """No browser Umami script: only sanitized server totals may leave the application."""
    return ""
