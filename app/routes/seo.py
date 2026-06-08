"""SEO-related endpoints (robots, sitemap)."""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from app.config import LANGUAGE_CODES, config

router = APIRouter()


@dataclass(frozen=True)
class SitemapPage:
    """Static sitemap metadata for an indexable page family."""

    path: str
    priority: str
    changefreq: str


SITEMAP_PAGES: tuple[SitemapPage, ...] = (
    SitemapPage("/", "1.0", "daily"),
    SitemapPage("/configure/calendar", "0.9", "weekly"),
    SitemapPage("/configure/teams", "0.9", "weekly"),
    SitemapPage("/stats", "0.9", "weekly"),
    SitemapPage("/api/docs/html", "0.8", "monthly"),
    SitemapPage("/changelog", "0.7", "weekly"),
    SitemapPage("/privacy", "0.3", "yearly"),
)


def _localized_path(path: str, lang: str) -> str:
    """Return the canonical localized path for sitemap and hreflang entries."""
    if lang == "en":
        return path
    if path == "/":
        return f"/{lang}/"
    return f"/{lang}{path}"


def _absolute_url(site_url: str, path: str) -> str:
    """Join a normalized site URL with a root-relative path."""
    return f"{site_url}{path}"


def _xml_escape(value: str) -> str:
    """Escape XML text and attribute values used in sitemap output."""
    return escape(value, {'"': "&quot;"})


@router.api_route("/robots.txt", methods=["GET", "HEAD"])
async def robots_txt() -> PlainTextResponse:
    """Serve robots.txt for crawlers."""
    site_url = str(config.SITE_URL).rstrip("/")
    content = f"""User-agent: *
Allow: /

Sitemap: {site_url}/sitemap.xml
"""
    return PlainTextResponse(content, media_type="text/plain")


@router.api_route("/sitemap.xml", methods=["GET", "HEAD"])
@router.api_route("/sitemap.xml/", methods=["GET", "HEAD"])
async def sitemap_xml() -> Response:
    """Serve sitemap.xml with subdirectory language URLs."""
    site_url = str(config.SITE_URL).rstrip("/")

    urls: list[str] = []

    for page in SITEMAP_PAGES:
        for lang in LANGUAGE_CODES:
            path = _localized_path(page.path, lang)
            url_loc = _absolute_url(site_url, path)
            alternates = [
                (
                    alternate_lang,
                    _absolute_url(site_url, _localized_path(page.path, alternate_lang)),
                )
                for alternate_lang in LANGUAGE_CODES
            ]
            alternates.append(("x-default", _absolute_url(site_url, page.path)))
            alternate_links = "\n".join(
                (
                    '    <xhtml:link rel="alternate" '
                    f'hreflang="{_xml_escape(alternate_lang)}" '
                    f'href="{_xml_escape(alternate_url)}" />'
                )
                for alternate_lang, alternate_url in alternates
            )

            urls.append(
                f"""  <url>
    <loc>{_xml_escape(url_loc)}</loc>
{alternate_links}
    <changefreq>{page.changefreq}</changefreq>
    <priority>{page.priority}</priority>
  </url>"""
            )

    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
{chr(10).join(urls)}
</urlset>"""

    return Response(content=sitemap_content, media_type="application/xml")
