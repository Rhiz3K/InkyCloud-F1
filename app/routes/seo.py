"""SEO-related endpoints (robots, sitemap)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from app.config import config

router = APIRouter()


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
async def sitemap_xml() -> Response:
    """Serve sitemap.xml with subdirectory language URLs."""
    site_url = str(config.SITE_URL).rstrip("/")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pages = [
        {"loc": "/", "priority": "1.0", "changefreq": "daily"},
        {"loc": "/configure/calendar", "priority": "0.9", "changefreq": "daily"},
        {"loc": "/configure/teams", "priority": "0.9", "changefreq": "daily"},
        {"loc": "/api/docs/html", "priority": "0.8", "changefreq": "monthly"},
        {"loc": "/changelog", "priority": "0.7", "changefreq": "weekly"},
        {"loc": "/stats", "priority": "0.6", "changefreq": "hourly"},
        {"loc": "/privacy", "priority": "0.3", "changefreq": "yearly"},
    ]

    languages = ["en", "cs"]
    urls: list[str] = []

    for page in pages:
        for lang in languages:
            # English uses root path, others use /{lang}/ prefix
            if lang == "en":
                url_loc = f"{site_url}{page['loc']}"
            else:
                url_loc = f"{site_url}/{lang}{page['loc']}"

            urls.append(
                f"""  <url>
    <loc>{url_loc}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>{page["changefreq"]}</changefreq>
    <priority>{page["priority"]}</priority>
  </url>"""
            )

    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    return Response(content=sitemap_content, media_type="application/xml")
