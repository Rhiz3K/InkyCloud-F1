"""Test renderer service."""

from io import BytesIO

import pytest
from PIL import Image

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


@pytest.fixture
def mock_race_data():
    """Create mock race data for testing."""
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
            {"name": "Qualifying", "display_time": "Sat 18:00"},
            {"name": "Race", "display_time": "Sun 17:00"},
        ],
    }


@pytest.fixture
def mock_historical_data():
    """Create mock historical data for testing."""
    return HistoricalData(
        season=2023,
        is_new_track=False,
        qualifying_results=[
            QualifyingResultEntry(
                position=1,
                driver=DriverInfo(
                    code="VER", given_name="Max", family_name="Verstappen"
                ),
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
                driver=DriverInfo(
                    code="LEC", given_name="Charles", family_name="Leclerc"
                ),
                constructor=ConstructorInfo(name="Ferrari"),
                q3_time="1:30.012",
            ),
        ],
        race_results=[
            RaceResultEntry(
                position=1,
                driver=DriverInfo(
                    code="VER", given_name="Max", family_name="Verstappen"
                ),
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
                driver=DriverInfo(
                    code="ALO", given_name="Fernando", family_name="Alonso"
                ),
                constructor=ConstructorInfo(name="Aston Martin"),
                time="+38.637",
            ),
        ],
    )


def test_render_calendar_english(mock_race_data):
    """Test rendering calendar in English."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    # Verify BMP data
    assert bmp_data is not None
    assert len(bmp_data) > 0

    # Verify it's a valid BMP
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


# ============================================================================
# Teams & Drivers Tests
# ============================================================================


@pytest.fixture
def mock_teams_data():
    """Create mock teams and drivers data for testing."""
    return TeamsData(
        season=2025,
        teams=[
            TeamEntry(
                constructor_id="red_bull",
                constructor_name="Red Bull",
                nationality="Austrian",
                drivers=[
                    TeamDriverEntry(
                        driver_id="verstappen",
                        driver_code="VER",
                        driver_number=1,
                        given_name="Max",
                        family_name="Verstappen",
                        nationality="Dutch",
                    ),
                    TeamDriverEntry(
                        driver_id="lawson",
                        driver_code="LAW",
                        driver_number=30,
                        given_name="Liam",
                        family_name="Lawson",
                        nationality="New Zealander",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="ferrari",
                constructor_name="Ferrari",
                nationality="Italian",
                drivers=[
                    TeamDriverEntry(
                        driver_id="leclerc",
                        driver_code="LEC",
                        driver_number=16,
                        given_name="Charles",
                        family_name="Leclerc",
                        nationality="Monegasque",
                    ),
                    TeamDriverEntry(
                        driver_id="hamilton",
                        driver_code="HAM",
                        driver_number=44,
                        given_name="Lewis",
                        family_name="Hamilton",
                        nationality="British",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="mclaren",
                constructor_name="McLaren",
                nationality="British",
                drivers=[
                    TeamDriverEntry(
                        driver_id="norris",
                        driver_code="NOR",
                        driver_number=4,
                        given_name="Lando",
                        family_name="Norris",
                        nationality="British",
                    ),
                    TeamDriverEntry(
                        driver_id="piastri",
                        driver_code="PIA",
                        driver_number=81,
                        given_name="Oscar",
                        family_name="Piastri",
                        nationality="Australian",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="mercedes",
                constructor_name="Mercedes",
                nationality="German",
                drivers=[
                    TeamDriverEntry(
                        driver_id="russell",
                        driver_code="RUS",
                        driver_number=63,
                        given_name="George",
                        family_name="Russell",
                        nationality="British",
                    ),
                    TeamDriverEntry(
                        driver_id="antonelli",
                        driver_code="ANT",
                        driver_number=12,
                        given_name="Andrea Kimi",
                        family_name="Antonelli",
                        nationality="Italian",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="aston_martin",
                constructor_name="Aston Martin",
                nationality="British",
                drivers=[
                    TeamDriverEntry(
                        driver_id="alonso",
                        driver_code="ALO",
                        driver_number=14,
                        given_name="Fernando",
                        family_name="Alonso",
                        nationality="Spanish",
                    ),
                    TeamDriverEntry(
                        driver_id="stroll",
                        driver_code="STR",
                        driver_number=18,
                        given_name="Lance",
                        family_name="Stroll",
                        nationality="Canadian",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="alpine",
                constructor_name="Alpine",
                nationality="French",
                drivers=[
                    TeamDriverEntry(
                        driver_id="gasly",
                        driver_code="GAS",
                        driver_number=10,
                        given_name="Pierre",
                        family_name="Gasly",
                        nationality="French",
                    ),
                    TeamDriverEntry(
                        driver_id="doohan",
                        driver_code="DOO",
                        driver_number=7,
                        given_name="Jack",
                        family_name="Doohan",
                        nationality="Australian",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="haas",
                constructor_name="Haas",
                nationality="American",
                drivers=[
                    TeamDriverEntry(
                        driver_id="ocon",
                        driver_code="OCO",
                        driver_number=31,
                        given_name="Esteban",
                        family_name="Ocon",
                        nationality="French",
                    ),
                    TeamDriverEntry(
                        driver_id="bearman",
                        driver_code="BEA",
                        driver_number=87,
                        given_name="Oliver",
                        family_name="Bearman",
                        nationality="British",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="rb",
                constructor_name="RB",
                nationality="Italian",
                drivers=[
                    TeamDriverEntry(
                        driver_id="tsunoda",
                        driver_code="TSU",
                        driver_number=22,
                        given_name="Yuki",
                        family_name="Tsunoda",
                        nationality="Japanese",
                    ),
                    TeamDriverEntry(
                        driver_id="hadjar",
                        driver_code="HAD",
                        driver_number=6,
                        given_name="Isack",
                        family_name="Hadjar",
                        nationality="French",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="williams",
                constructor_name="Williams",
                nationality="British",
                drivers=[
                    TeamDriverEntry(
                        driver_id="albon",
                        driver_code="ALB",
                        driver_number=23,
                        given_name="Alexander",
                        family_name="Albon",
                        nationality="Thai",
                    ),
                    TeamDriverEntry(
                        driver_id="sainz",
                        driver_code="SAI",
                        driver_number=55,
                        given_name="Carlos",
                        family_name="Sainz",
                        nationality="Spanish",
                    ),
                ],
            ),
            TeamEntry(
                constructor_id="sauber",
                constructor_name="Sauber",
                nationality="Swiss",
                drivers=[
                    TeamDriverEntry(
                        driver_id="hulkenberg",
                        driver_code="HUL",
                        driver_number=27,
                        given_name="Nico",
                        family_name="Hulkenberg",
                        nationality="German",
                    ),
                    TeamDriverEntry(
                        driver_id="bortoleto",
                        driver_code="BOR",
                        driver_number=5,
                        given_name="Gabriel",
                        family_name="Bortoleto",
                        nationality="Brazilian",
                    ),
                ],
            ),
        ],
    )


def test_render_teams_drivers_english(mock_teams_data):
    """Test rendering teams and drivers in English."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_teams_drivers(mock_teams_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_teams_drivers_czech(mock_teams_data):
    """Test rendering teams and drivers in Czech."""
    translator = get_translator("cs")
    renderer = Renderer(translator)
    bmp_data = renderer.render_teams_drivers(mock_teams_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_teams_drivers_empty():
    """Test rendering teams and drivers with no teams."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    empty_data = TeamsData(season=2025, teams=[])
    bmp_data = renderer.render_teams_drivers(empty_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_teams_drivers_partial_data():
    """Test rendering teams with partial driver info (missing numbers)."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    partial_data = TeamsData(
        season=2025,
        teams=[
            TeamEntry(
                constructor_id="test_team",
                constructor_name="Test Team",
                nationality="Test",
                drivers=[
                    TeamDriverEntry(
                        driver_id="driver1",
                        driver_code="",
                        driver_number=None,
                        given_name="Test",
                        family_name="Driver",
                        nationality="Test",
                    ),
                ],
            ),
        ],
    )
    bmp_data = renderer.render_teams_drivers(partial_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_with_new_track(mock_race_data):
    """Test rendering calendar when track is new (no historical data)."""
    translator = get_translator("cs")
    renderer = Renderer(translator)

    new_track_data = HistoricalData(is_new_track=True)
    bmp_data = renderer.render_calendar(mock_race_data, new_track_data)

    # Verify BMP data
    assert bmp_data is not None
    assert len(bmp_data) > 0

    # Verify it's a valid BMP
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_without_historical_data(mock_race_data):
    """Test rendering calendar without historical data (None)."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data, None)

    # Verify BMP data
    assert bmp_data is not None
    assert len(bmp_data) > 0

    # Verify it's a valid BMP
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


@pytest.fixture
def mock_driver_standings():
    return [
        DriverStanding(
            position=1,
            points=255.0,
            wins=7,
            driver_code="VER",
            driver_name="Verstappen",
            driver_given_name="Max",
            nationality="Dutch",
            constructor_name="Red Bull",
        ),
        DriverStanding(
            position=2,
            points=150.0,
            wins=2,
            driver_code="NOR",
            driver_name="Norris",
            driver_given_name="Lando",
            nationality="British",
            constructor_name="McLaren",
        ),
        DriverStanding(
            position=3,
            points=140.0,
            wins=1,
            driver_code="LEC",
            driver_name="Leclerc",
            driver_given_name="Charles",
            nationality="Monegasque",
            constructor_name="Ferrari",
        ),
    ]


@pytest.fixture
def mock_constructor_standings():
    return [
        ConstructorStanding(
            position=1,
            points=400.0,
            wins=9,
            constructor_name="Red Bull",
            nationality="Austrian",
        ),
        ConstructorStanding(
            position=2,
            points=280.0,
            wins=3,
            constructor_name="Ferrari",
            nationality="Italian",
        ),
        ConstructorStanding(
            position=3,
            points=250.0,
            wins=2,
            constructor_name="McLaren",
            nationality="British",
        ),
    ]


def test_render_standings_split(mock_driver_standings, mock_constructor_standings):
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_driver_standings,
        constructor_standings=mock_constructor_standings,
        view="split",
        season=2024,
        after_round=10,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_drivers_only(
    mock_driver_standings, mock_constructor_standings
):
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_driver_standings,
        constructor_standings=mock_constructor_standings,
        view="drivers",
        season=2024,
        after_round=10,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_constructors_only(
    mock_driver_standings, mock_constructor_standings
):
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_driver_standings,
        constructor_standings=mock_constructor_standings,
        view="constructors",
        season=2024,
        after_round=10,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_czech(mock_driver_standings, mock_constructor_standings):
    translator = get_translator("cs")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_driver_standings,
        constructor_standings=mock_constructor_standings,
        view="split",
        season=2024,
        after_round=10,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_empty():
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=[],
        constructor_standings=[],
        view="split",
        season=2024,
        after_round=0,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_24_drivers():
    """Test rendering standings with 24 drivers (2026 grid expansion)."""
    driver_codes = [
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
        "GAS",
        "TSU",
        "ALB",
        "SAR",
        "BOT",
        "ZHO",
        "MAG",
        "HUL",
        "RIC",
        "LAW",
        "BEA",
        "HAD",
        "ANT",
        "DOO",
    ]
    mock_24_drivers = [
        DriverStanding(
            position=i + 1,
            points=max(0, 400 - i * 15),
            wins=max(0, 10 - i),
            driver_code=driver_codes[i],
            driver_name=f"Driver{i + 1}",
            driver_given_name=f"First{i + 1}",
            nationality="Test",
            constructor_name=f"Team{(i // 2) + 1}",
        )
        for i in range(24)
    ]

    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_24_drivers,
        constructor_standings=[],
        view="drivers",
        season=2026,
        after_round=10,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_standings_20_drivers():
    """Test rendering standings with 20 drivers (current 2024-2025 grid)."""
    driver_codes = [
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
        "GAS",
        "TSU",
        "ALB",
        "SAR",
        "BOT",
        "ZHO",
        "MAG",
        "HUL",
        "RIC",
        "LAW",
    ]
    mock_20_drivers = [
        DriverStanding(
            position=i + 1,
            points=max(0, 400 - i * 18),
            wins=max(0, 8 - i),
            driver_code=driver_codes[i],
            driver_name=f"Driver{i + 1}",
            driver_given_name=f"First{i + 1}",
            nationality="Test",
            constructor_name=f"Team{(i // 2) + 1}",
        )
        for i in range(20)
    ]

    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_standings(
        driver_standings=mock_20_drivers,
        constructor_standings=[],
        view="drivers",
        season=2025,
        after_round=15,
    )

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


# ============================================================================
# Error Rendering Tests
# ============================================================================


def test_render_error_english():
    """Test rendering error message in English."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_error("Test error message")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_error_czech():
    """Test rendering error message in Czech."""
    translator = get_translator("cs")
    renderer = Renderer(translator)
    bmp_data = renderer.render_error("Chybová zpráva")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_error_empty_message():
    """Test rendering error with empty message."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_error("")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_error_long_message():
    """Test rendering error with long message (truncation)."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    long_message = "A" * 500
    bmp_data = renderer.render_error(long_message)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


# ============================================================================
# Calendar Edge Cases
# ============================================================================


def test_render_calendar_czech(mock_race_data):
    """Test rendering calendar in Czech."""
    translator = get_translator("cs")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_with_historical_data(mock_race_data, mock_historical_data):
    """Test rendering calendar with historical qualifying and race results."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data, mock_historical_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_minimal_schedule(mock_race_data):
    """Test rendering calendar with minimal schedule (only race)."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    minimal_data = mock_race_data.copy()
    minimal_data["schedule"] = [{"name": "Race", "display_time": "Sun 17:00"}]
    bmp_data = renderer.render_calendar(minimal_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_full_schedule(mock_race_data):
    """Test rendering calendar with full weekend schedule."""
    translator = get_translator("en")
    renderer = Renderer(translator)
    full_data = mock_race_data.copy()
    full_data["schedule"] = [
        {"name": "FP1", "display_time": "Fri 13:30"},
        {"name": "FP2", "display_time": "Fri 17:00"},
        {"name": "FP3", "display_time": "Sat 12:30"},
        {"name": "Qualifying", "display_time": "Sat 16:00"},
        {"name": "Race", "display_time": "Sun 15:00"},
    ]
    bmp_data = renderer.render_calendar(full_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"
