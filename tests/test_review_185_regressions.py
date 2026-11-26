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
from app.services import bmp_artifacts, weather_service
from app.services import teams_service as teams
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
    bmp_artifacts._singleflight_tasks.clear()
    yield
    teams.TeamsService._shared_cache.clear()
    teams.TeamsService._negative_cache.clear()
    bmp_artifacts._singleflight_tasks.clear()


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
    monkeypatch.setattr(bmp_artifacts, "run_render", render)

    artifact = await asyncio.wait_for(
        bmp_artifacts.render_teams_artifact(
            cache_key="teams-c1", lang="en", year=2026, display="1bit"
        ),
        1,
    )

    assert artifact.artifact[0] == b"roster-bmp"
    assert artifact.verdict() is bmp_artifacts.CacheVerdict.INCOMPLETE
    rendered_data = render.await_args.args[0].args[2]
    assert rendered_data.teams and rendered_data.standings_complete is False
    # Degraded output is served but never pinned in the image cache.
    assert "teams-c1" not in bmp_artifacts.teams_image_cache(images.TeamsExtra.NONE)


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
    context = AsyncMock(return_value=(None, None, {"off": None}))
    monkeypatch.setattr(bmp_artifacts, "get_weather_context", context)
    monkeypatch.setattr(bmp_artifacts, "run_render", AsyncMock(return_value=b"bmp"))
    monkeypatch.setattr(images.config, "WEATHER_ENABLED", True)

    await bmp_artifacts.render_calendar(
        race_data=race,
        lang="en",
        target_tz="UTC",
        weather_type="off",
        display="1bit",
    )

    context.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_timeout_keeps_already_resolved_current_weather(monkeypatch):
    current = WeatherData(21.0, 2, 20)

    async def partial(_race_data, weather_by_type, **_kwargs):
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
            "app.services.bmp_artifacts.shared_error_artifact",
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
            "app.services.bmp_artifacts.shared_error_artifact",
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
    stats_tables = (
        "api_calls",
        "request_stats",
        "api_call_totals",
        "perf_metrics",
        "perf_buckets",
        "stats_batches",
    )
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
    assert all(table in result.stdout for table in stats_tables)


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


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="reset-db requires the sqlite3 CLI")
def test_reset_stats_aborts_when_table_lookup_fails(tmp_path):
    """A failed existence lookup (e.g. SQLITE_BUSY) must abort, not skip the table silently."""
    database_path = tmp_path / "f1.db"
    with closing(sqlite3.connect(database_path)) as conn:
        for table in ("api_calls", "perf_buckets"):
            conn.execute(f"CREATE TABLE {table} (value INTEGER)")
            conn.execute(f"INSERT INTO {table} VALUES (1)")
        conn.commit()

    real_sqlite = shutil.which("sqlite3")
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "sqlite3"
    # Fail only the lookup of perf_buckets, as a lock that outlives the busy timeout would.
    shim.write_text(
        "#!/bin/bash\n"
        'case "${*: -1}" in\n'
        "  *sqlite_master*perf_buckets*) echo 'Error: database is locked' >&2; exit 5 ;;\n"
        "esac\n"
        f'exec "{real_sqlite}" "$@"\n'
    )
    shim.chmod(0o755)

    env = os.environ.copy()
    env.update(
        DATABASE_PATH=str(database_path),
        IMAGES_PATH=str(tmp_path / "images"),
        PATH=f"{shim_dir}{os.pathsep}{env.get('PATH', '')}",
    )
    result = subprocess.run(
        ["bash", str(RESET_SCRIPT), "stats"],
        input="y\n",
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Statistics reset complete" not in result.stdout
    assert "Could not check table perf_buckets" in result.stderr
    with closing(sqlite3.connect(database_path)) as conn:
        # Nothing was deleted: the abort happens before the DELETE batch runs.
        assert conn.execute("SELECT COUNT(*) FROM api_calls").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM perf_buckets").fetchone()[0] == 1


# --- B1: provider circuit IDs and complete podiums --------------------------------------------


def _podium_payload(key: str, positions: list[int]) -> dict:
    time_field = {"Q3": "1:48.000"} if key == "QualifyingResults" else {"Time": {"time": "1:30"}}
    rows = [
        {
            "position": str(position),
            "Driver": {"code": f"D{position:02d}", "familyName": f"Driver{position}"},
            "Constructor": {"name": "Team"},
            **time_field,
        }
        for position in positions
    ]
    return {"MRData": {"RaceTable": {"Races": [{key: rows}]}}}


@pytest.mark.asyncio
async def test_historical_refresh_requests_provider_circuit_id():
    from app.services import historical_refresh as historical

    empty = SimpleNamespace(json=lambda: {})
    fetch = AsyncMock(return_value=empty)
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        await historical._fetch_results_with_status(object(), "las_vegas")

    urls = [call.args[1] for call in fetch.await_args_list]
    assert urls and all("/circuits/vegas/" in url for url in urls)


@pytest.mark.asyncio
async def test_historical_refresh_rejects_podium_missing_third_place():
    from app.services import historical_refresh as historical

    gap = SimpleNamespace(json=lambda: _podium_payload("QualifyingResults", [1, 2, 4]))
    race = SimpleNamespace(json=lambda: _podium_payload("Results", [1, 2, 3]))
    fetch = AsyncMock(side_effect=[gap, race] * 3)
    with (
        patch("app.services.historical_refresh._current_year", return_value=2026),
        patch("app.services.historical_refresh.fetch_with_retry", new=fetch),
    ):
        outcome = await historical._fetch_results_with_status(object(), "monza")

    assert outcome.results is None
    assert outcome.completed is False


# --- B4: a historical precipitation amount survives a restart ---------------------------------


@pytest.mark.asyncio
async def test_precipitation_amount_survives_db_round_trip(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from app.services.database import Database

    db = Database(str(tmp_path / "weather.db"))
    race_dt = datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
    archived = WeatherData(19.0, 61, 0, precipitation_display_override="1.6mm")
    service = MagicMock()
    service.get_current_weather = AsyncMock(return_value=None)
    service.get_race_weather = AsyncMock(return_value=archived)
    monkeypatch.setattr(weather_service, "WeatherService", lambda **_: service)
    monkeypatch.setattr(weather_service, "_get_next_race_details", lambda: (1.0, 2.0, race_dt))
    try:
        await weather_service.prefetch_weather_for_next_race(db)
        weather_service._weather_cache.clear()

        assert await weather_service.load_prefetched_weather_from_db(db) == 1
        key = weather_service._race_weather_cache_key(1.0, 2.0, race_dt)
        restored = weather_service._weather_cache[key][0]
        assert restored.precip_display == "1.6mm"
        assert (await weather_service.get_cached_weather_from_db(db, key)) == archived
    finally:
        weather_service._weather_cache.clear()
        await db.close()


@pytest.mark.asyncio
async def test_weather_cache_migration_adds_precipitation_column(tmp_path):
    from app.services.database import DATABASE_SCHEMA_VERSION, Database

    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE weather_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT UNIQUE NOT NULL,
                temperature_c REAL NOT NULL,
                weather_code INTEGER NOT NULL,
                precipitation_probability INTEGER NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    db = Database(str(path))
    try:
        await db.save_weather_cache("k", 18.0, 3, 0, precipitation_display_override="0.4mm")
        assert (await db.get_weather_cache("k"))[4] == "0.4mm"
    finally:
        await db.close()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


# --- D3 + B12: one constructor mapping; malformed standings rows don't drop the table ----------


@pytest.mark.parametrize(
    ("constructor", "expected"),
    [
        ("Audi", "audi"),
        ("Cadillac", "cadillac"),
        ("Cadillac-Ferrari", "cadillac"),
        ("Haas F1 Team", "haas"),
        ("RB F1 Team", "racing_bulls"),
        ("Racing Bulls", "racing_bulls"),
        ("Red Bull", "red_bull"),
        ("Kick Sauber", "sauber"),
        ("Alfa Romeo", "sauber"),
        ("Unknown Racing", None),
    ],
)
def test_leader_team_id_matches_renderer_team_key(constructor, expected):
    from app.routes import api
    from app.services.renderer_text import get_team_key

    assert api._get_team_id(constructor) == expected == get_team_key(constructor)


def _standings_response(payload: dict):
    return SimpleNamespace(json=lambda: payload)


@pytest.mark.asyncio
async def test_driver_standings_skip_malformed_row_and_use_current_team(monkeypatch):
    from app.services import standings_service

    payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "round": "5",
                        "DriverStandings": [
                            {
                                "position": "1",
                                "points": "100",
                                "wins": "3",
                                "Driver": {"code": "LAW", "familyName": "Lawson"},
                                "Constructors": [{"name": "RB F1 Team"}, {"name": "Red Bull"}],
                            },
                            {
                                "position": "",
                                "points": "0",
                                "Driver": {"code": "BAD", "familyName": "Broken"},
                            },
                            None,
                            "not-a-row",
                            {"Driver": None},
                            {"Constructors": [None]},
                            {"Constructors": "invalid"},
                            {
                                "position": "2",
                                "points": "80",
                                "Driver": {"code": "HAD", "familyName": "Hadjar"},
                                "Constructors": [{"name": "Red Bull"}],
                            },
                        ],
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(
        standings_service, "fetch_with_retry", AsyncMock(return_value=_standings_response(payload))
    )
    service = standings_service.StandingsService()
    service._cache.clear()
    service._negative_cache.clear()
    try:
        standings = await service.get_driver_standings(2026, limit=10)
    finally:
        service._cache.clear()

    # Non-mapping rows (null, strings) are skipped without discarding the valid rows around them.
    assert [s.driver_code for s in standings] == ["LAW", "HAD"]
    assert standings[0].constructor_name == "Red Bull"


@pytest.mark.asyncio
async def test_constructor_standings_skip_malformed_row(monkeypatch):
    from app.services import standings_service

    payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "round": "5",
                        "ConstructorStandings": [
                            None,
                            {"position": "1", "points": "200", "Constructor": {"name": "Audi"}},
                            {"position": "2", "points": "n/a", "Constructor": {"name": "Haas"}},
                            ["bad", "row"],
                            {"Constructor": None},
                            {"position": "3", "points": "90", "Constructor": {"name": "Alpine"}},
                        ],
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(
        standings_service, "fetch_with_retry", AsyncMock(return_value=_standings_response(payload))
    )
    service = standings_service.StandingsService()
    service._cache.clear()
    service._negative_cache.clear()
    try:
        standings = await service.get_constructor_standings(2026, limit=10)
    finally:
        service._cache.clear()

    assert [s.constructor_name for s in standings] == ["Audi", "Alpine"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "rows_key", "valid_row", "attr", "expected"),
    [
        (
            "get_driver_standings",
            "DriverStandings",
            {"position": "1", "points": "25", "Driver": {"code": "NOR"}},
            "driver_code",
            "NOR",
        ),
        (
            "get_constructor_standings",
            "ConstructorStandings",
            {"position": "1", "points": "40", "Constructor": {"name": "McLaren"}},
            "constructor_name",
            "McLaren",
        ),
    ],
)
async def test_all_rejected_standings_rows_recover_after_the_negative_cache(
    monkeypatch, method, rows_key, valid_row, attr, expected
):
    from app.services import standings_service

    def payload(rows):
        return {"MRData": {"StandingsTable": {"StandingsLists": [{"round": "5", rows_key: rows}]}}}

    clock = [1_000.0]
    monkeypatch.setattr(standings_service.time, "time", lambda: clock[0])
    fetch = AsyncMock(return_value=_standings_response(payload([{"position": ""}, None])))
    monkeypatch.setattr(standings_service, "fetch_with_retry", fetch)
    service = standings_service.StandingsService()
    service._cache.clear()
    service._negative_cache.clear()
    try:
        # Only rejected rows: nothing is positive-cached for an hour.
        assert await getattr(service, method)(2026) == []
        assert service._cache == {}

        fetch.return_value = _standings_response(payload([valid_row]))
        assert await getattr(service, method)(2026) == []  # still inside the negative cache
        clock[0] += standings_service.NEGATIVE_CACHE_TTL_SECONDS + 1
        recovered = await getattr(service, method)(2026)
    finally:
        service._cache.clear()
        service._negative_cache.clear()

    assert [getattr(standing, attr) for standing in recovered] == [expected]
    assert fetch.await_count == 2


def test_standing_numbers_reject_non_mapping_rows():
    from app.services.standings_service import _parse_standing_numbers

    assert _parse_standing_numbers(["1", "10"]) is None
    assert _parse_standing_numbers({"position": "2", "points": "4.5"}) == (2, 4.5, 0)


# --- A11y: focus rings stay visible in forced-colors mode ---------------------------------------


def test_ring_focus_indicators_keep_a_forced_colors_outline():
    """A ring-only focus style must not remove the outline outright.

    Forced-colors mode drops box-shadow rings, so ``focus:outline-none`` (``outline-style: none``
    in Tailwind v4) would leave no visible focus. ``focus:outline-hidden`` keeps a transparent
    outline that forced colors paint in the system colour.
    """
    root = Path(__file__).resolve().parents[1] / "app"
    # Built by concatenation: Tailwind scans the repository, and a literal class name here would
    # add a utility to the compiled stylesheet.
    ring, outline_none = "focus-visible:" + "ring", "focus:outline-" + "none"
    offenders = []
    for template in (root / "templates").rglob("*.html"):
        for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
            if ring in line and outline_none in line:
                offenders.append(f"{template.relative_to(root)}:{number}")
    assert offenders == []

    css = (root / "assets" / "css" / "tailwind.min.css").read_text(encoding="utf-8")
    assert "@media (forced-colors:active){.focus\\:outline-hidden:focus{" in css
