"""Image endpoints returning BMPs for E-Ink displays."""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.config import LANGUAGE_CODES, config
from app.services import bmp_artifacts
from app.services.artifact_metadata import artifact_matches, calendar_identity
from app.services.auto_screen import (
    ACTIVE_SCREEN_HEADER,
    DEFAULT_LEAD_HOURS,
    MAX_LEAD_HOURS,
    AutoMode,
    AutoScreen,
    ScreenChoice,
    choose_screen,
)
from app.services.f1_service import NEXT_RACE_GRACE, F1Service
from app.services.generation_freshness import PREGENERATED_MAX_AGE_SECONDS
from app.services.image_generation import (
    StaleSelection,
    selected_generation,
    with_current_selection,
)
from app.services.image_keys import get_calendar_image_key, get_teams_image_key
from app.services.renderers import DISPLAY_TYPES
from app.services.teams_extra import (
    TeamsExtra,
    build_teams_extra_context,
    points_session_starts,
)
from app.services.teams_service import (
    get_default_teams_year,
)
from app.services.track_catalog import (
    DEFAULT_TRACK_OPTIONS,
    TrackAccent,
    TrackOptions,
    TrackSource,
    TrackStyle,
)
from app.services.track_renderer import attribution_headers
from app.state import (
    BmpArtifact,
    ImageCache,
    record_api_call,
)
from app.utils.etag import if_none_match_matches, read_etag_sidecar, strong_etag
from app.utils.f1_season import race_selection_error
from app.utils.rate_limit import enforce_rate_limit
from app.utils.refresh_hint import (
    ERROR_REFRESH_SECONDS,
    MAX_REFRESH_SECONDS,
    MIN_REFRESH_SECONDS,
    REFRESH_HINT_HEADER,
    CountdownMode,
    RefreshHint,
    calendar_refresh_hint,
    teams_refresh_hint,
)
from app.utils.timezones import get_timezone, is_valid_timezone, normalize_timezone

from .deps import get_f1_service
from .params import (
    calendar_cache_key,
    canonical_weather_type,
    normalize_display,
    normalize_lang,
    validate_calendar_selection,
    validate_timezone,
    weather_file_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Revalidation also prevents day-based optional panels surviving local midnight.
TEAMS_BMP_CACHE_CONTROL = "no-cache"
CALENDAR_BMP_CACHE_CONTROL = "public, max-age=3600"
# X-Cache values: HIT (in-process cache, including its 304s), PREGENERATED (scheduler-written
# file on disk), REVALIDATED (304 against a pregenerated file's ETag) and MISS (rendered for
# this request). Pregenerated files are not copied into the in-process cache, so serving one
# never adds a stale entry. An earlier on-demand render can still sit in that cache, and it
# is checked first; the scheduler clears it at the end of each generation run (or the TTL
# expires if a run fails before that).
PREGENERATED_CACHE_STATUS = "PREGENERATED"
# Panels that print a day count, a date or the next rounds, so change at local midnight.
_DAY_BASED_TEAMS_EXTRAS = frozenset({TeamsExtra.COUNTDOWN, TeamsExtra.FLAGS, TeamsExtra.TRACK})
_MAX_AGE_PATTERN = re.compile(r"\bmax-age=(\d+)")


def _utc_now() -> datetime:
    """Return the current instant for refresh hints (a seam for tests)."""
    return datetime.now(UTC)


def _calendar_poll_seconds(
    hint: RefreshHint | None,
    countdown: CountdownMode,
    cache: ImageCache | None,
    key: str,
    status: str | None,
) -> int:
    """Evaluate the source deadline immediately before returning a calendar response."""
    if hint is None:
        return ERROR_REFRESH_SECONDS
    if (
        not config.SCHEDULER_ENABLED
        and countdown == CountdownMode.HOURS
        and cache is not None
        and status in ("HIT", "MISS")
        and key in cache
    ):
        return hint.seconds(
            _utc_now(), cache_seconds=cache.remaining_seconds(key), cache_ttl=cache.ttl
        )
    return hint.seconds(_utc_now())


def _calendar_cache_control(countdown: CountdownMode, hint: RefreshHint) -> str:
    """Return the calendar Cache-Control; countdown variants never outlive their next change.

    The default (hours) keeps its historical one-hour lifetime. A day count, ``off`` or ``auto``
    image is keyed by its content period, so a browser or shared cache must not reuse it past
    local midnight, the race start or the auto switch.
    """
    if countdown == CountdownMode.HOURS:
        return CALENDAR_BMP_CACHE_CONTROL
    return f"public, max-age={hint.max_age(_utc_now())}"


def _to_round_number(round_value: str | int | None) -> int | None:
    """Convert a round identifier to a positive integer when possible."""
    if round_value is None or round_value == "":
        return None

    try:
        round_number = int(round_value)
    except TypeError, ValueError:
        return None

    return round_number if round_number > 0 else None


def _get_race_info_for_stats(
    f1_service: F1Service,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
) -> tuple[bool, int | None, int | None, str | None]:
    """Resolve the race metadata that should be recorded in call statistics."""
    is_auto_selected = year is None and race_round is None and race_key is None
    actual_year = year
    actual_round = race_round
    actual_race_name: str | None = None

    if race_key is not None and year is None:
        return is_auto_selected, actual_year, actual_round, actual_race_name

    # Compare with None, not truthiness: round=0, year=0 and race_key= are invalid explicit
    # selections (error image), never a request for the next race.
    if year is not None and (race_round is not None or race_key is not None):
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


def _record_calendar_api_call(
    *,
    endpoint: str = "/calendar.bmp",
    start_time: float,
    size_bytes: int,
    lang: str,
    display: str,
    tz: str | None,
    actual_year: int | None,
    actual_round: int | None,
    actual_race_name: str | None,
    is_auto_selected: bool,
    status_code: int,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
) -> None:
    """Persist a calendar API call with render metadata."""
    record_api_call(
        endpoint,
        (time.time() - start_time) * 1000,
        size_bytes,
        lang,
        tz,
        actual_year,
        actual_round,
        actual_race_name,
        is_auto_selected,
        display_type=display,
        status_code=status_code,
        track_style=track_options.style,
        track_source=track_options.source,
        track_accent=track_options.accent,
    )


def _record_teams_api_call(
    *,
    endpoint: str = "/teams.bmp",
    start_time: float,
    size_bytes: int,
    lang: str,
    display: str,
    year: int | None,
    status_code: int,
) -> None:
    """Persist a teams API call with the same metadata shape as calendar calls."""
    record_api_call(
        endpoint,
        (time.time() - start_time) * 1000,
        size_bytes,
        lang,
        None,
        year,
        None,
        None,
        False,
        display_type=display,
        status_code=status_code,
    )


def _bmp_response(
    request: Request,
    *,
    content: bytes | None,
    etag: str,
    filename: str,
    cache_control: str,
    record_call: Callable[[int, int], None],
    cache_status: str | None = None,
    refresh_seconds: int | None = None,
) -> Response:
    """Build a 200/304 BMP response and record the call exactly once.

    ``refresh_seconds`` becomes the ``X-Next-Refresh-Seconds`` hint on both 200 and 304.
    """
    not_modified = if_none_match_matches(request.headers.get("If-None-Match"), etag)
    status_code = 304 if not_modified else 200
    body = b"" if not_modified else (content or b"")
    headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "Cache-Control": cache_control,
        "ETag": etag,
    }
    if cache_status is not None:
        headers["X-Cache"] = cache_status
    if refresh_seconds is not None:
        headers[REFRESH_HINT_HEADER] = str(refresh_seconds)

    record_call(status_code, len(body))

    return Response(
        content=body,
        status_code=status_code,
        media_type="image/bmp" if status_code == 200 else None,
        headers=headers,
    )


def _calendar_response(
    request: Request,
    *,
    content: bytes | None,
    etag: str,
    start_time: float,
    lang: str,
    display: str,
    tz: str | None,
    actual_year: int | None,
    actual_round: int | None,
    actual_race_name: str | None,
    is_auto_selected: bool,
    cache_control: str,
    cache_status: str | None,
    track_options: TrackOptions = DEFAULT_TRACK_OPTIONS,
    refresh_seconds: int | None = None,
    endpoint: str = "/calendar.bmp",
) -> Response:
    """Apply the shared calendar response and call-record epilogue."""
    return _bmp_response(
        request,
        content=content,
        etag=etag,
        filename="calendar.bmp",
        cache_control=cache_control,
        cache_status=cache_status,
        refresh_seconds=refresh_seconds,
        record_call=lambda status, size: _record_calendar_api_call(
            endpoint=endpoint,
            start_time=start_time,
            size_bytes=size,
            lang=lang,
            display=display,
            tz=tz,
            actual_year=actual_year,
            actual_round=actual_round,
            actual_race_name=actual_race_name,
            is_auto_selected=is_auto_selected,
            status_code=status,
            track_options=track_options,
        ),
    )


def _teams_response(
    request: Request,
    *,
    content: bytes | None,
    etag: str,
    start_time: float,
    lang: str,
    display: str,
    year: int | None,
    cache_control: str = TEAMS_BMP_CACHE_CONTROL,
    cache_status: str | None = None,
    refresh_seconds: int | None = None,
    endpoint: str = "/teams.bmp",
) -> Response:
    """Apply the shared teams response and call-record epilogue."""
    response = _bmp_response(
        request,
        content=content,
        etag=etag,
        filename="teams.bmp",
        cache_control=cache_control,
        cache_status=cache_status,
        refresh_seconds=refresh_seconds,
        record_call=lambda status, size: _record_teams_api_call(
            endpoint=endpoint,
            start_time=start_time,
            size_bytes=size,
            lang=lang,
            display=display,
            year=year,
            status_code=status,
        ),
    )

    response.headers["Link"] = f'<{config.SITE_URL.rstrip("/")}/credits>; rel="license"'
    return response


# Pre-defined whitelist of valid generated filenames (no user input in paths)
# Serve a pregenerated file only while it is plausibly current. Generation runs hourly, so a
# healthy file is at most ~1h old; 6h tolerates a few failed runs while bounding how long a
# stale file (e.g. a timezone that dropped out of the popular set, or a variant whose render
# keeps failing) can keep showing the previous race before falling back to on-demand rendering.
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
    if time.time() - mtime > PREGENERATED_MAX_AGE_SECONDS:
        return None
    return Path(resolved)


def _read_pregenerated_artifact_snapshot(
    image_path: Path,
    if_none_match: str | None,
    expected_identity: str | None = None,
) -> tuple[BmpArtifact | None, str | None]:
    """Read one pregenerated generation without mixing its sidecar and BMP body.

    A matching sidecar is read twice before taking the body-free 304 path. When a body is
    needed, the sidecar is checked again after the read and trusted only if it did not change.
    Atomic scheduler replacements can therefore linearize before or after this snapshot, but
    cannot pair bytes from one generation with the ETag from another.
    """
    if expected_identity is not None and not artifact_matches(image_path, expected_identity):
        return None, None
    sidecar_etag = read_etag_sidecar(image_path)
    if sidecar_etag is not None and if_none_match_matches(if_none_match, sidecar_etag):
        confirmed_etag = read_etag_sidecar(image_path)
        if confirmed_etag == sidecar_etag:
            if expected_identity is not None and not artifact_matches(
                image_path, expected_identity
            ):
                return None, None
            return None, sidecar_etag
        sidecar_etag = confirmed_etag

    try:
        bmp_data = image_path.read_bytes()
    except OSError:
        return None, None

    confirmed_etag = read_etag_sidecar(image_path)
    etag = (
        sidecar_etag
        if sidecar_etag is not None and confirmed_etag == sidecar_etag
        else strong_etag(bmp_data)
    )
    if expected_identity is not None and not artifact_matches(image_path, expected_identity):
        return None, None
    return (bmp_data, etag), None


async def _read_pregenerated_artifact(
    image_path: Path,
    if_none_match: str | None,
    expected_identity: str | None = None,
) -> tuple[BmpArtifact | None, str | None]:
    """Read and revalidate a pregenerated artifact outside the event-loop thread."""
    return await asyncio.to_thread(
        _read_pregenerated_artifact_snapshot,
        image_path,
        if_none_match,
        expected_identity,
    )


def _get_valid_teams_filenames(year: int) -> dict[tuple[str, str], str]:
    """Return none-only filenames; optional panels are rendered on demand."""
    return {
        (lang, display): f"{get_teams_image_key(lang, year, display=display)}.bmp"
        for lang in LANGUAGE_CODES
        for display in DISPLAY_TYPES
    }


def _is_pregenerated_selection(
    *,
    requested_year: int | None,
    pregenerated_year: int | None = None,
    race_round: int | None = None,
    race_key: str | None = None,
) -> bool:
    """Return whether a request can use the deterministic pregenerated selection."""
    if race_round is not None or race_key is not None:
        return False
    return requested_year is None or (
        pregenerated_year is not None and requested_year == pregenerated_year
    )


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
    expected_identity: str | None = None,
) -> Path | None:
    """Return a pregenerated calendar BMP path when the request matches one."""
    if not _is_pregenerated_selection(
        requested_year=year,
        race_round=race_round,
        race_key=race_key,
    ):
        return None

    # Normalize inputs to allowed values; the filename is then derived solely from these
    # enum-like values plus a validated IANA timezone, so no raw user input reaches the path.
    safe_lang = normalize_lang(lang)
    safe_display = normalize_display(display)
    key_weather = weather_file_key(canonical_weather_type(weather_type, weather=weather))

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
    path = _fresh_pregenerated_path(Path(config.IMAGES_PATH) / f"{image_key}.bmp")
    identity = (
        expected_identity
        if expected_identity is not None
        else calendar_identity(F1Service(timezone_name=target_tz).get_next_race_from_static())
    )
    return path if path is not None and artifact_matches(path, identity) else None


def _get_pregenerated_teams_path(*, lang: str, year: int | None, display: str) -> Path | None:
    """Return a pregenerated teams BMP path when the request matches one."""
    default_year = get_default_teams_year()
    if not _is_pregenerated_selection(
        requested_year=year,
        pregenerated_year=default_year,
    ):
        return None

    safe_lang = normalize_lang(lang)
    safe_display = normalize_display(display)
    filename = _get_valid_teams_filenames(default_year).get((safe_lang, safe_display))
    if not filename:
        return None

    path = _fresh_pregenerated_path(Path(config.IMAGES_PATH) / filename)
    return path if path is not None and artifact_matches(path, f"teams:{default_year}") else None


async def _select_calendar_race(
    f1_service: F1Service, year: int | None, race_round: int | None, race_key: str | None
) -> dict | None:
    """Resolve the current selection, fetching a valid explicit round when absent locally."""
    selected_race = _get_race_data_from_static(f1_service, year, race_round, race_key)
    if (
        selected_race is None
        and year is not None
        and race_round is not None
        and race_selection_error(year, race_round) is None
    ):
        selected_race = await f1_service.get_race_by_round(year, race_round)
    return selected_race


def _get_race_data_from_static(
    f1_service: F1Service,
    year: int | None,
    race_round: int | None,
    race_key: str | None,
) -> dict | None:
    """Load race data for either an explicit selection or the next race."""
    if race_key is not None and year is None:
        return None

    # Compare with None, not truthiness: round=0, year=0 and race_key= are invalid explicit
    # selections (error image), never a request for the next race.
    if year is not None and (race_round is not None or race_key is not None):
        for race in f1_service.get_all_races_from_static(year):
            if race_key and race.get("race_key") == race_key:
                return race
            if race_round is not None and _to_round_number(race.get("round")) == race_round:
                return race
        return None

    return f1_service.get_next_race_from_static()


_LANG_DESCRIPTION = f"Language code ({', '.join(LANGUAGE_CODES)})"
_DISPLAY_DESCRIPTION = (
    f"Display type: '1bit' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} monochrome), "
    f"'spectra6' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} 6-color), "
    f"'bwr' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red), "
    f"or 'bwry' ({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} black/white/red/yellow)"
)
_WEATHER_TYPE_DESCRIPTION = "Weather type: 'race_day' (alias 'race'), 'current', or 'off'"
_COUNTDOWN_DESCRIPTION = (
    "Countdown: hours (default, days and hours), days (whole days), "
    "auto (days until 48 h before the start, then days and hours) or off (race date)"
)
_TEAMS_EXTRA_DESCRIPTION = (
    "Teams panel: none, countdown, flags (next 3 races), track, weather; "
    "timeline/combo alias countdown"
)


@with_current_selection
async def _serve_calendar_bmp(
    request: Request,
    *,
    f1_service: F1Service,
    start_time: float,
    lang: str,
    display: str,
    tz: str | None,
    weather_type: str,
    track_options: TrackOptions,
    countdown: CountdownMode,
    year: int | None = None,
    race_round: int | None = None,
    race_key: str | None = None,
    endpoint: str = "/calendar.bmp",
) -> Response:
    """Serve one calendar BMP from validated, normalized inputs.

    Shared by ``/calendar.bmp`` and ``/auto.bmp``: the in-process caches, the pregenerated
    files, singleflight rendering, ETag/304 handling and the error image are the same, so
    equal parameters always produce the same bytes from the same cache entry. ``endpoint``
    only names the route in the aggregate call statistics.
    """
    weather = weather_type != "off"
    # Defaults let the error path build a response even when selection itself fails.
    is_auto_selected = year is None and race_round is None and race_key is None
    actual_year, actual_round = year, race_round
    actual_race_name: str | None = None
    selected_race: dict | None = None
    # Until the selection is known (and on the error image) ask for a retry soon.
    poll_hint: RefreshHint | None = None
    image_cache: ImageCache | None = None
    cache_key = ""

    def respond(
        content: bytes | None,
        etag: str,
        *,
        cache_control: str,
        cache_status: str | None,
    ) -> Response:
        """Build the calendar response with the request's normalized statistics context."""
        response = _calendar_response(
            request,
            content=content,
            etag=etag,
            start_time=start_time,
            lang=lang,
            display=display,
            tz=tz,
            actual_year=actual_year,
            actual_round=actual_round,
            actual_race_name=actual_race_name,
            is_auto_selected=is_auto_selected,
            cache_control=cache_control,
            cache_status=cache_status,
            track_options=track_options,
            refresh_seconds=_calendar_poll_seconds(
                poll_hint, countdown, image_cache, cache_key, cache_status
            ),
            endpoint=endpoint,
        )
        response.headers.update(
            attribution_headers(
                (selected_race or {}).get("circuit", {}).get("circuitId", ""),
                track_options,
            )
        )
        return response

    # Everything after input validation is guarded: a validated BMP request must always get
    # an image back (error BMP on failure), never a text/plain 500 from selection or disk I/O.
    try:
        is_auto_selected, actual_year, actual_round, actual_race_name = _get_race_info_for_stats(
            f1_service, year, race_round, race_key
        )
        selected_race = await _select_calendar_race(f1_service, year, race_round, race_key)
        identity = calendar_identity(selected_race, track_options)
        hint = calendar_refresh_hint(
            selected_race,
            now=_utc_now(),
            timezone_name=tz,
            countdown=countdown,
            weather_type=weather_type,
            weather_enabled=config.WEATHER_ENABLED,
            selection_grace=NEXT_RACE_GRACE if race_round is None and race_key is None else None,
        )
        # Without a race the route serves the "failed to fetch" error image: retry soon.
        poll_hint = hint if selected_race else None
        cache_key = f"{identity}|" + calendar_cache_key(
            lang, year, race_round, race_key, tz, weather_type, display
        )
        if countdown != CountdownMode.HOURS:
            # The content period keeps a cached day count from outliving local midnight.
            cache_key += f":countdown={countdown}:{hint.cache_token()}"
        image_cache = bmp_artifacts.calendar_image_cache(countdown)
        cached_artifact = image_cache.get(cache_key)
        if cached_artifact is not None:
            logger.debug("Cache hit for %s", cache_key)
            return respond(
                *cached_artifact,
                cache_control=_calendar_cache_control(countdown, hint),
                cache_status="HIT",
            )

        image_path = (
            _get_pregenerated_calendar_path(
                lang=lang,
                year=year,
                race_round=race_round,
                race_key=race_key,
                tz=tz,
                display=display,
                weather=weather,
                weather_type=weather_type,
                expected_identity=identity,
            )
            if track_options == DEFAULT_TRACK_OPTIONS and countdown == CountdownMode.HOURS
            else None
        )
        if image_path is not None:
            logger.debug("Serving pre-generated image: %s", image_path)
            artifact, matching_etag = await _read_pregenerated_artifact(
                image_path,
                request.headers.get("If-None-Match"),
                identity,
            )
            if matching_etag is not None:
                return respond(
                    None,
                    matching_etag,
                    cache_control=CALENDAR_BMP_CACHE_CONTROL,
                    cache_status="REVALIDATED",
                )
            if artifact is not None:
                return respond(
                    *artifact,
                    cache_control=CALENDAR_BMP_CACHE_CONTROL,
                    cache_status=PREGENERATED_CACHE_STATUS,
                )
            logger.info("Pre-generated image disappeared before read; rendering")

        artifact = await bmp_artifacts.singleflight(
            f"calendar:{selected_generation()}:{cache_key}",
            functools.partial(
                bmp_artifacts.render_calendar_artifact,
                cache_key=cache_key,
                race_data=selected_race,
                lang=lang,
                target_tz=tz or config.DEFAULT_TIMEZONE,
                weather_type=weather_type,
                display=display,
                track_options=track_options,
                countdown_mode=countdown,
            ),
        )
        is_cacheable = image_cache.get(cache_key) is artifact
        return respond(
            *artifact,
            cache_control=_calendar_cache_control(countdown, hint) if is_cacheable else "no-store",
            cache_status="MISS",
        )
    except Exception as exc:
        if isinstance(exc, (HTTPException, StaleSelection)):
            raise
        logger.error("Error generating calendar: %s", exc, exc_info=True)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(exc)
        bmp_data, etag = await bmp_artifacts.shared_error_artifact(
            display, lang, "Temporary rendering error"
        )
        poll_hint = None
        return respond(
            bmp_data,
            etag,
            cache_control="no-store",
            cache_status=None,
        )


@with_current_selection
async def _serve_teams_bmp(
    request: Request,
    *,
    start_time: float,
    lang: str,
    display: str,
    tz: str | None,
    year: int | None,
    extra: TeamsExtra,
    endpoint: str = "/teams.bmp",
) -> Response:
    """Serve one teams BMP from validated, normalized inputs.

    Shared by ``/teams.bmp`` and ``/auto.bmp`` with the same caches, pregenerated files,
    singleflight rendering, ETag/304 handling and error image.
    """

    try:
        if year is None:
            year = get_default_teams_year()

        def respond(
            content: bytes | None,
            etag: str,
            *,
            cache_control: str = TEAMS_BMP_CACHE_CONTROL,
            cache_status: str | None = None,
        ) -> Response:
            """Build the teams response with the request's normalized context."""
            return _teams_response(
                request,
                content=content,
                etag=etag,
                start_time=start_time,
                lang=lang,
                display=display,
                year=year,
                cache_control=cache_control,
                cache_status=cache_status,
                refresh_seconds=hint.seconds(_utc_now()),
                endpoint=endpoint,
            )

        ctx = (
            build_teams_extra_context(extra, year, timezone_name=tz)
            if extra != TeamsExtra.NONE
            else None
        )
        effective = extra if ctx is not None else TeamsExtra.NONE
        hint = teams_refresh_hint(
            now=_utc_now(),
            points_sessions=points_session_starts(year),
            timezone_name=tz,
            day_panel=effective in _DAY_BASED_TEAMS_EXTRAS,
            hourly=effective == TeamsExtra.WEATHER,
        )
        cache_key = get_teams_image_key(
            lang, year, display=display, extra=effective, extra_context=ctx
        )
        cached_artifact = bmp_artifacts.teams_image_cache(effective).get(cache_key)
        if cached_artifact is not None:
            return respond(*cached_artifact, cache_status="HIT")

        image_path = (
            _get_pregenerated_teams_path(lang=lang, year=year, display=display)
            if effective == TeamsExtra.NONE
            else None
        )
        if image_path is not None:
            artifact, matching_etag = await _read_pregenerated_artifact(
                image_path,
                request.headers.get("If-None-Match"),
                f"teams:{year}",
            )
            if matching_etag is not None:
                return respond(None, matching_etag, cache_status="REVALIDATED")
            if artifact is not None:
                return respond(*artifact, cache_status=PREGENERATED_CACHE_STATUS)
            logger.info("Pre-generated teams image disappeared before read; rendering")

        artifact = await bmp_artifacts.singleflight(
            f"teams:{selected_generation()}:{cache_key}",
            functools.partial(
                bmp_artifacts.render_teams_artifact,
                cache_key=cache_key,
                lang=lang,
                year=year,
                display=display,
                extra=effective,
                extra_context=ctx,
            ),
        )
        return respond(*artifact, cache_status="MISS")

    except Exception as exc:
        if isinstance(exc, (HTTPException, StaleSelection)):
            raise
        logger.error("Error generating teams: %s", exc, exc_info=True)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(exc)
        bmp_data, etag = await bmp_artifacts.shared_error_artifact(
            display, lang, "Temporary rendering error"
        )
        return _teams_response(
            request,
            content=bmp_data,
            etag=etag,
            start_time=start_time,
            lang=lang,
            display=display,
            year=year,
            cache_control="no-store",
            refresh_seconds=ERROR_REFRESH_SECONDS,
            endpoint=endpoint,
        )


@router.get("/calendar.bmp")
async def get_calendar_bmp(
    request: Request,
    lang: str = Query(default="en", description=_LANG_DESCRIPTION),
    year: int | None = Query(
        default=None, description="Season year; combine with round or race_key"
    ),
    race_round: int | None = Query(
        default=None,
        description="Round from /api/races/{year}; ignored without year",
        alias="round",
    ),
    race_key: str | None = Query(
        default=None,
        description="Race identifier from /api/races/{year}; requires year",
        alias="race_key",
    ),
    tz: str | None = Query(
        default=None, description="IANA timezone; defaults to the server's DEFAULT_TIMEZONE"
    ),
    weather: bool = Query(default=True, description="Show weather forecast"),
    weather_type: str = Query(default="race_day", description=_WEATHER_TYPE_DESCRIPTION),
    display: str = Query(default="1bit", description=_DISPLAY_DESCRIPTION),
    f1_service: F1Service = Depends(get_f1_service),
    track_style: TrackStyle = TrackStyle.RELIEF,
    track_source: TrackSource = TrackSource.JULES,
    track_accent: TrackAccent = TrackAccent.ALL,
    countdown: Annotated[
        CountdownMode, Query(description=_COUNTDOWN_DESCRIPTION)
    ] = CountdownMode.HOURS,
):
    """Render the calendar endpoint for the requested display and selection."""
    start_time = time.time()
    enforce_rate_limit(request, bucket="calendar_bmp", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    tz = validate_timezone(tz)
    validate_calendar_selection(year, race_key)
    return await _serve_calendar_bmp(
        request,
        f1_service=f1_service,
        start_time=start_time,
        lang=normalize_lang(lang),
        display=normalize_display(display),
        tz=tz,
        # One canonical variant for weather=false / weather_type=off and for race / race_day.
        weather_type=canonical_weather_type(weather_type, weather=weather),
        track_options=TrackOptions(track_style, track_source, track_accent),
        countdown=countdown,
        year=year,
        # Documented as ignored without year; dropping it keeps the cache key and the
        # pregenerated-file match identical to the plain auto-selected request.
        race_round=race_round if year is not None else None,
        race_key=race_key,
    )


@router.get("/teams.bmp")
async def get_teams_bmp(
    request: Request,
    lang: str = Query(default="en", description=_LANG_DESCRIPTION),
    year: int | None = Query(default=None, description="Season year"),
    tz: Annotated[
        str | None,
        Query(
            description="IANA timezone for extra panels; defaults to the server's DEFAULT_TIMEZONE"
        ),
    ] = None,
    extra: Annotated[TeamsExtra, Query(description=_TEAMS_EXTRA_DESCRIPTION)] = TeamsExtra.NONE,
    display: str = Query(default="1bit", description=_DISPLAY_DESCRIPTION),
):
    """Render the teams and drivers dashboard as a BMP image."""
    start_time = time.time()
    enforce_rate_limit(request, bucket="teams_bmp", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    return await _serve_teams_bmp(
        request,
        start_time=start_time,
        lang=normalize_lang(lang),
        display=normalize_display(display),
        tz=validate_timezone(tz),
        year=year,
        extra=extra,
    )


def _choose_auto_screen(
    mode: AutoMode,
    *,
    now: datetime,
    f1_service: F1Service,
    lead_hours: int,
    tz: str | None,
) -> ScreenChoice:
    """Pick the /auto.bmp screen; a failing schedule lookup falls back to teams."""
    try:
        race = f1_service.get_next_race_from_static() if mode == AutoMode.RACE_WEEK else None
        return choose_screen(
            mode,
            now=now,
            race_data=race,
            lead_hours=lead_hours,
            zone=get_timezone(tz or config.DEFAULT_TIMEZONE),
        )
    except Exception as exc:
        logger.error("Error choosing the auto screen: %s", exc, exc_info=True)
        if not config.MINIMAL_DATA_MODE:
            sentry_sdk.capture_exception(exc)
        return ScreenChoice(AutoScreen.TEAMS, now + timedelta(seconds=ERROR_REFRESH_SECONDS))


def _clamp_max_age(cache_control: str, max_age: int) -> str:
    """Lower an existing ``max-age`` directive to at most ``max_age`` seconds."""
    return _MAX_AGE_PATTERN.sub(
        lambda match: f"max-age={min(int(match.group(1)), max_age)}", cache_control
    )


def _apply_auto_screen(response: Response, choice: ScreenChoice, now: datetime) -> Response:
    """Label the active screen and keep hints and caches from outliving the next switch.

    ``X-Next-Refresh-Seconds`` becomes the earlier of the screen's own hint and the switch,
    and a shared cache may keep the image at most until the switch. The ETag stays the
    screen's own content hash, so a 304 is only possible while the client already shows the
    active screen.
    """
    response.headers[ACTIVE_SCREEN_HEADER] = choice.screen.value
    if choice.next_switch is None:
        return response
    until_switch = (choice.next_switch - now).total_seconds()
    # No settling delay: the switch itself needs no new pregenerated image, and the hint is
    # documented never to pass it (apart from the MIN_REFRESH_SECONDS floor).
    switch_hint = max(MIN_REFRESH_SECONDS, math.ceil(until_switch))
    screen_hint = int(response.headers.get(REFRESH_HINT_HEADER, MAX_REFRESH_SECONDS))
    response.headers[REFRESH_HINT_HEADER] = str(min(screen_hint, switch_hint))
    response.headers["Cache-Control"] = _clamp_max_age(
        response.headers["Cache-Control"], max(0, math.floor(until_switch))
    )
    return response


@router.get("/auto.bmp")
async def get_auto_bmp(
    request: Request,
    mode: Annotated[
        AutoMode,
        Query(
            description="race_week (default): calendar from lead_hours before the first "
            "session until the next race is selected, teams otherwise; daily: calendar on "
            "even local days, teams on odd local days in tz"
        ),
    ] = AutoMode.RACE_WEEK,
    lead_hours: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_LEAD_HOURS,
            description="Hours before the first session when race_week shows the calendar",
        ),
    ] = DEFAULT_LEAD_HOURS,
    lang: str = Query(default="en", description=_LANG_DESCRIPTION),
    tz: str | None = Query(
        default=None,
        description="IANA timezone for both screens and the daily switch; defaults to the "
        "server's DEFAULT_TIMEZONE",
    ),
    display: str = Query(default="1bit", description=_DISPLAY_DESCRIPTION),
    weather: bool = Query(default=True, description="Calendar: show weather forecast"),
    weather_type: str = Query(
        default="race_day", description=f"Calendar: {_WEATHER_TYPE_DESCRIPTION}"
    ),
    track_style: TrackStyle = TrackStyle.RELIEF,
    track_source: TrackSource = TrackSource.JULES,
    track_accent: TrackAccent = TrackAccent.ALL,
    countdown: Annotated[
        CountdownMode, Query(description=f"Calendar: {_COUNTDOWN_DESCRIPTION}")
    ] = CountdownMode.HOURS,
    extra: Annotated[
        TeamsExtra | None,
        Query(description=f"Teams: {_TEAMS_EXTRA_DESCRIPTION}; default countdown"),
    ] = None,
    year: Annotated[
        int | None, Query(description="Teams: season year; the calendar always shows the next race")
    ] = None,
    f1_service: F1Service = Depends(get_f1_service),
):
    """Serve the calendar or the teams BMP on one URL, switching by race week or by day."""
    start_time = time.time()
    enforce_rate_limit(request, bucket="auto_bmp", limit=config.IMAGE_RATE_LIMIT_PER_MINUTE)
    lang = normalize_lang(lang)
    display = normalize_display(display)
    tz = validate_timezone(tz)
    now = _utc_now()
    choice = _choose_auto_screen(mode, now=now, f1_service=f1_service, lead_hours=lead_hours, tz=tz)
    if choice.screen == AutoScreen.CALENDAR:
        response = await _serve_calendar_bmp(
            request,
            f1_service=f1_service,
            start_time=start_time,
            lang=lang,
            display=display,
            tz=tz,
            weather_type=canonical_weather_type(weather_type, weather=weather),
            track_options=TrackOptions(track_style, track_source, track_accent),
            countdown=countdown,
            endpoint="/auto.bmp",
        )
    else:
        response = await _serve_teams_bmp(
            request,
            start_time=start_time,
            lang=lang,
            display=display,
            tz=tz,
            year=year,
            extra=TeamsExtra.COUNTDOWN if extra is None else extra,
            endpoint="/auto.bmp",
        )
    # A fresh clock reading: time spent rendering must not extend max-age past the switch.
    return _apply_auto_screen(response, choice, _utc_now())
