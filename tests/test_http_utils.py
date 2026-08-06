"""Tests for shared HTTP retry helpers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.utils.http import AsyncPacer, fetch_with_retry


class MockResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request, headers=self.headers)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


@pytest.mark.parametrize(
    "min_interval",
    [0, -1, float("inf"), float("-inf"), float("nan")],
)
def test_async_pacer_rejects_invalid_interval(min_interval):
    with pytest.raises(ValueError, match="finite and positive"):
        AsyncPacer(min_interval)


def test_async_pacer_rejects_nonpositive_burst_capacity():
    with pytest.raises(ValueError, match="burst_capacity"):
        AsyncPacer(1.0, burst_capacity=0)


@pytest.mark.asyncio
async def test_async_pacer_waits_for_the_next_request_slot():
    loop = SimpleNamespace(time=MagicMock(side_effect=[10.0, 10.5, 12.0]))
    pacer = AsyncPacer(2.0)

    with (
        patch("app.utils.http.asyncio.get_running_loop", return_value=loop),
        patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
        await pacer.wait()
        await pacer.wait()

    sleep.assert_awaited_once_with(1.5)


@pytest.mark.asyncio
async def test_async_pacer_allows_a_burst_then_resumes_sustained_pacing():
    now = 10.0

    def monotonic_time():
        return now

    async def advance_time(delay):
        nonlocal now
        now += delay

    loop = SimpleNamespace(time=monotonic_time)
    pacer = AsyncPacer(2.0, burst_capacity=4)

    with (
        patch("app.utils.http.asyncio.get_running_loop", return_value=loop),
        patch("app.utils.http.asyncio.sleep", new=AsyncMock(side_effect=advance_time)) as sleep,
    ):
        await asyncio.gather(*(pacer.wait() for _ in range(4)))
        sleep.assert_not_awaited()
        await pacer.wait()

    sleep.assert_awaited_once_with(2.0)


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
    pacer = MagicMock(wait=AsyncMock())

    with (
        patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        patch("app.utils.http.random.uniform", return_value=0.0),
    ):
        result = await fetch_with_retry(client, "https://example.com", logger=logger, pacer=pacer)

    assert result.status_code == 200
    assert client.get.await_count == 2
    assert pacer.wait.await_count == 2
    mock_sleep.assert_awaited_once_with(1.0)
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_with_retry_honors_retry_after_header():
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[MockResponse(429, {"Retry-After": "12"}), MockResponse(200)]
    )

    with (
        patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as mock_sleep,
        patch("app.utils.http.random.uniform", return_value=0.0),
    ):
        result = await fetch_with_retry(client, "https://example.com")

    assert result.status_code == 200
    mock_sleep.assert_awaited_once_with(12.0)


@pytest.mark.asyncio
async def test_fetch_with_retry_raises_non_retryable_error_immediately():
    client = MagicMock()
    client.get = AsyncMock(return_value=MockResponse(400))

    with pytest.raises(httpx.HTTPStatusError):
        await fetch_with_retry(client, "https://example.com")

    client.get.assert_awaited_once_with("https://example.com")


@pytest.mark.asyncio
async def test_fetch_with_retry_retries_http_500_until_success():
    client = MagicMock()
    client.get = AsyncMock(side_effect=[MockResponse(500), MockResponse(200)])

    with (
        patch("app.utils.http.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("app.utils.http.random.uniform", return_value=0.0),
    ):
        result = await fetch_with_retry(client, "https://example.com")

    assert result.status_code == 200
    sleep.assert_awaited_once_with(1.0)
