"""Preview images and lightweight static endpoints."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from PIL import Image

from app.config import LANGUAGE_CODES, config
from app.services.bwr_renderer import BwrRenderer
from app.services.bwry_renderer import BwryRenderer
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.teams_service import TeamsService, get_default_teams_year

router = APIRouter()
_ALLOWED_LANGS = {lang: lang for lang in LANGUAGE_CODES}
logger = logging.getLogger(__name__)


def _bmp_to_png(
    bmp_data: bytes, width: int = 400, full_size: bool = False, preserve_color: bool = False
) -> bytes:
    """Convert a rendered BMP into a PNG preview."""
    img_file = Image.open(BytesIO(bmp_data))
    img = img_file.convert("RGB" if preserve_color else "L")

    if not full_size:
        ratio = width / img.width
        img = img.resize((width, int(img.height * ratio)), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _get_teams_renderer(display: str, translator: dict, lang: str):
    if display == "spectra6":
        return Spectra6Renderer(translator, lang)
    if display == "bwr":
        return BwrRenderer(translator, lang)
    if display == "bwry":
        return BwryRenderer(translator, lang)
    return Renderer(translator, lang)


async def _render_teams_preview(
    lang: str, display: str = "1bit", *, full_size: bool
) -> StreamingResponse:
    translator = get_translator(lang)
    season = get_default_teams_year()
    teams_service = TeamsService()
    teams_data = await teams_service.get_teams_and_drivers(season)
    if not teams_data.teams:
        raise RuntimeError(f"No teams data available for preview season {season}")

    renderer = _get_teams_renderer(display, translator, lang)
    bmp_data = renderer.render_teams_drivers(teams_data)
    png_data = _bmp_to_png(
        bmp_data,
        width=400,
        full_size=full_size,
        preserve_color=display in {"spectra6", "bwr", "bwry"},
    )
    return StreamingResponse(
        BytesIO(png_data),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/preview/{screen_type}.png", response_model=None)
async def get_preview_png(screen_type: str, lang: str = Query(default="en")) -> Response:
    """Serve pre-generated preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}

    safe_screen = allowed_screens.get(screen_type)
    if not safe_screen:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    safe_lang = _ALLOWED_LANGS.get(lang, "en")

    if safe_screen == "teams":
        try:
            return await _render_teams_preview(safe_lang, full_size=False)
        except Exception as exc:
            logger.warning("Falling back to static teams homepage preview: %s", exc)

    filename = f"preview_{safe_screen}_{safe_lang}.png"
    preview_path = Path(config.IMAGES_PATH) / filename
    if preview_path.exists():
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    raise HTTPException(status_code=404, detail="Preview not generated yet")


@router.get("/preview/configure/{screen_type}.png", response_model=None)
async def get_configure_preview_png(
    screen_type: str,
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

    if safe_screen == "teams":
        try:
            return await _render_teams_preview(
                safe_lang,
                safe_display,
                full_size=True,
            )
        except Exception as exc:
            logger.warning("Falling back to static teams configure preview: %s", exc)

    # Build filename matching scheduler's format
    if safe_screen == "calendar":
        filename = f"configure_calendar_{safe_lang}"
        if safe_display != "1bit":
            filename += f"_{safe_display}"
        if safe_weather != "off":
            filename += f"_weather_{safe_weather}"
        filename += ".png"
    else:
        filename = f"configure_{safe_screen}_{safe_lang}"
        if safe_display != "1bit":
            filename += f"_{safe_display}"
        filename += ".png"

    configure_path = Path(config.IMAGES_PATH) / filename
    if configure_path.exists():
        return FileResponse(
            configure_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Fallback to default variant if specific one not found
    fallback_filename = f"configure_{safe_screen}_{safe_lang}.png"
    fallback_path = Path(config.IMAGES_PATH) / fallback_filename
    if fallback_path.exists():
        return FileResponse(
            fallback_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    raise HTTPException(status_code=404, detail="Configure preview not generated yet")


@router.get("/preview")
async def preview_redirect() -> RedirectResponse:
    """Redirect /preview to / for backwards compatibility."""
    return RedirectResponse(url="/", status_code=301)


@router.get("/favicon.ico")
async def favicon() -> StreamingResponse:
    """Serve favicon as SVG with F1 car emoji."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<text y=".9em" font-size="90">🏎️</text>
</svg>"""
    return StreamingResponse(
        iter([svg.encode()]),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """Serve service worker script."""
    return FileResponse(
        Path("app/assets/js/sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
