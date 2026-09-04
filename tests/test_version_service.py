"""Tests for GitHub version metadata fetching."""

from types import SimpleNamespace
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


# Extended cache and partial-response coverage for GitHub version metadata.


@pytest.fixture(autouse=True)
def reset_version_cache():
    version_service._version_cache = None
    version_service._version_cache_fetched_at = None
    version_service._version_fetch_failed_at = None
    yield
    version_service._version_cache = None
    version_service._version_cache_fetched_at = None
    version_service._version_fetch_failed_at = None


@pytest.mark.asyncio
async def test_initial_failed_refresh_opens_negative_cache_window(monkeypatch):
    client = AsyncMock()
    client.get.side_effect = [AsyncMock(status_code=503), AsyncMock(status_code=503)]
    monkeypatch.setattr(version_service, "get_shared_http_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(version_service.time, "time", lambda: 1000.0)

    await fetch_version_info()

    assert version_service._version_fetch_failed_at == 1000.0
    assert version_service.version_fetch_recently_failed() is True

    later = 1000.0 + version_service.VERSION_NEGATIVE_CACHE_TTL_SECONDS + 1
    monkeypatch.setattr(version_service.time, "time", lambda: later)
    assert version_service.version_fetch_recently_failed() is False


@pytest.mark.asyncio
async def test_successful_refresh_clears_negative_cache_window(monkeypatch):
    version_service._version_fetch_failed_at = 5.0
    release = AsyncMock(status_code=200)
    release.json = lambda: {"tag_name": "v1.0.0", "name": "Release", "published_at": "2026"}
    commit = AsyncMock(status_code=200)
    commit.json = lambda: {
        "sha": "abc1234567",
        "commit": {"committer": {"date": "2026"}, "message": "feat: x\nbody"},
    }
    client = AsyncMock()
    client.get.side_effect = [release, commit]
    monkeypatch.setattr(version_service, "get_shared_http_client", lambda *_args, **_kwargs: client)

    result = await fetch_version_info()

    assert result.version_string == "v1.0.0 (abc1234)"
    assert version_service._version_fetch_failed_at is None
    assert version_service.version_fetch_recently_failed() is False


def _info(release: str | None = None, commit: str | None = None) -> version_service.VersionInfo:
    return version_service.VersionInfo(
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
    assert version_service.get_cached_version() is None

    cached = _info("v1.0.0", "abcdefghi")
    version_service._version_cache = cached
    version_service._version_cache_fetched_at = 100.0
    with patch("app.services.version_service.time.time", return_value=200.0):
        assert version_service.get_cached_version() is cached
    with patch(
        "app.services.version_service.time.time",
        return_value=100.0 + version_service.VERSION_CACHE_TTL_SECONDS + 1,
    ):
        assert version_service.get_cached_version() is None


@pytest.mark.asyncio
async def test_fetch_version_info_handles_untagged_release_and_commit_without_sha():
    client = AsyncMock()
    client.get.side_effect = [
        SimpleNamespace(status_code=200, json=lambda: {"name": "Untagged"}),
        SimpleNamespace(status_code=200, json=lambda: {"commit": {"message": "No SHA"}}),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        result = await version_service.fetch_version_info()

    assert result.version_string == "unknown"
    assert version_service._version_cache is None


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
        result = await version_service.fetch_version_info()
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
        result = await version_service.fetch_version_info()
    assert result.commit_sha_short == "1234567"


@pytest.mark.asyncio
async def test_fetch_version_info_handles_commit_exception_and_preserves_empty_previous():
    previous = _info()
    version_service._version_cache = previous
    client = AsyncMock()
    client.get.side_effect = [
        SimpleNamespace(status_code=404),
        RuntimeError("commit failed"),
    ]
    with patch("app.services.version_service.get_shared_http_client", return_value=client):
        assert await version_service.fetch_version_info() is previous


@pytest.mark.asyncio
async def test_refresh_version_info_returns_result_or_none(monkeypatch):
    info = _info("v1.0.0")
    with patch("app.services.version_service.fetch_version_info", new=AsyncMock(return_value=info)):
        assert await version_service.refresh_version_info() is info

    monkeypatch.setattr(version_service.time, "time", lambda: 1234.0)
    with patch(
        "app.services.version_service.fetch_version_info",
        new=AsyncMock(side_effect=RuntimeError("failed")),
    ):
        assert await version_service.refresh_version_info() is None

    assert version_service._version_fetch_failed_at == 1234.0
    assert version_service.version_fetch_recently_failed() is True
