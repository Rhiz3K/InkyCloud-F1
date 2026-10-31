"""Verify nonce policies, local documentation assets and cache boundaries."""

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.config import config
from app.main import app
from app.services.csp import REDOC_STYLE_HASHES, build_csp

client = TestClient(app)


def _nonce(policy: str) -> str:
    """Extract the request nonce from a response policy."""
    match = re.search(r"'nonce-([A-Za-z0-9_-]{22,})'", policy)
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/configure/calendar",
        "/configure/teams",
        "/stats",
        "/credits",
        "/changelog",
        "/api/docs/html",
        "/privacy",
        "/missing-csp-test-page",
    ],
)
def test_page_markup_uses_nonce_csp(path):
    """Keep rendered pages free of inline handlers/styles and nonce inline blocks."""
    response = client.get(path)
    assert response.status_code == (404 if path == "/missing-csp-test-page" else 200)
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers.get("content-security-policy") or response.headers[
        "content-security-policy-report-only"
    ]
    nonce = _nonce(policy)
    markup = response.text
    # header.html is intentionally untouched in this PR and blocks CSP enforce mode.
    # Exclude only its two known handlers; any other inline handler must fail.
    for handler in (' onclick="toggleMobileMenu()"', ' onchange="switchUiLanguage()"'):
        markup = markup.replace(handler, "", 1)
    assert not re.search(r"\son[a-z]+\s*=", markup)
    assert 'style="' not in markup
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        if tag.name == "style" or not tag.has_attr("src"):
            assert tag.get("nonce") == nonce


@pytest.mark.parametrize("enforce", [False, True])
def test_html_policy(monkeypatch, enforce):
    """Apply a fresh nonce and the configured policy mode to each HTML response."""
    monkeypatch.setattr(config, "CSP_ENFORCE", enforce)
    header = "content-security-policy" if enforce else "content-security-policy-report-only"
    other = "content-security-policy-report-only" if enforce else "content-security-policy"
    first = client.get("/privacy")
    second = client.get("/privacy")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"
    assert other not in first.headers
    policy = first.headers[header]
    nonce = _nonce(policy)
    assert nonce != _nonce(second.headers[header])
    assert policy == (
        f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_cache_and_non_html_boundaries():
    """Preserve explicit no-store and static caching without adding CSP to JSON or CSS."""
    missing = client.get("/missing-csp-test-page")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert "content-security-policy-report-only" in missing.headers
    for path in ("/health", "/static/css/styles.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert not any(name.startswith("content-security-policy") for name in response.headers)
        if path.startswith("/static"):
            assert response.headers["cache-control"] == "public, max-age=3600"
            assert response.headers["etag"]
    response = client.get("https://testserver/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


@pytest.mark.parametrize("path", ["/docs", "/docs/oauth2-redirect", "/redoc"])
@pytest.mark.parametrize("enforce", [False, True])
def test_local_docs(monkeypatch, path, enforce):
    """Always enforce matching nonces and serve every referenced asset locally."""
    monkeypatch.setattr(config, "CSP_ENFORCE", enforce)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert "content-security-policy-report-only" not in response.headers
    policy = response.headers["content-security-policy"]
    nonce = _nonce(policy)
    assert set(re.findall(r'nonce="([^"]+)"', response.text)) == {nonce}
    assert not re.search(r'(?:src|href)=["\'](?!/)[^"\']+', response.text)
    assert not re.search(r"https?://", response.text)
    for forbidden in ("cdn.jsdelivr", "fonts.googleapis", "fonts.gstatic", "tiangolo"):
        assert forbidden not in response.text
    register = json.loads(Path("app/assets/asset-register.json").read_text())["assets"]
    urls = re.findall(r'(?:src|href)="([^"]+)"', response.text)
    assert urls
    for url in urls:
        assert url.removeprefix("/static/") in register
        assert client.get(url).status_code == 200
    if path == "/redoc":
        assert REDOC_STYLE_HASHES in policy
        assert "worker-src 'self' blob:" in policy
        assert f'"nonce": "{nonce}"' in response.text
        assert "ui-monospace" in response.text
        assert "system-ui" in response.text
    else:
        assert "sha256-" not in policy
        assert "worker-src" not in policy


@pytest.mark.parametrize("path", ["/docs", "/docs/oauth2-redirect", "/redoc"])
@pytest.mark.parametrize("enforce", [False, True])
def test_local_docs_head(monkeypatch, path, enforce):
    """Preserve HEAD support with enforced CSP and uncached documentation headers."""
    monkeypatch.setattr(config, "CSP_ENFORCE", enforce)
    response = client.head(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "content-security-policy-report-only" not in response.headers
    assert _nonce(response.headers["content-security-policy"])


@pytest.mark.parametrize("prefix", ["", "/prefix"])
def test_docs_root_path_and_openapi_url(prefix):
    """Honor the mounting prefix and configured OpenAPI location in docs HTML."""
    original = app.openapi_url
    try:
        app.openapi_url = "/schema.json"
        mounted = TestClient(app, root_path="/prefix")
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response = mounted.get(prefix + path)
            assert "content-security-policy" in response.headers
            if path == "/redoc":
                assert "worker-src" in response.headers["content-security-policy"]
            assert response.status_code == 200
            assert '"/prefix/static/' in response.text
            if path != "/docs/oauth2-redirect":
                assert '"/prefix/schema.json"' in response.text
    finally:
        app.openapi_url = original


def test_policy_builder_redoc_exceptions():
    """Limit ReDoc exceptions to the two reviewed style hashes and blob worker."""
    site = build_csp("test")
    redoc = build_csp("test", redoc=True)
    assert (
        redoc
        == site.replace(
            "style-src 'self' 'nonce-test'",
            f"style-src 'self' 'nonce-test' {REDOC_STYLE_HASHES}",
        )
        + "; worker-src 'self' blob:"
    )
