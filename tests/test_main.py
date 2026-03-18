"""Test main FastAPI application endpoints."""

import re
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ConstructorStanding, DriverStanding, StandingsData
from app.routes.images import _get_race_data_from_static, _get_race_info_for_stats
from app.services.f1_service import F1Service

client = TestClient(app)

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
    assert 'value="en"' in html
    assert 'value="cs"' in html
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
        "app.routes.pages.Database.get_stats_for_range", new_callable=AsyncMock
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
    # Test /api/docs alias works the same
    response_docs = client.get("/api/docs")
    assert response_docs.status_code == 200
    assert response_docs.json()["service"] == "F1 E-Ink Calendar API"


def test_health_endpoint():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


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


def test_header_contains_credits_dropdown():
    """Test header contains Credits dropdown with key links."""
    response = client.get("/")
    html = response.text
    # Credits section
    assert "Credits" in html
    # Key credit links - use href pattern to avoid CodeQL false positive
    assert "FoxeeLab" in html
    assert 'href="https://coolify.io"' in html
    assert 'href="https://hetzner.com"' in html
    # LaskaKit link has full product URL
    assert 'href="https://www.laskakit.cz/' in html
    assert "jolpica" in html
    assert 'href="https://open-meteo.com"' in html


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
            "app.routes.pages.Database.get_stats_for_range", new=AsyncMock(return_value=mock_stats)
        ),
        patch(
            "app.routes.pages.Database.get_perf_stats",
            new=AsyncMock(return_value={"sample_count": 0}),
        ),
        patch("app.routes.pages.Database.get_perf_stats_by_page", new=AsyncMock(return_value=[])),
        patch("app.routes.pages.Database.get_perf_trends", new=AsyncMock(return_value=[])),
        patch("app.routes.pages.track_pageview", new=AsyncMock()),
    ):
        response = client.get("/stats")

    assert response.status_code == 200
    html = response.text

    language_section = re.search(r"By Language.*?By Display", html, re.S)
    assert language_section is not None
    assert "bg-racing-red" in language_section.group(0)
    assert "bg-white" in language_section.group(0)

    assert "stats-card-display" in html

    display_section = re.search(r"stats-card-display.*?Races - Full Width", html, re.S)
    assert display_section is not None
    assert "bg-racing-red" in display_section.group(0)
    assert "bg-black" in display_section.group(0)
    assert "bg-white" in display_section.group(0)
    assert "background-color:" not in display_section.group(0)


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

    # Values should be integers/floats or None
    assert isinstance(requests["last_24h"], int)
    assert isinstance(requests["total_bytes_24h"], int)
    assert requests["avg_response_ms"] is None or isinstance(
        requests["avg_response_ms"], (int, float)
    )


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


def test_configure_teams_no_timezone_selector():
    """Test configure teams page hides timezone selector."""
    response = client.get("/configure/teams")
    html = response.text
    assert 'id="mobileTzContainer"' in html
    assert 'id="mobileRaceContainer"' in html


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
    assert 'settingsBtn: "Settings"' in html
    assert 'yearLabel: "Season"' in html
    assert 'loadingText: "Loading..."' in html


def test_configure_page_translations_czech():
    """Test configure page Czech translations."""
    response = client.get("/configure/calendar?lang=cs")
    html = response.text
    assert 'settingsBtn: "Nastavení"' in html
    assert 'yearLabel: "Sezóna"' in html
    assert 'loadingText: "Načítání..."' in html


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


def test_teams_bmp_default():
    """Test /teams.bmp returns BMP image."""
    response = client.get("/teams.bmp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"


def test_teams_bmp_with_year():
    """Test /teams.bmp with year parameter."""
    for year in [2024, 2025]:
        response = client.get(f"/teams.bmp?year={year}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/bmp"


def test_teams_bmp_with_lang():
    """Test /teams.bmp with language parameter."""
    response = client.get("/teams.bmp?lang=cs")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"


# ============================================================================
# API Endpoint Tests
# ============================================================================


def test_api_races_endpoint():
    """Test /api/races/{year} returns race data."""
    response = client.get("/api/races/2025")
    assert response.status_code == 200
    data = response.json()
    assert "races" in data or "year" in data or isinstance(data, list)


def test_api_races_invalid_year():
    """Test /api/races with invalid year."""
    response = client.get("/api/races/1900")
    assert response.status_code in [200, 404]


# ============================================================================
# Changelog Page Tests
# ============================================================================


def test_changelog_returns_html():
    """Test /changelog endpoint returns HTML page."""
    response = client.get("/changelog")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


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

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
