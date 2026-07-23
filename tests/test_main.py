"""Test main FastAPI application endpoints."""

import asyncio
import json
import runpy
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from bs4 import BeautifulSoup
from bs4.element import Tag
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from starlette.requests import Request

from app import main
from app.config import LANGUAGE_CODES, config
from app.main import app
from app.models import (
    ConstructorStanding,
    DriverStanding,
    HistoricalData,
    StandingsData,
    TeamEntry,
    TeamsData,
)
from app.routes import api as api_routes
from app.routes import images as images_routes
from app.routes import pages as pages_routes
from app.routes import previews as previews_routes
from app.routes.api import _get_driver_number, _get_team_id
from app.routes.images import (
    _get_pregenerated_calendar_path,
    _get_pregenerated_teams_path,
    _get_race_data_from_static,
    _get_race_info_for_stats,
    _schedule_calendar_analytics,
)
from app.services.f1_service import F1Service
from app.services.image_keys import get_teams_image_key
from app.services.teams_service import TeamsService
from app.state import clear_bmp_cache, get_bmp_cache
from app.utils.etag import strong_etag
from app.utils.rate_limit import _reset_rate_limit_state_for_tests
from app.version import APP_VERSION

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_main_endpoint_tests_from_live_services(monkeypatch):
    """Keep endpoint tests deterministic and independent of public upstream APIs."""
    monkeypatch.setattr(images_routes.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(pages_routes, "refresh_version_info", AsyncMock(return_value=None))
    monkeypatch.setattr(
        TeamsService,
        "_fetch_standings",
        AsyncMock(return_value=([], [])),
    )


MOCK_DRIVER_STANDINGS = [
    DriverStanding(
        position=1,
        points=255.0,
        wins=7,
        driver_code="VER",
        driver_name="Verstappen",
        driver_given_name="Max",
        nationality="Dutch",
        constructor_name="Red Bull",
    ),
    DriverStanding(
        position=2,
        points=150.0,
        wins=2,
        driver_code="NOR",
        driver_name="Norris",
        driver_given_name="Lando",
        nationality="British",
        constructor_name="McLaren",
    ),
]

MOCK_CONSTRUCTOR_STANDINGS = [
    ConstructorStanding(
        position=1,
        points=400.0,
        wins=9,
        constructor_name="Red Bull",
        nationality="Austrian",
    ),
    ConstructorStanding(
        position=2,
        points=280.0,
        wins=3,
        constructor_name="Ferrari",
        nationality="Italian",
    ),
]

MOCK_STANDINGS_DATA = StandingsData(
    season=2024,
    round=10,
    driver_standings=MOCK_DRIVER_STANDINGS,
    constructor_standings=MOCK_CONSTRUCTOR_STANDINGS,
)


def test_root_endpoint_returns_html():
    """Test root endpoint returns HTML preview page."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/static/images/f1_homepage_logo_optimized.png" in response.text


def test_root_page_contains_tailwind():
    """Test root page uses Tailwind CSS."""
    response = client.get("/")
    assert "tailwind.min.css" in response.text


def test_root_page_contains_required_elements():
    """Test root page contains all required UI elements."""
    response = client.get("/")
    html = response.text

    assert 'id="uiLangSwitch"' in html
    for code in LANGUAGE_CODES:
        assert f'value="{code}"' in html
    assert "/configure/calendar" in html
    assert "/configure/teams" in html
    assert "Credits" in html


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/cs/",
        "/configure/calendar",
        "/cs/configure/calendar",
        "/configure/teams",
        "/cs/configure/teams",
        "/api/docs/html",
        "/cs/api/docs/html",
        "/changelog",
        "/cs/changelog",
        "/stats",
        "/cs/stats",
        "/privacy",
        "/cs/privacy",
    ],
)
def test_public_html_pages_support_head(path: str):
    """Test public HTML pages accept HEAD requests for crawler validation."""
    response = client.request("HEAD", path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.content == b""


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/cs/",
        "/configure/calendar",
        "/cs/configure/calendar",
        "/configure/teams",
        "/cs/configure/teams",
        "/api/docs/html",
        "/cs/api/docs/html",
        "/changelog",
        "/cs/changelog",
        "/privacy",
        "/cs/privacy",
    ],
)
def test_public_html_pages_head_does_not_track_pageview(path: str):
    """HEAD requests should skip analytics tracking on public HTML pages."""
    with patch("app.routes.pages.track_pageview", new_callable=AsyncMock) as mock_track:
        response = client.request("HEAD", path)

    assert response.status_code == 200
    mock_track.assert_not_awaited()


def test_stats_head_does_not_query_database():
    """HEAD requests to stats should avoid expensive database queries."""
    with patch(
        "app.services.database.Database.get_stats_for_range", new_callable=AsyncMock
    ) as mock_stats:
        response = client.request("HEAD", "/stats")

    assert response.status_code == 200
    mock_stats.assert_not_awaited()


def test_preview_redirect():
    """Test /preview redirects to / for backwards compatibility."""
    response = client.get("/preview", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/"


def test_api_endpoint():
    """Test /api endpoint returns API information."""
    response = client.get("/api")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "F1 E-Ink Calendar API"
    assert "/" in data["endpoints"]
    assert "/calendar.bmp" in data["endpoints"]
    assert "/api" in data["endpoints"]
    assert "/health" in data["endpoints"]
    assert "/health/ready" in data["endpoints"]
    # Test /api/docs alias works the same
    response_docs = client.get("/api/docs")
    assert response_docs.status_code == 200
    assert response_docs.json()["service"] == "F1 E-Ink Calendar API"


def test_health_endpoint():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_sitemap_xml_get_returns_valid_xml():
    """Sitemap should return XML with the public site URL entries."""
    site_url = str(config.SITE_URL).rstrip("/")
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]

    root = ET.fromstring(response.text)
    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }
    locs = [elem.text for elem in root.findall("sm:url/sm:loc", ns)]

    assert any(loc == f"{site_url}/" for loc in locs)
    assert any(loc == f"{site_url}/cs/" for loc in locs)
    assert any(loc == f"{site_url}/configure/calendar" for loc in locs)
    assert any(loc == f"{site_url}/stats" for loc in locs)
    assert any(loc == f"{site_url}/cs/stats" for loc in locs)
    assert all("?" not in loc for loc in locs)
    assert len(locs) == len(set(locs))
    assert len(locs) == 91

    root_entry = None
    for url in root.findall("sm:url", ns):
        loc = url.find("sm:loc", ns)
        if loc is not None and loc.text == f"{site_url}/":
            root_entry = url
            break

    assert root_entry is not None
    alternates = root_entry.findall("xhtml:link", ns)
    alternate_hreflangs = {link.attrib["hreflang"] for link in alternates}
    alternate_hrefs = {link.attrib["href"] for link in alternates}
    assert "x-default" in alternate_hreflangs
    assert "cs" in alternate_hreflangs
    assert f"{site_url}/cs/" in alternate_hrefs
    assert f"{site_url}/" in alternate_hrefs


def test_sitemap_xml_trailing_slash_alias_returns_xml():
    """Trailing-slash sitemap URL should stay fetchable for Search Console retries."""
    canonical_response = client.get("/sitemap.xml")
    alias_response = client.get("/sitemap.xml/")

    assert alias_response.status_code == 200
    assert "application/xml" in alias_response.headers["content-type"]
    assert alias_response.text == canonical_response.text


def test_sitemap_xml_omits_unverified_lastmod_values():
    """Sitemap should not emit lastmod unless it reflects real page changes."""
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    assert root.findall("sm:url/sm:lastmod", ns) == []


def test_sitemap_xml_escapes_site_url_values():
    """Sitemap XML should stay parseable when SITE_URL contains escapable characters."""
    with patch("app.routes.seo.config.SITE_URL", "https://example.test?src=seo&lang=en"):
        response = client.get("/sitemap.xml")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }
    first_loc = root.find("sm:url/sm:loc", ns)
    first_alternate = root.find("sm:url/xhtml:link", ns)
    assert first_loc is not None
    assert first_alternate is not None
    assert "&" in first_loc.text
    assert "&" in first_alternate.attrib["href"]


def test_sitemap_xml_head_returns_empty_body():
    """HEAD requests to sitemap should succeed without a body."""
    response = client.request("HEAD", "/sitemap.xml")
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert response.content == b""


def test_robots_txt_get_contains_sitemap_reference():
    """robots.txt should allow crawling and advertise the canonical sitemap URL."""
    site_url = str(config.SITE_URL).rstrip("/")
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "User-agent: *" in response.text
    assert "Allow: /" in response.text
    assert f"Sitemap: {site_url}/sitemap.xml" in response.text


def test_robots_txt_head_returns_empty_body():
    """HEAD requests to robots.txt should succeed without a body."""
    response = client.request("HEAD", "/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert response.content == b""


def test_language_root_redirect_is_relative_and_hsts_on_https():
    """Language-root normalization should use a safe relative redirect and preserve HSTS."""
    with patch("app.main.config.SITE_URL", "https://example.test"):
        https_client = TestClient(app, base_url="https://example.test")
        response = https_client.get("/cs", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == "/cs/"
    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_www_host_redirects_to_canonical_apex():
    """The application should redirect www traffic to the canonical apex host."""
    with patch("app.main.config.SITE_URL", "https://example.test"):
        www_client = TestClient(app, base_url="https://www.example.test")
        response = www_client.get("/stats?range=7d", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "https://example.test/stats?range=7d"
    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_responses_include_baseline_security_headers():
    response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_www_host_redirect_ignores_site_url_port_when_matching_host():
    """Canonical host detection should still work when SITE_URL includes a non-default port."""
    with patch("app.main.config.SITE_URL", "https://staging.example.com:8443"):
        www_client = TestClient(app, base_url="https://www.staging.example.com")
        response = www_client.get("/privacy", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "https://staging.example.com:8443/privacy"


def test_static_css_sets_cache_control_header():
    """Static CSS assets should carry cache headers from ASGI middleware."""
    response = client.get("/static/css/styles.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_missing_static_asset_does_not_set_cache_control_header():
    """Static error responses should not be cached by the middleware."""
    response = client.get("/static/css/does-not-exist.css")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_configure_trailing_slash_redirects_to_canonical_path():
    """Configure pages should redirect to their canonical no-trailing-slash path."""
    response = client.get("/configure/calendar/", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == "/configure/calendar"


def test_unknown_html_route_returns_direct_html_404():
    """Unknown browser-facing pages should return a direct HTML 404 without a redirect hop."""
    response = client.get("/nonexistent-test-page-12345", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "text/html" in response.headers["content-type"]
    assert "Page Not Found" in response.text
    assert "/nonexistent-test-page-12345" in response.text


def test_unknown_html_route_is_localized_for_language_prefix():
    """Localized 404 pages should render translated copy instead of English fallback text."""
    response = client.get("/fr/nonexistent-test-page-12345", follow_redirects=False)
    assert response.status_code == 404
    assert "Page introuvable" in response.text
    assert "pages publiques vérifiées" in response.text
    assert "/fr/nonexistent-test-page-12345" in response.text


def test_unknown_html_head_returns_nostore_404():
    """HEAD requests for missing browser routes should still be non-cacheable."""
    response = client.request("HEAD", "/nonexistent-test-page-12345")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "text/html" in response.headers["content-type"]
    assert response.content == b""


def test_unknown_api_route_returns_json_404_without_caching():
    """Non-HTML 404 responses should remain JSON and avoid caches."""
    response = client.get("/api/missing-route")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"] == "Not Found"


def test_invalid_configure_screen_slash_returns_404_instead_of_redirect():
    """Unknown configure screen variants should not canonicalize into redirect targets."""
    response = client.get("/configure/unknown/", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_invalid_localized_configure_screen_slash_returns_404():
    """Localized configure slash redirects should reject invalid screen types."""
    response = client.get("/cs/configure/unknown/", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_favicon_endpoint_serves_real_ico_asset():
    """The /favicon.ico endpoint should serve the packaged ICO asset."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] in {"image/x-icon", "image/vnd.microsoft.icon"}
    favicon_path = (
        Path(__file__).resolve().parents[1] / "app" / "assets" / "favicon" / "favicon.ico"
    )
    assert response.content == favicon_path.read_bytes()


def test_configure_page_contains_api_references():
    """Test configure page includes relative API endpoint references."""
    response = client.get("/configure/calendar")
    html = response.text
    assert "/calendar.bmp" in html
    assert "/api/races/" in html
    assert "BASE_URL" not in html


def test_configure_page_i18n_default_english():
    """Test configure page defaults to English for non-CZ/SK users."""
    response = client.get("/configure/calendar", headers={"Accept-Language": "en-US,en;q=0.9"})
    html = response.text
    assert 'currentUiLang = "en"' in html


def test_configure_page_i18n_ignores_accept_language():
    """Test configure page ignores Accept-Language header (English is default)."""
    response = client.get("/configure/calendar", headers={"Accept-Language": "cs-CZ,cs;q=0.9"})
    html = response.text
    assert 'currentUiLang = "en"' in html


def test_configure_page_i18n_respects_cookie():
    """Test configure page with Czech language via subdirectory URL."""
    # With subdirectory URLs, Czech content is served at /cs/ path
    response = client.get("/cs/configure/calendar")
    html = response.text
    assert 'currentUiLang = "cs"' in html


def test_configure_page_lang_parameter():
    """Test configure page ?lang= redirects to subdirectory URL."""
    # ?lang=cs should redirect to /cs/configure/calendar
    response = client.get("/configure/calendar?lang=cs", follow_redirects=True)
    html = response.text
    assert 'currentUiLang = "cs"' in html

    # ?lang=en on English page stays at /configure/calendar
    response = client.get("/configure/calendar?lang=en", follow_redirects=True)
    html = response.text
    assert 'currentUiLang = "en"' in html


def test_configure_invalid_screen_type():
    """Test configure page returns 404 for invalid screen type."""
    response = client.get("/configure/invalid")
    assert response.status_code == 404


def test_configure_invalid_screen_type_with_lang_redirect_query_returns_404():
    """Invalid configure screen types should 404 before localized redirect logic runs."""
    response = client.get("/configure/invalid?lang=cs", follow_redirects=False)
    assert response.status_code == 404

    response = client.get("/en/configure/invalid", follow_redirects=False)
    assert response.status_code == 404


def test_www_host_redirect_uses_method_preserving_status_for_post_requests():
    """Canonical host redirects should preserve POST semantics for operational endpoints."""
    with patch("app.main.config.SITE_URL", "https://example.test"):
        www_client = TestClient(app, base_url="https://www.example.test")
        response = www_client.post(
            "/api/perf-metrics",
            json={"page_path": "/calendar.bmp", "lcp_ms": 1234.5},
            follow_redirects=False,
        )

    assert response.status_code == 308
    assert response.headers["location"] == "https://example.test/api/perf-metrics"


@pytest.mark.asyncio
async def test_lifespan_cancels_initial_generation_before_closing_resources():
    """Shutdown should cancel pending initial generation before closing shared resources."""
    events: list[str] = []

    async def fake_initial_generation():
        events.append("started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    async def fake_close_shared_http_clients():
        events.append("close_http")

    async def fake_close_all_databases():
        events.append("close_db")

    def fake_stop_scheduler():
        events.append("stop_scheduler")

    def fake_shutdown_render_executor():
        events.append("shutdown_render")

    with (
        patch("app.main.warm_teams_renderer_assets", lambda: None),
        patch("app.main.start_scheduler"),
        patch("app.main.stop_scheduler", fake_stop_scheduler),
        patch("app.main.shutdown_render_executor", fake_shutdown_render_executor),
        patch("app.main.run_initial_generation", fake_initial_generation),
        patch("app.main.close_shared_http_clients", fake_close_shared_http_clients),
        patch("app.main.close_shared_database", fake_close_all_databases),
    ):
        async with main.lifespan(app):
            await asyncio.sleep(0)

    assert events == [
        "started",
        "cancelled",
        "stop_scheduler",
        "shutdown_render",
        "close_http",
        "close_db",
    ]


def test_header_contains_language_switcher():
    """Test header contains language switcher dropdown."""
    response = client.get("/")
    html = response.text
    assert 'id="uiLangSwitch"' in html
    assert "switchUiLanguage()" in html


def test_header_contains_nav_links():
    """Test header contains navigation links."""
    response = client.get("/")
    html = response.text
    # GitHub link
    assert "https://github.com/Rhiz3K/InkyCloud-F1" in html
    # API link
    assert "/api/docs/html" in html
    # Privacy link
    assert "/privacy" in html


def test_homepage_contains_credits_links():
    """Test homepage contains credits links rendered in current layout."""
    response = client.get("/")
    html = response.text
    assert "Credits" in html
    assert "FoxeeLab" in html
    assert 'href="https://coolify.io"' in html
    assert 'href="https://hetzner.com"' in html
    assert 'href="https://www.laskakit.cz/' in html
    assert "Weather" in html
    assert "Icons" in html
    assert "Jolpica" in html
    assert 'href="https://open-meteo.com"' in html
    assert 'href="https://github.com/erikflowers/weather-icons"' in html


def test_configure_page_has_sidebar():
    """Test configure page contains settings sidebar."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="settingsSidebar"' in html
    assert "GitHub" in html


def test_privacy_page_header_nav():
    """Test privacy page has navigation in header."""
    response = client.get("/privacy")
    html = response.text
    # English is default - home link should NOT have ?lang= parameter
    assert 'href="/"' in html
    assert "/api/docs/html" in html


def test_api_docs_header_nav():
    """Test API docs page has navigation in header."""
    response = client.get("/api/docs/html")
    html = response.text
    # English is default - home link should NOT have ?lang= parameter
    assert 'href="/"' in html
    assert "/privacy" in html


def test_privacy_endpoint_returns_html():
    """Test /privacy endpoint returns HTML page."""
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Privacy Policy" in response.text or "Ochrana soukromí" in response.text


def test_privacy_page_contains_required_sections():
    """Test privacy page has all required sections."""
    response = client.get("/privacy?lang=en")
    html = response.text
    assert "Introduction" in html
    assert "Data We Collect" in html
    assert "Third-Party" in html
    assert "GDPR" in html
    assert "Open Source" in html
    assert "Contact" in html


def test_privacy_page_lang_parameter():
    """Test privacy page respects ?lang= query parameter."""
    response = client.get("/privacy?lang=cs")
    html = response.text
    assert "Zásady ochrany osobních údajů" in html or "Ochrana soukromí" in html

    response = client.get("/privacy?lang=en")
    html = response.text
    assert "Privacy Policy" in html


def test_privacy_page_i18n_czech():
    """Test privacy page respects preferredLang cookie for Czech."""
    with TestClient(app) as cookie_client:
        cookie_client.cookies.set("preferredLang", "cs")
        response = cookie_client.get("/privacy")

    html = response.text
    assert 'lang="cs"' in html


def test_api_docs_html_endpoint_returns_html():
    """Test /api/docs/html endpoint returns HTML documentation page."""
    response = client.get("/api/docs/html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "API Documentation" in response.text or "Dokumentace API" in response.text


def test_api_docs_html_contains_required_sections():
    """Test API docs HTML page has all required sections."""
    response = client.get("/api/docs/html?lang=en")
    html = response.text

    # Main endpoint section
    assert "/calendar.bmp" in html
    assert "GET" in html

    # Parameters section
    assert "lang" in html
    assert "year" in html
    assert "round" in html
    assert "tz" in html

    # Code examples section
    assert "cURL" in html
    assert "Python" in html
    assert "JavaScript" in html

    # Other endpoints section
    assert "/api/races/" in html
    assert "/api/stats" in html
    assert "/health" in html

    # Try it buttons
    assert "tryCalendarBmp" in html or "Try it" in html


def test_api_docs_examples_use_configured_public_site_url():
    with patch("app.routes.pages.config.SITE_URL", "https://public.example.test"):
        external_host_client = TestClient(app, base_url="https://attacker.example")
        response = external_host_client.get("/api/docs/html")

    assert "https://public.example.test/calendar.bmp" in response.text
    assert "https://attacker.example/calendar.bmp" not in response.text


def test_api_docs_html_lang_parameter():
    """Test API docs HTML page respects ?lang= query parameter."""
    response = client.get("/api/docs/html?lang=cs")
    html = response.text
    assert "Dokumentace API" in html
    assert "Parametry" in html

    response = client.get("/api/docs/html?lang=en")
    html = response.text
    assert "API Documentation" in html
    assert "Parameters" in html


def test_api_docs_html_i18n_czech():
    """Test API docs HTML page with Czech language via subdirectory URL."""
    # With subdirectory URLs, Czech content is served at /cs/ path
    response = client.get("/cs/api/docs/html")
    html = response.text
    assert 'currentUiLang = "cs"' in html


def test_api_docs_html_contains_language_switcher():
    """Test API docs HTML page contains language switcher dropdown."""
    response = client.get("/api/docs/html")
    html = response.text
    assert 'id="uiLangSwitch"' in html
    assert "switchUiLanguage()" in html


def test_stats_dashboard_returns_html():
    """Test /stats endpoint returns HTML dashboard."""
    response = client.get("/stats")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Statistics" in response.text or "Statistiky" in response.text


def test_stats_dashboard_renders_zero_state_when_database_fails():
    """The DB failure fallback must satisfy the full template contract."""
    with (
        patch("app.routes.pages.get_database", side_effect=RuntimeError("database unavailable")),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/stats")

    assert response.status_code == 200
    assert "Total Requests" in response.text


def test_stats_dashboard_reuses_database_without_closing_it():
    """Request handlers must leave the application-scoped database open."""
    database = Mock()
    database.get_stats_for_range = AsyncMock(return_value=pages_routes._empty_stats())
    database.get_perf_stats = AsyncMock(return_value={"sample_count": 0})
    database.close = AsyncMock(side_effect=RuntimeError("close failed"))

    with (
        patch("app.routes.pages.get_database", return_value=database),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/stats")

    assert response.status_code == 200
    database.close.assert_not_awaited()


def test_stats_dashboard_accepts_range_parameter():
    """Test /stats endpoint accepts range query parameter."""
    for range_val in ["1h", "24h", "7d", "30d", "365d"]:
        response = client.get(f"/stats?range={range_val}")
        assert response.status_code == 200
        assert range_val.upper() in response.text


def test_stats_dashboard_contains_required_sections():
    """Test stats dashboard contains key sections."""
    response = client.get("/stats")
    assert response.status_code == 200

    # Check for main stat cards
    assert "Total Requests" in response.text or "Celkem požadavků" in response.text
    assert "Avg Response" in response.text or "Průměrná doba" in response.text
    assert "Data Transfer" in response.text or "Přenesená data" in response.text

    # Check for breakdown sections
    assert "By Endpoint" in response.text or "Podle endpointu" in response.text
    assert "By Race" in response.text or "Podle závodu" in response.text
    assert "By Language" in response.text or "Podle jazyka" in response.text


def test_stats_dashboard_lang_parameter():
    """Test stats dashboard respects lang parameter."""
    # English
    response_en = client.get("/stats?lang=en")
    assert response_en.status_code == 200
    assert "Statistics Dashboard" in response_en.text

    # Czech
    response_cs = client.get("/stats?lang=cs")
    assert response_cs.status_code == 200
    assert "Statistiky" in response_cs.text


def test_stats_dashboard_uses_ranked_breakdown_bar_colors():
    """Stats breakdown bars use a consistent rank-based color order."""
    mock_stats = {
        "total_requests": 10,
        "avg_response_ms": 120,
        "min_response_ms": 20,
        "max_response_ms": 400,
        "total_bytes": 4096,
        "endpoints": [
            {"endpoint": "/calendar.bmp", "count": 7},
            {"endpoint": "/teams.bmp", "count": 2},
            {"endpoint": "/health", "count": 1},
        ],
        "languages": [
            {"lang": "cs", "count": 7},
            {"lang": "en", "count": 3},
        ],
        "display_types": [
            {"display_type": "1bit", "count": 5},
            {"display_type": "spectra6", "count": 3},
            {"display_type": "bwr", "count": 2},
            {"display_type": "bwry", "count": 1},
        ],
        "teams_display_types": [
            {"display_type": "bwr", "count": 4},
            {"display_type": "spectra6", "count": 2},
        ],
        "races": [
            {"race_name": "Japanese Grand Prix", "count": 7, "is_auto_selected": 1},
            {"race_name": "Chinese Grand Prix", "count": 2, "is_auto_selected": 0},
            {"race_name": "Miami Grand Prix", "count": 1, "is_auto_selected": 0},
        ],
        "timezones": [
            {"tz": "Europe/Prague", "count": 6},
            {"tz": "UTC", "count": 3},
            {"tz": "America/New_York", "count": 1},
        ],
    }

    with (
        patch(
            "app.services.database.Database.get_stats_for_range",
            new=AsyncMock(return_value=mock_stats),
        ),
        patch(
            "app.services.database.Database.get_perf_stats",
            new=AsyncMock(return_value={"sample_count": 0}),
        ),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/stats")

    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")

    language_card = soup.find(attrs={"data-testid": "stats-card-languages"})
    assert language_card is not None
    language_fill_bars = language_card.find_all(attrs={"data-testid": "stats-fill-bar"})
    language_summaries = language_card.find_all(attrs={"data-testid": "stats-row-summary"})
    assert len(language_fill_bars) == 2
    assert len(language_summaries) == 2
    language_bar_classes = [cast(list[str], bar.get("class") or []) for bar in language_fill_bars]
    assert "bg-racing-red" in language_bar_classes[0]
    assert "bg-white" in language_bar_classes[1]
    assert "border-l" in language_bar_classes[1]
    assert "border-r" in language_bar_classes[1]
    assert "70.0%" in language_summaries[0].get_text(" ", strip=True)

    display_card = soup.find(attrs={"data-testid": "stats-card-display"})
    assert display_card is not None
    display_fill_bars = display_card.find_all(attrs={"data-testid": "stats-fill-bar"})
    display_summaries = display_card.find_all(attrs={"data-testid": "stats-row-summary"})
    assert len(display_fill_bars) == 4
    assert len(display_summaries) == 4
    display_bar_classes = [cast(list[str], bar.get("class") or []) for bar in display_fill_bars]
    assert "bg-racing-red" in display_bar_classes[0]
    assert "bg-black" in display_bar_classes[1]
    assert "bg-black" in display_bar_classes[2]
    assert "bg-white" in display_bar_classes[3]
    assert "border-l" in display_bar_classes[3]
    assert "border-r" in display_bar_classes[3]
    assert all("background-color:" not in (bar.get("style") or "") for bar in display_fill_bars)

    teams_display_card = soup.find(attrs={"data-testid": "stats-card-teams-display"})
    assert teams_display_card is not None
    teams_display_fill_bars = teams_display_card.find_all(attrs={"data-testid": "stats-fill-bar"})
    teams_display_summaries = teams_display_card.find_all(
        attrs={"data-testid": "stats-row-summary"}
    )
    assert len(teams_display_fill_bars) == 2
    assert len(teams_display_summaries) == 2
    teams_display_bar_classes = [
        cast(list[str], bar.get("class") or []) for bar in teams_display_fill_bars
    ]
    assert "bg-racing-red" in teams_display_bar_classes[0]
    assert "bg-white" in teams_display_bar_classes[1]
    assert "border-l" in teams_display_bar_classes[1]
    assert "border-r" in teams_display_bar_classes[1]
    assert "66.7%" in teams_display_summaries[0].get_text(" ", strip=True)

    assert "Japanese GP" in response.text


def test_stats_dashboard_localizes_range_and_fallback_labels():
    """Stats page uses localized range labels and fallback strings."""
    mock_stats = {
        "total_requests": 3,
        "avg_response_ms": 120,
        "min_response_ms": 20,
        "max_response_ms": 400,
        "total_bytes": 4096,
        "endpoints": [],
        "languages": [],
        "display_types": [],
        "teams_display_types": [],
        "races": [{"race_name": None, "count": 3, "is_auto_selected": 1}],
        "timezones": [{"tz": None, "count": 3}],
    }

    with (
        patch(
            "app.services.database.Database.get_stats_for_range",
            new=AsyncMock(return_value=mock_stats),
        ),
        patch(
            "app.services.database.Database.get_perf_stats",
            new=AsyncMock(return_value={"sample_count": 0}),
        ),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/cs/stats")

    assert response.status_code == 200
    html = response.text
    assert "Posledních 24 hodin" in html
    assert "Žádná data" in html
    assert "Neznámé" in html
    assert "výchozí" in html
    assert "100.0%" in html


def test_operational_api_endpoints_require_token_when_configured():
    """Read-only operational API endpoints should require a token when configured."""
    with patch.object(api_routes.config, "ADMIN_API_TOKEN", SecretStr("secret-token")):
        response = client.get("/api/stats")
        assert response.status_code == 401
        assert response.json() == {"detail": "Unauthorized"}

        response = client.get("/api/stats", headers={"X-Admin-Token": "secret-token"})
        assert response.status_code == 200

        response = client.get("/api/stats/history")
        assert response.status_code == 401

        response = client.get("/api/perf-metrics")
        assert response.status_code == 401

        response = client.get(
            "/api/perf-metrics",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert response.status_code == 200


def test_operational_api_rejects_runtime_empty_token_misconfiguration():
    with patch.object(api_routes.config, "ADMIN_API_TOKEN", SecretStr("")):
        response = client.get("/api/stats", headers={"X-Admin-Token": ""})

    assert response.status_code == 503


def test_perf_metrics_post_remains_public_when_operational_token_is_configured():
    """POST /api/perf-metrics stays public for browser-side web-vitals ingestion."""
    payload = {
        "page_path": "/calendar.bmp",
        "lcp_ms": 1200.5,
        "cls": 0.05,
        "fcp_ms": 800.0,
        "ttfb_ms": 150.0,
        "inp_ms": 50.0,
        "connection_type": "4g",
        "device_memory": 8.0,
    }

    with patch.object(api_routes.config, "ADMIN_API_TOKEN", SecretStr("secret-token")):
        response = client.post("/api/perf-metrics", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_stats_endpoint_returns_correct_structure():
    """Test /api/stats endpoint returns new structure with 24h stats."""
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()

    # Check structure
    assert "requests" in data
    assert "cache_size" in data
    assert "cache_max_size" in data

    # Check requests structure (new format)
    requests = data["requests"]
    assert "last_24h" in requests
    assert "avg_response_ms" in requests
    assert "total_bytes_24h" in requests
    assert "by_status" in requests

    # Values should be integers/floats or None
    assert isinstance(requests["last_24h"], int)
    assert isinstance(requests["total_bytes_24h"], int)
    assert requests["avg_response_ms"] is None or isinstance(
        requests["avg_response_ms"], (int, float)
    )


def test_api_stats_history_endpoint_returns_hourly_history():
    """Test /api/stats/history returns hourly stats derived from request data."""
    mock_history = [
        {
            "timestamp": "2026-04-01T11:00:00+00:00",
            "hour_count": 1,
            "day_count": 3,
        },
        {
            "timestamp": "2026-04-01T10:00:00+00:00",
            "hour_count": 2,
            "day_count": 2,
        },
    ]

    with patch(
        "app.services.database.Database.get_request_stats_history",
        new=AsyncMock(return_value=mock_history),
    ):
        response = client.get("/api/stats/history?limit=24")

    assert response.status_code == 200
    assert response.json() == {"history": mock_history, "count": 2}


def test_stats_link_in_header():
    """Test header contains link to stats page instead of inline stats display."""
    response = client.get("/")
    html = response.text
    # English is default - stats link should NOT have ?lang= parameter
    assert 'href="/stats"' in html
    assert 'id="statsLast24h"' not in html
    assert 'id="statsDataTransfer"' not in html


# ============================================================================
# Homepage Tests
# ============================================================================


def test_homepage_screen_type_cards():
    """Test homepage contains screen type selection cards."""
    response = client.get("/")
    html = response.text
    assert "/configure/calendar" in html
    assert "/configure/teams" in html
    assert "Calendar" in html or "Kalendář" in html
    assert "Teams" in html or "Týmy" in html
    assert f"/preview/calendar.png?lang=en&amp;v={APP_VERSION}" in html
    assert "this.src = '/calendar.bmp?lang=en&amp;weather=false'" in html


def test_calendar_preview_renders_dynamically_before_pregeneration(tmp_path, monkeypatch):
    """The homepage calendar card should not show a generic image during cold start."""
    race_data = {
        "race_name": "Next Test Grand Prix",
        "round": "8",
        "season": "2026",
        "circuit": {
            "circuitId": "test_circuit",
            "name": "Test Circuit",
            "location": "Test City",
            "country": "Test Country",
        },
        "race_date": "19.07.2026",
        "schedule": [
            {"name": "FP1", "display_time": "Fri 13:30"},
            {"name": "Qualifying", "display_time": "Sat 16:00"},
            {"name": "Race", "display_time": "Sun 15:00"},
        ],
    }

    class StubF1Service:
        """Provide deterministic static race data to the dynamic preview renderer."""

        @staticmethod
        def get_next_race_from_static():
            """Return the fixed next race used by this test."""
            return race_data

        @staticmethod
        def get_historical_from_static(circuit_id):
            """Return a new-track marker for the requested test circuit."""
            assert circuit_id == "test_circuit"
            return HistoricalData(is_new_track=True)

    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(previews_routes.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(previews_routes, "F1Service", StubF1Service)
    try:
        response = client.get("/preview/calendar.png?lang=en")
    finally:
        _reset_rate_limit_state_for_tests()

    image = Image.open(BytesIO(response.content))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=300"
    assert image.format == "PNG"
    assert image.size == (400, 240)


def test_common_js_sends_beacon_payload_as_json():
    """Web-vitals beacons must use JSON media type accepted by FastAPI."""
    response = client.get("/static/js/common.js")

    assert response.status_code == 200
    assert 'new Blob([jsonPayload], { type: "application/json" })' in response.text


def test_homepage_mobile_menu_button():
    """Test homepage contains mobile menu button."""
    response = client.get("/")
    html = response.text
    assert 'id="mobileMenuBtn"' in html
    assert "toggleMobileMenu" in html


def test_homepage_language_switcher():
    """Test homepage language switcher functionality."""
    response_en = client.get("/?lang=en")
    assert 'lang="en"' in response_en.text

    response_cs = client.get("/?lang=cs")
    assert 'lang="cs"' in response_cs.text


# ============================================================================
# Configure Page Mobile Tests
# ============================================================================


def test_configure_calendar_mobile_settings_button():
    """Test configure calendar page has mobile settings button."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="settingsBtnText"' in html
    assert "toggleSidebar()" in html


def test_configure_calendar_contains_color_display_options():
    """Test configure calendar page exposes BWR and BWRY display selections."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="displayBwrBtn"' in html
    assert 'id="displayBwrBtnMobile"' in html
    assert "setDisplayType('bwr')" in html
    assert 'id="displayBwryBtn"' in html
    assert 'id="displayBwryBtnMobile"' in html
    assert "setDisplayType('bwry')" in html


def test_configure_calendar_mobile_timezone_selector():
    """Test configure calendar page has mobile timezone selector."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="mobileTzContainer"' in html
    assert 'id="tzSelectMobile"' in html


def test_configure_calendar_normalizes_legacy_ist_timezone_alias():
    """Test configure JS normalizes browser legacy IST aliases before URL updates."""
    response = client.get("/configure/calendar")
    html = response.text
    assert "const timezoneAliases = " in html
    assert "Asia/Calcutta" in html
    assert "Asia/Kolkata" in html
    assert "normalizeTimezone(" in html


def test_configure_calendar_mobile_race_selector():
    """Test configure calendar page has mobile race selector."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="mobileRaceContainer"' in html
    assert 'id="raceSelectMobile"' in html


def test_configure_teams_mobile_year_selector():
    """Test configure teams page has mobile year selector."""
    response = client.get("/configure/teams")
    html = response.text
    assert 'id="mobileYearContainer"' in html
    assert 'id="yearSelectMobile"' in html
    assert "selectYearMobile()" in html


def test_configure_teams_display_menu_matches_calendar_variants():
    """Teams configure page should expose the same display variants as calendar."""
    response = client.get("/configure/teams")
    soup = BeautifulSoup(response.text, "html.parser")

    desktop_buttons = [
        soup.find(id="display1bitBtn"),
        soup.find(id="displayBwrBtn"),
        soup.find(id="displayBwryBtn"),
        soup.find(id="displaySpectra6Btn"),
    ]
    mobile_buttons = [
        soup.find(id="display1bitBtnMobile"),
        soup.find(id="displayBwrBtnMobile"),
        soup.find(id="displayBwryBtnMobile"),
        soup.find(id="displaySpectra6BtnMobile"),
    ]

    assert all(btn is not None for btn in desktop_buttons)
    assert all(btn is not None for btn in mobile_buttons)

    assert [cast(Tag, btn).get_text(" ", strip=True) for btn in desktop_buttons] == [
        "B/W",
        "B/W/R",
        "B/W/R/Y",
        "6C",
    ]
    assert [cast(Tag, btn).get_text(" ", strip=True) for btn in mobile_buttons] == [
        "B/W",
        "B/W/R",
        "B/W/R/Y",
        "6C",
    ]


def test_configure_teams_no_timezone_selector():
    """Test configure teams page omits timezone controls entirely."""
    response = client.get("/configure/teams")
    html = response.text
    assert 'id="mobileTzContainer"' not in html
    assert 'id="desktopTzContainer"' not in html
    assert 'id="tzSelectMobile"' not in html
    assert 'id="tzSearch"' not in html
    assert 'id="tz"' not in html
    assert 'id="mobileYearContainer"' in html
    assert "Season Leaders" in html
    assert 'id="rightPanelCalendar"' not in html


def test_configure_calendar_omits_teams_specific_controls():
    """Calendar configure page renders only calendar-specific partials."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="mobileYearContainer"' not in html
    assert 'id="teamsSeasonButtons"' not in html
    assert 'id="rightPanelTeams"' not in html
    assert 'id="rightPanelCalendar"' in html


def test_configure_calendar_display_menu_is_compact_and_reordered():
    """Calendar configure menu uses compact display labels and expected ordering."""
    response = client.get("/configure/calendar")
    soup = BeautifulSoup(response.text, "html.parser")

    assert "overflow-y-auto" in response.text

    desktop_buttons = [
        soup.find(id="display1bitBtn"),
        soup.find(id="displayBwrBtn"),
        soup.find(id="displayBwryBtn"),
        soup.find(id="displaySpectra6Btn"),
    ]
    mobile_buttons = [
        soup.find(id="display1bitBtnMobile"),
        soup.find(id="displayBwrBtnMobile"),
        soup.find(id="displayBwryBtnMobile"),
        soup.find(id="displaySpectra6BtnMobile"),
    ]

    assert all(btn is not None for btn in desktop_buttons)
    assert all(btn is not None for btn in mobile_buttons)

    assert [cast(Tag, btn).get_text(" ", strip=True) for btn in desktop_buttons] == [
        "B/W",
        "B/W/R",
        "B/W/R/Y",
        "6C",
    ]
    assert [cast(Tag, btn).get_text(" ", strip=True) for btn in mobile_buttons] == [
        "B/W",
        "B/W/R",
        "B/W/R/Y",
        "6C",
    ]


def test_configure_teams_urls_do_not_include_timezone_params():
    """Teams configure JS should not add timezone to API or preview URLs."""
    response = client.get("/configure/teams")
    html = response.text

    api_url_block = html.split("function getApiUrl() {", 1)[1].split(
        "function sortRacesWithCancelledLast", 1
    )[0]
    teams_api_branch = api_url_block.split("} else {", 1)[1]
    assert "`${window.location.origin}/teams.bmp`" in teams_api_branch
    assert "params.push(`year=${selectedTeamsYear}`)" in teams_api_branch
    assert "params.push(`lang=${currentUiLang}`)" in teams_api_branch
    assert "params.push(`display=${currentDisplayType}`)" in teams_api_branch
    assert "params.push(`tz=" not in teams_api_branch

    preview_url_block = html.split("function getPreviewUrl() {", 1)[1].split(
        "async function loadRaces()", 1
    )[0]
    teams_preview_branch = preview_url_block.split(
        '} else if (canUsePrerenderedPreview() && currentScreenType === "teams") {', 1
    )[1].split("return getApiUrl();", 1)[0]
    assert (
        "`${window.location.origin}/preview/configure/${currentScreenType}.png`"
        in teams_preview_branch
    )
    assert "params.push(`lang=${currentUiLang}`)" in teams_preview_branch
    assert "params.push(`display=${currentDisplayType}`)" in teams_preview_branch
    assert "tz=" not in teams_preview_branch


def test_teams_configure_preview_route_supports_display_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Teams configure preview route should resolve display-specific filenames."""
    monkeypatch.setattr("app.routes.previews.config.IMAGES_PATH", str(tmp_path))
    preview_path = tmp_path / "configure_teams_en_bwr.png"
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(preview_path, format="PNG")

    response = client.get("/preview/configure/teams.png?display=bwr")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == preview_path.read_bytes()


def test_configure_sidebar_mobile_nav_links():
    """Test configure page sidebar has navigation links for mobile."""
    response = client.get("/configure/calendar?lang=en")
    html = response.text
    # English is default - links should NOT have ?lang=en parameter
    assert 'href="/stats"' in html
    assert 'href="/api/docs/html"' in html
    assert 'href="/privacy"' in html
    assert 'href="/changelog"' in html
    assert 'href="https://github.com/Rhiz3K/InkyCloud-F1"' in html


def test_configure_sidebar_mobile_nav_links_czech():
    """Test configure page sidebar nav links use subdirectory URLs for Czech."""
    response = client.get("/cs/configure/calendar")
    html = response.text
    # With subdirectory URLs, Czech nav links use /cs/ prefix
    assert 'href="/cs/stats"' in html
    assert 'href="/cs/api/docs/html"' in html
    assert 'href="/cs/privacy"' in html
    assert 'href="/cs/changelog"' in html


def test_configure_page_translations_english():
    """Test configure page English translations."""
    response = client.get("/configure/calendar?lang=en")
    html = response.text
    assert f'"settingsBtn": {json.dumps("Settings")}' in html
    assert f'"yearLabel": {json.dumps("Season")}' in html
    assert f'"loadingText": {json.dumps("Loading...")}' in html


def test_configure_page_translations_czech():
    """Test configure page Czech translations."""
    response = client.get("/configure/calendar?lang=cs")
    html = response.text
    assert f'"settingsBtn": {json.dumps("Nastavení")}' in html
    assert f'"yearLabel": {json.dumps("Sezóna")}' in html
    assert f'"loadingText": {json.dumps("Načítání...")}' in html


def test_configure_page_loading_overlay():
    """Test configure page has loading overlay with spinner."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="loadingOverlay"' in html
    assert 'id="loadingText"' in html
    assert "animate-spin" in html


def test_configure_page_right_panel_hidden_mobile():
    """Test configure page right panel has correct responsive classes."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'id="rightPanel"' in html
    assert 'class="hidden lg:flex' in html


def test_configure_teams_screen_type():
    """Test configure teams page has correct screen type."""
    response = client.get("/configure/teams")
    html = response.text
    assert 'currentScreenType = "teams"' in html


def test_configure_teams_season_buttons_do_not_duplicate_current_year():
    """Test teams season buttons derive extra seasons dynamically."""
    response = client.get("/configure/teams")
    html = response.text
    assert "function getTeamsSeasonEntries()" in html
    assert '{ label: "2026", year: 2026, disabled: true }' not in html


def test_configure_teams_seasons_are_supplied_by_backend():
    with (
        patch("app.routes.pages.get_current_f1_season", return_value=2027),
        patch("app.routes.pages.get_available_teams_years", return_value=[2025, 2026]),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/configure/teams")

    assert response.status_code == 200
    assert "const currentTeamsSeason = 2027;" in response.text
    assert "const availableTeamsSeasons = [2027, 2026, 2025];" in response.text
    assert "season2026Start" not in response.text


def test_configure_calendar_screen_type():
    """Test configure calendar page has correct screen type."""
    response = client.get("/configure/calendar")
    html = response.text
    assert 'currentScreenType = "calendar"' in html


# ============================================================================
# BMP Endpoint Tests
# ============================================================================


def test_calendar_bmp_default():
    """Test /calendar.bmp returns BMP image."""
    response = client.get("/calendar.bmp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"


def test_calendar_bmp_executes_real_render_pipeline(monkeypatch):
    clear_bmp_cache()
    monkeypatch.setattr(images_routes, "_get_pregenerated_calendar_path", lambda **_kwargs: None)
    render_calendar = Mock(wraps=images_routes._render_calendar_bytes)
    monkeypatch.setattr(images_routes, "_render_calendar_bytes", render_calendar)

    response = client.get("/calendar.bmp?weather=false")

    image = Image.open(BytesIO(response.content))
    assert response.status_code == 200
    assert render_calendar.call_count == 1
    assert image.size == (800, 480)
    assert image.mode == "1"


def test_calendar_bmp_with_lang():
    """Test /calendar.bmp with language parameter."""
    for lang in ["en", "cs"]:
        response = client.get(f"/calendar.bmp?lang={lang}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/bmp"


def test_calendar_bmp_with_timezone():
    """Test /calendar.bmp with timezone parameter."""
    response = client.get("/calendar.bmp?tz=Europe/Prague")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"


def test_calendar_bmp_accepts_legacy_ist_timezone_alias():
    """Test /calendar.bmp accepts browser legacy IST timezone aliases."""
    response = client.get("/calendar.bmp?tz=Asia%2FCalcutta")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"


def test_calendar_bmp_with_year_round():
    """Test /calendar.bmp with year and round parameters."""
    response = client.get("/calendar.bmp?year=2025&round=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"


def test_calendar_bmp_rejects_race_key_without_year():
    """Race-key selection should require an explicit year."""
    response = client.get("/calendar.bmp?race_key=2026-round-1-albert-park-2026-03-08")
    assert response.status_code == 400
    assert response.json() == {"detail": "race_key requires year"}


def test_get_race_info_for_stats_matches_string_round_values():
    """Round lookup should work even when static data stores rounds as strings."""

    class StubF1Service:
        @staticmethod
        def get_all_races_from_static(year):
            assert year == 2026
            return [{"round": "4", "race_name": "Bahrain Grand Prix"}]

        @staticmethod
        def get_next_race_from_static():
            raise AssertionError("next race lookup should not be used")

    is_auto_selected, actual_year, actual_round, actual_race_name = _get_race_info_for_stats(
        cast(F1Service, StubF1Service()), 2026, 4, None
    )

    assert is_auto_selected is False
    assert actual_year == 2026
    assert actual_round == 4
    assert actual_race_name == "Bahrain Grand Prix"


def test_get_driver_number_uses_shared_metadata_map():
    """Driver number helper should resolve known F1 codes from shared metadata."""
    assert _get_driver_number("VER", 2026) == 3
    assert _get_driver_number("NOR", 2026) == 1
    assert _get_driver_number("PER", 2026) == 11
    assert _get_driver_number("LIN", 2026) == 41
    assert _get_driver_number("UNKNOWN", 2026) is None


def test_get_team_id_uses_shared_metadata_map():
    """Team id helper should resolve route payload ids from shared metadata."""
    assert _get_team_id("Oracle Red Bull Racing") == "red_bull"
    assert _get_team_id("Unknown Team") is None


def test_get_race_data_from_static_matches_string_round_values():
    """Round-based race lookup should accept string rounds from cached static data."""

    class StubF1Service:
        @staticmethod
        def get_all_races_from_static(year):
            assert year == 2026
            return [{"round": "4", "race_name": "Bahrain Grand Prix"}]

        @staticmethod
        def get_next_race_from_static():
            raise AssertionError("next race lookup should not be used")

    race = _get_race_data_from_static(cast(F1Service, StubF1Service()), 2026, 4, None)

    assert race == {"round": "4", "race_name": "Bahrain Grand Prix"}


def test_calendar_bmp_with_bwr_display():
    """Test /calendar.bmp with BWR display parameter."""
    response = client.get("/calendar.bmp?display=bwr")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"
    assert int.from_bytes(response.content[28:30], byteorder="little") == 4


def test_calendar_bmp_with_bwry_display():
    """Test /calendar.bmp with BWRY display parameter."""
    response = client.get("/calendar.bmp?display=bwry")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"
    assert int.from_bytes(response.content[28:30], byteorder="little") == 4


def test_schedule_calendar_analytics_uses_background_task():
    tracked_coro = object()

    with (
        patch.object(
            images_routes, "_track_calendar_analytics", new=Mock(return_value=tracked_coro)
        ) as mock_track,
        patch.object(images_routes, "create_supervised_task") as mock_create_task,
    ):
        _schedule_calendar_analytics(
            lang="en",
            tz="Europe/Prague",
            year=2026,
            race_round=5,
            race_key="2026-round-5-monaco-2026-05-24",
            user_agent="TestAgent/1.0",
            referrer="https://example.com",
        )

    mock_track.assert_called_once_with(
        lang="en",
        tz="Europe/Prague",
        year=2026,
        race_round=5,
        race_key="2026-round-5-monaco-2026-05-24",
        user_agent="TestAgent/1.0",
        referrer="https://example.com",
    )
    mock_create_task.assert_called_once_with(tracked_coro, name="calendar_analytics")


def test_calendar_bmp_rate_limit_returns_429(monkeypatch):
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(images_routes.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(images_routes.config, "IMAGE_RATE_LIMIT_PER_MINUTE", 1)

    try:
        first = client.get("/calendar.bmp")
        second = client.get("/calendar.bmp")
    finally:
        _reset_rate_limit_state_for_tests()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "Rate limit exceeded"
    assert second.headers["Retry-After"] == "60"


def test_calendar_bmp_error_response_is_not_cached(monkeypatch):
    """Transient calendar errors must not poison the normal BMP cache."""
    clear_bmp_cache()
    monkeypatch.setattr(images_routes, "_get_race_data_from_static", lambda *args, **kwargs: None)

    response = client.get("/calendar.bmp?year=2099&round=1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.headers["cache-control"] == "no-store"
    assert response.content[:2] == b"BM"
    assert get_bmp_cache() == {}


def test_pregenerated_calendar_path_uses_off_variant_when_weather_disabled(tmp_path, monkeypatch):
    """`weather=false` should never resolve to a weather-overlay pregenerated BMP."""
    monkeypatch.setattr(images_routes.config, "IMAGES_PATH", str(tmp_path))
    off_path = tmp_path / "calendar_en.bmp"
    weather_path = tmp_path / "calendar_en_weather_race.bmp"
    off_path.write_bytes(b"off")
    weather_path.write_bytes(b"weather")

    image_path = _get_pregenerated_calendar_path(
        lang="en",
        year=None,
        race_round=None,
        race_key=None,
        tz=None,
        display="1bit",
        weather=False,
        weather_type="race_day",
    )

    assert image_path == off_path


def test_teams_bmp_default():
    """Test /teams.bmp returns BMP image."""
    response = client.get("/teams.bmp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"


def test_teams_bmp_with_year():
    """Test /teams.bmp with year parameter."""
    for year in [2025, 2026]:
        response = client.get(f"/teams.bmp?year={year}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/bmp"


def test_teams_bmp_with_lang():
    """Test /teams.bmp with language parameter."""
    response = client.get("/teams.bmp?lang=cs")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"


def test_teams_bmp_with_bwr_display():
    """Test /teams.bmp with BWR display parameter."""
    response = client.get("/teams.bmp?display=bwr")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"
    assert int.from_bytes(response.content[28:30], byteorder="little") == 4


def test_teams_bmp_with_bwry_display():
    """Test /teams.bmp with BWRY display parameter."""
    response = client.get("/teams.bmp?display=bwry")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"
    assert int.from_bytes(response.content[28:30], byteorder="little") == 4


def test_teams_bmp_with_spectra6_display():
    """Test /teams.bmp with Spectra 6 display parameter."""
    response = client.get("/teams.bmp?display=spectra6")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"
    assert int.from_bytes(response.content[28:30], byteorder="little") == 8


def test_teams_bmp_executes_real_render_pipeline(monkeypatch):
    clear_bmp_cache()
    monkeypatch.setattr(images_routes, "_get_pregenerated_teams_path", lambda **_kwargs: None)
    render_teams = Mock(wraps=images_routes._render_teams_bytes)
    monkeypatch.setattr(images_routes, "_render_teams_bytes", render_teams)

    response = client.get("/teams.bmp?year=2026")

    image = Image.open(BytesIO(response.content))
    assert response.status_code == 200
    assert render_teams.call_count == 1
    assert image.size == (800, 480)
    assert image.mode == "1"


def test_get_pregenerated_teams_path_uses_default_year_in_filename(tmp_path, monkeypatch):
    """Pregenerated teams lookup should select the current season-specific BMP."""
    monkeypatch.setattr(images_routes.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(images_routes, "get_default_teams_year", lambda: 2026)
    old_path = tmp_path / "teams_2025_en.bmp"
    current_path = tmp_path / "teams_2026_en.bmp"
    old_path.write_bytes(b"old")
    current_path.write_bytes(b"current")

    image_path = _get_pregenerated_teams_path(lang="en", year=None, display="1bit")

    assert image_path == current_path


def test_teams_bmp_caches_pregenerated_assets_with_shared_image_key(tmp_path, monkeypatch):
    """Teams endpoint should cache pregenerated BMPs under the shared image-key helper."""
    clear_bmp_cache()
    monkeypatch.setattr(images_routes.config, "IMAGES_PATH", str(tmp_path))
    image_path = tmp_path / "teams_2026_en_bwr.bmp"
    image_path.write_bytes(b"BMpregenerated")

    response = client.get("/teams.bmp?year=2026&display=bwr")

    assert response.status_code == 200
    expected_key = get_teams_image_key("en", 2026, display="bwr")
    assert get_bmp_cache()[expected_key] == (
        b"BMpregenerated",
        strong_etag(b"BMpregenerated"),
    )
    assert "teams:en:2026:bwr" not in get_bmp_cache()


def test_teams_bmp_falls_back_when_pregenerated_read_races_with_cleanup(monkeypatch):
    """A vanished prebuilt image should fall through to dynamic rendering."""
    clear_bmp_cache()
    disappearing_path = Mock()
    disappearing_path.read_bytes.side_effect = OSError("removed")
    monkeypatch.setattr(
        images_routes, "_get_pregenerated_teams_path", lambda **_kwargs: disappearing_path
    )
    monkeypatch.setattr(images_routes, "run_render", AsyncMock(return_value=b"BMdynamic"))
    teams_service_cls = Mock()
    teams_service_cls.return_value.get_teams_and_drivers = AsyncMock(
        return_value=TeamsData(season=2026, teams=[], standings_complete=False)
    )
    monkeypatch.setattr(images_routes, "TeamsService", teams_service_cls)

    response = client.get("/teams.bmp?year=2026")

    assert response.status_code == 200
    assert response.content == b"BMdynamic"


def test_teams_bmp_does_not_cache_degraded_standings(monkeypatch):
    """A transient standings outage should not pin a degraded teams BMP in memory."""
    clear_bmp_cache()
    degraded_teams = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Test Team")],
        standings_complete=False,
    )

    monkeypatch.setattr(images_routes, "_get_pregenerated_teams_path", lambda **_kwargs: None)
    monkeypatch.setattr(images_routes, "run_render", AsyncMock(return_value=b"BMdynamic"))
    teams_service_cls = Mock()
    teams_service_cls.return_value.get_teams_and_drivers = AsyncMock(return_value=degraded_teams)
    monkeypatch.setattr(images_routes, "TeamsService", teams_service_cls)

    response = client.get("/teams.bmp?year=2026")

    assert response.status_code == 200
    assert response.content == b"BMdynamic"
    assert get_teams_image_key("en", 2026, display="1bit") not in get_bmp_cache()


def test_teams_bmp_redacts_internal_error_details(monkeypatch):
    captured: dict[str, str] = {}

    def render_error(_display: str, _lang: str, message: str) -> bytes:
        captured["message"] = message
        return b"BM"

    clear_bmp_cache()
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(images_routes, "_get_pregenerated_teams_path", lambda **_kwargs: None)
    teams_service_cls = Mock()
    teams_service_cls.return_value.get_teams_and_drivers = AsyncMock(
        side_effect=RuntimeError("/srv/private/data.json")
    )
    monkeypatch.setattr(images_routes, "TeamsService", teams_service_cls)
    monkeypatch.setattr(images_routes, "_render_error_bytes", render_error)

    response = client.get("/teams.bmp?year=2026")

    assert response.status_code == 200
    assert captured["message"] == "Temporary rendering error"
    assert response.headers["cache-control"] == "no-store"


# ============================================================================
# API Endpoint Tests
# ============================================================================


def test_perf_metrics_rate_limit_returns_429(monkeypatch):
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(api_routes.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(api_routes.config, "PERF_METRICS_RATE_LIMIT_PER_MINUTE", 1)

    payload = {
        "page_path": "/calendar.bmp",
        "lcp_ms": 1000.0,
        "cls": 0.01,
        "fcp_ms": 500.0,
        "ttfb_ms": 200.0,
        "inp_ms": 100.0,
        "connection_type": "4g",
        "device_memory": 8.0,
    }

    try:
        first = client.post("/api/perf-metrics", json=payload)
        second = client.post("/api/perf-metrics", json=payload)
    finally:
        _reset_rate_limit_state_for_tests()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"] == "Rate limit exceeded"


def test_stats_api_reads_share_rate_limit(monkeypatch):
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(api_routes.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(api_routes.config, "STATS_RATE_LIMIT_PER_MINUTE", 1)

    try:
        first = client.get("/api/stats")
        second = client.get("/api/perf-metrics")
    finally:
        _reset_rate_limit_state_for_tests()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "60"


def test_stats_dashboard_rate_limit_skips_head(monkeypatch):
    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(pages_routes.config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(pages_routes.config, "STATS_RATE_LIMIT_PER_MINUTE", 1)

    try:
        assert client.head("/stats").status_code == 200
        first = client.get("/stats")
        second = client.get("/stats")
    finally:
        _reset_rate_limit_state_for_tests()

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_api_races_endpoint():
    """Test /api/races/{year} returns race data."""
    response = client.get("/api/races/2025")
    assert response.status_code == 200
    data = response.json()
    assert "races" in data or "year" in data or isinstance(data, list)


def test_api_info_uses_package_version():
    response = client.get("/api")

    assert response.status_code == 200
    assert response.json()["version"] == APP_VERSION


def test_api_races_invalid_year():
    """Test /api/races with invalid year."""
    response = client.get("/api/races/1900")
    assert response.status_code == 422


def test_live_data_endpoints_reject_unsupported_year_before_fetch():
    for path in ("/api/teams/999999", "/api/standings/leader/999999"):
        response = client.get(path)
        assert response.status_code == 422


def test_teams_api_uses_shared_data_rate_limit(monkeypatch):
    async def fake_teams(_service, year):
        return TeamsData(season=year, teams=[], standings_complete=False)

    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(config, "DATA_API_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(TeamsService, "get_teams_and_drivers", fake_teams)
    try:
        assert client.get("/api/teams/2026").status_code == 200
        assert client.get("/api/teams/2026").status_code == 429
    finally:
        _reset_rate_limit_state_for_tests()


# ============================================================================
# Changelog Page Tests
# ============================================================================


def test_changelog_returns_html():
    """Test /changelog endpoint returns HTML page."""
    response = client.get("/changelog")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_missing_changelog_message_is_localized(tmp_path):
    """The missing-file fallback should use the active locale and remain escaped."""
    missing_path = tmp_path / "missing.md"
    with (
        patch.object(pages_routes, "CHANGELOG_PATH", missing_path),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/cs/changelog")

    assert response.status_code == 200
    assert "Historie změn nebyla nalezena." in response.text


def test_service_worker_revalidates_unversioned_fonts():
    """Font replacements must use the same stale-while-revalidate path as other assets."""
    service_worker = (previews_routes.ASSETS_DIR / "js" / "sw.js").read_text(encoding="utf-8")

    assert 'startsWith("/static/fonts/")' not in service_worker
    assert "event.waitUntil(network)" in service_worker


def test_changelog_render_is_cached_by_file_version():
    pages_routes._render_changelog_file.cache_clear()
    with (
        patch.object(
            pages_routes.markdown,
            "markdown",
            wraps=pages_routes.markdown.markdown,
        ) as render_markdown,
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        assert client.get("/changelog").status_code == 200
        assert client.get("/changelog").status_code == 200

    assert render_markdown.call_count == 1


def test_changelog_contains_version_history():
    """Test changelog page contains version history."""
    response = client.get("/changelog?lang=en")
    html = response.text
    assert "Changelog" in html or "Changes" in html


def test_changelog_hides_empty_unreleased_heading():
    """Test changelog page does not render an empty Unreleased section."""
    response = client.get("/changelog?lang=en")
    html = response.text
    assert ">Unreleased<" not in html


def test_changelog_has_no_collapsible_sections():
    """Changelog page should render backend sections expanded by default."""
    response = client.get("/changelog?lang=en")
    html = response.text
    assert "<details" not in html
    assert "<summary>Backend</summary>" not in html


def test_changelog_sanitizer_blocks_extended_uri_and_inline_style_vectors():
    from app.routes.pages import _sanitize_rendered_html

    sanitized = _sanitize_rendered_html(
        '<form action="java\tscript:alert(1)">'
        '<button formaction="data:text/html,x">x</button>'
        '<video poster="vbscript:alert(1)"></video>'
        '<a xlink:href="javascript:alert(1)" style="background:url(javascript:x)">x</a>'
        "</form>"
    )

    assert "javascript:" not in sanitized.lower()
    assert "vbscript:" not in sanitized.lower()
    assert "data:text" not in sanitized.lower()
    assert "style=" not in sanitized.lower()


def test_redirect_helper_rejects_backslash_network_paths():
    from app.routes.pages import _redirect_path

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("example.test", 443),
            "client": ("127.0.0.1", 1234),
        }
    )

    with pytest.raises(ValueError, match="stay on-site"):
        _redirect_path(request, "/\\evil.example/path")


def test_changelog_lang_parameter():
    """Test changelog page respects lang parameter."""
    response_en = client.get("/changelog?lang=en")
    response_cs = client.get("/changelog?lang=cs")
    assert response_en.status_code == 200
    assert response_cs.status_code == 200


def test_changelog_header_nav():
    """Test changelog page has navigation in header."""
    response = client.get("/changelog")
    html = response.text
    assert 'href="/"' in html


def test_strip_empty_unreleased_section_removes_blank_heading_only():
    """Empty Unreleased heading is removed without regex backtracking."""
    from app.routes.pages import _strip_empty_unreleased_section

    changelog = """# Changelog

## [Unreleased]


## [1.2.9] - 2026-03-13

- Added release notes
"""

    stripped = _strip_empty_unreleased_section(changelog)

    assert "## [Unreleased]" not in stripped
    assert "## [1.2.9] - 2026-03-13" in stripped


def test_convert_race_times_to_timezone():
    """Test _convert_race_times_to_timezone correctly converts schedule times."""
    from app.main import _convert_race_times_to_timezone

    race_data = {
        "race_date": "01.03.2025",
        "schedule": [
            {"name": "FP1", "datetime": "2025-03-01T10:30:00+00:00", "display_time": "Sat 10:30"},
            {"name": "Race", "datetime": "2025-03-02T14:00:00+00:00", "display_time": "Sun 14:00"},
        ],
    }

    result = _convert_race_times_to_timezone(race_data, "America/New_York")

    assert result["timezone"] == "America/New_York"
    assert result["schedule"][0]["display_time"] == "Sat 05:30"
    assert result["schedule"][1]["display_time"] == "Sun 09:00"
    assert "-05:00" in result["schedule"][0]["datetime"]


def test_convert_race_times_to_timezone_normalizes_legacy_ist_timezone_alias():
    """Test timezone conversion accepts and canonicalizes legacy IST aliases."""
    from app.main import _convert_race_times_to_timezone

    race_data = {
        "race_date": "02.03.2025",
        "schedule": [
            {"name": "Race", "datetime": "2025-03-02T14:00:00+00:00"},
        ],
    }

    result = _convert_race_times_to_timezone(race_data, "Asia/Calcutta")

    assert result["timezone"] == "Asia/Kolkata"
    assert result["schedule"][0]["display_time"] == "Sun 19:30"
    assert "+05:30" in result["schedule"][0]["datetime"]


def test_convert_race_times_to_timezone_invalid_tz():
    """Test _convert_race_times_to_timezone handles invalid timezone gracefully."""
    from app.main import _convert_race_times_to_timezone

    race_data = {"schedule": [{"name": "Race", "datetime": "2025-03-02T14:00:00+00:00"}]}

    result = _convert_race_times_to_timezone(race_data, "Invalid/Timezone")

    assert result == race_data


def test_convert_race_times_to_timezone_updates_race_date():
    """Test _convert_race_times_to_timezone updates race_date from Race event."""
    from app.main import _convert_race_times_to_timezone

    race_data = {
        "race_date": "02.03.2025",
        "schedule": [
            {"name": "Race", "datetime": "2025-03-02T14:00:00+00:00", "display_time": "Sun 14:00"},
        ],
    }

    result = _convert_race_times_to_timezone(race_data, "Europe/Prague")

    assert result["race_date"] == "02.03.2025"
    assert result["timezone"] == "Europe/Prague"


def test_perf_metrics_post_endpoint():
    """Test POST /api/perf-metrics endpoint accepts valid payload."""
    payload = {
        "page_path": "/calendar.bmp",
        "lcp_ms": 1200.5,
        "cls": 0.05,
        "fcp_ms": 800.0,
        "ttfb_ms": 150.0,
        "inp_ms": 50.0,
        "connection_type": "4g",
        "device_memory": 8.0,
    }

    response = client.post("/api/perf-metrics", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_perf_metrics_post_invalid_payload():
    """Test POST /api/perf-metrics handles invalid payload gracefully."""
    response = client.post("/api/perf-metrics", json={"invalid": "data"})

    assert response.status_code == 422


def test_perf_metrics_database_failure_returns_503(monkeypatch):
    db = Mock(save_perf_metric=AsyncMock(side_effect=RuntimeError("database offline")))
    db.close = AsyncMock()
    monkeypatch.setattr(api_routes, "get_database", lambda: db)

    response = client.post("/api/perf-metrics", json={"page_path": "/"})

    assert response.status_code == 503
    db.close.assert_not_awaited()


def test_operational_query_bounds_return_422():
    assert client.get("/api/perf-metrics?hours=-100000000").status_code == 422
    assert client.get("/api/stats/history?limit=0").status_code == 422


@pytest.mark.parametrize(
    ("screen_type", "renderer_name"),
    [
        ("calendar", "_render_calendar_preview"),
        ("teams", "_render_teams_preview"),
    ],
)
def test_dynamic_preview_preserves_rate_limit_429(
    tmp_path, monkeypatch, screen_type, renderer_name
):
    """Dynamic homepage previews must preserve rate-limit responses."""
    from fastapi.responses import Response

    async def fake_preview(*_args, **_kwargs):
        return Response(content=b"png", media_type="image/png")

    _reset_rate_limit_state_for_tests()
    monkeypatch.setattr(previews_routes.config, "IMAGES_PATH", str(tmp_path))
    monkeypatch.setattr(previews_routes.config, "IMAGE_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(previews_routes, renderer_name, fake_preview)
    try:
        assert client.get(f"/preview/{screen_type}.png").status_code == 200
        limited = client.get(f"/preview/{screen_type}.png")
    finally:
        _reset_rate_limit_state_for_tests()

    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


# Extended startup, persistence, and ASGI middleware coverage.


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _http_scope(path: str, *, scheme: str = "https", host: str = "test") -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 443),
    }


def test_persistence_check_covers_skip_existing_write_failure_warning_and_first_deploy(tmp_path):
    with patch("app.main.config.SKIP_PERSISTENCE_CHECK", True):
        assert main._check_persistent_storage() is True

    marker = MagicMock()
    marker.exists.return_value = True
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main._PERSISTENCE_MARKER", marker),
    ):
        assert main._check_persistent_storage() is True

    marker.exists.return_value = False
    marker.write_text.side_effect = OSError("read only")
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main._PERSISTENCE_MARKER", marker),
    ):
        assert main._check_persistent_storage() is False

    database = tmp_path / "f1.db"
    database.touch()
    persistence_marker = tmp_path / ".marker"
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main.config.DATABASE_PATH", str(database)),
        patch("app.main._PERSISTENCE_MARKER", persistence_marker),
    ):
        assert main._check_persistent_storage() is False

    persistence_marker.unlink()
    database.unlink()
    with (
        patch("app.main.config.SKIP_PERSISTENCE_CHECK", False),
        patch("app.main.config.DATABASE_PATH", str(database)),
        patch("app.main._PERSISTENCE_MARKER", persistence_marker),
    ):
        assert main._check_persistent_storage() is True


@pytest.mark.asyncio
async def test_lifespan_isolates_startup_and_shutdown_failures():
    def completed_task(coroutine, *, name):
        coroutine.close()
        return SimpleNamespace(done=lambda: True)

    with (
        patch("app.main._check_persistent_storage"),
        patch("app.main.ensure_runtime_circuits_data", side_effect=RuntimeError("seed")),
        patch("app.main.RENDER_WORKER_COUNT", 1),
        patch("app.main.run_render", new=AsyncMock(side_effect=RuntimeError("warm"))),
        patch("app.main.start_scheduler"),
        patch("app.main.create_supervised_task", side_effect=completed_task),
        patch("app.main.stop_scheduler"),
        patch("app.main.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.main.flush_api_calls_to_db",
            new=AsyncMock(side_effect=RuntimeError("flush")),
        ),
        patch("app.main.drain_background_tasks", new=AsyncMock()),
        patch("app.main.shutdown_render_executor", side_effect=RuntimeError("executor")),
        patch(
            "app.main.close_shared_http_clients",
            new=AsyncMock(side_effect=RuntimeError("http")),
        ),
        patch(
            "app.main.close_shared_database",
            new=AsyncMock(side_effect=RuntimeError("database")),
        ),
        patch("app.main.sentry_sdk.capture_exception") as capture,
    ):
        async with main.lifespan(FastAPI()):
            pass

    assert capture.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status", "expected_cache"),
    [
        ("/static/fonts/font.ttf", 200, "public, max-age=31536000, immutable"),
        ("/static/image.png", 200, "public, max-age=86400"),
        ("/static/app.css", 200, "public, max-age=3600"),
        ("/static/data.txt", 200, None),
        ("/static/app.css", 404, None),
        ("/page", 200, None),
    ],
)
async def test_static_cache_middleware_policies(path, status, expected_cache):
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        messages.append(message)

    await main.StaticCacheMiddleware(app)(_http_scope(path), _receive, send)
    headers = dict(messages[0]["headers"])
    assert headers.get(b"cache-control") == (
        expected_cache.encode() if expected_cache is not None else None
    )


@pytest.mark.asyncio
async def test_static_and_security_middleware_forward_non_http_scopes():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["type"])

    scope = {"type": "websocket"}
    await main.StaticCacheMiddleware(app)(scope, _receive, AsyncMock())
    await main.SecurityHeadersMiddleware(app)(scope, _receive, AsyncMock())
    assert calls == ["websocket", "websocket"]


@pytest.mark.asyncio
async def test_security_middleware_redirects_www_with_query_and_sets_https_headers():
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        messages.append(message)

    scope = _http_scope("/path", host="www.example.test")
    scope["query_string"] = b"a=1"
    with patch("app.main.config.SITE_URL", "https://example.test"):
        await main.SecurityHeadersMiddleware(app)(scope, _receive, send)

    headers = dict(messages[0]["headers"])
    assert headers[b"location"] == b"https://example.test/path?a=1"
    assert headers[b"strict-transport-security"] == b"max-age=31536000"

    messages.clear()
    scope = _http_scope("/path", scheme="http", host="example.test")
    with patch("app.main.config.SITE_URL", "https://example.test"):
        await main.SecurityHeadersMiddleware(app)(scope, _receive, send)
    headers = dict(messages[0]["headers"])
    assert b"strict-transport-security" not in headers
    assert headers[b"x-frame-options"] == b"DENY"


def test_html_404_predicate_rejects_non_get_requests():
    scope = _http_scope("/unknown")
    scope["method"] = "POST"
    scope["headers"].append((b"accept", b"text/html"))

    assert main._should_render_html_404(Request(scope)) is False


@pytest.mark.filterwarnings("ignore:.*app.main.*found in sys.modules.*:RuntimeWarning")
def test_main_entrypoint_initializes_sentry_and_invokes_uvicorn_without_starting_server():
    with (
        patch("app.config.config.SENTRY_DSN", "https://sentry.example/1"),
        patch("sentry_sdk.init") as sentry_init,
        patch("uvicorn.run") as uvicorn_run,
    ):
        runpy.run_module("app.main", run_name="__main__")

    sentry_init.assert_called_once()
    uvicorn_run.assert_called_once()
