"""Test teams service helpers and season data."""

from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services.teams_service import TeamsService


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
