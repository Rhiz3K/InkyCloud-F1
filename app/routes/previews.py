"""Preview images and lightweight static endpoints."""

from __future__ import annotations

import asyncio
import functools
import logging
from datetime import UTC, datetime
from email.utils import formatdate
from io import BytesIO
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.config import config
from app.paths import ASSETS_DIR
from app.routes.params import (
    calendar_cache_key,
    canonical_weather_type,
    normalize_display,
    normalize_lang,
    temp_unit_cache_suffix,
    validate_timezone,
    weather_file_key,
)
from app.services import bmp_artifacts
from app.services.artifact_metadata import calendar_identity, read_artifact_metadata
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_generation import (
    StaleSelection,
    require_current_selection,
    retrying_selection,
    selected_generation,
    with_current_selection,
)
from app.services.image_keys import (
    get_configure_preview_filename,
    get_preview_filename,
    get_teams_image_key,
)
from app.services.renderer_theme import ThemeMode
from app.services.renderers import COLOR_DISPLAYS, create_renderer
from app.services.teams_extra import TeamsExtra, TeamsExtraContext, build_teams_extra_context
from app.services.teams_service import TeamsService, get_default_teams_year, is_teams_data_cacheable
from app.services.track_catalog import (
    DEFAULT_TRACK_OPTIONS,
    TrackAccent,
    TrackOptions,
    TrackSource,
    TrackStyle,
)
from app.services.track_renderer import attribution_headers
from app.services.weather_service import TempUnit
from app.services.weekend_phase import WeekendMode, select_weekend_race
from app.services.weekend_results import weekend_context
from app.state import bmp_cache_generation, get_bmp_variant_cache
from app.utils.async_tasks import run_render
from app.utils.etag import if_none_match_matches, strong_etag
from app.utils.image_conversion import bmp_to_png
from app.utils.rate_limit import enforce_rate_limit
from app.utils.refresh_hint import CountdownMode, calendar_refresh_hint
from app.utils.render_limit import render_slot

router = APIRouter()
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


async def _configure_preview_snapshot(
    path: Path, request: Request, screen: str, weather: str, countdown: CountdownMode
) -> Response | None:
    """Match a pregenerated weekend preview to its source period and upcoming session."""
    if screen != "calendar" or weather != "weekend":
        return await _cached_preview_response(path, request, _preview_identity(screen))
    race = F1Service().get_next_race_from_static()
    now = datetime.now(UTC)
    identity = calendar_identity(race, weather_type=weather, now=now)
    hint = calendar_refresh_hint(
        race,
        now=now,
        timezone_name=None,
        countdown=countdown,
        weather_type=weather,
        weather_enabled=config.WEATHER_ENABLED,
        selection_grace=None,
    )
    cached = await _cached_preview_response(path, request, identity)
    if cached is not None:
        cached.headers["Cache-Control"] = f"public, max-age={hint.max_age(datetime.now(UTC), 300)}"
    return cached


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
    async with render_slot():
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
    request: Request,
    lang: str,
    display: str,
    weather: str,
    options: TrackOptions,
    countdown: CountdownMode = CountdownMode.HOURS,
    weekend_mode: WeekendMode = WeekendMode.OFF,
    temp_unit: TempUnit = TempUnit.C,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> Response:
    """Share calendar BMP caching and singleflight, then encode the matching PNG preview.

    ``weather`` is canonical (off/current/race_day/weekend), so the key equals the
    /calendar.bmp one.
    """
    race = select_weekend_race(F1Service(), weekend_mode)
    now = datetime.now(UTC)
    key = (
        calendar_identity(race, options, weather_type=weather, now=now)
        + "|"
        + calendar_cache_key(lang, None, None, None, None, weather, display)
    )
    weekend = weekend_context(weekend_mode, race, now=now, timezone_name=None, countdown=countdown)
    hint = None
    if countdown != CountdownMode.HOURS or weekend.mode != WeekendMode.OFF or weather == "weekend":
        hint = calendar_refresh_hint(
            race,
            now=now,
            timezone_name=None,
            countdown=countdown,
            weather_type=weather,
            weather_enabled=config.WEATHER_ENABLED,
            selection_grace=weekend_mode.selection_grace,
        ).with_changes(
            weekend.changes, hourly=weekend.pending, period_keyed=weekend.mode == WeekendMode.AUTO
        )
        if countdown != CountdownMode.HOURS:
            key += f":countdown={countdown}:{hint.cache_token()}"
        key += weekend.cache_suffix(hint)
    key += temp_unit_cache_suffix(temp_unit) + bmp_artifacts.theme_cache_suffix(theme)
    image_cache = bmp_artifacts.calendar_image_cache(
        countdown, weekend.mode, temp_unit, weather, theme
    )
    artifact = image_cache.get(key)
    if artifact is None:
        artifact = await bmp_artifacts.singleflight(
            f"calendar:{selected_generation()}:{key}",
            functools.partial(
                bmp_artifacts.render_calendar_artifact,
                cache_key=key,
                race_data=race,
                lang=lang,
                target_tz=config.DEFAULT_TIMEZONE,
                weather_type=weather,
                display=display,
                track_options=options,
                countdown_mode=countdown,
                weekend_mode=weekend.mode,
                weekend=weekend.phase,
                temp_unit=temp_unit,
                **bmp_artifacts.theme_kwargs(theme),
            ),
        )
    async with render_slot(variant=theme != ThemeMode.LIGHT):
        content = await run_render(
            functools.partial(
                bmp_to_png, artifact[0], full_size=True, preserve_color=display in COLOR_DISPLAYS
            )
        )
    cache_control = _PREVIEW_CACHE_CONTROL
    if weather == "weekend" and hint is not None:
        cache_control = f"public, max-age={hint.max_age(datetime.now(UTC), 300)}"
    etag = strong_etag(content)
    unchanged = if_none_match_matches(request.headers.get("If-None-Match"), etag)
    return Response(
        content=b"" if unchanged else content,
        status_code=304 if unchanged else 200,
        media_type=None if unchanged else "image/png",
        headers={
            "ETag": etag,
            "Cache-Control": cache_control if key in image_cache else "no-store",
            **attribution_headers((race or {}).get("circuit", {}).get("circuitId", ""), options),
        },
    )


def _build_teams_preview_png(
    lang: str,
    display: str,
    teams_data,
    full_size: bool,
    *,
    extra: TeamsExtra = TeamsExtra.NONE,
    extra_context: TeamsExtraContext | None = None,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> bytes:
    """Construct the renderer, render, and convert to PNG — all in the worker thread."""
    translator = get_translator(lang)
    renderer = create_renderer(display, translator, lang, **bmp_artifacts.theme_kwargs(theme))
    bmp_data = renderer.render_teams_drivers(teams_data, extra, extra_context)
    return bmp_to_png(
        bmp_data,
        width=400,
        full_size=full_size,
        preserve_color=display in COLOR_DISPLAYS,
    )


async def _render_teams_preview(
    lang: str, display: str = "1bit", *, full_size: bool, theme: ThemeMode = ThemeMode.LIGHT
) -> StreamingResponse:
    """Render a dynamic Teams PNG when no pregenerated preview is available."""
    async with render_slot(variant=theme != ThemeMode.LIGHT):
        season = get_default_teams_year()
        teams_service = TeamsService()
        async with asyncio.timeout(config.TEAMS_ENRICHMENT_TIMEOUT_SECONDS):
            teams_data = await teams_service.get_teams_and_drivers(season)
        if not teams_data.teams:
            raise RuntimeError(f"No teams data available for preview season {season}")

        png_data = await run_render(
            functools.partial(
                _build_teams_preview_png,
                lang,
                display,
                teams_data,
                full_size,
                **bmp_artifacts.theme_kwargs(theme),
            )
        )
    return StreamingResponse(
        BytesIO(png_data),
        media_type="image/png",
        headers={"Cache-Control": _PREVIEW_CACHE_CONTROL},
    )


async def _render_teams_extra_preview(
    request: Request,
    lang: str,
    display: str,
    extra: TeamsExtra,
    ctx: TeamsExtraContext | None,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> Response:
    """Cache a full-size panel (or plain dark) preview under its day/weather/theme token."""
    season = ctx.season if ctx is not None else get_default_teams_year()
    key = (
        "preview:"
        + get_teams_image_key(lang, season, display=display, extra=extra, extra_context=ctx)
        + bmp_artifacts.theme_cache_suffix(theme)
    )

    async def produce() -> tuple[bytes, str]:
        """Render and cache only complete teams data in the variant cache."""
        generation = selected_generation()
        async with render_slot(variant=True):
            require_current_selection(generation)
            async with asyncio.timeout(config.TEAMS_ENRICHMENT_TIMEOUT_SECONDS):
                data = await TeamsService().get_teams_and_drivers(season)
            if not data.teams:
                raise RuntimeError(f"No teams data available for preview season {season}")
            content = await run_render(
                functools.partial(
                    _build_teams_preview_png,
                    lang,
                    display,
                    data,
                    True,
                    extra=extra,
                    extra_context=ctx,
                    **bmp_artifacts.theme_kwargs(theme),
                )
            )
            artifact = (content, strong_etag(content))
            if is_teams_data_cacheable(data) and bmp_cache_generation() == generation:
                get_bmp_variant_cache()[key] = artifact
            return artifact

    artifact = get_bmp_variant_cache().get(key)
    if artifact is None:
        artifact = await bmp_artifacts.singleflight(f"{selected_generation()}:{key}", produce)
    content, etag = artifact
    unchanged = if_none_match_matches(request.headers.get("If-None-Match"), etag)
    return Response(
        content=b"" if unchanged else content,
        status_code=304 if unchanged else 200,
        media_type=None if unchanged else "image/png",
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache" if key in get_bmp_variant_cache() else "no-store",
        },
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

    safe_lang = normalize_lang(lang)

    filename = get_preview_filename(safe_screen, safe_lang)
    preview_path = Path(config.IMAGES_PATH) / filename
    cached = await _cached_preview_response(preview_path, request, _preview_identity(safe_screen))
    if cached is not None:
        return cached

    enforce_rate_limit(request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    try:
        return await _render_dynamic_preview(safe_screen, safe_lang, full_size=False)
    except Exception as exc:
        if isinstance(exc, (HTTPException, StaleSelection)):
            raise
        logger.warning("Dynamic %s homepage preview failed: %s", safe_screen, exc)

    raise HTTPException(status_code=404, detail="Preview not generated yet")


async def _try_teams_extra_preview(
    request: Request,
    lang: str,
    display: str,
    extra: TeamsExtra,
    *,
    tz: str | None,
    temp_unit: TempUnit,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> tuple[Response | None, bool]:
    """Render the requested Teams extra panel (or plain dark) preview for the configurator.

    Returns ``(response, False)`` on success, ``(None, False)`` when no panel applies and
    ``(None, True)`` when the panel failed after the rate limit was charged (the caller then
    serves the plain grid marked as a fallback).
    """
    dark = theme != ThemeMode.LIGHT
    if extra == TeamsExtra.NONE and not dark:
        return None, False
    ctx = (
        build_teams_extra_context(
            extra, get_default_teams_year(), timezone_name=tz, temp_unit=temp_unit
        )
        if extra != TeamsExtra.NONE
        else None
    )
    if ctx is None and not dark:
        return None, False
    _charge_configure_preview(request)
    try:
        response = await _render_teams_extra_preview(
            request,
            lang,
            display,
            extra if ctx is not None else TeamsExtra.NONE,
            ctx,
            **bmp_artifacts.theme_kwargs(theme),
        )
    except Exception as exc:
        if isinstance(exc, (HTTPException, StaleSelection)):
            raise
        logger.warning("Dynamic teams extra preview failed: %s", exc)
        if ctx is None:
            raise HTTPException(
                status_code=404,
                detail="Configure preview not generated yet",
                headers={"Cache-Control": "no-store"},
            ) from exc
        return None, True
    response.headers["Link"] = f'<{config.SITE_URL.rstrip("/")}/credits>; rel="license"'
    return response, False


def _has_pregenerated_configure_preview(
    screen: str,
    options: TrackOptions,
    countdown: CountdownMode,
    weekend_mode: WeekendMode,
    temp_unit: TempUnit,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> bool:
    """Return whether a pregenerated configure preview can match the request.

    Dark previews are never pregenerated; light Teams previews always are, light calendar
    ones only with the default options.
    """
    if theme != ThemeMode.LIGHT:
        return False
    return (
        options == DEFAULT_TRACK_OPTIONS
        and countdown == CountdownMode.HOURS
        and weekend_mode == WeekendMode.OFF
        and temp_unit == TempUnit.C
    ) or screen == "teams"


@router.get("/preview/configure/{screen_type}.png", response_model=None)
@with_current_selection
async def get_configure_preview_png(
    screen_type: str,
    request: Request,
    lang: str = Query(default="en"),
    tz: Annotated[
        str | None,
        Query(description="IANA timezone for teams extra panels; defaults to DEFAULT_TIMEZONE"),
    ] = None,
    weather_type: str = Query(default="off"),
    extra: Annotated[
        TeamsExtra,
        Query(
            description="Teams panel: none, countdown, flags (next 3 races), track, weather; "
            "timeline/combo alias countdown"
        ),
    ] = TeamsExtra.NONE,
    display: str = Query(default="1bit"),
    track_style: TrackStyle = TrackStyle.RELIEF,
    track_source: TrackSource = TrackSource.JULES,
    track_accent: TrackAccent = TrackAccent.ALL,
    countdown: Annotated[
        CountdownMode,
        Query(description="Calendar countdown: hours (default), days, auto or off"),
    ] = CountdownMode.HOURS,
    weekend_mode: Annotated[
        WeekendMode,
        Query(description="Calendar weekend mode: off (default) or auto"),
    ] = WeekendMode.OFF,
    temp_unit: Annotated[
        TempUnit,
        Query(description="Temperature unit for calendar and teams weather: c (default) or f"),
    ] = TempUnit.C,
    theme: Annotated[
        ThemeMode,
        Query(description="Theme: light (default) or dark (experimental, rendered on demand)"),
    ] = ThemeMode.LIGHT,
) -> Response:
    """Serve pre-generated configure-preview images."""
    allowed_screens = {"calendar": "calendar", "teams": "teams"}

    safe_screen = allowed_screens.get(screen_type)
    if not safe_screen:
        raise HTTPException(status_code=404, detail="Unknown screen type")

    safe_lang = normalize_lang(lang)
    # Unknown values mean no weather here (the configurator's default), not race-day weather.
    safe_weather = canonical_weather_type(weather_type, default="off")
    safe_display = normalize_display(display)

    extra_failed = False
    tz = validate_timezone(tz)
    if safe_screen == "teams":
        response, extra_failed = await _try_teams_extra_preview(
            request, safe_lang, safe_display, extra, tz=tz, temp_unit=temp_unit, theme=theme
        )
        if response is not None:
            return response

    filename = get_configure_preview_filename(
        safe_screen,
        safe_lang,
        display=safe_display,
        weather=weather_file_key(safe_weather) if safe_screen == "calendar" else "off",
    )
    configure_path = Path(config.IMAGES_PATH) / filename
    options = TrackOptions(track_style, track_source, track_accent)
    cached = (
        await _configure_preview_snapshot(
            configure_path, request, safe_screen, safe_weather, countdown
        )
        if _has_pregenerated_configure_preview(
            safe_screen, options, countdown, weekend_mode, temp_unit, theme
        )
        else None
    )
    if cached is not None:
        return _configure_cached_response(cached, safe_screen, options, extra_failed)

    if safe_screen == "calendar":
        _charge_configure_preview(request)
        try:
            return await _render_configure_calendar(
                request,
                safe_lang,
                safe_display,
                safe_weather,
                options,
                countdown,
                weekend_mode,
                temp_unit,
                **bmp_artifacts.theme_kwargs(theme),
            )
        except Exception as exc:
            if isinstance(exc, (HTTPException, StaleSelection)):
                raise
            logger.warning("Dynamic calendar configure preview failed: %s", exc)

    if safe_screen == "teams":
        # A failed extra panel already charged the rate limit for this request.
        _charge_configure_preview(request, already_charged=extra_failed)
        try:
            response = await _render_teams_preview(
                safe_lang,
                safe_display,
                full_size=True,
                **bmp_artifacts.theme_kwargs(theme),
            )
            response.headers["Link"] = f'<{config.SITE_URL.rstrip("/")}/credits>; rel="license"'
            return _mark_extra_fallback(response) if extra_failed else response
        except Exception as exc:
            if isinstance(exc, (HTTPException, StaleSelection)):
                raise
            logger.warning("Dynamic teams configure preview failed: %s", exc)

    raise HTTPException(status_code=404, detail="Configure preview not generated yet")


def _charge_configure_preview(request: Request, *, already_charged: bool = False) -> None:
    """Charge once across a panel fallback or a cache-generation retry."""
    if not already_charged and not retrying_selection():
        enforce_rate_limit(
            request, bucket="dynamic_preview", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE
        )


def _configure_cached_response(
    cached: Response, screen: str, options: TrackOptions, extra_failed: bool
) -> Response:
    """Attach attribution and panel fallback headers to a cached preview."""
    if screen == "calendar":
        race = F1Service().get_next_race_from_static() or {}
        cached.headers.update(
            attribution_headers(race.get("circuit", {}).get("circuitId", ""), options)
        )
    if screen == "teams":
        cached.headers["Link"] = f'<{config.SITE_URL.rstrip("/")}/credits>; rel="license"'
    return _mark_extra_fallback(cached) if extra_failed else cached


def _mark_extra_fallback(response: Response) -> Response:
    """Flag a plain Teams preview served because the requested extra panel failed.

    The plain image must not be cached under the extra panel's URL (the configurator would
    keep showing it after the panel recovers), and the header lets clients tell it apart.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Preview-Fallback"] = "plain"
    return response


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
