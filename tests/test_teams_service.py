"""Test teams service helpers and season data."""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest

from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services import teams_service as teams
from app.services.teams_service import TeamsService, get_default_teams_year
from app.utils.f1_season import get_current_f1_season


def test_load_2026_teams_data():
    teams_data = TeamsService()._load_from_json(2026)

    assert teams_data is not None

    red_bull = next(
        (team for team in teams_data.teams if "Red Bull Racing" in team.constructor_name), None
    )
    audi = next((team for team in teams_data.teams if team.constructor_name == "Audi"), None)
    cadillac = next(
        (team for team in teams_data.teams if "Cadillac" in team.constructor_name), None
    )

    assert red_bull is not None
    assert audi is not None
    assert cadillac is not None

    assert [driver.driver_number for driver in red_bull.drivers] == [3, 6]
    assert [driver.name for driver in audi.drivers] == ["Gabriel Bortoleto", "Nico Hülkenberg"]
    assert [driver.name for driver in cadillac.drivers] == ["Sergio Pérez", "Valtteri Bottas"]


def test_bundled_teams_loader_rejects_symlinked_files(tmp_path, monkeypatch):
    seasons_dir = tmp_path / "seasons"
    seasons_dir.mkdir()
    outside_file = tmp_path / "2026_teams.json"
    outside_file.write_text('{"year": 2026, "teams": []}', encoding="utf-8")
    (seasons_dir / "2026_teams.json").symlink_to(outside_file)
    monkeypatch.setattr(teams, "SEASONS_DIR", seasons_dir)

    assert TeamsService()._load_from_json(2026) is None


def test_apply_manual_2026_driver_number_overrides_uses_driver_id():
    teams_data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="McLaren-Mercedes",
                drivers=[
                    TeamDriverEntry(driver_id="norris", name="Lando Norris", driver_number=4),
                ],
            ),
            TeamEntry(
                constructor_name="Red Bull Racing-Red Bull Ford",
                drivers=[
                    TeamDriverEntry(driver_id="verstappen", name="Max Verstappen", driver_number=1),
                    TeamDriverEntry(driver_id="hadjar", name="Isack Hadjar", driver_number=99),
                ],
            ),
            TeamEntry(
                constructor_name="Cadillac-Ferrari",
                drivers=[
                    TeamDriverEntry(driver_id="perez", name="Sergio Perez", driver_number=None),
                    TeamDriverEntry(driver_id="bottas", name="Valtteri Bottas", driver_number=0),
                ],
            ),
        ],
    )

    updated = TeamsService()._apply_manual_overrides(teams_data)

    mclaren = updated.teams[0]
    red_bull = updated.teams[1]
    cadillac = updated.teams[2]

    assert [driver.driver_number for driver in mclaren.drivers] == [1]
    assert [driver.driver_number for driver in red_bull.drivers] == [3, 6]
    assert [driver.driver_number for driver in cadillac.drivers] == [11, 77]


def test_merge_standings_matches_ascii_names_against_diacritics():
    teams_data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Audi",
                drivers=[TeamDriverEntry(name="Nico Hülkenberg")],
            ),
            TeamEntry(
                constructor_name="Cadillac-Ferrari",
                drivers=[TeamDriverEntry(name="Sergio Pérez")],
            ),
        ],
    )

    driver_standings = {
        "Nico Hulkenberg": {"position": 9, "points": 2.0, "wins": 0},
        "Sergio Perez": {"position": 16, "points": 0.0, "wins": 0},
    }

    updated = TeamsService()._merge_standings(teams_data, driver_standings, {})

    assert updated.teams[0].drivers[0].position == 9
    assert updated.teams[1].drivers[0].position == 16


def test_match_constructor_name_handles_sponsor_prefixed_williams():
    matched = TeamsService._match_constructor_name(
        "Atlassian Williams-Mercedes",
        ["Mercedes", "Ferrari", "Williams"],
    )

    assert matched == "Williams"


@pytest.mark.asyncio
async def test_get_teams_and_drivers_marks_standings_failure_as_incomplete(monkeypatch):
    service = TeamsService()
    service._cache.clear()
    teams_data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Audi",
                drivers=[TeamDriverEntry(driver_id="hulkenberg", name="Nico Hülkenberg")],
            )
        ],
    )
    calls = 0

    async def fail_standings(_year: int):
        nonlocal calls
        calls += 1
        raise RuntimeError("standings unavailable")

    monkeypatch.setattr(service, "_load_from_json", lambda _year: teams_data)
    monkeypatch.setattr(service, "_fetch_standings", fail_standings)

    first = await service.get_teams_and_drivers(2026)
    second = await service.get_teams_and_drivers(2026)

    assert first.teams
    assert first.standings_complete is False
    assert second.standings_complete is False
    assert calls == 2


def test_get_current_f1_season_falls_back_to_current_year_for_future_seasons(caplog):
    future_date = datetime(2027, 4, 1, tzinfo=timezone.utc)

    with caplog.at_level("WARNING"):
        season = get_current_f1_season(future_date)

    assert season == 2027
    assert "falling back to current year 2027" in caplog.text


def test_get_default_teams_year_falls_back_to_latest_bundled_season(monkeypatch):
    monkeypatch.setattr("app.services.teams_service.get_current_f1_season", lambda: 2027)

    assert get_default_teams_year() == 2026


@pytest.mark.asyncio
async def test_api_fallback_without_standings_is_not_cacheable(monkeypatch):
    class Response:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    async def fake_fetch(_client, url, **_kwargs):
        if "/drivers.json" in url:
            return Response(
                {
                    "MRData": {
                        "DriverTable": {
                            "Drivers": [
                                {
                                    "driverId": "verstappen",
                                    "givenName": "Max",
                                    "familyName": "Verstappen",
                                }
                            ]
                        }
                    }
                }
            )
        if "/constructors.json" in url:
            return Response(
                {
                    "MRData": {
                        "ConstructorTable": {
                            "Constructors": [{"constructorId": "red_bull", "name": "Red Bull"}]
                        }
                    }
                }
            )
        return Response({"MRData": {"StandingsTable": {"StandingsLists": []}}})

    monkeypatch.setattr(teams, "fetch_with_retry", fake_fetch)

    data = await TeamsService()._fetch_from_api(2025)

    assert len(data.teams) == 1
    assert data.teams[0].drivers == []
    assert data.standings_complete is False
    assert teams.is_teams_data_cacheable(data) is False


@pytest.mark.asyncio
async def test_invalid_year_is_rejected_before_fetch_lock_creation():
    service = TeamsService()

    with pytest.raises(ValueError, match="Unsupported F1 season"):
        await service.get_teams_and_drivers(999999)


@pytest.mark.asyncio
async def test_waiting_fetch_rechecks_negative_cache_inside_lock(monkeypatch):
    """Concurrent misses should produce one empty upstream fetch, not a serialized stampede."""
    service = TeamsService()
    service._cache.clear()
    service._negative_cache.clear()
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    calls = 0

    async def load_empty(year: int) -> TeamsData:
        nonlocal calls
        calls += 1
        fetch_started.set()
        await release_fetch.wait()
        return TeamsData(season=year, teams=[], standings_complete=False)

    monkeypatch.setattr(service, "_load_teams_and_drivers", load_empty)
    first_task = asyncio.create_task(service.get_teams_and_drivers(2026))
    await fetch_started.wait()
    second_task = asyncio.create_task(service.get_teams_and_drivers(2026))
    await asyncio.sleep(0)
    release_fetch.set()

    first, second = await asyncio.gather(first_task, second_task)

    assert not first.teams
    assert not second.teams
    assert calls == 1
    service._negative_cache.clear()


# Extended coverage for teams API normalization and cache behavior.


@pytest.fixture(autouse=True)
def clear_shared_caches():
    teams.TeamsService._shared_cache.clear()
    teams.TeamsService._negative_cache.clear()
    yield
    teams.TeamsService._shared_cache.clear()
    teams.TeamsService._negative_cache.clear()


@pytest.mark.parametrize(
    ("payload", "rows_name", "expected"),
    [
        ({}, "DriverStandings", []),
        (
            {"MRData": {"StandingsTable": {"StandingsLists": "invalid"}}},
            "DriverStandings",
            [],
        ),
        (
            {"MRData": {"StandingsTable": {"StandingsLists": ["invalid"]}}},
            "DriverStandings",
            [],
        ),
        (
            {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": "invalid"}]}}},
            "DriverStandings",
            [],
        ),
        (
            {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [{"id": 1}]}]}}},
            "DriverStandings",
            [{"id": 1}],
        ),
    ],
)
def test_extract_standings_rows_validates_nested_shapes(payload, rows_name, expected):
    assert teams._extract_standings_rows(payload, rows_name) == expected


@pytest.mark.asyncio
async def test_fetch_api_standings_rows_fetches_both_tables():
    driver_payload = {
        "MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [{"driver": 1}]}]}}
    }
    constructor_payload = {
        "MRData": {
            "StandingsTable": {"StandingsLists": [{"ConstructorStandings": [{"constructor": 1}]}]}
        }
    }
    fetch = AsyncMock(
        side_effect=[
            SimpleNamespace(json=lambda: driver_payload),
            SimpleNamespace(json=lambda: constructor_payload),
        ]
    )
    with patch("app.services.teams_service.fetch_with_retry", new=fetch):
        result = await teams._fetch_api_standings_rows(object(), "https://api.example", 2026)

    assert result == ([{"driver": 1}], [{"constructor": 1}])
    assert [item.args[1] for item in fetch.await_args_list] == [
        "https://api.example/2026/driverStandings.json",
        "https://api.example/2026/constructorStandings.json",
    ]
    pacers = [item.kwargs["pacer"] for item in fetch.await_args_list]
    assert pacers[0] is pacers[1]


def test_api_builders_normalize_driver_constructor_and_standings_data():
    standings = [
        {
            "position": "1",
            "points": "25.5",
            "wins": "2",
            "Driver": {"driverId": "driver"},
            "Constructors": [{"constructorId": "old"}, {"constructorId": "team"}],
        },
        {"Driver": {"driverId": "unassigned"}, "Constructors": []},
    ]
    driver_rows = [
        {
            "driverId": "driver",
            "code": "DRV",
            "permanentNumber": "7",
            "givenName": "Test",
            "familyName": "Driver",
            "nationality": "Testland",
        },
        {"driverId": "unassigned", "givenName": "No", "familyName": "Number"},
    ]
    assignments, drivers = teams._build_driver_api_maps(standings, driver_rows)
    constructors = teams._build_constructor_api_map(
        [
            {
                "position": "1",
                "points": "40.5",
                "Constructor": {"constructorId": "team"},
            }
        ]
    )
    built = teams._build_api_teams(
        [{"constructorId": "team", "name": "Team", "nationality": "Testland"}],
        assignments,
        drivers,
        constructors,
    )

    assert assignments == {"driver": "team"}
    assert drivers["driver"].driver_number == 7
    assert drivers["unassigned"].driver_number is None
    assert built[0].position == 1
    assert [driver.driver_id for driver in built[0].drivers] == ["driver"]
    assert teams._has_complete_api_standings(built, standings, list(constructors.values())) is True
    assert teams._has_complete_api_standings([], standings, list(constructors.values())) is False


def test_default_teams_year_covers_current_empty_and_future_only_bundles(tmp_path):
    with (
        patch("app.services.teams_service.SEASONS_DIR", tmp_path),
        patch("app.services.teams_service.get_current_f1_season", return_value=2026),
    ):
        (tmp_path / "2026_teams.json").touch()
        assert teams.get_default_teams_year() == 2026
        (tmp_path / "2026_teams.json").unlink()
        assert teams.get_default_teams_year() == 2026
        (tmp_path / "2027_teams.json").touch()
        assert teams.get_default_teams_year() == 2027


def test_cache_entry_and_service_positive_and_expired_caches():
    data = TeamsData(season=2026, teams=[TeamEntry(constructor_name="Team")])
    with patch("app.services.teams_service.time.time", return_value=100.0):
        entry = teams.CacheEntry(data, ttl=10)
    with patch("app.services.teams_service.time.time", return_value=105.0):
        assert entry.is_valid() is True
    with patch("app.services.teams_service.time.time", return_value=111.0):
        assert entry.is_valid() is False

    service = teams.TeamsService()
    service._set_cache(2026, data)
    assert service._get_cached(2026) is data


def test_negative_cache_evicts_expired_entries():
    teams.TeamsService._negative_cache[2026] = time.time() - 1

    assert teams.TeamsService._is_negative_cached(2026) is False
    assert 2026 not in teams.TeamsService._negative_cache


def test_override_driver_id_uses_fallback_and_missing_id_paths():
    driver = TeamDriverEntry(driver_id="unknown", name="Lando Norris")
    empty = TeamDriverEntry(driver_id="", name="Unknown Driver")
    with patch(
        "app.services.teams_service.get_season_driver_number_by_id",
        side_effect=lambda driver_id, season: 4 if driver_id == "norris" else None,
    ):
        assert teams.TeamsService._get_override_driver_id(driver, 2026) == "norris"
        assert teams.TeamsService._get_override_driver_id(empty, 2025) is None
        data = TeamsData(
            season=2025,
            teams=[TeamEntry(constructor_name="Team", drivers=[empty])],
        )
        assert teams.TeamsService._apply_manual_overrides(data) is data


def test_load_from_json_rejects_invalid_year_and_malformed_file(tmp_path):
    service = teams.TeamsService()
    with patch("app.services.teams_service.is_supported_f1_season", return_value=False):
        assert service._load_from_json(1800) is None

    malformed = tmp_path / "2026_teams.json"
    malformed.write_text("not json", encoding="utf-8")
    with (
        patch("app.services.teams_service.is_supported_f1_season", return_value=True),
        patch("app.services.teams_service._find_bundled_teams_path", return_value=malformed),
    ):
        assert service._load_from_json(2026) is None


@pytest.mark.asyncio
async def test_fetch_standings_normalizes_driver_and_constructor_tables():
    driver_payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "DriverStandings": [
                            {
                                "position": "1",
                                "points": "25",
                                "wins": "1",
                                "Driver": {"givenName": "Test", "familyName": "Driver"},
                            }
                        ]
                    }
                ]
            }
        }
    }
    constructor_payload = {
        "MRData": {
            "StandingsTable": {
                "StandingsLists": [
                    {
                        "ConstructorStandings": [
                            {
                                "position": "1",
                                "points": "40",
                                "Constructor": {"name": "Team"},
                            }
                        ]
                    }
                ]
            }
        }
    }
    fetch = AsyncMock(
        side_effect=[
            SimpleNamespace(json=lambda: driver_payload),
            SimpleNamespace(json=lambda: constructor_payload),
        ]
    )
    with (
        patch("app.services.teams_service.get_shared_http_client", return_value=object()),
        patch(
            "app.services.teams_service.get_jolpica_base_url", return_value="https://api.example"
        ),
        patch("app.services.teams_service.fetch_with_retry", new=fetch),
    ):
        result = await teams.TeamsService()._fetch_standings(2026)

    assert result == (
        {"Test Driver": {"position": 1, "points": 25.0, "wins": 1}},
        {"Team": {"position": 1, "points": 40.0}},
    )
    pacers = [item.kwargs["pacer"] for item in fetch.await_args_list]
    assert pacers[0] is pacers[1]


@pytest.mark.asyncio
async def test_fetch_standings_handles_empty_tables():
    response = SimpleNamespace(json=lambda: {})
    with (
        patch("app.services.teams_service.get_shared_http_client", return_value=object()),
        patch("app.services.teams_service.fetch_with_retry", new=AsyncMock(return_value=response)),
    ):
        assert await teams.TeamsService()._fetch_standings(2026) == ({}, {})


@pytest.mark.parametrize(
    ("json_name", "api_names", "expected"),
    [
        ("Ferrari", ["Ferrari"], "Ferrari"),
        ("Ferrari Racing", ["Ferrari"], "Ferrari"),
        ("Kick Sauber", ["Sauber"], "Sauber"),
        ("Unknown", ["Ferrari"], None),
    ],
)
def test_match_constructor_name_paths(json_name, api_names, expected):
    assert teams.TeamsService._match_constructor_name(json_name, api_names) == expected


def test_match_constructor_mapping_continues_when_target_is_absent():
    assert teams.TeamsService._match_constructor_name("Kick Sauber", ["Ferrari"]) is None


def test_merge_standings_marks_unmatched_entities_and_uses_family_fallback():
    matched_driver = TeamDriverEntry(name="Alice Racer")
    unmatched_driver = TeamDriverEntry(name="No Match")
    data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Unknown Team",
                drivers=[matched_driver, unmatched_driver],
            )
        ],
    )
    result = teams.TeamsService()._merge_standings(
        data,
        {"Other Racer": {"position": 2, "points": 10.0, "wins": 0}},
        {"Known Team": {"position": 1, "points": 20.0}},
    )

    assert matched_driver.position == 2
    assert unmatched_driver.position is None
    assert result.standings_complete is False


def test_merge_standings_covers_direct_clean_and_short_name_matches():
    direct = TeamDriverEntry(name="Direct Driver")
    suffix = TeamDriverEntry(name="Suffix Driver Jr.")
    short = TeamDriverEntry(name="Solo")
    data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Team",
                drivers=[direct, suffix, short],
            )
        ],
    )
    result = teams.TeamsService()._merge_standings(
        data,
        {
            "Direct Driver": {"position": 1, "points": 25.0, "wins": 1},
            "Suffix Driver": {"position": 2, "points": 18.0, "wins": 0},
        },
        {"Team": {"position": 1, "points": 43.0}},
    )

    assert direct.position == 1
    assert suffix.position == 2
    assert short.position is None
    assert result.teams[0].points == 43.0
    assert result.standings_complete is False

    no_standings = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Team",
                drivers=[TeamDriverEntry(name="No Standings")],
            )
        ],
    )
    assert teams.TeamsService()._merge_standings(no_standings, {}, {}).standings_complete is False


@pytest.mark.asyncio
async def test_fetch_from_api_uses_previous_standings_and_builds_complete_team():
    drivers = {
        "MRData": {
            "DriverTable": {
                "Drivers": [
                    {
                        "driverId": "driver",
                        "givenName": "Test",
                        "familyName": "Driver",
                    }
                ]
            }
        }
    }
    constructors = {
        "MRData": {
            "ConstructorTable": {"Constructors": [{"constructorId": "team", "name": "Team"}]}
        }
    }
    responses = [
        SimpleNamespace(json=lambda: drivers),
        SimpleNamespace(json=lambda: constructors),
        SimpleNamespace(json=lambda: {}),
        SimpleNamespace(json=lambda: {}),
    ]
    fallback_driver = [
        {
            "position": "1",
            "points": "25",
            "wins": "1",
            "Driver": {"driverId": "driver"},
            "Constructors": [{"constructorId": "team"}],
        }
    ]
    fallback_constructor = [
        {
            "position": "1",
            "points": "40",
            "Constructor": {"constructorId": "team"},
        }
    ]
    with (
        patch("app.services.teams_service.get_shared_http_client", return_value=object()),
        patch("app.services.teams_service.fetch_with_retry", new=AsyncMock(side_effect=responses)),
        patch(
            "app.services.teams_service._fetch_api_standings_rows",
            new=AsyncMock(return_value=(fallback_driver, fallback_constructor)),
        ) as fallback,
    ):
        result = await teams.TeamsService()._fetch_from_api(2026)

    fallback.assert_awaited_once()
    assert result.standings_complete is True
    assert result.teams[0].drivers[0].name == "Test Driver"


@pytest.mark.asyncio
async def test_get_teams_and_drivers_covers_positive_negative_and_double_checked_caches():
    service = teams.TeamsService()
    data = TeamsData(season=2026, teams=[TeamEntry(constructor_name="Team")])
    service._set_cache(2026, data)
    assert await service.get_teams_and_drivers(2026) is data

    service._cache.clear()
    service._negative_cache[2026] = time.time() + 60
    negative = await service.get_teams_and_drivers(2026)
    assert negative.teams == []

    service._negative_cache.clear()
    with patch.object(service, "_get_cached", side_effect=[None, data]):
        assert await service.get_teams_and_drivers(2026) is data

    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_is_negative_cached", side_effect=[False, True]),
    ):
        assert (await service.get_teams_and_drivers(2026)).teams == []


@pytest.mark.asyncio
async def test_get_teams_and_drivers_sets_and_clears_negative_cache():
    service = teams.TeamsService()
    empty = TeamsData(season=2026, teams=[], standings_complete=False)
    complete = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Team")],
        standings_complete=True,
    )
    with patch.object(service, "_load_teams_and_drivers", new=AsyncMock(return_value=empty)):
        assert (await service.get_teams_and_drivers(2026)).teams == []
    assert 2026 in service._negative_cache

    service._negative_cache[2026] = 0
    with patch.object(service, "_load_teams_and_drivers", new=AsyncMock(return_value=complete)):
        assert await service.get_teams_and_drivers(2026) is complete
    assert 2026 not in service._negative_cache


@pytest.mark.asyncio
async def test_load_teams_and_drivers_covers_default_cache_fallback_cache_and_errors():
    service = teams.TeamsService()
    complete = TeamsData(
        season=2026,
        teams=[TeamEntry(constructor_name="Team")],
        standings_complete=True,
    )
    with (
        patch("app.services.teams_service.get_default_teams_year", return_value=2026),
        patch.object(service, "_get_cached", return_value=complete),
    ):
        assert await service._load_teams_and_drivers(None) is complete

    bundled = TeamsData(season=2026, teams=[TeamEntry(constructor_name="Team")])
    enriched = bundled.model_copy(update={"standings_complete": True})
    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_load_from_json", return_value=bundled),
        patch.object(
            service,
            "_fetch_standings",
            new=AsyncMock(side_effect=[({}, {}), ({"d": {}}, {"c": {}})]),
        ) as fetch,
        patch.object(service, "_merge_standings", return_value=enriched),
        patch.object(service, "_set_cache") as set_cache,
    ):
        assert await service._load_teams_and_drivers(2026) is enriched
    assert fetch.await_args_list == [call(2026), call(2025)]
    set_cache.assert_called_once_with(2026, enriched)

    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_load_from_json", return_value=None),
        patch.object(service, "_fetch_from_api", new=AsyncMock(return_value=complete)),
        patch.object(service, "_set_cache") as set_cache,
    ):
        assert await service._load_teams_and_drivers(2026) is complete
    set_cache.assert_called_once_with(2026, complete)

    degraded = bundled.model_copy(update={"standings_complete": False})
    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_load_from_json", return_value=bundled),
        patch.object(
            service,
            "_fetch_standings",
            new=AsyncMock(return_value=({"driver": {}}, {"constructor": {}})),
        ),
        patch.object(service, "_merge_standings", return_value=degraded),
        patch.object(service, "_set_cache") as set_cache,
    ):
        assert await service._load_teams_and_drivers(2026) is degraded
    set_cache.assert_not_called()

    api_degraded = TeamsData(season=2026, teams=[], standings_complete=False)
    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_load_from_json", return_value=None),
        patch.object(service, "_fetch_from_api", new=AsyncMock(return_value=api_degraded)),
        patch.object(service, "_set_cache") as set_cache,
    ):
        assert await service._load_teams_and_drivers(2026) is api_degraded
    set_cache.assert_not_called()

    with (
        patch.object(service, "_get_cached", return_value=None),
        patch.object(service, "_load_from_json", side_effect=RuntimeError("failed")),
    ):
        result = await service._load_teams_and_drivers(2026)
    assert result.teams == []
    assert result.standings_complete is False
