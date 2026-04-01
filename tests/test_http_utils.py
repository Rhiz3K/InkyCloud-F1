"""Tests for shared HTTP retry helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.utils.http import fetch_with_retry


class MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


@pytest.mark.asyncio
async def test_fetch_with_retry_returns_response_on_first_success():
    client = MagicMock()
    response = MockResponse(200)
    client.get = AsyncMock(return_value=response)

    result = await fetch_with_retry(client, "https://example.com")

    assert result is response
    client.get.assert_awaited_once_with("https://example.com")


@pytest.mark.asyncio
async def test_fetch_with_retry_retries_http_429_until_success():
    client = MagicMock()
    client.get = AsyncMock(side_effect=[MockResponse(429), MockResponse(200)])
    logger = MagicMock()

    with patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        result = await fetch_with_retry(client, "https://example.com", logger=logger)

    assert result.status_code == 200
    assert client.get.await_count == 2
    mock_sleep.assert_awaited_once_with(1.0)
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_with_retry_raises_non_retryable_error_immediately():
    client = MagicMock()
    client.get = AsyncMock(return_value=MockResponse(500))

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_with_retry(client, "https://example.com")

    client.get.assert_awaited_once_with("https://example.com")
