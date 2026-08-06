"""Shared Jolpica endpoint normalization."""

from __future__ import annotations

import asyncio
import weakref
from urllib.parse import urlsplit

from app.config import config
from app.utils.http import AsyncPacer

_jolpica_pacers: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, AsyncPacer]] = (
    weakref.WeakKeyDictionary()
)


def get_jolpica_base_url(api_url: str | None = None) -> str:
    """Return the season endpoint root from a configured Jolpica URL."""
    normalized = (api_url or str(config.JOLPICA_API_URL)).rstrip("/")
    for suffix in ("/current/next.json", "/current.json", ".json"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].rstrip("/")
    return normalized


def get_jolpica_pacer(api_url: str | None = None) -> AsyncPacer:
    """Return the shared pacing gate for a Jolpica host in the running event loop."""
    loop = asyncio.get_running_loop()
    pacers = _jolpica_pacers.setdefault(loop, {})
    upstream_host = urlsplit(get_jolpica_base_url(api_url)).netloc.casefold()
    pacer = pacers.get(upstream_host)
    if pacer is None:
        pacer = AsyncPacer(
            config.JOLPICA_MIN_REQUEST_INTERVAL,
            burst_capacity=config.JOLPICA_BURST_CAPACITY,
        )
        pacers[upstream_host] = pacer
    return pacer


def _reset_jolpica_pacers_for_tests() -> None:
    """Clear shared pacing state between tests that mutate Jolpica configuration."""
    _jolpica_pacers.clear()
