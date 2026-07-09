"""Version service for fetching release and commit info from GitHub API."""

import logging
import time
from dataclasses import dataclass

import httpx

from app.config import config
from app.services.http_client import get_shared_http_client

logger = logging.getLogger(__name__)

# GitHub repository info
GITHUB_OWNER = "Rhiz3K"
GITHUB_REPO = "InkyCloud-F1"

# Cache for version info (refreshed hourly by the scheduler and on deployment).
# TTL matches the hourly refresh cadence so the changelog page serves the cached value instead
# of doing a blocking inline GitHub fetch for ~55 minutes of every hour.
_version_cache: "VersionInfo | None" = None
_version_cache_fetched_at: float | None = None
VERSION_CACHE_TTL_SECONDS = 3600


@dataclass
class VersionInfo:
    """Version information from GitHub."""

    release_tag: str | None  # e.g., "v1.0.0"
    release_name: str | None  # e.g., "Initial Release"
    release_date: str | None  # ISO format
    commit_sha: str | None  # Full SHA
    commit_sha_short: str | None  # First 7 characters
    commit_date: str | None  # ISO format
    commit_message: str | None  # First line of commit message
    last_updated: str | None  # Last commit date on main branch (ISO format)

    @property
    def version_string(self) -> str:
        """Return formatted version string like 'v1.0.0 (abc1234)'."""
        parts = []
        if self.release_tag:
            parts.append(self.release_tag)
        if self.commit_sha_short:
            parts.append(f"({self.commit_sha_short})")
        return " ".join(parts) if parts else "unknown"


def get_cached_version() -> VersionInfo | None:
    """Get cached version info unless the cache is stale."""
    if _version_cache is None or _version_cache_fetched_at is None:
        return None

    if time.time() - _version_cache_fetched_at > VERSION_CACHE_TTL_SECONDS:
        return None

    return _version_cache


async def fetch_version_info() -> VersionInfo:
    """
    Fetch latest release and commit info from GitHub API.

    Returns:
        VersionInfo with release and commit details
    """
    global _version_cache, _version_cache_fetched_at

    release_tag = None
    release_name = None
    release_date = None
    commit_sha = None
    commit_sha_short = None
    commit_date = None
    commit_message = None

    client = get_shared_http_client(httpx.AsyncClient, timeout=config.REQUEST_TIMEOUT)
    # Fetch latest release
    try:
        release_url = (
            f"{str(config.GITHUB_API_BASE_URL).rstrip('/')}/repos/"
            f"{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
        )
        response = await client.get(
            release_url,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if response.status_code == 200:
            data = response.json()
            release_tag = data.get("tag_name")
            release_name = data.get("name")
            release_date = data.get("published_at")
            logger.info("Fetched latest release: %s", release_tag)
        elif response.status_code == 404:
            logger.info("No releases found on GitHub")
        else:
            logger.warning("GitHub releases API returned %s", response.status_code)
    except Exception as e:
        logger.error("Error fetching GitHub release: %s", e)

    # Fetch latest commit on main branch
    try:
        commits_url = (
            f"{str(config.GITHUB_API_BASE_URL).rstrip('/')}/repos/"
            f"{GITHUB_OWNER}/{GITHUB_REPO}/commits/main"
        )
        response = await client.get(
            commits_url,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if response.status_code == 200:
            data = response.json()
            commit_sha = data.get("sha")
            commit_sha_short = commit_sha[:7] if commit_sha else None
            commit_info = data.get("commit", {})
            commit_date = commit_info.get("committer", {}).get("date")
            commit_message = commit_info.get("message", "").split("\n")[0]
            logger.info("Fetched latest commit: %s", commit_sha_short)
        else:
            logger.warning("GitHub commits API returned %s", response.status_code)
    except Exception as e:
        logger.error("Error fetching GitHub commit: %s", e)

    info = VersionInfo(
        release_tag=release_tag,
        release_name=release_name,
        release_date=release_date,
        commit_sha=commit_sha,
        commit_sha_short=commit_sha_short,
        commit_date=commit_date,
        commit_message=commit_message,
        last_updated=commit_date,
    )

    # Don't overwrite a previously-good cache with an all-None result: with the 1h TTL a
    # single failed refresh (GitHub down/rate-limited) would otherwise pin "unknown" on the
    # changelog page for the whole hour instead of keeping the last known version.
    if release_tag is None and commit_sha is None and _version_cache is not None:
        logger.warning("Version fetch returned no data; keeping previous cached version info")
        _version_cache_fetched_at = time.time()
        return _version_cache

    _version_cache = info
    _version_cache_fetched_at = time.time()

    return _version_cache


async def refresh_version_info() -> VersionInfo | None:
    """
    Refresh version info from GitHub API.

    Called by the scheduler hourly (at :05) and on startup.

    Returns:
        VersionInfo if fetch succeeded, None otherwise.
    """
    logger.info("Refreshing version info from GitHub")
    try:
        return await fetch_version_info()
    except Exception as e:
        logger.error("Error refreshing version info: %s", e, exc_info=True)
        return None
