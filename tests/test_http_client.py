"""Tests for shared outbound HTTP client helpers."""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.services.http_client import (
    _reset_shared_http_clients_for_tests,
    close_shared_http_clients,
    get_shared_http_client,
)


def setup_function():
    _reset_shared_http_clients_for_tests()


def teardown_function():
    _reset_shared_http_clients_for_tests()


def test_get_shared_http_client_reuses_equivalent_timeout_configuration():
    factory = Mock(side_effect=lambda timeout=None: object())

    client_a = get_shared_http_client(factory, timeout=httpx.Timeout(5.0))
    client_b = get_shared_http_client(factory, timeout=httpx.Timeout(5.0))

    assert client_a is client_b
    assert factory.call_count == 1


def test_get_shared_http_client_separates_different_timeouts():
    factory = Mock(side_effect=lambda timeout=None: object())

    client_a = get_shared_http_client(factory, timeout=5.0)
    client_b = get_shared_http_client(factory, timeout=10.0)

    assert client_a is not client_b
    assert factory.call_count == 2


@pytest.mark.asyncio
async def test_close_shared_http_clients_closes_and_clears_cache():
    class ClosableClient:
        def __init__(self):
            self.aclose = AsyncMock()

    factory = Mock(side_effect=lambda timeout=None: ClosableClient())

    client_a = get_shared_http_client(factory, timeout=5.0)
    client_b = get_shared_http_client(factory, timeout=10.0)

    await close_shared_http_clients()

    client_a.aclose.assert_awaited_once()
    client_b.aclose.assert_awaited_once()

    fresh_client = get_shared_http_client(factory, timeout=5.0)
    assert fresh_client is not client_a
