"""Test main FastAPI application endpoints."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint_returns_html():
    """Test root endpoint returns HTML preview page."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/static/images/f1_homepage_logo.png" in response.text


def test_root_page_contains_tailwind():
    """Test root page uses Tailwind CSS."""
    response = client.get("/")
    assert "tailwindcss" in response.text


def test_root_page_contains_required_elements():
    """Test root page contains all required UI elements."""
    response = client.get("/")
    html = response.text

    # Check for language switcher in header (lang select removed from sidebar)
    assert 'id="uiLangSwitch"' in html
    assert 'value="en"' in html
    assert 'value="cs"' in html

    # Check for URL display
    assert 'id="apiUrl"' in html

    # Check for preview section
    assert 'id="previewImage"' in html

    # Check for API documentation
    assert "API" in html


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


def test_root_page_contains_api_references():
    """Test root page includes relative API endpoint references."""
    response = client.get("/")
    html = response.text

    # Should contain relative API endpoint references (no absolute BASE_URL)
    assert "/calendar.bmp" in html
    assert "/api/races/" in html
    # Ensure we're using relative URLs, not absolute BASE_URL
    assert "BASE_URL" not in html


def test_root_page_i18n_default_english():
    """Test root page defaults to English for non-CZ/SK users."""
    response = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    html = response.text
    assert "currentUiLang = 'en'" in html


def test_root_page_i18n_czech_for_cz():
    """Test root page uses Czech for CZ users."""
    response = client.get("/", headers={"Accept-Language": "cs-CZ,cs;q=0.9"})
    html = response.text
    assert "currentUiLang = 'cs'" in html


def test_root_page_i18n_czech_for_sk():
    """Test root page uses Czech for SK users."""
    response = client.get("/", headers={"Accept-Language": "sk-SK,sk;q=0.9"})
    html = response.text
    assert "currentUiLang = 'cs'" in html


def test_root_page_lang_parameter():
    """Test root page respects ?lang= query parameter."""
    response = client.get("/?lang=cs")
    html = response.text
    assert "currentUiLang = 'cs'" in html

    response = client.get("/?lang=en")
    html = response.text
    assert "currentUiLang = 'en'" in html


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
    # Key credit links
    assert "FoxeeLab" in html
    assert "coolify.io" in html
    assert "hetzner.com" in html
    assert "laskakit.cz" in html
    assert "jolpica" in html


def test_mobile_sidebar_contains_links():
    """Test mobile sidebar contains Links and Credits sections."""
    response = client.get("/")
    html = response.text
    # Sidebar exists
    assert 'id="settingsSidebar"' in html
    # Links section with key items (check URLs which are stable across translations)
    assert "GitHub" in html
    assert "/api/docs/html" in html
    assert "/privacy" in html


def test_privacy_page_header_nav():
    """Test privacy page has navigation in header."""
    response = client.get("/privacy")
    html = response.text
    assert 'href="/?lang=' in html
    assert "/api/docs/html" in html


def test_api_docs_header_nav():
    """Test API docs page has navigation in header."""
    response = client.get("/api/docs/html")
    html = response.text
    assert 'href="/?lang=' in html
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
    """Test privacy page detects Czech language from header."""
    response = client.get("/privacy", headers={"Accept-Language": "cs-CZ,cs;q=0.9"})
    html = response.text
    # Check HTML lang attribute is set to Czech
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
    """Test API docs HTML page detects Czech language from header."""
    response = client.get("/api/docs/html", headers={"Accept-Language": "cs-CZ,cs;q=0.9"})
    html = response.text
    assert "currentUiLang = 'cs'" in html


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
    # Should have link to stats page (with lang parameter)
    assert 'href="/stats?lang=' in html
    # Should NOT have inline stats display elements (moved to /stats page)
    assert 'id="statsLast24h"' not in html
    assert 'id="statsDataTransfer"' not in html
