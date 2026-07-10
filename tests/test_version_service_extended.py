"""Extended cache and partial-response coverage for GitHub version metadata."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import version_service as version


@pytest.fixture(autouse=True)
def reset_version_cache():
    version._version_cache = None
    version._version_cache_fetched_at = None
    yield
    version._version_cache = None
    version._version_cache_fetched_at = None


def _info(release: str | None = None, commit: str | None = None) -> version.VersionInfo:
    return version.VersionInfo(
        release,
        None,
        None,
        commit,
        commit[:7] if commit else None,
        None,
        None,
        None,
    )


def test_version_string_and_cache_freshness_variants():
    assert _info("v1.0.0").version_string == "v1.0.0"
    assert _info(commit="abcdefghi").version_string == "(abcdefg)"
    assert _info().version_string == "unknown"
    assert version.get_cached_version() is None

    cached = _info("v1.0.0", "abcdefghi")
    version._version_cache = cached
    version._version_cache_fetched_at = 100.0
    with patch("app.services.version_service.time.time", return_value=200.0):
        assert version.get_cached_version() is cached
    with patch(
        "app.services.version_service.time.time",
        return_value=100.0 + version.VERSION_CACHE_TTL_SECONDS + 1,
    ):
        assert version.get_cached_version() is None


@pytest.mark.asyncio
async def test_fetch_version_info_handles_untagged_release_and_commit_without_sha():
    client = AsyncMock()
    client.get.side_effect = [
        SimpleNamespace(status_code=200, json=lambda: {"name": "Untagged"}),
        SimpleNamespace(status_code=200, json=lambda: {"commit": {"message": "No SHA"}}),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        result = await version.fetch_version_info()

    assert result.version_string == "unknown"
    assert version._version_cache is None


@pytest.mark.asyncio
async def test_fetch_version_info_handles_no_release_and_release_request_exception():
    client = AsyncMock()
    client.get.side_effect = [
        SimpleNamespace(status_code=404),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "sha": "abcdefghi",
                "commit": {"committer": {"date": "2026-01-01"}, "message": "Commit"},
            },
        ),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        result = await version.fetch_version_info()
    assert result.release_tag is None
    assert result.commit_sha_short == "abcdefg"

    client.get.side_effect = [
        RuntimeError("release failed"),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "sha": "123456789",
                "commit": {"committer": {}, "message": "Commit"},
            },
        ),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        result = await version.fetch_version_info()
    assert result.commit_sha_short == "1234567"


@pytest.mark.asyncio
async def test_fetch_version_info_handles_commit_exception_and_preserves_empty_previous():
    previous = _info()
    version._version_cache = previous
    client = AsyncMock()
    client.get.side_effect = [
        SimpleNamespace(status_code=404),
        RuntimeError("commit failed"),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        assert await version.fetch_version_info() is previous


@pytest.mark.asyncio
async def test_refresh_version_info_returns_result_or_none():
    info = _info("v1.0.0")
    with patch("app.services.version_service.fetch_version_info", new=AsyncMock(return_value=info)):
        assert await version.refresh_version_info() is info

    with patch(
        "app.services.version_service.fetch_version_info",
        new=AsyncMock(side_effect=RuntimeError("failed")),
    ):
        assert await version.refresh_version_info() is None
