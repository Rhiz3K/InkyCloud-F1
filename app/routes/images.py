"""Image endpoints returning BMPs for E-Ink displays."""

from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

import pytz
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.config import VALID_LANGUAGES, config
from app.services.analytics import track_event, track_pageview
from app.services.bwr_renderer import BwrRenderer
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.teams_service import TeamsService
from app.services.weather_service import get_weather_context
from app.state import get_bmp_cache, record_api_call
from app.utils.f1_season import get_current_f1_season
from app.utils.race_times import convert_race_times_to_timezone

from .deps import get_f1_service

logger = logging.getLogger(__name__)

router = APIRouter()

TEAMS_BMP_CACHE_CONTROL = "no-store, max-age=0"


def _normalize_lang(lang: str) -> str:
    return lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG


def _normalize_display(display: str) -> str:
    return display if display in ("1bit", "spectra6", "bwr") else "1bit"


def _validate_timezone_param(tz: str | None) -> None:
    if tz and tz not in pytz.all_timezones_set:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}")


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
    weather_key = weather_type if weather else "no_weather"
    return f"{lang}:{year or 'next'}:{round_num or 'next'}:{race_key or 'auto'}:{tz or 'default'}:{weather_key}:{display}"


def _to_round_number(round_value: str | int | None) -> int | None:
    if round_value in (None, ""):
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
    is_auto_selected = year is None and race_round is None and race_key is None
    actual_year = year
    actual_round = race_round
    actual_race_name: str | None = None

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


# Pre-defined whitelist of valid calendar image filenames (no user input in paths)
_VALID_CALENDAR_FILENAMES: dict[tuple[str, str, str], str] = {
    # (lang, display, weather) -> filename
    ("en", "1bit", "off"): "calendar_en.bmp",
    ("en", "1bit", "current"): "calendar_en_weather_current.bmp",
    ("en", "1bit", "race"): "calendar_en_weather_race.bmp",
    ("en", "1bit", "race_day"): "calendar_en_weather_race.bmp",
    ("en", "spectra6", "off"): "calendar_en_spectra6.bmp",
    ("en", "spectra6", "current"): "calendar_en_spectra6_weather_current.bmp",
    ("en", "spectra6", "race"): "calendar_en_spectra6_weather_race.bmp",
    ("en", "spectra6", "race_day"): "calendar_en_spectra6_weather_race.bmp",
    ("en", "bwr", "off"): "calendar_en_bwr.bmp",
    ("en", "bwr", "current"): "calendar_en_bwr_weather_current.bmp",
    ("en", "bwr", "race"): "calendar_en_bwr_weather_race.bmp",
    ("en", "bwr", "race_day"): "calendar_en_bwr_weather_race.bmp",
    ("cs", "1bit", "off"): "calendar_cs.bmp",
    ("cs", "1bit", "current"): "calendar_cs_weather_current.bmp",
    ("cs", "1bit", "race"): "calendar_cs_weather_race.bmp",
    ("cs", "1bit", "race_day"): "calendar_cs_weather_race.bmp",
    ("cs", "spectra6", "off"): "calendar_cs_spectra6.bmp",
    ("cs", "spectra6", "current"): "calendar_cs_spectra6_weather_current.bmp",
    ("cs", "spectra6", "race"): "calendar_cs_spectra6_weather_race.bmp",
    ("cs", "spectra6", "race_day"): "calendar_cs_spectra6_weather_race.bmp",
    ("cs", "bwr", "off"): "calendar_cs_bwr.bmp",
    ("cs", "bwr", "current"): "calendar_cs_bwr_weather_current.bmp",
    ("cs", "bwr", "race"): "calendar_cs_bwr_weather_race.bmp",
    ("cs", "bwr", "race_day"): "calendar_cs_bwr_weather_race.bmp",
}


def _get_pregenerated_calendar_path(
    *,
    lang: str,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
    tz: str | None,
    display: str,
    weather_type: str,
) -> Path | None:
    if year is not None or race_round is not None or race_key is not None:
        return None

    target_tz_for_key = tz or config.DEFAULT_TIMEZONE
    if target_tz_for_key != config.DEFAULT_TIMEZONE:
        return None

    # Normalize inputs to allowed values
    safe_lang = "cs" if lang == "cs" else "en"
    safe_display = display if display in {"spectra6", "bwr"} else "1bit"
    safe_weather = _normalize_weather_type(weather_type)

    # Lookup filename from whitelist (no user input in path construction)
    filename = _VALID_CALENDAR_FILENAMES.get((safe_lang, safe_display, safe_weather))
    if not filename:
        return None

    image_path = Path(config.IMAGES_PATH) / filename
    return image_path if image_path.exists() else None


def _get_race_data_from_static(
    f1_service: F1Service,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
) -> dict | None:
    if year and (race_round or race_key):
        for race in f1_service.get_all_races_from_static(year):
            if race_key and race.get("race_key") == race_key:
                return race
            if race_round is not None and _to_round_number(race.get("round")) == race_round:
                return race
        return None

    return f1_service.get_next_race_from_static()


def _maybe_convert_timezone(race_data: dict, target_tz: str) -> dict:
    cached_tz = race_data.get("timezone", config.DEFAULT_TIMEZONE)
    if cached_tz == target_tz:
        return race_data

    logger.debug("Converting times from %s to %s", cached_tz, target_tz)
    return convert_race_times_to_timezone(race_data, target_tz)


def _get_renderer(display: str, translator) -> Renderer | Spectra6Renderer | BwrRenderer:
    if display == "spectra6":
        return Spectra6Renderer(translator)
    if display == "bwr":
        return BwrRenderer(translator)
    return Renderer(translator)


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
) -> tuple[bytes, dict | None]:
    translator = get_translator(lang)

    race_data = _get_race_data_from_static(f1_service, year, race_round, race_key)
    if not race_data:
        renderer = _get_renderer(display, translator)
        return renderer.render_error("Failed to fetch race data"), None

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

    renderer = _get_renderer(display, translator)
    return renderer.render_calendar(
        race_data, historical_data, weather_data, weather_type
    ), race_data


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
            f"or 'bwr' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red)"
        ),
    ),
    f1_service: F1Service = Depends(get_f1_service),
):
    start_time = time.time()
    user_agent = request.headers.get("User-Agent")
    referrer = request.headers.get("Referer", "")

    lang = _normalize_lang(lang)
    display = _normalize_display(display)
    weather_type = _normalize_weather_type(weather_type)
    _validate_timezone_param(tz)

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
        await _track_calendar_analytics(
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
        weather_type=weather_type,
    )
    if image_path is not None:
        logger.info("Serving pre-generated image: %s", image_path)
        bmp_data = image_path.read_bytes()
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
        await _track_calendar_analytics(
            lang=lang,
            tz=tz,
            year=year,
            race_round=race_round,
            race_key=race_key,
            user_agent=user_agent,
            referrer=referrer,
        )
        return FileResponse(
            path=str(image_path),
            media_type="image/bmp",
            filename="calendar.bmp",
            headers={"Cache-Control": "public, max-age=3600", "X-Cache": "MISS"},
        )

    try:
        target_tz = tz or config.DEFAULT_TIMEZONE
        bmp_data, race_data = await _render_calendar(
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
        await _track_calendar_analytics(
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

    except Exception as exc:
        logger.error("Error generating calendar: %s", exc, exc_info=True)
        sentry_sdk.capture_exception(exc)

        translator = get_translator(lang)
        renderer = _get_renderer(display, translator)
        bmp_data = renderer.render_error(str(exc))

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
            headers={"Content-Disposition": 'inline; filename="calendar.bmp"'},
        )


@router.get("/teams.bmp")
async def get_teams_bmp(
    request: Request,
    lang: str = Query(default="en", description="Language code (cs, en)"),
    year: int | None = Query(default=None, description="Season year"),
):
    start_time = time.time()

    try:
        lang = _normalize_lang(lang)

        if year is None:
            year = get_current_f1_season()

        translator = get_translator(lang)
        teams_service = TeamsService()
        teams_data = await teams_service.get_teams_and_drivers(year)

        renderer = Renderer(translator)
        bmp_data = renderer.render_teams_drivers(teams_data)

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

        translator = get_translator(lang)
        renderer = Renderer(translator)
        bmp_data = renderer.render_error(str(exc))

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
        )

        return StreamingResponse(
            BytesIO(bmp_data),
            media_type="image/bmp",
            headers={
                "Content-Disposition": 'inline; filename="teams.bmp"',
                "Cache-Control": TEAMS_BMP_CACHE_CONTROL,
            },
        )
