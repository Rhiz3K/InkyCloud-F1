"""Accessible, durable per-file artwork attribution pages."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import VALID_LANGUAGES
from app.services.track_catalog import TrackSource, load_track_catalog, track_credit
from app.web.templates import get_template_context, lang_url, templates

router = APIRouter(include_in_schema=False)
_CREDITS_PATHS = {language: lang_url("/credits", language) for language in VALID_LANGUAGES}


@router.api_route("/credits", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/{lang_prefix}/credits", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def credits_page(request: Request, lang_prefix: str | None = None) -> Response:
    """Keep complete source and adaptation notices available without hover or JavaScript."""
    if lang_prefix is not None and lang_prefix not in VALID_LANGUAGES:
        raise HTTPException(status_code=404, detail="Not found")
    if lang_prefix == "en":
        return RedirectResponse("/credits", status_code=301)
    if request.method == "HEAD":
        return Response(media_type="text/html")
    context = get_template_context(request, lang_prefix or "en")
    context["track_credits"] = [
        {
            **track,
            "variants": [
                {"id": source, "credit": track_credit(track["id"], source)}
                for source in TrackSource
            ],
        }
        for track in load_track_catalog()["tracks"]
    ]
    context["active_page"] = "credits"
    return templates.TemplateResponse(request, "credits.html", context)


@router.api_route("/credits/", methods=["GET", "HEAD"])
@router.api_route("/{lang_prefix}/credits/", methods=["GET", "HEAD"])
async def credits_slash(lang_prefix: str | None = None) -> RedirectResponse:
    """Normalise the canonical attribution URL, including translated routes."""
    target = _CREDITS_PATHS.get("en" if lang_prefix is None else lang_prefix)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")
    return RedirectResponse(target, status_code=301)
