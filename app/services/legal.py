"""Public attribution and privacy defaults for the independent service."""

from __future__ import annotations

from typing import Any

from app.config import config

BRAND_NAME = "F1.InkyCloud.click"
CONTENT_POLICY_VERSION = "open-tracks-display-no-caption-2026-09-14"
DATA_LICENSE_URL = "https://creativecommons.org/licenses/by-nc-sa/4.0/"


def source_headers() -> dict[str, str]:
    """Link mixed data/image responses to complete notices without relicensing the code."""
    return {
        "Link": f'<{str(config.SITE_URL).rstrip("/")}/credits#data>; rel="describedby"',
        "X-Data-Attribution": "Jolpica-F1 / Ergast; CC BY-NC-SA 4.0; formatted and enriched",
    }


def scrub_error_event(event: Any, _hint: Any = None) -> Any:
    """Return only diagnostic fields without request payloads or local variables."""
    clean = {
        key: event[key]
        for key in ("event_id", "timestamp", "level", "platform", "release", "environment")
        if key in event
    }
    values = event.get("exception", {}).get("values", [])
    clean["exception"] = {"values": []}
    for exception in values:
        item = {key: exception[key] for key in ("type", "module") if key in exception}
        item["value"] = "[message removed]"
        frames = exception.get("stacktrace", {}).get("frames", [])
        item["stacktrace"] = {
            "frames": [
                {
                    key: frame[key]
                    for key in ("module", "function", "lineno", "in_app")
                    if key in frame
                }
                for frame in frames
            ]
        }
        clean["exception"]["values"].append(item)
    return clean


class AttributionMiddleware:
    """Attach source notices to calendar, teams, preview and data API exports."""

    def __init__(self, app):
        """Wrap the ASGI application without buffering response bodies."""
        self.app = app

    async def __call__(self, scope, receive, send):
        """Append provenance on successful data responses, including conditional requests."""

        async def send_with_notices(message):
            """Attach notices to response headers without touching the body."""
            if message["type"] == "http.response.start" and message["status"] in {200, 304}:
                path = scope.get("path", "")
                if path in {
                    "/calendar.bmp",
                    "/teams.bmp",
                    "/api/next-race",
                    "/api/teams",
                } or path.startswith(
                    ("/api/races", "/api/race/", "/api/teams/", "/api/standings/", "/preview/")
                ):
                    headers = list(message.get("headers", []))
                    headers.extend(
                        (key.lower().encode(), value.encode())
                        for key, value in source_headers().items()
                    )
                    message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_notices)
