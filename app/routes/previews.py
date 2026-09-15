"""Preview images and lightweight static endpoints."""

from __future__ import annotations

import asyncio
import functools
import logging
from email.utils import formatdate
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.config import LANGUAGE_CODES, config
from app.paths import ASSETS_DIR
from app.services.artifact_metadata import calendar_identity, read_artifact_metadata
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_keys import get_configure_preview_filename, get_preview_filename
from app.services.renderers import COLOR_DISPLAYS, create_renderer
from app.services.teams_service import TeamsService, get_default_teams_year
from app.services.track_catalog import (
    DEFAULT_TRACK_OPTIONS,
    TrackAccent,
    TrackOptions,
    TrackSource,
    TrackStyle,
)
from app.services.track_renderer import attribution_headers
from app.state import get_bmp_cache
from app.utils.async_tasks import run_render
from app.utils.etag import if_none_match_matches, strong_etag
from app.utils.image_conversion import bmp_to_png
from app.utils.rate_limit import enforce_rate_limit

router = APIRouter()
_ALLOWED_LANGS = {lang: lang for lang in LANGUAGE_CODES}
logger = logging.getLogger(__name__)
_PREVIEW_CACHE_CONTROL = "public, max-age=300"


def _preview_snapshot(path: Path, identity: str) -> tuple[bytes, dict] | None:
    """Read a content-verified preview without mixing concurrent generations."""
    metadata = read_artifact_metadata(path)
    if not identity or metadata is None or metadata["identity"] != identity:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return None
    if strong_etag(content) != metadata["etag"] or read_artifact_metadata(path) != metadata:
        return None
    return content, metadata


async def _cached_preview_response(path: Path, request: Request, identity: str) -> Response | None:
    """Serve a fresh PNG snapshot or let the caller render/fall back to the BMP."""
    snapshot = await asyncio.to_thread(_preview_snapshot, path, identity)
    if snapshot is None:
        return None
    content, metadata = snapshot
    not_modified = if_none_match_matches(request.headers.get("If-None-Match"), metadata["etag"])
    return Response(
        content=b"" if not_modified else content,
        status_code=304 if not_modified else 200,
        media_type=None if not_modified else "image/png",
        headers={
            "Cache-Control": _PREVIEW_CACHE_CONTROL,
            "ETag": metadata["etag"],
            "Last-Modified": formatdate(metadata["mtime_ns"] / 1e9, usegmt=True),
        },
    )


def _preview_identity(screen: str) -> str:
    """Resolve the current race or season represented by automatic previews."""
    if screen == "calendar":
        return calendar_identity(F1Service().get_next_race_from_static())
    return f"teams:{get_default_teams_year()}"


def _build_calendar_preview_png(
    lang: str,
    display: str,
    race_data: dict,
    historical_data,
    full_size: bool,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
) -> bytes:
    """Render the next-race calendar and convert it to a browser preview PNG."""
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang)
    bmp_data = renderer.render_calendar(
        race_data, historical_data, None, "off", track_options=track_options
    )
    return bmp_to_png(
        bmp_data,
        width=400,
        full_size=full_size,
        preserve_color=display in COLOR_DISPLAYS,
    )


async def _render_calendar_preview(
    lang: str,
    display: str = "1bit",
    *,
    full_size: bool,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
) -> StreamingResponse:
    """Render the next race when startup has not produced a calendar preview yet."""
    f1_service = F1Service()
    race_data = f1_service.get_next_race_from_static()
    if not race_data:
        raise RuntimeError("No race data available for calendar preview")

    circuit_id = race_data.get("circuit", {}).get("circuitId", "")
    historical_data = F1Service.get_historical_from_static(circuit_id)
    png_data = await run_render(
        functools.partial(
            _build_calendar_preview_png,
            lang,
            display,
            race_data,
            historical_data,
            full_size,
            track_options,
        )
    )
    return StreamingResponse(
        BytesIO(png_data),
        media_type="image/png",
        headers={
            "Cache-Control": _PREVIEW_CACHE_CONTROL,
            **attribution_headers(circuit_id, track_options),
        },
    )


async def _render_configure_calendar(
    request: Request, lang: str, display: str, weather: str, options: TrackOptions
) -> Response:
    """Share calendar BMP caching and singleflight, then encode the matching PNG preview."""
    from app.routes.images import _get_cache_key, _render_calendar_artifact, _singleflight

    service = F1Service()
    race = service.get_next_race_from_static()
    weather_type = "race_day" if weather == "race" else weather
    key = (
        calendar_identity(race, options)
        + "|"
        + _get_cache_key(
            lang,
            None,
            None,
            None,
            None,
            weather != "off",
            weather_type,
            display,
        )
    )
    artifact = get_bmp_cache().get(key)
    if artifact is None:
        artifact = await _singleflight(
            f"calendar:{key}",
            functools.partial(
                _render_calendar_artifact,
                cache_key=key,
                f1_service=service,
                lang=lang,
                year=None,
                race_round=None,
                race_key=None,
                target_tz=config.DEFAULT_TIMEZONE,
                weather=weather != "off",
                weather_type=weather_type,
                display=display,
                selected_race=race,
                track_options=options,
            ),
        )
    content = await run_render(
        functools.partial(
            bmp_to_png, artifact[0], full_size=True, preserve_color=display in COLOR_DISPLAYS
        )
    )
    etag = strong_etag(content)
    unchanged = if_none_match_matches(request.headers.get("If-None-Match"), etag)
    return Response(
        content=b"" if unchanged else content,
        status_code=304 if unchanged else 200,
        media_type=None if unchanged else "image/png",
        headers={
            "ETag": etag,
            "Cache-Control": _PREVIEW_CACHE_CONTROL if key in get_bmp_cache() else "no-store",
            **attribution_headers((race or {}).get("circuit", {}).get("circuitId", ""), options),
        },
    )


def _build_teams_preview_png(lang: str, display: str, teams_data, full_size: bool) -> bytes:
    """Construct the renderer, render, and convert to PNG — all in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang)
    bmp_data = renderer.render_teams_drivers(teams_data)
    return bmp_to_png(
        bmp_data,
        width=400,
        full_size=full_size,
        preserve_color=display in COLOR_DISPLAYS,
    )


async def _render_teams_preview(
    lang: str, display: str = "1bit", *, full_size: bool
) -> StreamingResponse:
    """Render a dynamic Teams PNG when no pregenerated preview is available."""
    season = get_default_teams_year()
    teams_service = TeamsService()
    async with asyncio.timeout(config.TEAMS_ENRICHMENT_TIMEOUT_SECONDS):
        teams_data = await teams_service.get_teams_and_drivers(season)
    if not teams_data.teams:
        raise RuntimeError(f"No teams data available for preview season {season}")

    png_data = await run_render(
        functools.partial(_build_teams_preview_png, lang, display, teams_data, full_size)
    )
    return StreamingResponse(
        BytesIO(png_data),
        media_type="image/png",
        headers={"Cache-Control": _PREVIEW_CACHE_CONTROL},
    )


async def _render_dynamic_preview(
    screen_type: str, lang: str, display: str = "1bit", *, full_size: bool
) -> StreamingResponse:
    """Render a calendar or Teams preview while pregeneration is still in progress."""
    if screen_type == "calendar":
        return await _render_calendar_preview(lang, display, full_size=full_size)
    return await _render_teams_preview(lang, display, full_size=full_size)


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
    cached = await _cached_preview_response(preview_path, request, _preview_identity(safe_screen))
    if cached is not None:
        return cached

    enforce_rate_limit(request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    try:
        return await _render_dynamic_preview(safe_screen, safe_lang, full_size=False)
    except Exception as exc:
        logger.warning("Dynamic %s homepage preview failed: %s", safe_screen, exc)

    raise HTTPException(status_code=404, detail="Preview not generated yet")


@router.get("/preview/configure/{screen_type}.png", response_model=None)
async def get_configure_preview_png(
    screen_type: str,
    request: Request,
    lang: str = Query(default="en"),
    weather_type: str = Query(default="off"),
    display: str = Query(default="1bit"),
    track_style: TrackStyle = TrackStyle.RELIEF,
    track_source: TrackSource = TrackSource.JULES,
    track_accent: TrackAccent = TrackAccent.ALL,
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
    options = TrackOptions(track_style, track_source, track_accent)
    cached = (
        await _cached_preview_response(configure_path, request, _preview_identity(safe_screen))
        if options == DEFAULT_TRACK_OPTIONS or safe_screen == "teams"
        else None
    )
    if cached is not None:
        if safe_screen == "calendar":
            race = F1Service().get_next_race_from_static() or {}
            cached.headers.update(
                attribution_headers(race.get("circuit", {}).get("circuitId", ""), options)
            )
        return cached

    if safe_screen == "calendar":
        enforce_rate_limit(
            request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE
        )
        try:
            return await _render_configure_calendar(
                request, safe_lang, safe_display, safe_weather, options
            )
        except Exception as exc:
            logger.warning("Dynamic calendar configure preview failed: %s", exc)

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
            logger.warning("Dynamic teams configure preview failed: %s", exc)

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
