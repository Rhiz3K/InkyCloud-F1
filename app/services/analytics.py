"""Analytics service for tracking requests with Umami."""

import html
import logging
from typing import Any, Optional

import httpx

from app.config import config
from app.services.http_client import get_shared_http_client
from app.utils.async_tasks import create_supervised_task

logger = logging.getLogger(__name__)


async def _send_to_umami(
    url: str,
    title: str,
    lang: str,
    user_agent: Optional[str] = None,
    referrer: str = "",
    event_name: Optional[str] = None,
    event_data: Optional[dict[str, Any]] = None,
):
    """
    Send data to Umami analytics API.

    Args:
        url: Full URL with query parameters
        title: Page title
        lang: Language code
        user_agent: User agent string
        referrer: Referrer URL
        event_name: Event name (if None, tracked as pageview)
        event_data: Additional event data (only used with event_name)
    """
    try:
        payload: dict[str, Any] = {
            "website": config.UMAMI_WEBSITE_ID,
            "url": url,
            "title": title,
            "referrer": referrer,
            "hostname": config.ANALYTICS_HOSTNAME,
            "language": lang,
            "screen": "800x480",
        }

        # Add event name and data if provided (makes it a custom event vs pageview)
        if event_name:
            payload["name"] = event_name
            if event_data:
                payload["data"] = event_data

        data = {
            "payload": payload,
            "type": "event",
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": user_agent or "F1-EInk-Calendar/1.0",
        }

        log_type = f"event '{event_name}'" if event_name else "pageview"
        logger.debug("Sending Umami %s: url=%s, lang=%s", log_type, url, lang)

        client = get_shared_http_client(httpx.AsyncClient, timeout=5.0)
        response = await client.post(
            str(config.UMAMI_API_URL),
            json=data,
            headers=headers,
        )

        # Log response for debugging
        if response.status_code == 200:
            logger.debug(
                "Umami %s tracked: url=%s, response=%s",
                log_type,
                url,
                response.text[:100] if response.text else "empty",
            )
        else:
            logger.warning(
                "Umami %s failed: url=%s, status=%s, response=%s",
                log_type,
                url,
                response.status_code,
                response.text[:200],
            )

    except httpx.HTTPError as e:
        logger.warning(
            "Failed to send Umami analytics: %s (url=%s, event=%s)",
            e,
            url,
            event_name,
        )
    except Exception as e:
        logger.warning("Unexpected error in Umami analytics: %s", e)


async def track_pageview(
    url: str,
    title: str,
    lang: str,
    user_agent: Optional[str] = None,
    referrer: str = "",
):
    """
    Track a pageview in Umami (server-side).

    Use this for tracking page loads and direct BMP requests.

    Args:
        url: Full URL with query parameters (e.g., "/calendar.bmp?lang=cs&tz=Europe/Prague")
        title: Page title for Umami dashboard
        lang: Language code
        user_agent: User agent string from request
        referrer: Referrer URL
    """
    if not config.UMAMI_ENABLED or not config.UMAMI_WEBSITE_ID:
        logger.debug("Umami tracking disabled")
        return

    create_supervised_task(
        _send_to_umami(
            url=url,
            title=title,
            lang=lang,
            user_agent=user_agent,
            referrer=referrer,
            event_name=None,  # No event name = pageview
            event_data=None,
        ),
        name="analytics_pageview",
    )


async def track_event(
    url: str,
    event_name: str,
    lang: str,
    user_agent: Optional[str] = None,
    event_data: Optional[dict[str, Any]] = None,
):
    """
    Track a custom event in Umami (server-side).

    Use this for tracking specific actions with additional data.

    Args:
        url: URL where event occurred
        event_name: Name of the event (e.g., "calendar_download")
        lang: Language code
        user_agent: User agent string from request
        event_data: Additional data to track with the event
    """
    if not config.UMAMI_ENABLED or not config.UMAMI_WEBSITE_ID:
        logger.debug("Umami tracking disabled")
        return

    create_supervised_task(
        _send_to_umami(
            url=url,
            title=f"Event: {event_name}",
            lang=lang,
            user_agent=user_agent,
            referrer="",
            event_name=event_name,
            event_data=event_data,
        ),
        name="analytics_event",
    )


def get_umami_script_tag() -> str:
    """
    Get Umami tracking script tag for HTML pages.

    Returns empty string if Umami is disabled.
    """
    if not config.UMAMI_ENABLED or not config.UMAMI_WEBSITE_ID:
        return ""

    # Extract base URL from API URL (remove /api/send)
    api_url = str(config.UMAMI_API_URL)
    base_url = api_url.removesuffix("/api/send")

    return (
        f'<script defer src="{html.escape(base_url, quote=True)}/script.js" '
        f'data-website-id="{html.escape(str(config.UMAMI_WEBSITE_ID), quote=True)}"></script>'
    )
