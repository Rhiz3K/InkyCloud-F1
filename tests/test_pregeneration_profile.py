"""Self-hosting pregeneration profile and limited weather prefetch (#185 F4)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from fastapi.testclient import TestClient

from app import config as config_module
from app.config import DISPLAY_CODES, LANGUAGE_CODES, Config
from app.main import app
from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services import scheduler_generation, scheduler_weather
from app.services.generation_freshness import GENERATION_REASONS_META_KEY, DegradedReason
from app.services.renderers import DISPLAY_TYPES
from app.services.weather_service import WeatherData


def _config(**values) -> Config:
    return Config(_env_file=None, **values)


@pytest.fixture
def profile(monkeypatch):
    """Apply a pregeneration profile to the shared configuration object."""

    def apply(languages: str = "", displays: str = "", weather: str = "") -> None:
        monkeypatch.setattr(scheduler_generation.config, "PREGENERATE_LANGUAGES", languages)
        monkeypatch.setattr(scheduler_generation.config, "PREGENERATE_DISPLAYS", displays)
        monkeypatch.setattr(scheduler_generation.config, "PREGENERATE_WEATHER_TYPES", weather)

    return apply


# --- configuration -----------------------------------------------------------------------


def test_profile_defaults_keep_every_language_display_and_weather_type():
    settings = _config()

    assert settings.pregenerate_languages == LANGUAGE_CODES
    assert settings.pregenerate_displays == DISPLAY_CODES
    assert settings.pregenerate_weather_types == ("current", "race_day")
    assert settings.WEATHER_PREFETCH_ALL_CIRCUITS is True
    assert settings.BACKUP_INCLUDE_DATA_FILES is True


def test_display_codes_are_the_renderer_display_types():
    assert DISPLAY_TYPES == DISPLAY_CODES


def test_profile_lists_are_normalized_case_insensitively_in_canonical_order():
    settings = _config(
        PREGENERATE_LANGUAGES=" PT-br , cs,,cs ",
        PREGENERATE_DISPLAYS="SPECTRA6,1bit",
        PREGENERATE_WEATHER_TYPES="race",
    )

    assert settings.PREGENERATE_LANGUAGES == "cs,pt-BR"
    assert settings.pregenerate_languages == ("cs", "pt-BR")
    assert settings.pregenerate_displays == ("1bit", "spectra6")
    assert settings.pregenerate_weather_types == ("race_day",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("off", ()),
        ("off,current", ("current",)),
        ("", ("current", "race_day")),
        (None, ("current", "race_day")),
    ],
)
def test_weather_profile_off_disables_optional_weather_variants(raw, expected):
    assert _config(PREGENERATE_WEATHER_TYPES=raw).pregenerate_weather_types == expected


def test_unknown_profile_entries_are_ignored_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        settings = _config(PREGENERATE_LANGUAGES="cs,klingon", PREGENERATE_DISPLAYS="bwry,oled")

    assert settings.pregenerate_languages == ("cs",)
    assert settings.pregenerate_displays == ("bwry",)
    assert "klingon" in caplog.text and "oled" in caplog.text


def test_profile_without_any_supported_entry_falls_back_to_everything(caplog):
    with caplog.at_level(logging.WARNING, logger="app.config"):
        settings = _config(PREGENERATE_LANGUAGES="xx, yy", PREGENERATE_DISPLAYS=" , ")

    assert settings.pregenerate_languages == LANGUAGE_CODES
    assert settings.pregenerate_displays == DISPLAY_CODES
    assert "Falling back" in caplog.text


def test_profile_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PREGENERATE_LANGUAGES", "de")
    monkeypatch.setenv("PREGENERATE_DISPLAYS", "bwr")
    monkeypatch.setenv("WEATHER_PREFETCH_ALL_CIRCUITS", "false")

    settings = _config()

    assert settings.pregenerate_languages == ("de",)
    assert settings.pregenerate_displays == ("bwr",)
    assert settings.WEATHER_PREFETCH_ALL_CIRCUITS is False


def test_backup_local_dir_defaults_beside_the_database(tmp_path):
    assert _config(DATABASE_PATH=str(tmp_path / "f1.db")).backup_local_dir == tmp_path / "backups"
    custom = _config(BACKUP_LOCAL_DIR=f" {tmp_path / 'elsewhere'} ")
    assert custom.backup_local_dir == tmp_path / "elsewhere"


@pytest.mark.parametrize("env_file", [".env.example", ".env.local.example"])
def test_shipped_env_files_keep_the_default_profile(env_file, monkeypatch):
    for name in ("PREGENERATE_LANGUAGES", "PREGENERATE_DISPLAYS", "WEATHER_PREFETCH_ALL_CIRCUITS"):
        monkeypatch.delenv(name, raising=False)

    settings = config_module.Config(_env_file=env_file)

    assert settings.pregenerate_languages == LANGUAGE_CODES
    assert settings.pregenerate_displays == DISPLAY_CODES
    assert settings.WEATHER_PREFETCH_ALL_CIRCUITS is True


# --- scheduler loops ---------------------------------------------------------------------


def test_profile_helpers_filter_supported_sets(profile):
    profile("cs,en", "bwry", "race_day")

    assert scheduler_generation._profile_languages() == ["cs", "en"]
    assert scheduler_generation._profile_displays() == ["bwry"]
    weather = {"off": None, "current": object(), "race": object()}
    assert scheduler_generation._profile_weather_types(weather) == ["off", "race"]

    profile(weather="off")
    assert scheduler_generation._profile_weather_types(weather) == ["off"]


def _teams_data() -> TeamsData:
    return TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Test Team",
                position=1,
                points=10,
                drivers=[TeamDriverEntry(full_name="Test Driver", code="TST")],
            )
        ],
        standings_complete=True,
    )


@pytest.mark.asyncio
async def test_hourly_generation_writes_only_the_profile_variants(tmp_path, profile, monkeypatch):
    """One language and one palette: 3 calendars, 1 teams BMP and their previews only."""
    profile("cs", "bwry")
    images_dir = tmp_path / "images"
    weather = WeatherData(21.0, 1, 10)
    db = SimpleNamespace(
        set_cache_meta=AsyncMock(),
        save_generated_image=AsyncMock(),
        get_popular_tz_variants=AsyncMock(return_value=[{"lang": "de", "tz": "Asia/Tokyo"}]),
        cleanup_old_stats=AsyncMock(),
    )
    teams_service = SimpleNamespace(get_teams_and_drivers=AsyncMock(return_value=_teams_data()))
    monkeypatch.setattr(scheduler_generation.config, "IMAGES_PATH", str(images_dir))
    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(
                return_value=(weather, weather, {"off": None, "current": weather, "race": weather})
            ),
        ),
        patch("app.services.teams_service.TeamsService", return_value=teams_service),
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
    ):
        await scheduler_generation.collect_and_generate()

    bmps = sorted(path.name for path in images_dir.glob("*.bmp"))
    pngs = sorted(path.name for path in images_dir.glob("*.png"))
    assert bmps == [
        "calendar_cs_bwry.bmp",
        "calendar_cs_bwry_weather_current.bmp",
        "calendar_cs_bwry_weather_race.bmp",
        "teams_2026_cs_bwry.bmp",
    ]
    assert pngs == [
        "configure_calendar_cs_bwry.png",
        "configure_calendar_cs_bwry_weather_current.png",
        "configure_calendar_cs_bwry_weather_race.png",
        "configure_teams_cs_bwry.png",
    ]
    # The popular "de" timezone variant is outside the profile and stays on demand.
    assert {c.kwargs.get("lang") for c in db.save_generated_image.await_args_list} == {"cs"}


@pytest.mark.asyncio
async def test_weather_profile_off_skips_weather_variants_but_keeps_core_calendars(
    tmp_path, profile
):
    profile("en", "1bit", "off")
    weather = WeatherData(21.0, 1, 10)
    generate = AsyncMock(return_value=({tmp_path / "calendar_en.bmp"}, 0))
    previews = AsyncMock()
    db = SimpleNamespace(set_cache_meta=AsyncMock(), cleanup_old_stats=AsyncMock())
    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service,
        patch("app.services.scheduler_generation._load_historical_data", return_value=None),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(return_value=(weather, None, {"off": None, "current": weather})),
        ),
        patch("app.services.scheduler_generation._generate_base_variants", new=generate),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch(
            "app.services.scheduler_generation._generate_teams_bmp_variants",
            new=AsyncMock(return_value=(set(), 0)),
        ),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=previews),
        patch("app.services.scheduler_generation._delete_stale_bmps", return_value=0),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path)),
        patch("app.services.scheduler_generation.config.WEATHER_ENABLED", False),
    ):
        f1_service.return_value.get_next_race_from_static.return_value = {"race_name": "Test"}
        await scheduler_generation.collect_and_generate()

    assert generate.await_count == 1
    assert generate.await_args.kwargs["display_types"] == ["1bit"]
    assert previews.await_args_list[-1] == call(["off"], previews.await_args_list[-1].args[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base", "weather", "tz", "teams", "teams_reason", "expected"),
    [
        (1, 0, 0, 0, False, "calendar_variant_failed"),
        (0, 1, 0, 0, False, "weather_variant_failed"),
        (0, 0, 1, 0, False, "timezone_variant_failed"),
        (0, 0, 0, 1, False, "teams_variant_failed"),
        (0, 0, 0, 1, True, "teams_data_unavailable"),
    ],
)
async def test_generation_records_degraded_reason_codes(
    tmp_path, base, weather, tz, teams, teams_reason, expected
):
    async def teams_variants(*, images_dir, db, reasons):
        if teams_reason:
            reasons.add(DegradedReason.TEAMS_DATA_UNAVAILABLE)
        return set(), teams

    core = tmp_path / "calendar_en.bmp"
    weather_data = WeatherData(21.0, 1, 10)
    db = SimpleNamespace(set_cache_meta=AsyncMock(), cleanup_old_stats=AsyncMock())
    with (
        patch("app.services.scheduler_generation.get_database", return_value=db),
        patch("app.services.scheduler_generation.F1Service") as f1_service,
        patch("app.services.scheduler_generation._load_historical_data", return_value=None),
        patch(
            "app.services.scheduler_generation._load_weather_context",
            new=AsyncMock(
                return_value=(weather_data, None, {"off": None, "current": weather_data})
            ),
        ),
        patch(
            "app.services.scheduler_generation._generate_base_variants",
            new=AsyncMock(side_effect=[({core}, base), (set(), weather)]),
        ),
        patch(
            "app.services.scheduler_generation._generate_popular_tz_variants",
            new=AsyncMock(return_value=(set(), tz)),
        ),
        patch("app.services.scheduler_generation._generate_teams_bmp_variants", new=teams_variants),
        patch("app.services.scheduler_generation.generate_preview_pngs", new=AsyncMock()),
        patch("app.services.scheduler_generation.clear_bmp_cache"),
        patch("app.services.scheduler_generation.config.IMAGES_PATH", str(tmp_path)),
        patch("app.services.scheduler_generation.config.WEATHER_ENABLED", False),
    ):
        f1_service.return_value.get_next_race_from_static.return_value = {"race_name": "Test"}
        await scheduler_generation.collect_and_generate()

    assert call(GENERATION_REASONS_META_KEY, expected) in db.set_cache_meta.await_args_list


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [RuntimeError("upstream"), TeamsData(season=2026), "incomplete"]
)
async def test_teams_data_problems_are_reported_as_data_unavailable(tmp_path, outcome):
    service = Mock()
    if isinstance(outcome, Exception):
        service.get_teams_and_drivers = AsyncMock(side_effect=outcome)
    elif outcome == "incomplete":
        service.get_teams_and_drivers = AsyncMock(return_value=_teams_data())
    else:
        service.get_teams_and_drivers = AsyncMock(return_value=outcome)
    reasons: set[DegradedReason] = set()
    with (
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch("app.services.teams_service.TeamsService", return_value=service),
        patch("app.services.teams_service.is_teams_data_cacheable", return_value=False),
    ):
        result = await scheduler_generation._generate_teams_bmp_variants(
            images_dir=tmp_path, db=object(), reasons=reasons
        )

    assert result == (set(), 1)
    assert reasons == {DegradedReason.TEAMS_DATA_UNAVAILABLE}


def test_on_demand_rendering_outside_the_profile_still_works(monkeypatch, profile):
    """A language and palette that are never pregenerated are rendered and cached on request."""
    from app.utils import rate_limit

    profile("cs", "bwry")
    monkeypatch.setattr(rate_limit.config, "RATE_LIMIT_ENABLED", False)
    client = TestClient(app)

    first = client.get("/calendar.bmp", params={"lang": "de", "display": "1bit"})
    second = client.get("/calendar.bmp", params={"lang": "de", "display": "1bit"})

    assert first.status_code == 200
    assert first.content[:2] == b"BM"
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.content == first.content


# --- limited weather prefetch ------------------------------------------------------------


def _race(circuit_id: str, start: datetime, lat: str = "1", long: str = "2") -> dict:
    return {
        "race_name": circuit_id,
        "circuit": {"circuitId": circuit_id, "name": circuit_id, "lat": lat, "long": long},
        "schedule": [{"name": "Race", "datetime": start.isoformat()}],
    }


async def _run_weather_job(f1: SimpleNamespace, *, prefetch_all: bool) -> AsyncMock:
    fetch = AsyncMock(return_value=WeatherData(20.0, 1, 0))
    db = SimpleNamespace(save_circuit_weather=AsyncMock())
    with (
        patch("app.services.scheduler_weather.config.WEATHER_ENABLED", True),
        patch("app.services.scheduler_weather.config.WEATHER_PREFETCH_ALL_CIRCUITS", prefetch_all),
        patch("app.services.scheduler_weather.get_database", return_value=db),
        patch("app.services.scheduler_weather.F1Service", return_value=f1),
        patch("app.services.scheduler_weather.WeatherService"),
        patch("app.services.scheduler_weather._fetch_single_circuit_weather", new=fetch),
        patch("app.services.scheduler_weather.set_cached_circuit_weather"),
        patch("app.services.scheduler_weather.asyncio.sleep", new=AsyncMock()),
    ):
        await scheduler_weather._fetch_all_circuits_weather_unlocked()
    return fetch


@pytest.mark.asyncio
async def test_limited_weather_prefetch_fetches_only_the_next_race_circuit():
    now = datetime.now(timezone.utc)
    season = [
        _race("past", now - timedelta(days=7), "10", "10"),
        _race("next", now + timedelta(days=3), "20", "20"),
        _race("later", now + timedelta(days=10), "30", "30"),
    ]
    f1 = SimpleNamespace(
        get_next_race_from_static=Mock(return_value=season[1]),
        get_all_races_from_static=Mock(side_effect=lambda year: season if year == now.year else []),
    )

    limited = await _run_weather_job(f1, prefetch_all=False)
    everything = await _run_weather_job(f1, prefetch_all=True)

    assert [c.args[1:] for c in limited.await_args_list] == [(20.0, 20.0)]
    assert len(everything.await_args_list) == 3


@pytest.mark.asyncio
async def test_limited_weather_prefetch_covers_the_race_after_lights_out():
    """During the grace window the selected race has started; the next one is warmed too."""
    now = datetime.now(timezone.utc)
    running = _race("running", now - timedelta(hours=1), "20", "20")
    following = _race("following", now + timedelta(days=7), "30", "30")
    f1 = SimpleNamespace(
        get_next_race_from_static=Mock(return_value=running),
        get_all_races_from_static=Mock(
            side_effect=lambda year: [] if year == now.year else [running, following]
        ),
    )

    fetch = await _run_weather_job(f1, prefetch_all=False)

    assert [c.args[1:] for c in fetch.await_args_list] == [(20.0, 20.0), (30.0, 30.0)]


@pytest.mark.asyncio
async def test_limited_weather_prefetch_without_upcoming_race_does_nothing(caplog):
    f1 = SimpleNamespace(
        get_next_race_from_static=Mock(return_value=None),
        get_all_races_from_static=Mock(return_value=[{"circuit": {}, "schedule": []}]),
    )

    with caplog.at_level(logging.WARNING, logger="app.services.scheduler_weather"):
        fetch = await _run_weather_job(f1, prefetch_all=False)

    fetch.assert_not_awaited()
    assert "No upcoming race" in caplog.text


@pytest.mark.asyncio
async def test_other_circuits_are_still_fetched_on_demand_when_prefetch_is_limited(monkeypatch):
    """A calendar for a circuit outside the prefetch set resolves weather live, as before."""
    from app.services import weather_service

    weather_service.clear_circuit_weather_cache()
    live = WeatherData(18.0, 3, 40)
    service = SimpleNamespace(get_current_weather=AsyncMock(return_value=live))
    monkeypatch.setattr(weather_service.config, "WEATHER_PREFETCH_ALL_CIRCUITS", False)
    monkeypatch.setattr(weather_service, "_build_weather_service", lambda: service)

    current, _ = await weather_service._resolve_current_weather(
        circuit_id="elsewhere", lat=1.0, lon=2.0, weather_service=None
    )

    assert current is live
    assert weather_service.get_cached_circuit_weather("elsewhere") is live
    weather_service.clear_circuit_weather_cache()
