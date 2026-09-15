"""Umami receives hourly server totals, never individual browser metadata."""

import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.config import config
from app.services import analytics


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", False)
    monkeypatch.setattr(config, "UMAMI_ENABLED", True)
    monkeypatch.setattr(config, "UMAMI_WEBSITE_ID", "site-id")
    analytics._counts.clear()
    yield
    analytics._counts.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minimal,enabled,website", [(True, True, "id"), (False, False, "id"), (False, True, None)]
)
async def test_disabled_analytics_does_not_collect_or_connect(
    monkeypatch, minimal, enabled, website
):
    monkeypatch.setattr(config, "MINIMAL_DATA_MODE", minimal)
    monkeypatch.setattr(config, "UMAMI_ENABLED", enabled)
    monkeypatch.setattr(config, "UMAMI_WEBSITE_ID", website)
    network = Mock(side_effect=AssertionError("Unexpected analytics connection"))
    monkeypatch.setattr(analytics, "get_shared_http_client", network)
    await analytics.track_pageview(url="/", user_agent="private")
    await analytics.track_event(
        url="/", event_name="calendar_download", event_data={"visitor": "private"}
    )
    await analytics._send_to_umami(page="/", kind="pageview", hour="now", count=1)
    await analytics.flush_analytics()
    assert not analytics._counts
    assert analytics.get_umami_script_tag() == ""
    network.assert_not_called()


@pytest.mark.asyncio
async def test_export_only_after_hour_and_contains_only_safe_summary(monkeypatch):
    now = ["2026-09-11T12:00:00+00:00"]
    monkeypatch.setattr(analytics, "hour_stamp", lambda *_: now[0])
    response = Mock()
    response.json.side_effect = AssertionError("Returned session IDs used")
    client = Mock(post=AsyncMock(return_value=response))
    monkeypatch.setattr(analytics, "get_shared_http_client", lambda *_, **__: client)
    monkeypatch.setattr(config, "ANALYTICS_HOSTNAME", "")
    monkeypatch.setattr(config, "SITE_URL", "https://racing.example.org")
    for _ in range(3):
        await analytics.track_pageview(
            url="/cs/configure/calendar?private=yes",
            title="private",
            user_agent="private",
            referrer="private",
        )
    await analytics.track_event(url="/", event_name="private", event_data={"secret": "private"})
    assert len(analytics._counts) == 1
    await analytics.flush_analytics()
    client.post.assert_not_called()
    await analytics.flush_analytics(force=True)
    sent = client.post.await_args.kwargs
    assert sent["json"] == {
        "type": "event",
        "payload": {
            "website": "site-id",
            "hostname": "racing.example.org",
            "url": "/configure/calendar",
            "name": "usage_summary",
            "data": {"hour": now[0], "kind": "pageview", "count": 3},
        },
    }
    assert sent["headers"] == {"User-Agent": "InkyCloud/1.3 (server usage summaries)"}
    response.json.assert_not_called()
    assert not analytics._counts
    assert analytics.get_umami_script_tag() == ""


@pytest.mark.asyncio
async def test_failed_or_cancelled_summary_is_retained_and_expired_batches_are_discarded(
    monkeypatch,
):
    send = AsyncMock(side_effect=httpx.ConnectError("private"))
    monkeypatch.setattr(analytics, "_send_to_umami", send)
    await analytics.track_event(
        url="/calendar.bmp?private", event_name="calendar_download", event_data={"private": True}
    )
    await analytics.flush_analytics(force=True)
    assert sum(analytics._counts.values()) == 1
    send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await analytics.flush_analytics(force=True)
    assert sum(analytics._counts.values()) == 1
    analytics._counts[("2000-01-01T00:00:00+00:00", "/", "pageview")] = 5
    send.side_effect = None
    await analytics.flush_analytics(force=True)
    assert not analytics._counts
