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
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.teams_service import TeamsService
from app.services.weather_service import get_cached_circuit_weather
from app.state import get_bmp_cache, record_api_call
from app.utils.f1_season import get_current_f1_season
from app.utils.race_times import convert_race_times_to_timezone

from .deps import get_f1_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _normalize_lang(lang: str) -> str:
    return lang if lang in VALID_LANGUAGES else config.DEFAULT_LANG


def _normalize_display(display: str) -> str:
    return display if display in ("1bit", "spectra6") else "1bit"


def _validate_timezone_param(tz: str | None) -> None:
    if tz and tz not in pytz.all_timezones_set:
        raise HTTPException(status_code=400, detail=f"Invalid timezone: {tz}")


def _get_cache_key(
    lang: str,
    year: int | None,
    round_num: int | None,
    tz: str | None,
    weather: bool,
    weather_type: str,
    display: str,
) -> str:
    weather_key = weather_type if weather else "no_weather"
    return (
        f"{lang}:{year or 'next'}:{round_num or 'next'}:{tz or 'default'}:{weather_key}:{display}"
    )


def _get_race_info_for_stats(
    f1_service: F1Service, year: int | None, race_round: int | None
) -> tuple[bool, int | None, int | None, str | None]:
    is_auto_selected = year is None and race_round is None
    actual_year = year
    actual_round = race_round
    actual_race_name: str | None = None

    if year and race_round:
        for race in f1_service.get_all_races_from_static(year):
            if int(race.get("round", 0)) == race_round:
                actual_race_name = race.get("race_name", "Unknown")
                break
        return is_auto_selected, actual_year, actual_round, actual_race_name

    race_info = f1_service.get_next_race_from_static()
    if race_info:
        actual_year = int(race_info.get("season", 0)) or None
        actual_round = int(race_info.get("round", 0)) or None
        actual_race_name = race_info.get("race_name", "Next Race")

    return is_auto_selected, actual_year, actual_round, actual_race_name


async def _track_calendar_analytics(
    *,
    lang: str,
    tz: str | None,
    year: int | None,
    race_round: int | None,
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
    )


def _get_pregenerated_calendar_path(
    *,
    lang: str,
    year: int | None,
    race_round: int | None,
    tz: str | None,
    display: str,
    weather_type: str,
) -> Path | None:
    if year is not None or race_round is not None:
        return None

    target_tz_for_key = tz or config.DEFAULT_TIMEZONE
    if target_tz_for_key != config.DEFAULT_TIMEZONE:
        return None

    # Build filename matching scheduler's _get_image_key format
    image_filename = "calendar_cs" if lang == "cs" else "calendar_en"
    if display == "spectra6":
        image_filename += "_spectra6"
    if weather_type != "off":
        image_filename += f"_weather_{weather_type}"
    image_filename += ".bmp"

    image_path = Path(config.IMAGES_PATH) / image_filename
    return image_path if image_path.exists() else None


def _get_race_data_from_static(
    f1_service: F1Service, year: int | None, race_round: int | None
) -> dict | None:
    if year and race_round:
        for race in f1_service.get_all_races_from_static(year):
            if int(race.get("round", 0)) == race_round:
                return race
        return None

    return f1_service.get_next_race_from_static()


def _maybe_convert_timezone(race_data: dict, target_tz: str) -> dict:
    cached_tz = race_data.get("timezone", config.DEFAULT_TIMEZONE)
    if cached_tz == target_tz:
        return race_data

    logger.debug("Converting times from %s to %s", cached_tz, target_tz)
    return convert_race_times_to_timezone(race_data, target_tz)


def _get_renderer(display: str, translator) -> Renderer | Spectra6Renderer:
    if display == "spectra6":
        return Spectra6Renderer(translator)
    return Renderer(translator)


def _render_calendar(
    *,
    f1_service: F1Service,
    lang: str,
    year: int | None,
    race_round: int | None,
    target_tz: str,
    weather: bool,
    weather_type: str,
    display: str,
) -> tuple[bytes, dict | None]:
    translator = get_translator(lang)

    race_data = _get_race_data_from_static(f1_service, year, race_round)
    if not race_data:
        renderer = _get_renderer(display, translator)
        return renderer.render_error("Failed to fetch race data"), None

    race_data = _maybe_convert_timezone(race_data, target_tz)

    circuit_id = race_data.get("circuit", {}).get("circuitId", "")
    historical_data = F1Service.get_historical_from_static(circuit_id) if circuit_id else None

    weather_data = None
    if weather and config.WEATHER_ENABLED:
        weather_data = get_cached_circuit_weather(circuit_id)

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
    tz: str | None = Query(default=None, description="Timezone"),
    weather: bool = Query(default=True, description="Show weather forecast"),
    weather_type: str = Query(
        default="race_day", description="Weather type: 'current' or 'race_day'"
    ),
    display: str = Query(
        default="1bit",
        description="Display type: '1bit' (800x480 monochrome) or 'spectra6' (800x480 6-color)",
    ),
    f1_service: F1Service = Depends(get_f1_service),
):
    start_time = time.time()
    user_agent = request.headers.get("User-Agent")
    referrer = request.headers.get("Referer", "")

    lang = _normalize_lang(lang)
    display = _normalize_display(display)
    _validate_timezone_param(tz)

    is_auto_selected, actual_year, actual_round, actual_race_name = _get_race_info_for_stats(
        f1_service, year, race_round
    )

    cache_key = _get_cache_key(lang, year, race_round, tz, weather, weather_type, display)
    cached_bmp = get_bmp_cache().get(cache_key)
    if cached_bmp is not None:
        logger.debug("Cache hit for %s", cache_key)
        _record_calendar_api_call(
            start_time=start_time,
            size_bytes=len(cached_bmp),
            lang=lang,
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
        bmp_data, race_data = _render_calendar(
            f1_service=f1_service,
            lang=lang,
            year=year,
            race_round=race_round,
            target_tz=target_tz,
            weather=weather,
            weather_type=weather_type,
            display=display,
        )
        get_bmp_cache()[cache_key] = bmp_data

        if race_data:
            actual_year = int(race_data.get("season", 0)) or actual_year
            actual_round = int(race_data.get("round", 0)) or actual_round
            actual_race_name = race_data.get("race_name", actual_race_name)

        _record_calendar_api_call(
            start_time=start_time,
            size_bytes=len(bmp_data),
            lang=lang,
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

        auto_selected = year is None and race_round is None
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
                "Cache-Control": "public, max-age=3600",
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
            headers={"Content-Disposition": 'inline; filename="teams.bmp"'},
        )
