"""JSON API routes."""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import LANGUAGE_CODES, config
from app.models import PerfMetricsPayload
from app.services.analytics import track_event
from app.services.database import get_database
from app.services.f1_service import F1Service
from app.services.renderers import DISPLAY_TYPES
from app.services.teams_service import TeamsService
from app.state import get_bmp_cache
from app.utils.async_tasks import create_supervised_task
from app.utils.f1_season import get_current_f1_season, is_supported_f1_season
from app.utils.rate_limit import enforce_rate_limit
from app.utils.standings_metadata import TEAM_ID_MAP, get_driver_number
from app.version import APP_VERSION

from .deps import get_f1_service

logger = logging.getLogger(__name__)

router = APIRouter()
_LANGUAGE_VALUES = list(LANGUAGE_CODES)
_DISPLAY_VALUES = list(DISPLAY_TYPES)
_MAX_USER_AGENT_LENGTH = 500


def _matches_round(race: dict, round_num: int) -> bool:
    """Return whether a race payload contains the requested numeric round."""
    round_value = race.get("round")
    if not isinstance(round_value, (str, int, float)):
        return False
    try:
        return int(round_value) == round_num
    except TypeError, ValueError:
        return False


def _require_supported_f1_season(year: int) -> None:
    """Raise HTTP 422 when a requested season falls outside supported bounds."""
    if not is_supported_f1_season(year):
        raise HTTPException(status_code=422, detail="Unsupported F1 season")


def _require_operational_api_auth(request: Request) -> None:
    """Gate operational read APIs (stats / perf-metrics) behind a token when one is configured.

    These endpoints mirror data already shown on the public ``/stats`` dashboard, so they are
    intentionally public by default. Set ``ADMIN_API_TOKEN`` to require ``X-Admin-Token`` /
    ``Authorization: Bearer`` and lock them down — until then access is open by design, not by
    oversight.
    """
    configured_token = config.ADMIN_API_TOKEN
    if configured_token is None:
        return

    expected = configured_token.get_secret_value()
    if not expected:
        logger.error("ADMIN_API_TOKEN is configured but empty")
        raise HTTPException(status_code=503, detail="Operational API authentication unavailable")
    provided = request.headers.get("X-Admin-Token")

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        provided = authorization.removeprefix("Bearer ")

    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/api")
@router.get("/api/docs")
async def api_info() -> dict:
    """API documentation endpoint."""
    return {
        "service": "F1 E-Ink Calendar API",
        "version": APP_VERSION,
        "description": (
            f"Generate {config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT} BMP images "
            "for E-Ink displays showing F1 race schedules"
        ),
        "endpoints": {
            "/": {
                "method": "GET",
                "description": "Interactive preview page with live image generation",
            },
            "/calendar.bmp": {
                "method": "GET",
                "description": (
                    f"Generate F1 calendar as BMP image "
                    f"({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT})"
                ),
                "parameters": {
                    "lang": {
                        "type": "string",
                        "description": "Language code for calendar text",
                        "values": _LANGUAGE_VALUES,
                        "default": "en",
                        "example": "?lang=cs",
                    },
                    "year": {
                        "type": "integer",
                        "description": "Season year for specific race",
                        "example": "?year=2025",
                        "optional": True,
                    },
                    "round": {
                        "type": "integer",
                        "description": "Round number (1-24) for specific race",
                        "example": "?round=5",
                        "optional": True,
                    },
                    "tz": {
                        "type": "string",
                        "description": "Timezone for schedule times (IANA format)",
                        "example": "?tz=America/New_York",
                        "default": "Europe/Prague",
                        "optional": True,
                    },
                    "display": {
                        "type": "string",
                        "description": "Display output mode",
                        "values": _DISPLAY_VALUES,
                        "default": "1bit",
                        "example": "?display=bwry",
                        "optional": True,
                    },
                    "weather": {
                        "type": "boolean",
                        "description": "Enable or disable weather overlay",
                        "values": [True, False],
                        "default": True,
                        "example": "?weather=false",
                        "optional": True,
                    },
                    "weather_type": {
                        "type": "string",
                        "description": (
                            "Weather source to render "
                            "('race' normalizes to 'race_day'; 'off' disables weather)"
                        ),
                        "values": ["race_day", "race", "current", "off"],
                        "default": "race_day",
                        "example": "?weather_type=current",
                        "optional": True,
                    },
                },
                "response": {
                    "content_type": "image/bmp",
                    "dimensions": f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}",
                    "color_depth": (
                        "1-bit monochrome, 4-bit indexed B/W/R, "
                        "4-bit indexed B/W/R/Y, or indexed Spectra 6"
                    ),
                },
                "examples": [
                    "/calendar.bmp",
                    "/calendar.bmp?lang=cs",
                    "/calendar.bmp?year=2025&round=1",
                    "/calendar.bmp?lang=en&tz=America/Los_Angeles",
                    "/calendar.bmp?display=bwr",
                    "/calendar.bmp?display=bwry",
                    "/calendar.bmp?display=spectra6&weather_type=current",
                ],
            },
            "/teams.bmp": {
                "method": "GET",
                "description": (
                    f"Generate F1 teams and drivers BMP image "
                    f"({config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT})"
                ),
                "parameters": {
                    "lang": {
                        "type": "string",
                        "description": "Language code for teams text",
                        "values": _LANGUAGE_VALUES,
                        "default": "en",
                        "example": "?lang=cs",
                    },
                    "year": {
                        "type": "integer",
                        "description": "Season year for team data",
                        "example": "?year=2026",
                        "optional": True,
                    },
                    "display": {
                        "type": "string",
                        "description": "Display output mode",
                        "values": _DISPLAY_VALUES,
                        "default": "1bit",
                        "example": "?display=bwr",
                        "optional": True,
                    },
                },
                "response": {
                    "content_type": "image/bmp",
                    "dimensions": f"{config.DISPLAY_WIDTH}x{config.DISPLAY_HEIGHT}",
                    "color_depth": (
                        "1-bit monochrome, 4-bit indexed B/W/R, "
                        "4-bit indexed B/W/R/Y, or indexed Spectra 6"
                    ),
                },
                "examples": [
                    "/teams.bmp",
                    "/teams.bmp?lang=cs",
                    "/teams.bmp?year=2026",
                    "/teams.bmp?display=bwr",
                    "/teams.bmp?display=spectra6",
                ],
            },
            "/api": {"method": "GET", "description": "API documentation (this endpoint)"},
            "/api/docs": {"method": "GET", "description": "API documentation (alias for /api)"},
            "/api/stats": {
                "method": "GET",
                "description": "Request statistics (last hour and 24h counts)",
            },
            "/api/stats/history": {
                "method": "GET",
                "description": "Historical hourly request statistics",
            },
            "/api/races/{year}": {
                "method": "GET",
                "description": "Get list of races for a season",
                "parameters": {
                    "year": {"type": "integer", "description": "Season year", "in": "path"}
                },
            },
            "/api/race/{year}/{round_num}": {
                "method": "GET",
                "description": "Get detailed race information",
                "parameters": {
                    "year": {"type": "integer", "description": "Season year", "in": "path"},
                    "round_num": {"type": "integer", "description": "Round number", "in": "path"},
                },
            },
            "/health": {"method": "GET", "description": "Process liveness check"},
            "/health/ready": {
                "method": "GET",
                "description": "SQLite, persistent storage, and generation readiness check",
            },
        },
        "e_ink_usage": {
            "description": "For E-Ink displays, fetch /calendar.bmp and display directly",
            "recommended_refresh": "Every 1-6 hours (data updates hourly)",
            "display_compatibility": 'Any 800x480 E-Ink display (e.g., Waveshare 7.5")',
        },
    }


@router.get("/api/stats")
async def get_stats(request: Request) -> dict:
    """Get API request statistics from database."""
    _require_operational_api_auth(request)
    enforce_rate_limit(request, bucket="stats_read", limit=config.STATS_RATE_LIMIT_PER_MINUTE)

    stats = await get_database().get_api_calls_stats_24h()
    return {
        "requests": {
            "last_24h": stats["count_24h"],
            "avg_response_ms": stats["avg_response_ms"],
            "total_bytes_24h": stats["total_bytes_24h"],
            "by_status": stats["status_codes"],
        },
        "cache_size": len(get_bmp_cache()),
        "cache_max_size": get_bmp_cache().maxsize,
    }


@router.post("/api/perf-metrics")
async def post_perf_metrics(payload: PerfMetricsPayload, request: Request) -> dict[str, str]:
    """Store client-side Web Vitals metrics for later aggregation."""
    enforce_rate_limit(
        request,
        bucket="perf_metrics",
        limit=config.PERF_METRICS_RATE_LIMIT_PER_MINUTE,
    )

    user_agent = (request.headers.get("User-Agent") or "")[:_MAX_USER_AGENT_LENGTH] or None
    db = get_database()
    try:
        await db.save_perf_metric(
            page_path=payload.page_path,
            lcp_ms=payload.lcp_ms,
            cls=payload.cls,
            fcp_ms=payload.fcp_ms,
            ttfb_ms=payload.ttfb_ms,
            inp_ms=payload.inp_ms,
            user_agent=user_agent,
            connection_type=payload.connection_type,
            device_memory=payload.device_memory,
        )
    except Exception as exc:
        logger.warning("Failed to save perf metrics: %s", exc)
        raise HTTPException(status_code=503, detail="Failed to save metrics") from exc

    create_supervised_task(
        track_event(
            url=payload.page_path,
            event_name="web_vitals",
            lang="en",
            user_agent=user_agent,
            event_data={
                "lcp": payload.lcp_ms,
                "cls": payload.cls,
                "fcp": payload.fcp_ms,
                "ttfb": payload.ttfb_ms,
            },
        ),
        name="track_web_vitals",
    )

    return {"status": "ok"}


@router.get("/api/perf-metrics")
async def get_perf_metrics(request: Request, hours: int = Query(default=24, ge=1, le=720)) -> dict:
    """Return aggregated performance metrics for the requested lookback window."""
    _require_operational_api_auth(request)
    enforce_rate_limit(request, bucket="stats_read", limit=config.STATS_RATE_LIMIT_PER_MINUTE)

    db = get_database()
    stats = await db.get_perf_stats(hours)
    by_page = await db.get_perf_stats_by_page(hours)
    return {"overall": stats, "by_page": by_page}


@router.get("/api/stats/history")
async def get_stats_history(
    request: Request, limit: int = Query(default=168, ge=1, le=720)
) -> dict:
    """Return recent hourly request history for the stats dashboard."""
    _require_operational_api_auth(request)
    enforce_rate_limit(request, bucket="stats_read", limit=config.STATS_RATE_LIMIT_PER_MINUTE)

    history = await get_database().get_request_stats_history(limit=limit)
    return {"history": history, "count": len(history)}


@router.get("/api/races/{year}")
async def get_season_races(
    year: int, request: Request, f1_service: F1Service = Depends(get_f1_service)
) -> dict:
    """Return all races for a given season."""
    enforce_rate_limit(request, bucket="f1_data_api", limit=config.DATA_API_RATE_LIMIT_PER_MINUTE)
    _require_supported_f1_season(year)
    races = f1_service.get_all_races_from_static(year)
    if not races:
        races = await f1_service.get_season_races(year)
    return {"year": year, "races": races}


@router.get("/api/race/{year}/{round_num}")
async def get_race_detail(
    year: int,
    round_num: int,
    request: Request,
    f1_service: F1Service = Depends(get_f1_service),
) -> dict:
    """Return details for a single race round."""
    enforce_rate_limit(request, bucket="f1_data_api", limit=config.DATA_API_RATE_LIMIT_PER_MINUTE)
    _require_supported_f1_season(year)
    if not 1 <= round_num <= 30:
        raise HTTPException(status_code=422, detail="Invalid race round")
    static_races = f1_service.get_all_races_from_static(year)
    race = next(
        (item for item in static_races if _matches_round(item, round_num)),
        None,
    )
    if race is None:
        race = await f1_service.get_race_by_round(year, round_num)
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


@router.get("/api/teams/{year}")
async def get_teams(year: int, request: Request) -> dict:
    """Get teams and drivers for a season."""
    enforce_rate_limit(request, bucket="f1_data_api", limit=config.DATA_API_RATE_LIMIT_PER_MINUTE)
    _require_supported_f1_season(year)
    try:
        teams_service = TeamsService()
        teams_data = await teams_service.get_teams_and_drivers(year)
        return {"season": teams_data.season, "teams": [t.model_dump() for t in teams_data.teams]}
    except Exception as exc:
        logger.error("Failed to load teams for %s: %s", year, exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Teams data temporarily unavailable") from exc


def _get_driver_number(driver_code: str, year: int) -> int | None:
    """Map a driver code to the display number used in leader payloads."""
    return get_driver_number(driver_code, year)


def _get_team_id(team_name: str) -> str | None:
    """Resolve a standings constructor name to the frontend team asset key."""
    for key, team_id in TEAM_ID_MAP.items():
        if key.lower() in team_name.lower():
            return team_id
    return None


@router.get("/api/standings/leader")
@router.get("/api/standings/leader/{year}")
async def get_standings_leader(request: Request, year: int | None = None) -> dict:
    """Return the current driver and constructor leaders for a season."""
    from app.services.standings_service import StandingsService

    if year is None:
        year = get_current_f1_season()
    enforce_rate_limit(request, bucket="f1_data_api", limit=config.DATA_API_RATE_LIMIT_PER_MINUTE)
    _require_supported_f1_season(year)

    standings_service = StandingsService()

    try:
        driver_standings = await standings_service.get_driver_standings(year, limit=1)
        constructor_standings = await standings_service.get_constructor_standings(year, limit=1)

        leader_driver = None
        leader_team = None

        if driver_standings:
            d = driver_standings[0]
            leader_driver = {
                "name": d.driver_name.upper(),
                "code": d.driver_code,
                "full_name": f"{d.driver_given_name} {d.driver_name}",
                "number": _get_driver_number(d.driver_code, year),
                "team": d.constructor_name,
            }

        if constructor_standings:
            c = constructor_standings[0]
            leader_team = {"name": c.constructor_name, "id": _get_team_id(c.constructor_name)}

        has_data = leader_driver is not None or leader_team is not None

        return {
            "season": year,
            "leader_team": leader_team,
            "leader_driver": leader_driver,
            "has_data": has_data,
        }

    except Exception as exc:
        logger.warning("Failed to get standings leader for %s: %s", year, exc)
        return {"season": year, "leader_team": None, "leader_driver": None, "has_data": False}
