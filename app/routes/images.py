"""Image endpoints returning BMPs for E-Ink displays."""

from __future__ import annotations

import functools
import logging
import os
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.config import LANGUAGE_CODES, VALID_LANGUAGES, config
from app.services.analytics import track_event, track_pageview
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_keys import get_calendar_image_key, get_teams_image_key
from app.services.renderers import create_renderer
from app.services.teams_service import (
    TeamsService,
    get_default_teams_year,
    is_teams_data_cacheable,
)
from app.services.weather_service import get_weather_context
from app.state import get_bmp_cache, record_api_call
from app.utils.async_tasks import create_supervised_task, run_render
from app.utils.race_times import convert_race_times_to_timezone
from app.utils.rate_limit import enforce_rate_limit
from app.utils.timezones import is_valid_timezone, normalize_timezone

from .deps import get_f1_service

logger = logging.getLogger(__name__)

router = APIRouter()

TEAMS_BMP_CACHE_CONTROL = "no-store, max-age=0"


def _normalize_lang(lang: str) -> str:
    """Normalize language input to a supported locale code."""
    return lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG


def _normalize_display(display: str) -> str:
    """Normalize display input to a supported renderer key."""
    return display if display in ("1bit", "spectra6", "bwr", "bwry") else "1bit"


def _validate_timezone_param(tz: str | None) -> None:
    """Reject invalid timezone query values."""
    if tz and not is_valid_timezone(tz):
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}")


def _validate_calendar_selection(year: int | None, race_key: str | None) -> None:
    """Validate query combinations for explicit calendar selection."""
    if race_key is not None and year is None:
        raise HTTPException(status_code=400, detail="race_key requires year")


def _get_cache_key(
    lang: str,
    year: int | None,
    round_num: int | None,
    race_key: str | None,
    tz: str | None,
    weather: bool,
    weather_type: str,
    display: str,
) -> str:
    """Build the cache key for calendar render variants."""
    weather_key = weather_type if weather else "no_weather"
    return (
        f"{lang}:{year or 'next'}:{round_num or 'next'}:{race_key or 'auto'}:"
        f"{tz or 'default'}:{weather_key}:{display}"
    )


def _to_round_number(round_value: str | int | None) -> int | None:
    """Convert a round identifier to a positive integer when possible."""
    if round_value is None or round_value == "":
        return None

    try:
        round_number = int(round_value)
    except (TypeError, ValueError):
        return None

    return round_number if round_number > 0 else None


def _get_race_info_for_stats(
    f1_service: F1Service,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
) -> tuple[bool, int | None, int | None, str | None]:
    """Resolve the race metadata that should be recorded in analytics."""
    is_auto_selected = year is None and race_round is None and race_key is None
    actual_year = year
    actual_round = race_round
    actual_race_name: str | None = None

    if race_key is not None and year is None:
        return is_auto_selected, actual_year, actual_round, actual_race_name

    if year and (race_round or race_key):
        for race in f1_service.get_all_races_from_static(year):
            if race_key and race.get("race_key") == race_key:
                actual_race_name = race.get("race_name", "Unknown")
                actual_round = _to_round_number(race.get("round"))
                break
            if race_round is not None and _to_round_number(race.get("round")) == race_round:
                actual_race_name = race.get("race_name", "Unknown")
                break
        return is_auto_selected, actual_year, actual_round, actual_race_name

    race_info = f1_service.get_next_race_from_static()
    if race_info:
        actual_year = int(race_info.get("season", 0)) or None
        actual_round = _to_round_number(race_info.get("round"))
        actual_race_name = race_info.get("race_name", "Next Race")

    return is_auto_selected, actual_year, actual_round, actual_race_name


def _schedule_calendar_analytics(
    *,
    lang: str,
    tz: str | None,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
    user_agent: str | None,
    referrer: str,
) -> None:
    """Schedule calendar analytics without blocking BMP responses."""
    create_supervised_task(
        _track_calendar_analytics(
            lang=lang,
            tz=tz,
            year=year,
            race_round=race_round,
            race_key=race_key,
            user_agent=user_agent,
            referrer=referrer,
        ),
        name="calendar_analytics",
    )


async def _track_calendar_analytics(
    *,
    lang: str,
    tz: str | None,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
    user_agent: str | None,
    referrer: str,
) -> None:
    """Send calendar download analytics events for direct BMP requests."""
    query_params: dict[str, str] = {"lang": lang}
    if tz:
        query_params["tz"] = tz
    if year is not None:
        query_params["year"] = str(year)
    if race_round is not None:
        query_params["round"] = str(race_round)
    if race_key is not None:
        query_params["race_key"] = race_key

    url = f"/calendar.bmp?{urlencode(query_params)}"

    await track_pageview(
        url=url,
        title=f"Calendar BMP - {lang}",
        lang=lang,
        user_agent=user_agent,
        referrer=referrer,
    )

    await track_event(
        url="/calendar.bmp",
        event_name="calendar_download",
        lang=lang,
        user_agent=user_agent,
        event_data={
            "language": lang,
            "timezone": tz or "default",
            "year": year,
            "round": race_round,
            "source": "direct" if not referrer else "referral",
        },
    )


def _record_calendar_api_call(
    *,
    start_time: float,
    size_bytes: int,
    lang: str,
    display: str,
    tz: str | None,
    actual_year: int | None,
    actual_round: int | None,
    actual_race_name: str | None,
    is_auto_selected: bool,
) -> None:
    """Persist a calendar API call with render metadata."""
    record_api_call(
        "/calendar.bmp",
        (time.time() - start_time) * 1000,
        size_bytes,
        lang,
        tz,
        actual_year,
        actual_round,
        actual_race_name,
        is_auto_selected,
        display_type=display,
    )


def _normalize_weather_type(weather_type: str) -> str:
    """Normalize weather_type to allowed values only (prevents path traversal)."""
    allowed = {"off", "current", "race_day", "race"}
    return weather_type if weather_type in allowed else "race_day"


_DISPLAY_FILE_SUFFIXES: dict[str, str] = {
    "1bit": "",
    "spectra6": "_spectra6",
    "bwr": "_bwr",
    "bwry": "_bwry",
}


# Pre-defined whitelist of valid generated filenames (no user input in paths)
# Serve a pregenerated file only while it is plausibly current. Generation runs hourly, so a
# healthy file is at most ~1h old; 6h tolerates a few failed runs while bounding how long a
# stale file (e.g. a timezone that dropped out of the popular set, or a variant whose render
# keeps failing) can keep showing the previous race before falling back to on-demand rendering.
_PREGEN_MAX_AGE_SECONDS = 6 * 3600


def _fresh_pregenerated_path(image_path: Path) -> Path | None:
    """Return the path only when it stays inside IMAGES_PATH and is recent enough to trust.

    The filename is derived from allowlisted enum values plus a validated IANA timezone, so
    traversal isn't reachable in practice — the realpath containment check is the explicit
    sink-side barrier (defense in depth, and the pattern scanners recognize).
    """
    images_dir = os.path.realpath(config.IMAGES_PATH)
    resolved = os.path.realpath(image_path)
    if not resolved.startswith(images_dir + os.sep):
        return None
    try:
        mtime = os.stat(resolved).st_mtime
    except OSError:
        return None
    if time.time() - mtime > _PREGEN_MAX_AGE_SECONDS:
        return None
    return Path(resolved)


def _get_valid_teams_filenames(year: int) -> dict[tuple[str, str], str]:
    """Return the whitelist of valid teams BMP filenames for a specific season."""
    return {
        (lang, display): f"teams_{year}_{lang}{_DISPLAY_FILE_SUFFIXES[display]}.bmp"
        for lang in LANGUAGE_CODES
        for display in _DISPLAY_FILE_SUFFIXES
    }


def _get_pregenerated_calendar_path(
    *,
    lang: str,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
    tz: str | None,
    display: str,
    weather: bool,
    weather_type: str,
) -> Path | None:
    """Return a pregenerated calendar BMP path when the request matches one."""
    if year is not None or race_round is not None or race_key is not None:
        return None

    # Normalize inputs to allowed values; the filename is then derived solely from these
    # enum-like values plus a validated IANA timezone, so no raw user input reaches the path.
    safe_lang = lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG
    safe_display = display if display in {"spectra6", "bwr", "bwry"} else "1bit"
    safe_weather = "off" if not weather else _normalize_weather_type(weather_type)
    # Scheduler filenames use the "race" form for both race/race_day.
    key_weather = "race" if safe_weather == "race_day" else safe_weather

    target_tz = normalize_timezone(tz) if tz else config.DEFAULT_TIMEZONE
    if not is_valid_timezone(target_tz):
        return None

    # Single source for route lookups and scheduler-written filenames; returns the base key
    # (no tz part) when target_tz equals the default.
    image_key = get_calendar_image_key(
        safe_lang,
        tz=target_tz,
        default_timezone=config.DEFAULT_TIMEZONE,
        display=safe_display,
        weather=key_weather,
    )
    return _fresh_pregenerated_path(Path(config.IMAGES_PATH) / f"{image_key}.bmp")


def _get_pregenerated_teams_path(*, lang: str, year: int | None, display: str) -> Path | None:
    """Return a pregenerated teams BMP path when the request matches one."""
    default_year = get_default_teams_year()
    if year is not None and year != default_year:
        return None

    safe_lang = lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG
    safe_display = display if display in {"spectra6", "bwr", "bwry"} else "1bit"
    filename = _get_valid_teams_filenames(default_year).get((safe_lang, safe_display))
    if not filename:
        return None

    return _fresh_pregenerated_path(Path(config.IMAGES_PATH) / filename)


def _get_race_data_from_static(
    f1_service: F1Service,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
) -> dict | None:
    """Load race data for either an explicit selection or the next race."""
    if race_key is not None and year is None:
        return None

    if year and (race_round or race_key):
        for race in f1_service.get_all_races_from_static(year):
            if race_key and race.get("race_key") == race_key:
                return race
            if race_round is not None and _to_round_number(race.get("round")) == race_round:
                return race
        return None

    return f1_service.get_next_race_from_static()


def _maybe_convert_timezone(race_data: dict, target_tz: str) -> dict:
    """Convert cached race times only when the target timezone differs."""
    cached_tz = race_data.get("timezone", config.DEFAULT_TIMEZONE)
    if cached_tz == target_tz:
        return race_data

    logger.debug("Converting times from %s to %s", cached_tz, target_tz)
    return convert_race_times_to_timezone(race_data, target_tz)


def _render_calendar_bytes(
    display: str, lang: str, race_data: dict, historical_data, weather_data, weather_type: str
) -> bytes:
    """Construct the renderer and render in the worker thread.

    Construction must happen in the SAME thread as the render: renderer __init__ loads fonts
    into a per-thread cache, so building on the event loop would share FreeTypeFont objects
    across render threads.
    """
    renderer = create_renderer(display, get_translator(lang), lang)
    return renderer.render_calendar(race_data, historical_data, weather_data, weather_type)


def _render_teams_bytes(display: str, lang: str, teams_data) -> bytes:
    """Construct the renderer and render the teams screen in the worker thread."""
    renderer = create_renderer(display, get_translator(lang), lang)
    return renderer.render_teams_drivers(teams_data)


def _render_error_bytes(display: str, lang: str, message: str) -> bytes:
    """Construct the renderer and render an error BMP in the worker thread."""
    renderer = create_renderer(display, get_translator(lang), lang)
    return renderer.render_error(message)


async def _render_calendar(
    *,
    f1_service: F1Service,
    lang: str,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
    target_tz: str,
    weather: bool,
    weather_type: str,
    display: str,
) -> tuple[bytes, dict | None, bool]:
    """Render a calendar BMP and return the output plus source race data."""
    race_data = _get_race_data_from_static(f1_service, year, race_round, race_key)
    if not race_data:
        error_bmp = await run_render(
            functools.partial(_render_error_bytes, display, lang, "Failed to fetch race data")
        )
        return error_bmp, None, False

    weather_data = None
    if weather and config.WEATHER_ENABLED and not race_data.get("is_cancelled"):
        _, _, weather_by_type = await get_weather_context(race_data)
        if weather_type in ("race_day", "race"):
            weather_data = weather_by_type.get("race")
        elif weather_type == "current":
            weather_data = weather_by_type.get("current")

    race_data = _maybe_convert_timezone(race_data, target_tz)

    circuit_id = race_data.get("circuit", {}).get("circuitId", "")
    historical_data = F1Service.get_historical_from_static(circuit_id) if circuit_id else None

    # render_calendar is CPU-bound (Pillow + pure-Python palette/packing). Run it on the render
    # executor so a cache miss doesn't block the event loop and stall in-flight requests.
    bmp_data = await run_render(
        functools.partial(
            _render_calendar_bytes,
            display,
            lang,
            race_data,
            historical_data,
            weather_data,
            weather_type,
        )
    )
    return (bmp_data, race_data, True)


@router.get("/calendar.bmp")
async def get_calendar_bmp(
    request: Request,
    lang: str = Query(default="en", description="Language code (cs, en)"),
    year: int | None = Query(default=None, description="Season year (e.g., 2025)"),
    race_round: int | None = Query(default=None, description="Round number", alias="round"),
    race_key: str | None = Query(default=None, description="Race identifier", alias="race_key"),
    tz: str | None = Query(default=None, description="Timezone"),
    weather: bool = Query(default=True, description="Show weather forecast"),
    weather_type: str = Query(
        default="race_day", description="Weather type: 'current' or 'race_day'"
    ),
    display: str = Query(
        default="1bit",
        description=(
            f"Display type: '1bit' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} monochrome), "
            f"'spectra6' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} 6-color), "
            f"'bwr' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red), "
            f"or 'bwry' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red/yellow)"
        ),
    ),
    f1_service: F1Service = Depends(get_f1_service),
):
    """Render the calendar endpoint for the requested display and selection."""
    start_time = time.time()
    enforce_rate_limit(request, bucket="calendar_bmp", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    user_agent = request.headers.get("User-Agent")
    referrer = request.headers.get("Referer", "")

    lang = _normalize_lang(lang)
    display = _normalize_display(display)
    weather_type = _normalize_weather_type(weather_type)
    _validate_timezone_param(tz)
    tz = normalize_timezone(tz) if tz else None
    _validate_calendar_selection(year, race_key)

    is_auto_selected, actual_year, actual_round, actual_race_name = _get_race_info_for_stats(
        f1_service, year, race_round, race_key
    )

    cache_key = _get_cache_key(lang, year, race_round, race_key, tz, weather, weather_type, display)
    cached_bmp = get_bmp_cache().get(cache_key)
    if cached_bmp is not None:
        logger.debug("Cache hit for %s", cache_key)
        _record_calendar_api_call(
            start_time=start_time,
            size_bytes=len(cached_bmp),
            lang=lang,
            display=display,
            tz=tz,
            actual_year=actual_year,
            actual_round=actual_round,
            actual_race_name=actual_race_name,
            is_auto_selected=is_auto_selected,
        )
        _schedule_calendar_analytics(
            lang=lang,
            tz=tz,
            year=year,
            race_round=race_round,
            race_key=race_key,
            user_agent=user_agent,
            referrer=referrer,
        )
        return StreamingResponse(
            BytesIO(cached_bmp),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="calendar.bmp"',
                "Cache-Control": "public, max-age=3600",
                "X-Cache": "HIT",
            },
        )

    image_path = _get_pregenerated_calendar_path(
        lang=lang,
        year=year,
        race_round=race_round,
        race_key=race_key,
        tz=tz,
        display=display,
        weather=weather,
        weather_type=weather_type,
    )
    if image_path is not None:
        logger.info("Serving pre-generated image: %s", image_path)
        try:
            bmp_data = image_path.read_bytes()
        except OSError as exc:
            logger.info("Pre-generated image disappeared before read; rendering: %s", exc)
        else:
            get_bmp_cache()[cache_key] = bmp_data
            _record_calendar_api_call(
                start_time=start_time,
                size_bytes=len(bmp_data),
                lang=lang,
                display=display,
                tz=tz,
                actual_year=actual_year,
                actual_round=actual_round,
                actual_race_name=actual_race_name,
                is_auto_selected=is_auto_selected,
            )
            _schedule_calendar_analytics(
                lang=lang,
                tz=tz,
                year=year,
                race_round=race_round,
                race_key=race_key,
                user_agent=user_agent,
                referrer=referrer,
            )
            return StreamingResponse(
                BytesIO(bmp_data),
                media_type="image/bmp",
                headers={
                    "Content-Disposition": 'inline; filename="calendar.bmp"',
                    "Cache-Control": "public, max-age=3600",
                    "X-Cache": "MISS",
                },
            )

    try:
        target_tz = tz or config.DEFAULT_TIMEZONE
        bmp_data, race_data, is_cacheable = await _render_calendar(
            f1_service=f1_service,
            lang=lang,
            year=year,
            race_round=race_round,
            race_key=race_key,
            target_tz=target_tz,
            weather=weather,
            weather_type=weather_type,
            display=display,
        )
        if is_cacheable:
            get_bmp_cache()[cache_key] = bmp_data

        if race_data:
            actual_year = int(race_data.get("season", 0)) or actual_year
            actual_round = _to_round_number(race_data.get("round")) or actual_round
            actual_race_name = race_data.get("race_name", actual_race_name)

        _record_calendar_api_call(
            start_time=start_time,
            size_bytes=len(bmp_data),
            lang=lang,
            display=display,
            tz=tz,
            actual_year=actual_year,
            actual_round=actual_round,
            actual_race_name=actual_race_name,
            is_auto_selected=is_auto_selected,
        )
        _schedule_calendar_analytics(
            lang=lang,
            tz=tz,
            year=year,
            race_round=race_round,
            race_key=race_key,
            user_agent=user_agent,
            referrer=referrer,
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="calendar.bmp"',
                "Cache-Control": "public, max-age=3600" if is_cacheable else "no-store",
                "X-Cache": "MISS",
            },
        )

    except Exception as exc:
        logger.error("Error generating calendar: %s", exc, exc_info=True)
        sentry_sdk.capture_exception(exc)

        bmp_data = await run_render(
            functools.partial(_render_error_bytes, display, lang, "Temporary rendering error")
        )

        auto_selected = year is None and race_round is None and race_key is None
        record_api_call(
            "/calendar.bmp",
            (time.time() - start_time) * 1000,
            len(bmp_data),
            lang,
            tz,
            year,
            race_round,
            None,
            auto_selected,
            display_type=display,
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="calendar.bmp"',
                "Cache-Control": "no-store",
            },
        )


@router.get("/teams.bmp")
async def get_teams_bmp(
    request: Request,
    lang: str = Query(default="en", description="Language code (cs, en)"),
    year: int | None = Query(default=None, description="Season year"),
    display: str = Query(
        default="1bit",
        description=(
            f"Display type: '1bit' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} monochrome), "
            f"'spectra6' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} 6-color), "
            f"'bwr' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red), "
            f"or 'bwry' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red/yellow)"
        ),
    ),
):
    """Render the teams and drivers dashboard as a BMP image."""
    start_time = time.time()
    enforce_rate_limit(request, bucket="teams_bmp", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)

    try:
        lang = _normalize_lang(lang)
        display = _normalize_display(display)

        if year is None:
            year = get_default_teams_year()

        cache_key = get_teams_image_key(lang, year, display=display)
        cached_bmp = get_bmp_cache().get(cache_key)
        if cached_bmp:
            record_api_call(
                "/teams.bmp",
                (time.time() - start_time) * 1000,
                len(cached_bmp),
                lang,
                None,
                year,
                None,
                None,
                False,
                display_type=display,
            )
            return StreamingResponse(
                BytesIO(cached_bmp),
                media_type="image/bmp",
                headers={
                    "Content-Disposition": 'inline; filename="teams.bmp"',
                    "Cache-Control": TEAMS_BMP_CACHE_CONTROL,
                },
            )

        image_path = _get_pregenerated_teams_path(lang=lang, year=year, display=display)
        if image_path is not None:
            bmp_data = image_path.read_bytes()
            get_bmp_cache()[cache_key] = bmp_data

            record_api_call(
                "/teams.bmp",
                (time.time() - start_time) * 1000,
                len(bmp_data),
                lang,
                None,
                year,
                None,
                None,
                False,
                display_type=display,
            )

            return StreamingResponse(
                BytesIO(bmp_data),
                media_type="image/bmp",
                headers={
                    "Content-Disposition": 'inline; filename="teams.bmp"',
                    "Cache-Control": TEAMS_BMP_CACHE_CONTROL,
                },
            )

        teams_service = TeamsService()
        teams_data = await teams_service.get_teams_and_drivers(year)

        bmp_data = await run_render(
            functools.partial(_render_teams_bytes, display, lang, teams_data)
        )
        # Don't cache incomplete dashboards: a transient upstream outage would otherwise pin a
        # blank or standings-less teams screen on every device for the full cache TTL.
        if is_teams_data_cacheable(teams_data):
            get_bmp_cache()[cache_key] = bmp_data
        elif teams_data.teams:
            logger.warning("Teams standings incomplete for %s; serving but not caching", year)
        else:
            logger.warning("Teams data empty for %s; serving but not caching", year)

        record_api_call(
            "/teams.bmp",
            (time.time() - start_time) * 1000,
            len(bmp_data),
            lang,
            None,
            year,
            None,
            None,
            False,
            display_type=display,
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="teams.bmp"',
                "Cache-Control": TEAMS_BMP_CACHE_CONTROL,
            },
        )

    except Exception as exc:
        logger.error("Error generating teams: %s", exc, exc_info=True)
        sentry_sdk.capture_exception(exc)

        display = _normalize_display(display)
        bmp_data = await run_render(
            functools.partial(_render_error_bytes, display, lang, "Temporary rendering error")
        )

        record_api_call(
            "/teams.bmp",
            (time.time() - start_time) * 1000,
            len(bmp_data),
            lang,
            None,
            year,
            None,
            None,
            False,
            display_type=display,
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="teams.bmp"',
                "Cache-Control": "no-store",
            },
        )
