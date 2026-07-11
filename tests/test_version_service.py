"""Tests for GitHub version metadata fetching."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import version_service
from app.services.version_service import VersionInfo, fetch_version_info


@pytest.mark.asyncio
async def test_fetch_version_info_uses_configured_github_api_base_url():
    client = AsyncMock()
    client.get.side_effect = [
        AsyncMock(
            status_code=200,
            json=lambda: {
                "tag_name": "v1.2.20",
                "name": "Release 1.2.20",
                "published_at": "2026-04-03T00:00:00Z",
            },
        ),
        AsyncMock(
            status_code=200,
            json=lambda: {
                "sha": "1234567890abcdef",
                "commit": {
                    "committer": {"date": "2026-04-03T00:00:00Z"},
                    "message": "Release commit\n\nBody",
                },
            },
        ),
    ]

    with (
        patch("app.services.version_service.config.GITHUB_API_BASE_URL", "https://gh.example.test"),
        patch("app.services.version_service.get_shared_http_client", return_value=client),
    ):
        info = await fetch_version_info()

    requested_urls = [call.args[0] for call in client.get.await_args_list]
    assert requested_urls == [
        "https://gh.example.test/repos/Rhiz3K/InkyCloud-F1/releases/latest",
        "https://gh.example.test/repos/Rhiz3K/InkyCloud-F1/commits/main",
    ]
    assert info.release_tag == "v1.2.20"
    assert info.commit_sha_short == "1234567"


@pytest.mark.asyncio
async def test_failed_refresh_extends_last_good_cache_freshness(monkeypatch):
    cached = VersionInfo(None, None, None, "abc", "abc", None, None, None)
    monkeypatch.setattr(version_service, "_version_cache", cached)
    monkeypatch.setattr(version_service, "_version_cache_fetched_at", 1.0)
    monkeypatch.setattr(version_service.time, "time", lambda: 42.0)

    client = AsyncMock()
    client.get.side_effect = [AsyncMock(status_code=503), AsyncMock(status_code=503)]
    monkeypatch.setattr(version_service, "get_shared_http_client", lambda *_args, **_kwargs: client)

    result = await fetch_version_info()

    assert result is cached
    assert version_service._version_cache_fetched_at == 42.0


@pytest.mark.asyncio
async def test_partial_refresh_keeps_release_from_last_good_cache(monkeypatch):
    cached = VersionInfo(
        "v1.2.30",
        "Release 1.2.30",
        "2026-04-01T00:00:00Z",
        "oldsha",
        "oldsha",
        None,
        None,
        None,
    )
    monkeypatch.setattr(version_service, "_version_cache", cached)
    client = AsyncMock()
    client.get.side_effect = [
        AsyncMock(status_code=403),
        AsyncMock(
            status_code=200,
            json=lambda: {
                "sha": "1234567890abcdef",
                "commit": {
                    "committer": {"date": "2026-04-03T00:00:00Z"},
                    "message": "New commit",
                },
            },
        ),
    ]
    monkeypatch.setattr(version_service, "get_shared_http_client", lambda *_args, **_kwargs: client)

    result = await fetch_version_info()

    assert result.release_tag == "v1.2.30"
    assert result.commit_sha_short == "1234567"


@pytest.mark.asyncio
async def test_initial_failed_refresh_does_not_start_cache_ttl(monkeypatch):
    monkeypatch.setattr(version_service, "_version_cache", None)
    monkeypatch.setattr(version_service, "_version_cache_fetched_at", None)
    client = AsyncMock()
    client.get.side_effect = [AsyncMock(status_code=503), AsyncMock(status_code=503)]
    monkeypatch.setattr(version_service, "get_shared_http_client", lambda *_args, **_kwargs: client)

    result = await fetch_version_info()

    assert result.version_string == "unknown"
    assert version_service._version_cache is None
    assert version_service._version_cache_fetched_at is None
