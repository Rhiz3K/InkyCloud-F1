"""Regression tests for the 2026-11-22 full review quick wins (issue #185)."""

import asyncio
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.routes import images
from app.services import teams_service as teams
from app.services import weather_service
from app.services.weather_service import WeatherData, WeatherService
from tests.test_routes_images import _request

RESET_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reset_db.sh"


def _roster(year: int = 2026) -> TeamsData:
    return TeamsData(
        season=year,
        teams=[
            TeamEntry(
                constructor_name="Audi",
                drivers=[TeamDriverEntry(driver_id="hulkenberg", name="Nico Hülkenberg")],
            )
        ],
    )


@pytest.fixture(autouse=True)
def _isolated_caches():
    teams.TeamsService._shared_cache.clear()
    teams.TeamsService._negative_cache.clear()
    images._singleflight_tasks.clear()
    yield
    teams.TeamsService._shared_cache.clear()
    teams.TeamsService._negative_cache.clear()
    images._singleflight_tasks.clear()


# --- C1: a stalled standings fetch must not take the bundled roster down with it ---------------


@pytest.mark.asyncio
async def test_stalled_standings_fall_back_to_bundled_roster(monkeypatch):
    async def stalled(_year: int):
        await asyncio.Event().wait()

    service = teams.TeamsService()
    monkeypatch.setattr(service, "_load_from_json", lambda _year: _roster())
    monkeypatch.setattr(service, "_fetch_standings", stalled)
    monkeypatch.setattr(teams.config, "TEAMS_ENRICHMENT_TIMEOUT_SECONDS", 0.05)

    data = await asyncio.wait_for(service.get_teams_and_drivers(2026), 1)

    assert data.teams
    assert data.standings_complete is False


@pytest.mark.asyncio
async def test_teams_artifact_renders_roster_when_only_standings_stall(monkeypatch):
    async def stalled(_self, _year: int):
        await asyncio.Event().wait()

    monkeypatch.setattr(teams.TeamsService, "_load_from_json", lambda _self, _year: _roster())
    monkeypatch.setattr(teams.TeamsService, "_fetch_standings", stalled)
    monkeypatch.setattr(teams.config, "TEAMS_ENRICHMENT_TIMEOUT_SECONDS", 0.05)
    render = AsyncMock(return_value=b"roster-bmp")
    monkeypatch.setattr(images, "run_render", render)

    artifact = await asyncio.wait_for(
        images._render_teams_artifact(cache_key="teams-c1", lang="en", year=2026, display="1bit"),
        1,
    )

    assert artifact[0] == b"roster-bmp"
    rendered_data = render.await_args.args[0].args[2]
    assert rendered_data.teams and rendered_data.standings_complete is False
    # Degraded output is served but never pinned in the image cache.
    assert "teams-c1" not in images._teams_image_cache(images.TeamsExtra.NONE)


# --- B3: an explicit null temperature is missing data, not a value to cache --------------------


class _JsonResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    @staticmethod
    def raise_for_status():
        return None

    def json(self):
        return self._payload


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [None, float("nan"), "21", True])
async def test_current_weather_rejects_non_numeric_temperature(monkeypatch, temperature):
    payload = {"current": {"temperature_2m": temperature, "weather_code": 1}, "hourly": {}}
    monkeypatch.setattr(
        weather_service, "fetch_with_retry", AsyncMock(return_value=_JsonResponse(payload))
    )
    service = WeatherService()
    weather_service._weather_cache.clear()

    assert await service.get_current_weather(lat=45.0, lon=9.0) is None
    assert weather_service._weather_cache == {}


@pytest.mark.asyncio
async def test_current_weather_tolerates_null_code_and_precipitation(monkeypatch):
    payload = {
        "current": {"temperature_2m": 18.4, "weather_code": None, "time": "2026-05-01T10:00"},
        "hourly": {"time": ["2026-05-01T10:00"], "precipitation_probability": [None]},
    }
    monkeypatch.setattr(
        weather_service, "fetch_with_retry", AsyncMock(return_value=_JsonResponse(payload))
    )
    weather_service._weather_cache.clear()

    result = await WeatherService().get_current_weather(lat=45.1, lon=9.1)

    assert result == WeatherData(18.4, 0, 0)
    assert result.temp_display == "18°"


# --- B5: weather_type=off skips weather; a timeout keeps lookups that already finished -----------


@pytest.mark.asyncio
async def test_off_weather_type_never_fetches_weather(monkeypatch):
    race = {"circuit": {"circuitId": "monza"}, "timezone": "UTC"}
    service = SimpleNamespace(get_next_race_from_static=MagicMock(return_value=race))
    context = AsyncMock(return_value=(None, None, {"off": None}))
    monkeypatch.setattr(images, "get_weather_context", context)
    monkeypatch.setattr(images, "run_render", AsyncMock(return_value=b"bmp"))
    monkeypatch.setattr(images.config, "WEATHER_ENABLED", True)

    await images._render_calendar(
        f1_service=service,
        lang="en",
        year=None,
        race_round=None,
        race_key=None,
        target_tz="UTC",
        weather=True,
        weather_type="off",
        display="1bit",
    )

    context.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_timeout_keeps_already_resolved_current_weather(monkeypatch):
    current = WeatherData(21.0, 2, 20)

    async def partial(_race_data, weather_by_type):
        weather_by_type["current"] = current
        await asyncio.Event().wait()

    monkeypatch.setattr(weather_service, "_get_weather_context_unbounded", partial)
    monkeypatch.setattr(weather_service.config, "WEATHER_ENRICHMENT_TIMEOUT_SECONDS", 0.01)

    result = await asyncio.wait_for(weather_service.get_weather_context({}), 0.5)

    assert result == (current, None, {"off": None, "current": current})


# --- A3: failures between validation and rendering still return an image -----------------------


@pytest.mark.asyncio
async def test_calendar_selection_failure_returns_error_bmp():
    f1 = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value={"season": 2026, "round": 1}),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch(
            "app.routes.images._get_pregenerated_calendar_path",
            side_effect=OSError("disk unavailable"),
        ),
        patch(
            "app.routes.images._shared_error_artifact",
            new=AsyncMock(return_value=(b"error-bmp", '"err"')),
        ),
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images.sentry_sdk.capture_exception"),
    ):
        response = await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )

    assert response.status_code == 200
    assert response.media_type == "image/bmp"
    assert response.body == b"error-bmp"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_calendar_stats_lookup_failure_returns_error_bmp():
    f1 = SimpleNamespace(
        get_next_race_from_static=MagicMock(side_effect=RuntimeError("bad static data")),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch(
            "app.routes.images._shared_error_artifact",
            new=AsyncMock(return_value=(b"error-bmp", '"err"')),
        ),
        patch("app.routes.images._record_calendar_api_call"),
        patch("app.routes.images.sentry_sdk.capture_exception"),
    ):
        response = await images.get_calendar_bmp(
            _request(),
            lang="en",
            year=None,
            race_round=None,
            race_key=None,
            tz=None,
            weather=True,
            weather_type="race_day",
            display="1bit",
            f1_service=f1,
        )

    assert response.body == b"error-bmp"
    assert response.headers["cache-control"] == "no-store"


# --- C3: reset-db stats clears the tables the default aggregate profile writes ------------------


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="reset-db requires the sqlite3 CLI")
def test_reset_stats_clears_aggregate_tables(tmp_path):
    database_path = tmp_path / "f1.db"
    stats_tables = ("api_calls", "request_stats", "api_call_totals", "perf_metrics")
    with closing(sqlite3.connect(database_path)) as conn:
        for table in (*stats_tables, "cache_meta", "generated_images"):
            conn.execute(f"CREATE TABLE {table} (value INTEGER)")
            conn.execute(f"INSERT INTO {table} VALUES (1)")
        conn.commit()

    env = os.environ.copy()
    env.update(DATABASE_PATH=str(database_path), IMAGES_PATH=str(tmp_path / "images"))
    result = subprocess.run(
        ["bash", str(RESET_SCRIPT), "stats"],
        input="y\n",
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(database_path)) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (*stats_tables, "cache_meta", "generated_images")
        }
    assert all(counts[table] == 0 for table in stats_tables)
    assert counts["cache_meta"] == counts["generated_images"] == 1
    assert "api_call_totals" in result.stdout and "perf_metrics" in result.stdout


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="reset-db requires the sqlite3 CLI")
def test_reset_stats_tolerates_databases_missing_newer_tables(tmp_path):
    database_path = tmp_path / "f1.db"
    with closing(sqlite3.connect(database_path)) as conn:
        conn.execute("CREATE TABLE api_calls (value INTEGER)")
        conn.execute("INSERT INTO api_calls VALUES (1)")
        conn.commit()

    env = os.environ.copy()
    env.update(DATABASE_PATH=str(database_path), IMAGES_PATH=str(tmp_path / "images"))
    result = subprocess.run(
        ["bash", str(RESET_SCRIPT), "stats"],
        input="y\n",
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(database_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == 0
