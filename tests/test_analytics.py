"""Tests for analytics service."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.analytics import (
    _send_to_umami,
    get_umami_script_tag,
    track_event,
    track_pageview,
)


@pytest.fixture
def mock_config():
    """Mock config with Umami enabled."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = True
        mock_cfg.UMAMI_WEBSITE_ID = "test-website-id"
        mock_cfg.UMAMI_API_URL = "https://analytics.example.com/api/send"
        mock_cfg.ANALYTICS_HOSTNAME = "test.example.com"
        yield mock_cfg


@pytest.mark.asyncio
async def test_send_to_umami_pageview(mock_config):
    """Test that _send_to_umami sends correct pageview payload."""
    with patch("app.services.analytics.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__.return_value.post = mock_post

        await _send_to_umami(
            url="/calendar.bmp?lang=cs",
            title="Calendar BMP - cs",
            lang="cs",
            user_agent="TestAgent/1.0",
        )

        # Verify the call was made
        assert mock_post.called
        call_args = mock_post.call_args

        # Check the payload
        payload = call_args.kwargs["json"]
        assert payload["payload"]["url"] == "/calendar.bmp?lang=cs"
        assert payload["payload"]["title"] == "Calendar BMP - cs"
        assert payload["payload"]["language"] == "cs"
        assert payload["payload"]["hostname"] == "test.example.com"
        assert payload["type"] == "event"
        # No event name for pageview
        assert "name" not in payload["payload"]


@pytest.mark.asyncio
async def test_send_to_umami_custom_event(mock_config):
    """Test that _send_to_umami sends correct custom event payload."""
    with patch("app.services.analytics.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__.return_value.post = mock_post

        await _send_to_umami(
            url="/calendar.bmp",
            title="Event: calendar_download",
            lang="en",
            user_agent="TestAgent/1.0",
            event_name="calendar_download",
            event_data={"language": "en", "timezone": "Europe/Prague"},
        )

        # Verify the call was made
        assert mock_post.called
        call_args = mock_post.call_args

        # Check the payload
        payload = call_args.kwargs["json"]
        assert payload["payload"]["url"] == "/calendar.bmp"
        assert payload["payload"]["name"] == "calendar_download"
        assert payload["payload"]["data"] == {"language": "en", "timezone": "Europe/Prague"}


@pytest.mark.asyncio
async def test_track_pageview_creates_task(mock_config):
    """Test that track_pageview creates a background task."""
    with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
        await track_pageview(
            url="/privacy",
            title="Privacy Policy",
            lang="en",
            user_agent="TestAgent/1.0",
        )

        # Give the background task a chance to run
        await asyncio.sleep(0.01)

        assert mock_send.called


@pytest.mark.asyncio
async def test_track_pageview_disabled_when_umami_disabled():
    """Test that track_pageview does nothing when Umami is disabled."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = False

        with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
            await track_pageview(
                url="/",
                title="Home",
                lang="en",
            )

            # Should not call _send_to_umami when disabled
            assert not mock_send.called


@pytest.mark.asyncio
async def test_track_pageview_disabled_when_website_id_missing():
    """Test that track_pageview does nothing when website ID is missing."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = True
        mock_cfg.UMAMI_WEBSITE_ID = None

        with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
            await track_pageview(
                url="/",
                title="Home",
                lang="en",
            )

            # Should not call _send_to_umami when website ID is missing
            assert not mock_send.called


@pytest.mark.asyncio
async def test_track_event_creates_task(mock_config):
    """Test that track_event creates a background task."""
    with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
        await track_event(
            url="/calendar.bmp",
            event_name="calendar_download",
            lang="cs",
            user_agent="TestAgent/1.0",
            event_data={"timezone": "Europe/Prague"},
        )

        # Give the background task a chance to run
        await asyncio.sleep(0.01)

        assert mock_send.called


@pytest.mark.asyncio
async def test_track_event_disabled_when_umami_disabled():
    """Test that track_event does nothing when Umami is disabled."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = False

        with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
            await track_event(
                url="/calendar.bmp",
                event_name="calendar_download",
                lang="en",
            )

            # Should not call _send_to_umami when disabled
            assert not mock_send.called


@pytest.mark.asyncio
async def test_track_event_disabled_when_website_id_missing():
    """Test that track_event does nothing when website ID is missing."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = True
        mock_cfg.UMAMI_WEBSITE_ID = None

        with patch("app.services.analytics._send_to_umami", new_callable=AsyncMock) as mock_send:
            await track_event(
                url="/calendar.bmp",
                event_name="calendar_download",
                lang="en",
            )

            # Should not call _send_to_umami when website ID is missing
            assert not mock_send.called


@pytest.mark.asyncio
async def test_send_to_umami_handles_http_errors_gracefully(mock_config):
    """Test that analytics handles HTTP errors without raising exceptions."""
    with patch("app.services.analytics.httpx.AsyncClient") as mock_client:
        # Simulate HTTP error
        mock_client.return_value.__aenter__.return_value.post.side_effect = Exception(
            "Connection error"
        )

        # Should not raise exception
        await _send_to_umami(
            url="/calendar.bmp",
            title="Test",
            lang="en",
            user_agent="TestAgent/1.0",
        )


@pytest.mark.asyncio
async def test_send_to_umami_uses_custom_user_agent(mock_config):
    """Test that analytics uses the provided user agent."""
    with patch("app.services.analytics.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__.return_value.post = mock_post

        custom_ua = "curl/7.68.0"
        await _send_to_umami(
            url="/calendar.bmp",
            title="Test",
            lang="cs",
            user_agent=custom_ua,
        )

        # Verify the user agent header
        assert mock_post.called
        call_args = mock_post.call_args
        headers = call_args.kwargs["headers"]
        assert headers["User-Agent"] == custom_ua


@pytest.mark.asyncio
async def test_send_to_umami_uses_default_user_agent_when_none(mock_config):
    """Test that analytics uses default user agent when none provided."""
    with patch("app.services.analytics.httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.raise_for_status = MagicMock()

        mock_post = AsyncMock(return_value=mock_response)
        mock_client.return_value.__aenter__.return_value.post = mock_post

        await _send_to_umami(
            url="/calendar.bmp",
            title="Test",
            lang="en",
            user_agent=None,
        )

        # Verify the default user agent header
        assert mock_post.called
        call_args = mock_post.call_args
        headers = call_args.kwargs["headers"]
        assert headers["User-Agent"] == "F1-EInk-Calendar/1.0"


def test_get_umami_script_tag_returns_script_when_enabled():
    """Test that get_umami_script_tag returns script tag when Umami is enabled."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = True
        mock_cfg.UMAMI_WEBSITE_ID = "test-website-id"
        mock_cfg.UMAMI_API_URL = "https://analytics.example.com/api/send"

        script_tag = get_umami_script_tag()

        assert "script" in script_tag
        assert "test-website-id" in script_tag
        assert "https://analytics.example.com/script.js" in script_tag


def test_get_umami_script_tag_returns_empty_when_disabled():
    """Test that get_umami_script_tag returns empty string when Umami is disabled."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = False

        script_tag = get_umami_script_tag()

        assert script_tag == ""


def test_get_umami_script_tag_returns_empty_when_website_id_missing():
    """Test that get_umami_script_tag returns empty string when website ID is missing."""
    with patch("app.services.analytics.config") as mock_cfg:
        mock_cfg.UMAMI_ENABLED = True
        mock_cfg.UMAMI_WEBSITE_ID = None

        script_tag = get_umami_script_tag()

        assert script_tag == ""
