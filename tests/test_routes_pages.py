"""Extended canonicalization and fallback coverage for HTML page routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routes import pages


def _request(method: str = "GET", query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": "/source",
            "raw_path": b"/source",
            "query_string": query.encode("ascii"),
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        }
    )


def test_sanitize_rendered_html_removes_unsafe_tags_attributes_and_uris():
    rendered = pages._sanitize_rendered_html(
        '<script>alert(1)</script><a href="java\tscript:alert(1)" onclick="x()" style="x">x</a>'
        '<img src="data:text/plain,bad"><p title="safe">safe</p>'
    )

    assert "script" not in rendered
    assert "onclick" not in rendered
    assert "style=" not in rendered
    assert "javascript:" not in rendered
    assert "data:" not in rendered
    assert 'title="safe"' in rendered


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Changelog", "# Changelog"),
        ("## [Unreleased]\n", "## [Unreleased]\n"),
        (
            "## [Unreleased]\n\n### Added\n- item\n\n## [1.0.0]\n",
            "## [Unreleased]\n\n### Added\n- item\n\n## [1.0.0]\n",
        ),
        ("## [Unreleased]\n\n## [1.0.0]\n", "## [1.0.0]\n"),
    ],
)
def test_strip_empty_unreleased_section_variants(text, expected):
    assert pages._strip_empty_unreleased_section(text) == expected


@pytest.mark.parametrize(
    "target",
    [
        "/bad\npath",
        "https://evil.example/path",
        "//evil.example/path",
        "relative/path",
        "/%5C%5Cevil.example/path",
        "/%2F%2Fevil.example/path",
    ],
)
def test_redirect_path_rejects_unsafe_targets(target):
    with pytest.raises(ValueError):
        pages._redirect_path(_request(), target)


def test_redirect_path_merges_existing_and_request_query():
    response = pages._redirect_path(_request(query="lang=cs&empty="), "/stats?range=7d#top")

    assert response.headers["location"] == "/stats?range=7d&lang=cs&empty=#top"


def test_redirect_localized_public_page_rejects_unknown_language():
    with pytest.raises(HTTPException) as error:
        pages._redirect_localized_public_page(_request(), "xx", "privacy")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_root_redirect_and_head_branches():
    assert (await pages.root(_request(), lang="invalid")).headers["location"] == "/"
    with pytest.raises(HTTPException):
        await pages.root_lang(_request(), "xx", lang=None)
    assert (await pages.root_lang(_request(), "en", lang=None)).headers["location"] == "/"
    assert (await pages.root_lang(_request(), "cs", lang="en")).headers["location"] == "/cs/"
    assert (await pages.root_lang(_request("HEAD"), "cs", lang=None)).body == b""


@pytest.mark.asyncio
async def test_configure_handler_and_routes_cover_invalid_and_canonical_redirects():
    with pytest.raises(HTTPException):
        await pages._configure_handler(_request(), "invalid", "en")
    with pytest.raises(HTTPException):
        await pages.configure_screen_slash_redirect(_request(), "invalid")

    assert (await pages.configure_screen(_request(), "calendar", lang="invalid")).headers[
        "location"
    ] == "/configure/calendar"
    with pytest.raises(HTTPException):
        await pages.configure_screen_lang_slash_redirect(_request(), "xx", "calendar")
    with pytest.raises(HTTPException):
        await pages.configure_screen_lang_slash_redirect(_request(), "cs", "invalid")
    assert (await pages.configure_screen_lang_slash_redirect(_request(), "en", "calendar")).headers[
        "location"
    ] == "/configure/calendar"
    assert (await pages.configure_screen_lang_slash_redirect(_request(), "cs", "calendar")).headers[
        "location"
    ] == "/cs/configure/calendar"

    with pytest.raises(HTTPException):
        await pages.configure_screen_lang(_request(), "xx", "calendar", lang=None)
    assert (await pages.configure_screen_lang(_request(), "en", "calendar", lang=None)).headers[
        "location"
    ] == "/configure/calendar"
    assert (await pages.configure_screen_lang(_request(), "cs", "calendar", lang="en")).headers[
        "location"
    ] == "/cs/configure/calendar"
    assert (
        await pages.configure_screen_lang(_request("HEAD"), "cs", "calendar", lang=None)
    ).body == b""


@pytest.mark.asyncio
async def test_privacy_routes_cover_invalid_default_localized_and_head_branches():
    assert (await pages.privacy(_request(), lang="invalid")).headers["location"] == "/privacy"
    with pytest.raises(HTTPException):
        await pages.privacy_lang(_request(), "xx", lang=None)
    assert (await pages.privacy_lang(_request(), "en", lang=None)).headers["location"] == "/privacy"
    assert (await pages.privacy_lang(_request(), "cs", lang="en")).headers[
        "location"
    ] == "/cs/privacy"
    assert (await pages.privacy_lang(_request("HEAD"), "cs", lang=None)).body == b""


@pytest.mark.asyncio
async def test_changelog_handler_uses_localized_fallback_when_version_refresh_fails():
    captured = {}

    def template_response(_request, template, context):
        captured.update(context)
        return SimpleNamespace(template=template)

    with (
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
        patch("app.routes.pages.asyncio.to_thread", new=AsyncMock(return_value=None)),
        patch("app.routes.pages.get_cached_version", return_value=None),
        patch(
            "app.routes.pages.refresh_version_info",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ),
        patch(
            "app.routes.pages.get_template_context",
            return_value={"t": {"changelog_not_found": "<missing>"}},
        ),
        patch("app.routes.pages.templates.TemplateResponse", side_effect=template_response),
    ):
        response = await pages._changelog_handler(_request(), "en")

    assert response.template == "changelog.html"
    assert captured["changelog_html"] == "<p>&lt;missing&gt;</p>"
    assert captured["version_info"] is None

    captured.clear()
    cached_version = {"current": "1.0.0"}
    with (
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
        patch("app.routes.pages.asyncio.to_thread", new=AsyncMock(return_value="<p>changes</p>")),
        patch("app.routes.pages.get_cached_version", return_value=cached_version),
        patch("app.routes.pages.refresh_version_info", new=AsyncMock()) as refresh,
        patch("app.routes.pages.get_template_context", return_value={"t": {}}),
        patch("app.routes.pages.templates.TemplateResponse", side_effect=template_response),
    ):
        await pages._changelog_handler(_request(), "en")
    assert captured["version_info"] is cached_version
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_changelog_routes_cover_invalid_default_localized_and_head_branches():
    assert (await pages.changelog(_request(), lang="invalid")).headers["location"] == "/changelog"
    with pytest.raises(HTTPException):
        await pages.changelog_lang(_request(), "xx", lang=None)
    assert (await pages.changelog_lang(_request(), "en", lang=None)).headers[
        "location"
    ] == "/changelog"
    assert (await pages.changelog_lang(_request(), "cs", lang="en")).headers["location"] == (
        "/cs/changelog"
    )
    assert (await pages.changelog_lang(_request("HEAD"), "cs", lang=None)).body == b""


@pytest.mark.asyncio
async def test_api_docs_routes_cover_invalid_default_localized_and_head_branches():
    assert (await pages.api_docs_html(_request(), lang="invalid")).headers["location"] == (
        "/api/docs/html"
    )
    with pytest.raises(HTTPException):
        await pages.api_docs_html_lang(_request(), "xx", lang=None)
    assert (await pages.api_docs_html_lang(_request(), "en", lang=None)).headers["location"] == (
        "/api/docs/html"
    )
    assert (await pages.api_docs_html_lang(_request(), "cs", lang="en")).headers["location"] == (
        "/cs/api/docs/html"
    )
    assert (await pages.api_docs_html_lang(_request("HEAD"), "cs", lang=None)).body == b""
    with pytest.raises(HTTPException):
        await pages.api_docs_html_lang_slash_redirect(_request(), "xx")


@pytest.mark.asyncio
async def test_stats_routes_cover_invalid_default_localized_and_head_branches():
    assert (await pages.stats_dashboard(_request(), "7d", lang="invalid")).headers[
        "location"
    ] == "/stats?range=7d"
    assert (await pages.stats_dashboard_slash_redirect(_request())).headers["location"] == "/stats"
    with pytest.raises(HTTPException):
        await pages.stats_dashboard_lang(_request(), "xx", "24h", lang=None)
    assert (await pages.stats_dashboard_lang(_request(), "en", "7d", lang=None)).headers[
        "location"
    ] == "/stats?range=7d"
    assert (await pages.stats_dashboard_lang(_request(), "cs", "7d", lang="en")).headers[
        "location"
    ] == "/cs/stats?range=7d"
    assert (await pages.stats_dashboard_lang(_request("HEAD"), "cs", "24h", lang=None)).body == b""
    with pytest.raises(HTTPException):
        await pages.stats_dashboard_lang_slash_redirect(_request(), "xx")


@pytest.mark.asyncio
async def test_language_root_no_slash_redirects_english_and_localized_paths():
    with pytest.raises(HTTPException):
        await pages.root_lang_no_slash(_request(), "xx")
    assert (await pages.root_lang_no_slash(_request(), "en")).headers["location"] == "/"
    assert (await pages.root_lang_no_slash(_request(), "cs")).headers["location"] == "/cs/"


@pytest.mark.asyncio
async def test_public_page_slash_routes_redirect_valid_locales():
    assert (await pages.privacy_slash_redirect(_request())).headers["location"] == "/privacy"
    assert (await pages.privacy_lang_slash_redirect(_request(), "cs")).headers["location"] == (
        "/cs/privacy"
    )
    assert (await pages.changelog_slash_redirect(_request())).headers["location"] == "/changelog"
    assert (await pages.changelog_lang_slash_redirect(_request(), "cs")).headers["location"] == (
        "/cs/changelog"
    )
    assert (await pages.api_docs_html_slash_redirect(_request())).headers["location"] == (
        "/api/docs/html"
    )
    assert (await pages.api_docs_html_lang_slash_redirect(_request(), "cs")).headers[
        "location"
    ] == "/cs/api/docs/html"
