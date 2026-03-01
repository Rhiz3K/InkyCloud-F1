"""Performance benchmarks for the F1 E-Ink Calendar renderer."""

import pytest

from app.models import (
    ConstructorInfo,
    ConstructorStanding,
    DriverInfo,
    DriverStanding,
    HistoricalData,
    QualifyingResultEntry,
    RaceResultEntry,
    TeamDriverEntry,
    TeamEntry,
    TeamsData,
)
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer

# ============================================================================
# Shared fixtures
# ============================================================================


@pytest.fixture
def race_data():
    """Standard race data for benchmarks."""
    return {
        "race_name": "Test Grand Prix",
        "round": "1",
        "season": "2024",
        "circuit": {
            "circuitId": "test_circuit",
            "name": "Test Circuit",
            "location": "Test City",
            "country": "Test Country",
        },
        "race_date": "01.01.2024",
        "schedule": [
            {"name": "FP1", "display_time": "Fri 13:30"},
            {"name": "FP2", "display_time": "Fri 17:00"},
            {"name": "FP3", "display_time": "Sat 12:30"},
            {"name": "Qualifying", "display_time": "Sat 16:00"},
            {"name": "Race", "display_time": "Sun 15:00"},
        ],
    }


@pytest.fixture
def historical_data():
    """Historical data with qualifying and race results."""
    return HistoricalData(
        season=2023,
        is_new_track=False,
        qualifying_results=[
            QualifyingResultEntry(
                position=1,
                driver=DriverInfo(code="VER", given_name="Max", family_name="Verstappen"),
                constructor=ConstructorInfo(name="Red Bull"),
                q3_time="1:29.708",
            ),
            QualifyingResultEntry(
                position=2,
                driver=DriverInfo(code="PER", given_name="Sergio", family_name="Perez"),
                constructor=ConstructorInfo(name="Red Bull"),
                q3_time="1:29.846",
            ),
            QualifyingResultEntry(
                position=3,
                driver=DriverInfo(code="LEC", given_name="Charles", family_name="Leclerc"),
                constructor=ConstructorInfo(name="Ferrari"),
                q3_time="1:30.012",
            ),
        ],
        race_results=[
            RaceResultEntry(
                position=1,
                driver=DriverInfo(code="VER", given_name="Max", family_name="Verstappen"),
                constructor=ConstructorInfo(name="Red Bull"),
                time="1:33:56.736",
            ),
            RaceResultEntry(
                position=2,
                driver=DriverInfo(code="PER", given_name="Sergio", family_name="Perez"),
                constructor=ConstructorInfo(name="Red Bull"),
                time="+11.987",
            ),
            RaceResultEntry(
                position=3,
                driver=DriverInfo(code="ALO", given_name="Fernando", family_name="Alonso"),
                constructor=ConstructorInfo(name="Aston Martin"),
                time="+38.637",
            ),
        ],
    )


def _build_team(cid, cname, nat, drivers):
    """Build a TeamEntry from compact driver tuples."""
    return TeamEntry(
        constructor_id=cid,
        constructor_name=cname,
        nationality=nat,
        drivers=[
            TeamDriverEntry(
                driver_id=code.lower(),
                driver_code=code,
                driver_number=num,
                given_name=gn,
                family_name=fn,
                nationality=dnat,
            )
            for code, num, gn, fn, dnat in drivers
        ],
    )


@pytest.fixture
def teams_data():
    """Teams and drivers data for a full 10-team grid."""
    teams = [
        _build_team(
            "red_bull",
            "Red Bull",
            "Austrian",
            [
                ("VER", 1, "Max", "Verstappen", "Dutch"),
                ("LAW", 30, "Liam", "Lawson", "New Zealander"),
            ],
        ),
        _build_team(
            "ferrari",
            "Ferrari",
            "Italian",
            [
                ("LEC", 16, "Charles", "Leclerc", "Monegasque"),
                ("HAM", 44, "Lewis", "Hamilton", "British"),
            ],
        ),
        _build_team(
            "mclaren",
            "McLaren",
            "British",
            [
                ("NOR", 4, "Lando", "Norris", "British"),
                ("PIA", 81, "Oscar", "Piastri", "Australian"),
            ],
        ),
        _build_team(
            "mercedes",
            "Mercedes",
            "German",
            [
                ("RUS", 63, "George", "Russell", "British"),
                ("ANT", 12, "Andrea Kimi", "Antonelli", "Italian"),
            ],
        ),
        _build_team(
            "aston_martin",
            "Aston Martin",
            "British",
            [
                ("ALO", 14, "Fernando", "Alonso", "Spanish"),
                ("STR", 18, "Lance", "Stroll", "Canadian"),
            ],
        ),
        _build_team(
            "alpine",
            "Alpine",
            "French",
            [("GAS", 10, "Pierre", "Gasly", "French"), ("DOO", 7, "Jack", "Doohan", "Australian")],
        ),
        _build_team(
            "haas",
            "Haas",
            "American",
            [("OCO", 31, "Esteban", "Ocon", "French"), ("BEA", 87, "Oliver", "Bearman", "British")],
        ),
        _build_team(
            "rb",
            "RB",
            "Italian",
            [("TSU", 22, "Yuki", "Tsunoda", "Japanese"), ("HAD", 6, "Isack", "Hadjar", "French")],
        ),
        _build_team(
            "williams",
            "Williams",
            "British",
            [("ALB", 23, "Alexander", "Albon", "Thai"), ("SAI", 55, "Carlos", "Sainz", "Spanish")],
        ),
        _build_team(
            "sauber",
            "Sauber",
            "Swiss",
            [
                ("HUL", 27, "Nico", "Hulkenberg", "German"),
                ("BOR", 5, "Gabriel", "Bortoleto", "Brazilian"),
            ],
        ),
    ]
    return TeamsData(season=2025, teams=teams)


@pytest.fixture
def driver_standings():
    """Driver championship standings."""
    codes = [
        "VER",
        "NOR",
        "LEC",
        "SAI",
        "HAM",
        "RUS",
        "PIA",
        "ALO",
        "STR",
        "OCO",
    ]
    return [
        DriverStanding(
            position=i + 1,
            points=max(0, 400 - i * 30),
            wins=max(0, 8 - i),
            driver_code=codes[i],
            driver_name=f"Driver{i + 1}",
            driver_given_name=f"First{i + 1}",
            nationality="Test",
            constructor_name=f"Team{(i // 2) + 1}",
        )
        for i in range(10)
    ]


@pytest.fixture
def constructor_standings():
    """Constructor championship standings."""
    return [
        ConstructorStanding(
            position=i + 1,
            points=max(0, 600 - i * 50),
            wins=max(0, 10 - i * 2),
            constructor_name=f"Team{i + 1}",
            nationality="Test",
        )
        for i in range(10)
    ]


# ============================================================================
# 1-bit Renderer Benchmarks
# ============================================================================


@pytest.mark.benchmark
def test_bench_render_calendar(race_data):
    """Benchmark rendering the calendar view as a 1-bit BMP."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(race_data)
    assert len(bmp_data) > 0


@pytest.mark.benchmark
def test_bench_render_calendar_with_historical(race_data, historical_data):
    """Benchmark rendering the calendar with historical results."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(race_data, historical_data)
    assert len(bmp_data) > 0


@pytest.mark.benchmark
def test_bench_render_teams_drivers(teams_data):
    """Benchmark rendering the full teams and drivers grid."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_teams_drivers(teams_data)
    assert len(bmp_data) > 0


@pytest.mark.benchmark
def test_bench_render_standings_split(driver_standings, constructor_standings):
    """Benchmark rendering the split standings view."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=driver_standings,
        constructor_standings=constructor_standings,
        view="split",
        season=2024,
        after_round=10,
    )
    assert len(bmp_data) > 0


@pytest.mark.benchmark
def test_bench_render_error():
    """Benchmark rendering an error message screen."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_error("Something went wrong")
    assert len(bmp_data) > 0


# ============================================================================
# Spectra 6 Renderer Benchmarks
# ============================================================================


@pytest.mark.benchmark
def test_bench_spectra6_render_calendar(race_data):
    """Benchmark Spectra 6 (6-color) calendar rendering."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_calendar(race_data)
    assert len(bmp_data) > 0


@pytest.mark.benchmark
def test_bench_spectra6_render_calendar_with_historical(race_data, historical_data):
    """Benchmark Spectra 6 calendar with historical data overlay."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_calendar(race_data, historical_data)
    assert len(bmp_data) > 0
