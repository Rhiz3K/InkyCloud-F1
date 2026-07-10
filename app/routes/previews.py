"""Preview images and lightweight static endpoints."""

from __future__ import annotations

import functools
import logging
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.config import LANGUAGE_CODES, config
from app.paths import ASSETS_DIR
from app.services.i18n import get_translator
from app.services.image_keys import get_configure_preview_filename, get_preview_filename
from app.services.renderers import create_renderer
from app.services.teams_service import TeamsService, get_default_teams_year
from app.utils.async_tasks import run_render
from app.utils.image_conversion import bmp_to_png
from app.utils.rate_limit import enforce_rate_limit

router = APIRouter()
_ALLOWED_LANGS = {lang: lang for lang in LANGUAGE_CODES}
logger = logging.getLogger(__name__)


def _build_teams_preview_png(lang: str, display: str, teams_data, full_size: bool) -> bytes:
    """Construct the renderer, render, and convert to PNG — all in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang)
    bmp_data = renderer.render_teams_drivers(teams_data)
    return bmp_to_png(
        bmp_data,
        width=400,
        full_size=full_size,
        preserve_color=display in {"spectra6", "bwr", "bwry"},
    )


async def _render_teams_preview(
    lang: str, display: str = "1bit", *, full_size: bool
) -> StreamingResponse:
    """Render a dynamic Teams PNG when no pregenerated preview is available."""
    season = get_default_teams_year()
    teams_service = TeamsService()
    teams_data = await teams_service.get_teams_and_drivers(season)
    if not teams_data.teams:
        raise RuntimeError(f"No teams data available for preview season {season}")

    png_data = await run_render(
        functools.partial(_build_teams_preview_png, lang, display, teams_data, full_size)
    )
    return StreamingResponse(
        BytesIO(png_data),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/preview/{screen_type}.png", response_model=None)
async def get_preview_png(
    screen_type: str, request: Request, lang: str = Query(default="en")
) -> Response:
    """Serve pre-generated preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}

    safe_screen = allowed_screens.get(screen_type)
    if not safe_screen:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    safe_lang = _ALLOWED_LANGS.get(lang, "en")

    filename = get_preview_filename(safe_screen, safe_lang)
    preview_path = Path(config.IMAGES_PATH) / filename
    if preview_path.exists():
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if safe_screen == "teams":
        enforce_rate_limit(
            request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE
        )
        try:
            return await _render_teams_preview(safe_lang, full_size=False)
        except Exception as exc:
            logger.warning("Falling back to dynamic teams homepage preview failed: %s", exc)

    raise HTTPException(status_code=404, detail="Preview not generated yet")


@router.get("/preview/configure/{screen_type}.png", response_model=None)
async def get_configure_preview_png(
    screen_type: str,
    request: Request,
    lang: str = Query(default="en"),
    weather_type: str = Query(default="off"),
    display: str = Query(default="1bit"),
) -> Response:
    """Serve pre-generated configure-preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}
    allowed_weather = {
        "off": "off",
        "current": "current",
        "race": "race",
        "race_day": "race",
    }
    allowed_display = {"1bit": "1bit", "spectra6": "spectra6", "bwr": "bwr", "bwry": "bwry"}

    safe_screen = allowed_screens.get(screen_type)
    if not safe_screen:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    safe_lang = _ALLOWED_LANGS.get(lang, "en")
    safe_weather = allowed_weather.get(weather_type, "off")
    safe_display = allowed_display.get(display, "1bit")

    filename = get_configure_preview_filename(
        safe_screen,
        safe_lang,
        display=safe_display,
        weather=safe_weather if safe_screen == "calendar" else "off",
    )
    configure_path = Path(config.IMAGES_PATH) / filename
    if configure_path.exists():
        return FileResponse(
            configure_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Fallback to default variant if specific one not found
    fallback_filename = get_configure_preview_filename(safe_screen, safe_lang)
    fallback_path = Path(config.IMAGES_PATH) / fallback_filename
    if fallback_path.exists():
        return FileResponse(
            fallback_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if safe_screen == "teams":
        enforce_rate_limit(
            request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE
        )
        try:
            return await _render_teams_preview(
                safe_lang,
                safe_display,
                full_size=True,
            )
        except Exception as exc:
            logger.warning("Falling back to dynamic teams configure preview failed: %s", exc)

    raise HTTPException(status_code=404, detail="Configure preview not generated yet")


@router.get("/preview")
async def preview_redirect() -> RedirectResponse:
    """Redirect /preview to / for backwards compatibility."""
    return RedirectResponse(url="/", status_code=301)


@router.get("/favicon.ico")
async def favicon() -> FileResponse:
    """Serve the real ICO favicon asset used by the site."""
    return FileResponse(
        ASSETS_DIR / "favicon" / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """Serve service worker script."""
    return FileResponse(
        ASSETS_DIR / "js" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
