"""Render, hash and cache BMP artifacts for the image and preview routes.

Routes decide *what* to serve (parameters, pregenerated files, HTTP semantics); this module
owns *how* a miss is produced: one shared in-flight render per key, the render slot, the
worker-thread renderer construction and the cache-generation guard on writes.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import TypedDict

from cachetools import TTLCache

from app.config import config
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_generation import (
    require_current_selection,
    selected_generation,
    selection_flight_key,
)
from app.services.renderer_theme import ThemeMode
from app.services.renderers import create_renderer, display_palette
from app.services.teams_extra import TeamsExtra, TeamsExtraContext
from app.services.teams_service import TeamsService, is_teams_data_cacheable
from app.services.track_catalog import DEFAULT_TRACK_OPTIONS, TrackOptions
from app.services.weather_service import TempUnit, get_weather_context
from app.services.weekend_phase import WeekendMode, WeekendPhase
from app.services.weekend_results import apply_weekend_results
from app.state import (
    BmpArtifact,
    ImageCache,
    bmp_cache_generation,
    get_bmp_cache,
    get_bmp_variant_cache,
)
from app.utils.async_tasks import run_render
from app.utils.etag import strong_etag
from app.utils.image_conversion import ImageOutput, transform_bmp_output
from app.utils.race_times import convert_race_times_to_timezone
from app.utils.refresh_hint import CountdownMode
from app.utils.render_limit import render_slot

logger = logging.getLogger(__name__)

_singleflight_tasks: dict[str, asyncio.Task[BmpArtifact]] = {}


async def singleflight(
    key: str,
    producer: Callable[[], Awaitable[BmpArtifact]],
) -> BmpArtifact:
    """Share one in-flight render per key on the application's event loop.

    The mapping is intentionally lock-free: routes and task cleanup access it only from the
    same asyncio event loop. Shielding keeps the producer alive when one waiter disconnects.
    """
    key = selection_flight_key(key)
    existing = _singleflight_tasks.get(key)
    if existing is not None:
        return await asyncio.shield(existing)

    async def execute() -> BmpArtifact:
        """Run the producer and release this key only when its own task completes."""
        try:
            return await producer()
        finally:
            if _singleflight_tasks.get(key) is asyncio.current_task():
                _singleflight_tasks.pop(key, None)

    task = asyncio.create_task(execute(), name=f"bmp-singleflight:{key}")
    _singleflight_tasks[key] = task
    return await asyncio.shield(task)


async def drain_singleflight(timeout: float = 10.0) -> None:
    """Let shared renders still in flight at shutdown finish, cancelling any that overrun.

    A producer keeps running after its last waiter disconnects (it is shielded), so without
    this the render executor and HTTP clients could close underneath it.
    """
    pending = [task for task in _singleflight_tasks.values() if not task.done()]
    if not pending:
        return
    _, overrun = await asyncio.wait(pending, timeout=timeout)
    for task in overrun:
        logger.warning("Cancelling shared render %s at shutdown", task.get_name())
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def maybe_convert_timezone(race_data: dict, target_tz: str) -> dict:
    """Convert cached race times only when the target timezone differs."""
    cached_tz = race_data.get("timezone", config.DEFAULT_TIMEZONE)
    if cached_tz == target_tz:
        return race_data

    logger.debug("Converting times from %s to %s", cached_tz, target_tz)
    return convert_race_times_to_timezone(race_data, target_tz)


def render_calendar_bytes(
    display: str,
    lang: str,
    race_data: dict,
    historical_data,
    weather_data,
    weather_type: str,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
    countdown_mode: CountdownMode = CountdownMode.HOURS,
    weekend: WeekendPhase | None = None,
    temp_unit: TempUnit = TempUnit.C,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> bytes:
    """Construct the renderer and render in the worker thread.

    Construction must happen in the SAME thread as the render: renderer __init__ loads fonts
    into a per-thread cache, so building on the event loop would share FreeTypeFont objects
    across render threads.
    """
    renderer = create_renderer(display, get_translator(lang), lang, **theme_kwargs(theme))
    return renderer.render_calendar(
        race_data,
        historical_data,
        weather_data,
        weather_type,
        track_options=track_options,
        countdown_mode=countdown_mode,
        weekend=weekend,
        temp_unit=temp_unit,
    )


def render_teams_bytes(
    display: str,
    lang: str,
    teams_data,
    *,
    extra: TeamsExtra = TeamsExtra.NONE,
    extra_context: TeamsExtraContext | None = None,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> bytes:
    """Construct the renderer and render the teams screen in the worker thread."""
    renderer = create_renderer(display, get_translator(lang), lang, **theme_kwargs(theme))
    return renderer.render_teams_drivers(teams_data, extra, extra_context)


def render_error_bytes(
    display: str, lang: str, message: str, theme: ThemeMode = ThemeMode.LIGHT
) -> bytes:
    """Construct the renderer and render an error BMP in the worker thread."""
    renderer = create_renderer(display, get_translator(lang), lang, **theme_kwargs(theme))
    return renderer.render_error(message)


async def render_error_artifact(
    display: str, lang: str, message: str, theme: ThemeMode = ThemeMode.LIGHT
) -> BmpArtifact:
    """Render one error BMP inside the shared render limit."""
    async with render_slot(variant=theme != ThemeMode.LIGHT):
        bmp_data = await run_render(
            functools.partial(render_error_bytes, display, lang, message, **theme_kwargs(theme))
        )
    return bmp_data, strong_etag(bmp_data)


async def shared_error_artifact(
    display: str, lang: str, message: str, theme: ThemeMode = ThemeMode.LIGHT
) -> BmpArtifact:
    """Share one in-flight error image (in the requested theme) across waiters."""
    return await singleflight(
        f"error:{display}:{lang}:{message}{theme_cache_suffix(theme)}",
        functools.partial(render_error_artifact, display, lang, message, **theme_kwargs(theme)),
    )


async def render_calendar(
    *,
    race_data: dict | None,
    lang: str,
    target_tz: str,
    weather_type: str,
    display: str,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
    countdown_mode: CountdownMode = CountdownMode.HOURS,
    weekend_mode: WeekendMode = WeekendMode.OFF,
    weekend: WeekendPhase | None = None,
    temp_unit: TempUnit = TempUnit.C,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> tuple[bytes, dict | None, bool]:
    """Render a calendar BMP for a selected race and a canonical weather type.

    ``weekend_mode=auto`` prefers this weekend's stored podiums in the footer and draws the
    ``weekend`` phase. ``weather_type=weekend`` draws the sessions' forecast summary.
    Returns the output, the timezone-converted race data and whether it may be cached.
    """
    if not race_data:
        error_bmp = await run_render(
            functools.partial(
                render_error_bytes,
                display,
                lang,
                "Failed to fetch race data",
                **theme_kwargs(theme),
            )
        )
        return error_bmp, None, False

    weather_data = None
    if weather_type != "off" and config.WEATHER_ENABLED and not race_data.get("is_cancelled"):
        _, _, weather_by_type = await get_weather_context(
            race_data,
            include_weekend=weather_type == "weekend",
            wanted=("weekend",) if weather_type == "weekend" else ("race", "current"),
        )
        weather_key = {"current": "current", "weekend": "weekend"}.get(weather_type, "race")
        weather_data = weather_by_type.get(weather_key)

    race_data = maybe_convert_timezone(race_data, target_tz)

    circuit_id = race_data.get("circuit", {}).get("circuitId", "")
    historical_data = F1Service.get_historical_from_static(circuit_id) if circuit_id else None
    if weekend_mode == WeekendMode.AUTO:
        historical_data = apply_weekend_results(historical_data, race_data)

    # render_calendar is CPU-bound (Pillow + pure-Python palette/packing). Run it on the render
    # executor so a cache miss doesn't block the event loop and stall in-flight requests.
    bmp_data = await run_render(
        functools.partial(
            render_calendar_bytes,
            display,
            lang,
            race_data,
            historical_data,
            weather_data,
            weather_type,
            track_options,
            countdown_mode,
            weekend,
            temp_unit,
            **theme_kwargs(theme),
        )
    )
    return (bmp_data, race_data, True)


async def render_calendar_artifact(
    *,
    cache_key: str,
    race_data: dict | None,
    lang: str,
    target_tz: str,
    weather_type: str,
    display: str,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
    countdown_mode: CountdownMode = CountdownMode.HOURS,
    weekend_mode: WeekendMode = WeekendMode.OFF,
    weekend: WeekendPhase | None = None,
    temp_unit: TempUnit = TempUnit.C,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> BmpArtifact:
    """Render/hash one calendar miss and cache the pair only when complete.

    A non-default countdown, weekend mode, temperature unit, theme or the weekend weather
    summary is an opt-in variant: it renders in a variant slot and is cached in the variant
    cache, so it can never displace the default calendars.
    """
    generation = selected_generation()
    variant = is_calendar_variant(countdown_mode, weekend_mode, temp_unit, weather_type, theme)
    async with render_slot(variant=variant):
        require_current_selection(generation)
        bmp_data, _race_data, is_cacheable = await render_calendar(
            race_data=race_data,
            lang=lang,
            target_tz=target_tz,
            weather_type=weather_type,
            display=display,
            track_options=track_options,
            countdown_mode=countdown_mode,
            weekend_mode=weekend_mode,
            weekend=weekend,
            temp_unit=temp_unit,
            **theme_kwargs(theme),
        )
        artifact = (bmp_data, strong_etag(bmp_data))
        if is_cacheable and bmp_cache_generation() == generation:
            calendar_image_cache(countdown_mode, weekend_mode, temp_unit, weather_type, theme)[
                cache_key
            ] = artifact
        return artifact


def calendar_image_cache(
    countdown_mode: CountdownMode,
    weekend_mode: WeekendMode = WeekendMode.OFF,
    temp_unit: TempUnit = TempUnit.C,
    weather_type: str = "off",
    theme: ThemeMode = ThemeMode.LIGHT,
) -> ImageCache:
    """Keep default calendars in the main cache and opt-in variants in the variant cache."""
    if is_calendar_variant(countdown_mode, weekend_mode, temp_unit, weather_type, theme):
        return get_bmp_variant_cache()
    return get_bmp_cache()


def is_calendar_variant(
    countdown_mode: CountdownMode,
    weekend_mode: WeekendMode = WeekendMode.OFF,
    temp_unit: TempUnit = TempUnit.C,
    weather_type: str = "off",
    theme: ThemeMode = ThemeMode.LIGHT,
) -> bool:
    """Return whether a calendar is an opt-in variant (variant render slot and cache)."""
    return (
        countdown_mode != CountdownMode.HOURS
        or weekend_mode != WeekendMode.OFF
        or temp_unit != TempUnit.C
        or weather_type == "weekend"
        or theme != ThemeMode.LIGHT
    )


class ThemeKwargs(TypedDict, total=False):
    """Keyword arguments that carry a non-default theme through a render call."""

    theme: ThemeMode


def theme_kwargs(theme: ThemeMode) -> ThemeKwargs:
    """Return ``{"theme": theme}`` for a non-default theme and nothing for light.

    Light calls keep their exact argument shape, so the default code path is unchanged.
    """
    return ThemeKwargs() if theme == ThemeMode.LIGHT else ThemeKwargs(theme=theme)


def theme_cache_suffix(theme: ThemeMode) -> str:
    """Return the cache-key suffix of a theme; empty for the light default."""
    return "" if theme == ThemeMode.LIGHT else f":theme={theme}"


def is_teams_variant(extra: TeamsExtra, theme: ThemeMode = ThemeMode.LIGHT) -> bool:
    """Return whether a teams dashboard is an opt-in variant (extra panel or dark theme)."""
    return extra != TeamsExtra.NONE or theme != ThemeMode.LIGHT


def teams_image_cache(
    extra: TeamsExtra, theme: ThemeMode = ThemeMode.LIGHT
) -> TTLCache[str, BmpArtifact]:
    """Keep plain light teams dashboards in the main cache and variants in the variant cache."""
    if is_teams_variant(extra, theme):
        return get_bmp_variant_cache()
    return get_bmp_cache()


async def render_teams_artifact(
    *,
    cache_key: str,
    lang: str,
    year: int,
    display: str,
    extra: TeamsExtra = TeamsExtra.NONE,
    extra_context: TeamsExtraContext | None = None,
    theme: ThemeMode = ThemeMode.LIGHT,
) -> BmpArtifact:
    """Fetch, render, and hash one teams miss, caching only a complete dashboard."""
    generation = selected_generation()
    async with render_slot(variant=is_teams_variant(extra, theme)):
        require_current_selection(generation)
        async with asyncio.timeout(config.TEAMS_ENRICHMENT_TIMEOUT_SECONDS):
            teams_data = await TeamsService().get_teams_and_drivers(year)
        bmp_data = await run_render(
            functools.partial(
                render_teams_bytes,
                display,
                lang,
                teams_data,
                extra=extra,
                extra_context=extra_context,
                **theme_kwargs(theme),
            )
        )
        artifact = (bmp_data, strong_etag(bmp_data))
        if is_teams_data_cacheable(teams_data) and bmp_cache_generation() == generation:
            teams_image_cache(extra, theme)[cache_key] = artifact
        elif teams_data.teams:
            logger.warning("Teams standings incomplete for %s; serving but not caching", year)
        else:
            logger.warning("Teams data empty for %s; serving but not caching", year)
        return artifact


def output_cache_key(artifact: BmpArtifact, display: str, output: ImageOutput) -> str:
    """Key a format/rotation variant by the default artifact's content hash.

    The ETag is the SHA-256 of the default bytes, so the derived bytes for one key never
    change and a new render (or pregenerated file) gets a new key instead of a stale entry.
    """
    return f"output:{output.cache_token}:{display}:{artifact[1]}"


def _transform_artifact(bmp_data: bytes, display: str, output: ImageOutput) -> BmpArtifact:
    """Derive the variant bytes and hash them in the worker thread."""
    content = transform_bmp_output(bmp_data, output, palette=display_palette(display))
    return content, strong_etag(content)


async def _render_output_artifact(
    key: str, artifact: BmpArtifact, display: str, output: ImageOutput
) -> BmpArtifact:
    """Encode one format/rotation variant on the render executor and cache it."""
    generation = selected_generation()
    async with render_slot(variant=True):
        derived = await run_render(
            functools.partial(_transform_artifact, artifact[0], display, output)
        )
    if bmp_cache_generation() == generation:
        get_bmp_variant_cache()[key] = derived
    return derived


async def output_artifact(
    artifact: BmpArtifact, display: str, output: ImageOutput
) -> tuple[BmpArtifact, bool]:
    """Return the artifact in the requested format and rotation, and whether it was cached.

    The default output is the artifact itself (same bytes, same ETag, no extra work). Other
    variants are derived from the default bytes once, in a variant render slot on the render
    executor, and kept in the variant cache, so they never displace default images.
    """
    if output.is_default:
        return artifact, True
    key = output_cache_key(artifact, display, output)
    cached = get_bmp_variant_cache().get(key)
    if cached is not None:
        return cached, True
    derived = await singleflight(
        key, functools.partial(_render_output_artifact, key, artifact, display, output)
    )
    return derived, False
