"""Tests for GitHub version metadata fetching."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.version_service import fetch_version_info


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
