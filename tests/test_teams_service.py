"""Test teams service helpers and season data."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services import teams_service as teams_service_module
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
    monkeypatch.setattr(teams_service_module, "SEASONS_DIR", seasons_dir)

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

    monkeypatch.setattr(teams_service_module, "fetch_with_retry", fake_fetch)

    data = await TeamsService()._fetch_from_api(2025)

    assert len(data.teams) == 1
    assert data.teams[0].drivers == []
    assert data.standings_complete is False
    assert teams_service_module.is_teams_data_cacheable(data) is False


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
