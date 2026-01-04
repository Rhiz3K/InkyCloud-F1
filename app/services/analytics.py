"""Analytics service for tracking requests with Umami."""

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set
from urllib.parse import urlencode

import httpx

from app.config import config

logger = logging.getLogger(__name__)

# Keep references to background tasks to prevent garbage collection
_background_tasks: Set[asyncio.Task] = set()


def _create_background_task(coro: Coroutine) -> Optional[asyncio.Task]:
    """Create a background task and keep reference to prevent garbage collection."""
    try:
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(lambda t: _background_tasks.discard(t))
        return task
    except Exception as e:
        logger.warning(f"Failed to create analytics task: {str(e)}")
        return None


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
        logger.debug(f"Sending Umami {log_type}: url={url}, lang={lang}")

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                str(config.UMAMI_API_URL),
                json=data,
                headers=headers,
            )

            # Log response for debugging
            if response.status_code == 200:
                logger.debug(
                    f"Umami {log_type} tracked: url={url}, "
                    f"response={response.text[:100] if response.text else 'empty'}"
                )
            else:
                logger.warning(
                    f"Umami {log_type} failed: url={url}, "
                    f"status={response.status_code}, response={response.text[:200]}"
                )

            response.raise_for_status()

    except httpx.HTTPError as e:
        logger.warning(f"Failed to send Umami analytics: {str(e)} (url={url}, event={event_name})")
    except Exception as e:
        logger.warning(f"Unexpected error in Umami analytics: {str(e)}")


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

    _create_background_task(
        _send_to_umami(
            url=url,
            title=title,
            lang=lang,
            user_agent=user_agent,
            referrer=referrer,
            event_name=None,  # No event name = pageview
            event_data=None,
        )
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

    _create_background_task(
        _send_to_umami(
            url=url,
            title=f"Event: {event_name}",
            lang=lang,
            user_agent=user_agent,
            referrer="",
            event_name=event_name,
            event_data=event_data,
        )
    )


# Legacy function for backwards compatibility
async def track_request(
    endpoint: str,
    lang: str,
    user_agent: Optional[str] = None,
    tz: Optional[str] = None,
    year: Optional[int] = None,
    round_num: Optional[int] = None,
):
    """
    Legacy function - Track request to Umami analytics.

    DEPRECATED: Use track_pageview() or track_event() instead.
    This function is kept for backwards compatibility.
    """
    # Build URL with query parameters
    query_params = {"lang": lang}
    if tz:
        query_params["tz"] = tz
    if year is not None:
        query_params["year"] = str(year)
    if round_num is not None:
        query_params["round"] = str(round_num)

    url = f"{endpoint}?{urlencode(query_params)}"

    await track_pageview(
        url=url,
        title=f"Calendar - {lang}",
        lang=lang,
        user_agent=user_agent,
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
        f'<script defer src="{base_url}/script.js" '
        f'data-website-id="{config.UMAMI_WEBSITE_ID}"></script>'
    )
