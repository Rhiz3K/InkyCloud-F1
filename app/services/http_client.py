"""Shared HTTP client helpers for outbound API calls."""

from __future__ import annotations

from typing import Any, Callable

import httpx

SharedClientFactory = Callable[..., Any]

_shared_http_clients: dict[tuple[int, object], Any] = {}


def _timeout_cache_key(timeout: object) -> object:
    """Convert an HTTPX timeout into a stable hashable shared-client key."""
    if isinstance(timeout, httpx.Timeout):
        return (timeout.connect, timeout.read, timeout.write, timeout.pool)
    return timeout


def get_shared_http_client(client_factory: SharedClientFactory, *, timeout: object) -> Any:
    """Return a shared outbound HTTP client keyed by factory identity and timeout."""
    cache_key = (id(client_factory), _timeout_cache_key(timeout))
    client = _shared_http_clients.get(cache_key)
    if client is None:
        client = client_factory(timeout=timeout)
        _shared_http_clients[cache_key] = client
    return client


async def close_shared_http_clients() -> None:
    """Close and clear all shared HTTP clients."""
    clients = list(_shared_http_clients.values())
    _shared_http_clients.clear()

    for client in clients:
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            await aclose()


def _reset_shared_http_clients_for_tests() -> None:
    """Clear cached shared clients in tests without awaiting network cleanup."""
    _shared_http_clients.clear()
