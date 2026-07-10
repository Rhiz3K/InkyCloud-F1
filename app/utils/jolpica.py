"""Shared Jolpica endpoint normalization."""

from __future__ import annotations

from app.config import config


def get_jolpica_base_url(api_url: str | None = None) -> str:
    """Return the season endpoint root from a configured Jolpica URL."""
    normalized = (api_url or str(config.JOLPICA_API_URL)).rstrip("/")
    for suffix in ("/current/next.json", "/current.json", ".json"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].rstrip("/")
    return normalized
