"""JSON API routes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.config import config
from app.services.analytics import track_event
from app.services.database import Database
from app.services.f1_service import F1Service
from app.services.teams_service import TeamsService
from app.state import get_bmp_cache
from app.utils.f1_season import get_current_f1_season

from .deps import get_f1_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api")
@router.get("/api/docs")
async def api_info() -> dict:
    """API documentation endpoint."""
    return {
        "service": "F1 E-Ink Calendar API",
        "version": "0.1.0",
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
                        "values": ["en", "cs"],
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
                        "values": ["1bit", "spectra6", "bwr"],
                        "default": "1bit",
                        "example": "?display=bwr",
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
                    "color_depth": "1-bit monochrome, 4-bit indexed B/W/R, or indexed Spectra 6",
                },
                "examples": [
                    "/calendar.bmp",
                    "/calendar.bmp?lang=cs",
                    "/calendar.bmp?year=2025&round=1",
                    "/calendar.bmp?lang=en&tz=America/Los_Angeles",
                    "/calendar.bmp?display=bwr",
                    "/calendar.bmp?display=spectra6&weather_type=current",
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
            "/health": {"method": "GET", "description": "Health check endpoint"},
        },
        "e_ink_usage": {
            "description": "For E-Ink displays, fetch /calendar.bmp and display directly",
            "recommended_refresh": "Every 1-6 hours (data updates hourly)",
            "display_compatibility": 'Any 800x480 E-Ink display (e.g., Waveshare 7.5")',
        },
    }


@router.get("/api/stats")
async def get_stats() -> dict:
    """Get API request statistics from database."""
    db = Database()
    stats = await db.get_api_calls_stats_24h()
    return {
        "requests": {
            "last_24h": stats["count_24h"],
            "avg_response_ms": stats["avg_response_ms"],
            "total_bytes_24h": stats["total_bytes_24h"],
        },
        "cache_size": len(get_bmp_cache()),
        "cache_max_size": get_bmp_cache().maxsize,
    }


@router.post("/api/perf-metrics")
async def post_perf_metrics(request: Request) -> dict[str, str]:
    from app.models import PerfMetricsPayload

    try:
        data = await request.json()
        payload = PerfMetricsPayload(**data)

        db = Database()
        await db.save_perf_metric(
            page_path=payload.page_path,
            lcp_ms=payload.lcp_ms,
            cls=payload.cls,
            fcp_ms=payload.fcp_ms,
            ttfb_ms=payload.ttfb_ms,
            inp_ms=payload.inp_ms,
            user_agent=request.headers.get("User-Agent"),
            connection_type=payload.connection_type,
            device_memory=payload.device_memory,
        )

        asyncio.create_task(
            track_event(
                url=payload.page_path,
                event_name="web_vitals",
                lang="en",
                user_agent=request.headers.get("User-Agent"),
                event_data={
                    "lcp": payload.lcp_ms,
                    "cls": payload.cls,
                    "fcp": payload.fcp_ms,
                    "ttfb": payload.ttfb_ms,
                },
            )
        )

        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Failed to save perf metrics: %s", exc)
        return {"status": "error", "message": "Failed to save metrics"}


@router.get("/api/perf-metrics")
async def get_perf_metrics(hours: int = Query(default=24, le=720)) -> dict:
    db = Database()
    stats = await db.get_perf_stats(hours)
    by_page = await db.get_perf_stats_by_page(hours)
    return {"overall": stats, "by_page": by_page}


@router.get("/api/stats/history")
async def get_stats_history(limit: int = Query(default=168, le=720)) -> dict:
    db = Database()
    history = await db.get_request_stats_history(limit=limit)
    return {"history": history, "count": len(history)}


@router.get("/api/races/{year}")
async def get_season_races(year: int, f1_service: F1Service = Depends(get_f1_service)) -> dict:
    races = await f1_service.get_season_races(year)
    return {"year": year, "races": races}


@router.get("/api/race/{year}/{round_num}")
async def get_race_detail(
    year: int, round_num: int, f1_service: F1Service = Depends(get_f1_service)
) -> dict:
    race = await f1_service.get_race_by_round(year, round_num)
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


@router.get("/api/teams/{year}")
async def get_teams(year: int) -> dict:
    """Get teams and drivers for a season."""
    teams_service = TeamsService()
    teams_data = await teams_service.get_teams_and_drivers(year)
    return {"season": teams_data.season, "teams": [t.model_dump() for t in teams_data.teams]}


DRIVER_NUMBERS = {
    "VER": 1,
    "NOR": 4,
    "LEC": 16,
    "SAI": 55,
    "HAM": 44,
    "RUS": 63,
    "PIA": 81,
    "ALO": 14,
    "STR": 18,
    "GAS": 10,
    "OCO": 31,
    "ALB": 23,
    "TSU": 22,
    "RIC": 3,
    "HUL": 27,
    "MAG": 20,
    "BOT": 77,
    "ZHO": 24,
    "SAR": 2,
    "LAW": 30,
    "BEA": 87,
    "COL": 43,
    "DOO": 7,
    "ANT": 12,
    "HAD": 6,
    "BOR": 5,
}

TEAM_ID_MAP = {
    "McLaren": "mclaren",
    "Ferrari": "ferrari",
    "Red Bull": "red_bull",
    "Mercedes": "mercedes",
    "Aston Martin": "aston_martin",
    "Alpine": "alpine",
    "Williams": "williams",
    "RB": "racing_bulls",
    "Racing Bulls": "racing_bulls",
    "Haas F1 Team": "haas",
    "Haas": "haas",
    "Kick Sauber": "sauber",
    "Sauber": "sauber",
    "Alfa Romeo": "sauber",
}


def _get_driver_number(driver_code: str, _year: int) -> int | None:
    return DRIVER_NUMBERS.get(driver_code)


def _get_team_id(team_name: str) -> str | None:
    for key, team_id in TEAM_ID_MAP.items():
        if key.lower() in team_name.lower():
            return team_id
    return None


@router.get("/api/standings/leader")
@router.get("/api/standings/leader/{year}")
async def get_standings_leader(year: int | None = None) -> dict:
    from app.services.standings_service import StandingsService

    if year is None:
        year = get_current_f1_season()

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
