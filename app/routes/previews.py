"""Preview images and lightweight static endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from app.config import config

router = APIRouter()


@router.get("/preview/{screen_type}.png")
async def get_preview_png(screen_type: str, lang: str = Query(default="en")) -> FileResponse:
    """Serve pre-generated preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}
    allowed_langs = {"en": "en", "cs": "cs"}

    safe_screen = allowed_screens.get(screen_type)
    if not safe_screen:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    safe_lang = allowed_langs.get(lang, "en")

    filename = f"preview_{safe_screen}_{safe_lang}.png"
    preview_path = Path(config.IMAGES_PATH) / filename
    if preview_path.exists():
        return FileResponse(
            preview_path,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    raise HTTPException(status_code=404, detail="Preview not generated yet")


@router.get("/preview/configure/{screen_type}.png")
async def get_configure_preview_png(
    screen_type: str,
    lang: str = Query(default="en"),
    weather_type: str = Query(default="off"),
    display: str = Query(default="1bit"),
) -> FileResponse:
    """Serve pre-generated configure-preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}
    allowed_langs = {"en": "en", "cs": "cs"}
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

    safe_lang = allowed_langs.get(lang, "en")
    safe_weather = allowed_weather.get(weather_type, "off")
    safe_display = allowed_display.get(display, "1bit")

    # Build filename matching scheduler's format
    if safe_screen == "calendar":
        filename = f"configure_calendar_{safe_lang}"
        if safe_display == "spectra6":
            filename += "_spectra6"
        elif safe_display == "bwr":
            filename += "_bwr"
        elif safe_display == "bwry":
            filename += "_bwry"
        if safe_weather != "off":
            filename += f"_weather_{safe_weather}"
        filename += ".png"
    else:
        filename = f"configure_{safe_screen}_{safe_lang}.png"

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
