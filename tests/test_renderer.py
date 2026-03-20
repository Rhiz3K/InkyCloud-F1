"""Test renderer service."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

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
from app.services import bwr_renderer as bwr_renderer_module
from app.services import bwry_renderer as bwry_renderer_module
from app.services import renderer as renderer_module
from app.services import spectra6_renderer as spectra6_renderer_module
from app.services.bwr_renderer import BwrColors, BwrRenderer
from app.services.bwry_renderer import BwryColors, BwryRenderer
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Colors, Spectra6Renderer
from app.services.teams_service import TeamsService
from app.services.weather_service import WeatherData
from app.utils.bmp import map_to_bwr_palette, map_to_bwry_palette


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


@pytest.fixture
def mock_teams_data():
    """Create sample teams data for team screen rendering."""
    return TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="McLaren-Mercedes",
                chassis="MCL40",
                power_unit="Mercedes-AMG F1 M17",
                position=1,
                points=38.0,
                drivers=[
                    TeamDriverEntry(
                        driver_id="norris",
                        name="Lando Norris",
                        driver_number=1,
                        position=1,
                        points=25.0,
                    ),
                    TeamDriverEntry(
                        driver_id="piastri",
                        name="Oscar Piastri",
                        driver_number=81,
                        position=3,
                        points=13.0,
                    ),
                ],
            ),
            TeamEntry(
                constructor_name="Ferrari",
                chassis="SF-26",
                power_unit="Ferrari 067/6",
                position=2,
                points=27.0,
                drivers=[
                    TeamDriverEntry(
                        driver_id="leclerc",
                        name="Charles Leclerc",
                        driver_number=16,
                        position=2,
                        points=18.0,
                    ),
                    TeamDriverEntry(
                        driver_id="hamilton",
                        name="Lewis Hamilton",
                        driver_number=44,
                        position=5,
                        points=9.0,
                    ),
                ],
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


def test_renderer_draws_cancelled_label_in_countdown(monkeypatch):
    """Cancelled races should render a centred cancelled label instead of countdown."""
    renderer = Renderer(get_translator("en"))
    image = Image.new("1", (800, 480), 1)
    draw = ImageDraw.Draw(image)
    captured_text = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(text)
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy_text)

    bottom = renderer._draw_countdown_box(
        draw,
        {
            "is_cancelled": True,
            "schedule": [{"name": "Race", "datetime": "2099-04-12T15:00:00+00:00"}],
        },
        220,
    )

    assert "CANCELLED" in captured_text
    assert bottom > 220

    buffer = BytesIO()
    image.save(buffer, format="BMP")
    rendered = Image.open(BytesIO(buffer.getvalue()))
    assert rendered.format == "BMP"
    assert rendered.size == (800, 480)
    assert rendered.mode == "1"


def test_spectra6_renderer_draws_cancelled_label_in_countdown(monkeypatch):
    """Spectra 6 renderer should also render the cancelled countdown label."""
    renderer = Spectra6Renderer(get_translator("cs"))
    image = Image.new("RGB", (800, 480), Spectra6Colors.WHITE)
    draw = ImageDraw.Draw(image)
    captured_text = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(text)
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy_text)

    bottom = renderer._draw_countdown_box(
        draw,
        {
            "is_cancelled": True,
            "schedule": [{"name": "Race", "datetime": "2099-04-19T17:00:00+00:00"}],
        },
        220,
    )

    assert "ZRUŠENO" in captured_text
    assert bottom > 220

    buffer = BytesIO()
    image.save(buffer, format="BMP")
    rendered = Image.open(BytesIO(buffer.getvalue()))
    assert rendered.format == "BMP"
    assert rendered.size == (800, 480)
    assert rendered.mode == "RGB"


def test_renderer_draws_ongoing_label_for_recently_started_race(monkeypatch):
    """Races started less than three hours ago should render ongoing status."""
    renderer = Renderer(get_translator("cs"))
    image = Image.new("1", (800, 480), 1)
    draw = ImageDraw.Draw(image)
    captured_text = []
    original_text = draw.text
    fixed_now = datetime(2026, 4, 12, 16, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(text)
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy_text)
    monkeypatch.setattr(renderer_module, "datetime", FixedDatetime)

    bottom = renderer._draw_countdown_box(
        draw,
        {
            "schedule": [{"name": "Race", "datetime": "2026-04-12T15:00:00+00:00"}],
        },
        220,
    )

    assert "PROBÍHÁ" in captured_text
    assert bottom > 220


def test_spectra6_renderer_draws_completed_label_for_finished_race(monkeypatch):
    """Races started more than three hours ago should render completed status."""
    renderer = Spectra6Renderer(get_translator("cs"))
    image = Image.new("RGB", (800, 480), Spectra6Colors.WHITE)
    draw = ImageDraw.Draw(image)
    captured_text = []
    original_text = draw.text
    fixed_now = datetime(2026, 4, 12, 18, 30, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(text)
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy_text)
    monkeypatch.setattr(spectra6_renderer_module, "datetime", FixedDatetime)

    bottom = renderer._draw_countdown_box(
        draw,
        {
            "schedule": [{"name": "Race", "datetime": "2026-04-12T15:00:00+00:00"}],
        },
        220,
    )

    assert "DOKONČEN" in captured_text
    assert bottom > 220


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


def test_team_logo_key_supports_2026_new_teams():
    assert Renderer._get_team_logo_key("Audi") == "audi"
    assert Renderer._get_team_logo_key("Cadillac Formula 1 Team") == "cadillac"


def test_split_teams_for_columns_keeps_11th_team_visible():
    teams = list(range(11))

    left, right = Renderer._split_teams_for_columns(teams)

    assert left == [0, 1, 2, 3, 4, 5]
    assert right == [6, 7, 8, 9, 10]


def test_render_2026_teams_en():
    teams_data = TeamsService()._load_from_json(2026)
    assert teams_data is not None

    renderer = Renderer(get_translator("en"))
    bmp_data = renderer.render_teams_drivers(teams_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_2026_teams_cs():
    teams_data = TeamsService()._load_from_json(2026)
    assert teams_data is not None

    renderer = Renderer(get_translator("cs"))
    bmp_data = renderer.render_teams_drivers(teams_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_draw_driver_photo_prefers_explicit_number_over_asset():
    translator = get_translator("en")
    renderer = Renderer(translator)
    image = Image.new("1", (120, 24), 1)
    renderer._driver_photos["verstappen"] = Image.new("1", (100, 20), 0)

    width = renderer._draw_driver_photo(
        image,
        0,
        0,
        "Max Verstappen",
        size=18,
        driver_number=3,
    )

    assert 0 < width < 30


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


def test_render_standings_drivers_only(mock_driver_standings, mock_constructor_standings):
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


def test_render_standings_constructors_only(mock_driver_standings, mock_constructor_standings):
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


def test_render_calendar_with_non_preprocessed_track_image(mock_race_data, tmp_path, monkeypatch):
    """Test ImageOps processing branch for non-1-bit track images."""
    fake_track = Image.new("L", (200, 150), color=128)
    draw = ImageDraw.Draw(fake_track)
    draw.rectangle([20, 20, 180, 130], fill=255)
    draw.ellipse([50, 40, 150, 110], fill=0)

    tracks_dir = tmp_path / "tracks_processed"
    tracks_dir.mkdir()
    track_path = tracks_dir / "test_circuit.bmp"
    fake_track.save(track_path, "BMP")

    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", tracks_dir)

    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_render_calendar_with_rgb_track_image(mock_race_data, tmp_path, monkeypatch):
    """Test ImageOps inversion and cropping pipeline for RGB track images."""
    fake_track = Image.new("RGB", (200, 150), color=(200, 200, 200))
    draw = ImageDraw.Draw(fake_track)
    draw.ellipse([30, 30, 170, 120], fill=(0, 0, 0))

    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    track_path = tracks_dir / "test_circuit.png"
    fake_track.save(track_path, "PNG")

    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    translator = get_translator("en")
    renderer = Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "1"


def test_renderer_load_track_image_prefers_bw_variant(mock_race_data, tmp_path, monkeypatch):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(12, 34, 56)).save(tracks_dir / "test_circuit.png", "PNG")
    Image.new("RGB", (4, 4), color=(200, 210, 220)).save(tracks_dir / "test_circuit_bw.png", "PNG")

    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    track_image = Renderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (200, 210, 220)


def test_renderer_load_track_image_falls_back_to_generic_source(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(12, 34, 56)).save(tracks_dir / "test_circuit.png", "PNG")
    Image.new("RGB", (4, 4), color=(220, 20, 20)).save(tracks_dir / "test_circuit_bwr.png", "PNG")

    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    track_image = Renderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (12, 34, 56)


def test_renderer_load_track_image_uses_circuit_id_mapping(tmp_path, monkeypatch):
    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    Image.new("1", (4, 4), color=1).save(processed_dir / "las_vegas.bmp", "BMP")

    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)

    race_data = {
        "circuit": {
            "circuitId": "vegas",
            "location": "Las Vegas",
        }
    }

    track_image = Renderer._load_track_image(race_data)

    assert track_image is not None
    assert track_image.size == (4, 4)


def test_renderer_load_track_image_prefers_source_over_preprocessed(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(200, 210, 220)).save(tracks_dir / "test_circuit_bw.png", "PNG")

    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    Image.new("1", (4, 4), color=0).save(processed_dir / "test_circuit.bmp", "BMP")
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    track_image = Renderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (200, 210, 220)


def test_render_calendar_english_uses_track_source_variant(mock_race_data, tmp_path, monkeypatch):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (32, 32), color=(0, 0, 0)).save(tracks_dir / "test_circuit_bw.png", "PNG")

    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    renderer = Renderer(get_translator("en"))
    bmp_data = renderer.render_calendar(mock_race_data)

    image = Image.open(BytesIO(bmp_data))
    assert image.format == "BMP"
    assert image.size == (800, 480)
    assert image.mode == "1"


def test_render_calendar_czech_uses_track_source_variant(mock_race_data, tmp_path, monkeypatch):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (32, 32), color=(0, 0, 0)).save(tracks_dir / "test_circuit_bw.png", "PNG")

    processed_dir = tmp_path / "tracks_processed"
    processed_dir.mkdir()
    monkeypatch.setattr(renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(renderer_module, "TRACKS_PROCESSED_DIR", processed_dir)

    renderer = Renderer(get_translator("cs"))
    bmp_data = renderer.render_calendar(mock_race_data)

    image = Image.open(BytesIO(bmp_data))
    assert image.format == "BMP"
    assert image.size == (800, 480)
    assert image.mode == "1"


def test_bwr_renderer_load_track_image_prefers_bwr_variant(mock_race_data, tmp_path, monkeypatch):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(30, 40, 50)).save(tracks_dir / "test_circuit.png", "PNG")
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(tracks_dir / "test_circuit_bwr.png", "PNG")

    bwr_dir = tmp_path / "tracks_bwr"
    bwr_dir.mkdir()
    monkeypatch.setattr(bwr_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(bwr_renderer_module, "TRACKS_BWR_DIR", bwr_dir)

    track_image = BwrRenderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (255, 0, 0)


def test_bwr_renderer_load_track_image_prefers_source_over_preprocessed(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(tracks_dir / "test_circuit_bwr.png", "PNG")

    bwr_dir = tmp_path / "tracks_bwr"
    bwr_dir.mkdir()
    Image.new("RGB", (4, 4), color=(123, 45, 67)).save(bwr_dir / "test_circuit.bmp", "BMP")

    monkeypatch.setattr(bwr_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(bwr_renderer_module, "TRACKS_BWR_DIR", bwr_dir)

    track_image = BwrRenderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (255, 0, 0)


def test_bwr_renderer_load_track_image_falls_back_to_generic_source(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(30, 40, 50)).save(tracks_dir / "test_circuit.png", "PNG")

    bwr_dir = tmp_path / "tracks_bwr"
    bwr_dir.mkdir()
    monkeypatch.setattr(bwr_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(bwr_renderer_module, "TRACKS_BWR_DIR", bwr_dir)

    track_image = BwrRenderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (30, 40, 50)


def test_bwry_renderer_load_track_image_prefers_bwry_variant(mock_race_data, tmp_path, monkeypatch):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(30, 40, 50)).save(tracks_dir / "test_circuit.png", "PNG")
    Image.new("RGB", (4, 4), color=(244, 208, 42)).save(tracks_dir / "test_circuit_bwry.png", "PNG")

    bwry_dir = tmp_path / "tracks_bwry"
    bwry_dir.mkdir()
    monkeypatch.setattr(bwry_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(bwry_renderer_module, "TRACKS_BWRY_DIR", bwry_dir)

    track_image = BwryRenderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (244, 208, 42)


def test_bwry_renderer_load_track_image_prefers_source_over_preprocessed(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(244, 208, 42)).save(tracks_dir / "test_circuit_bwry.png", "PNG")

    bwry_dir = tmp_path / "tracks_bwry"
    bwry_dir.mkdir()
    Image.new("RGB", (4, 4), color=(12, 34, 56)).save(bwry_dir / "test_circuit.bmp", "BMP")

    monkeypatch.setattr(bwry_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(bwry_renderer_module, "TRACKS_BWRY_DIR", bwry_dir)

    track_image = BwryRenderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (244, 208, 42)


def test_spectra6_renderer_load_track_image_prefers_spectra6_variant(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(10, 10, 10)).save(tracks_dir / "test_circuit.png", "PNG")
    Image.new("RGB", (4, 4), color=(80, 128, 184)).save(
        tracks_dir / "test_circuit_spectra6.png", "PNG"
    )

    spectra6_dir = tmp_path / "tracks_spectra6"
    spectra6_dir.mkdir()
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_SPECTRA6_DIR", spectra6_dir)

    track_image = Spectra6Renderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (80, 128, 184)


def test_spectra6_renderer_load_track_image_prefers_source_over_preprocessed(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(80, 128, 184)).save(
        tracks_dir / "test_circuit_spectra6.png", "PNG"
    )

    spectra6_dir = tmp_path / "tracks_spectra6"
    spectra6_dir.mkdir()
    Image.new("RGB", (4, 4), color=(12, 34, 56)).save(spectra6_dir / "test_circuit.bmp", "BMP")
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_SPECTRA6_DIR", spectra6_dir)

    track_image = Spectra6Renderer._load_track_image(mock_race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (80, 128, 184)


def test_spectra6_renderer_load_track_image_can_fallback_to_location(
    mock_race_data, tmp_path, monkeypatch
):
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    Image.new("RGB", (4, 4), color=(45, 55, 65)).save(tracks_dir / "test_city_spectra6.png", "PNG")

    spectra6_dir = tmp_path / "tracks_spectra6"
    spectra6_dir.mkdir()
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_DIR", tracks_dir)
    monkeypatch.setattr(spectra6_renderer_module, "TRACKS_SPECTRA6_DIR", spectra6_dir)

    race_data = {
        **mock_race_data,
        "circuit": {
            **mock_race_data["circuit"],
            "circuitId": "",
            "location": "Test City",
        },
    }

    track_image = Spectra6Renderer._load_track_image(race_data)

    assert track_image is not None
    assert track_image.getpixel((0, 0)) == (45, 55, 65)


# ============================================================================
# Spectra 6 Renderer Tests (6-color E-Ink display)
# ============================================================================


def test_spectra6_render_calendar_english(mock_race_data):
    """Test Spectra 6 rendering calendar in English."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert img.getpalette() is not None


def test_spectra6_render_calendar_czech(mock_race_data):
    """Test Spectra 6 rendering calendar in Czech."""
    translator = get_translator("cs")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_calendar_with_historical_data(mock_race_data, mock_historical_data):
    """Test Spectra 6 rendering calendar with historical qualifying and race results."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data, mock_historical_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_calendar_new_track(mock_race_data):
    """Test Spectra 6 rendering calendar when track is new (no historical data)."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    new_track_data = HistoricalData(is_new_track=True)
    bmp_data = renderer.render_calendar(mock_race_data, new_track_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_error_english():
    """Test Spectra 6 rendering error message in English."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_error("Test error message")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_error_czech():
    """Test Spectra 6 rendering error message in Czech."""
    translator = get_translator("cs")
    renderer = Spectra6Renderer(translator)
    bmp_data = renderer.render_error("Chybová zpráva")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_colors_palette():
    assert Spectra6Colors.BLACK == (0x00, 0x00, 0x00)
    assert Spectra6Colors.WHITE == (0xFF, 0xFF, 0xFF)
    assert Spectra6Colors.RED == (0xFF, 0x00, 0x00)
    assert Spectra6Colors.YELLOW == (0xFF, 0xD8, 0x00)
    assert Spectra6Colors.GREEN == (0x00, 0xD8, 0x00)
    assert Spectra6Colors.BLUE == (0x00, 0xA8, 0xFF)

    assert len(Spectra6Colors.PALETTE) == 6
    assert Spectra6Colors.PALETTE[0] == Spectra6Colors.BLACK
    assert Spectra6Colors.PALETTE[1] == Spectra6Colors.WHITE


def test_spectra6_session_colors():
    """Test Spectra 6 session color assignment - only Race is RED, all else BLACK."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    # Only "Race" session should be RED
    assert renderer._get_session_color("Race") == Spectra6Colors.RED
    assert renderer._get_session_color("race") == Spectra6Colors.RED

    # All other sessions should be BLACK (simplified color scheme)
    assert renderer._get_session_color("Qualifying") == Spectra6Colors.BLACK
    assert renderer._get_session_color("Q1") == Spectra6Colors.BLACK
    assert renderer._get_session_color("Q2") == Spectra6Colors.BLACK
    assert renderer._get_session_color("Q3") == Spectra6Colors.BLACK

    assert renderer._get_session_color("FP1") == Spectra6Colors.BLACK
    assert renderer._get_session_color("FP2") == Spectra6Colors.BLACK
    assert renderer._get_session_color("FP3") == Spectra6Colors.BLACK
    assert renderer._get_session_color("Practice 1") == Spectra6Colors.BLACK

    assert renderer._get_session_color("Sprint") == Spectra6Colors.BLACK
    assert renderer._get_session_color("Sprint Qualifying") == Spectra6Colors.BLACK


def test_spectra6_render_calendar_with_weather():
    """Test Spectra 6 rendering calendar with weather data."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    race_datetime = datetime.now() + timedelta(days=3)
    race_data = {
        "race_name": "Monaco Grand Prix",
        "round": "8",
        "season": "2025",
        "circuit": {
            "circuitId": "monaco",
            "name": "Circuit de Monaco",
            "location": "Monte Carlo",
            "country": "Monaco",
        },
        "schedule": [
            {"name": "Race", "datetime": race_datetime},
        ],
    }

    weather = WeatherData(
        temperature_c=24.5,
        precipitation_probability=10,
        weather_code=0,
    )

    bmp_data = renderer.render_calendar(race_data, weather_data=weather)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_calendar_with_datetime_schedule():
    """Test Spectra 6 rendering with datetime objects in schedule."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    now = datetime.now(timezone.utc)
    race_data = {
        "race_name": "British Grand Prix",
        "season": "2025",
        "circuit": {
            "circuitId": "silverstone",
            "name": "Silverstone Circuit",
            "location": "Silverstone",
            "country": "UK",
        },
        "schedule": [
            {"name": "FP1", "datetime": now + timedelta(days=1)},
            {"name": "FP2", "datetime": now + timedelta(days=1, hours=4)},
            {"name": "FP3", "datetime": now + timedelta(days=2)},
            {"name": "Qualifying", "datetime": now + timedelta(days=2, hours=4)},
            {"name": "Race", "datetime": now + timedelta(days=3)},
        ],
    }

    bmp_data = renderer.render_calendar(race_data)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_render_calendar_las_vegas():
    """Test Spectra 6 rendering with Las Vegas circuit (tests CIRCUIT_ID_MAP)."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    race_data = {
        "race_name": "Las Vegas Grand Prix",
        "season": "2025",
        "circuit": {
            "circuitId": "vegas",
            "name": "Las Vegas Street Circuit",
            "location": "Las Vegas",
            "country": "USA",
        },
        "schedule": [
            {"name": "Race", "display_time": "Sun 22:00"},
        ],
    }

    bmp_data = renderer.render_calendar(race_data)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


# ==========================================================================
# BWR Renderer Tests (black/white/red E-Ink display)
# ==========================================================================


def test_bwr_render_calendar_english(mock_race_data):
    """Test BWR rendering calendar in English."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert img.getpalette() is not None


def test_bwr_render_calendar_czech(mock_race_data):
    """Test BWR rendering calendar in Czech."""
    translator = get_translator("cs")
    renderer = BwrRenderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_calendar_with_historical_data(mock_race_data, mock_historical_data):
    """Test BWR rendering calendar with historical qualifying and race results."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)
    bmp_data = renderer.render_calendar(mock_race_data, mock_historical_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_calendar_new_track(mock_race_data):
    """Test BWR rendering calendar when track is new (no historical data)."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)

    new_track_data = HistoricalData(is_new_track=True)
    bmp_data = renderer.render_calendar(mock_race_data, new_track_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_error_english():
    """Test BWR rendering error message in English."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)
    bmp_data = renderer.render_error("Test error message")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_error_czech():
    """Test BWR rendering error message in Czech."""
    translator = get_translator("cs")
    renderer = BwrRenderer(translator)
    bmp_data = renderer.render_error("Chybova zprava")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_colors_palette():
    assert BwrColors.BLACK == (0x00, 0x00, 0x00)
    assert BwrColors.WHITE == (0xFF, 0xFF, 0xFF)
    assert BwrColors.RED == (0xFF, 0x00, 0x00)

    assert len(BwrColors.PALETTE) == 3
    assert BwrColors.PALETTE[0] == BwrColors.BLACK
    assert BwrColors.PALETTE[1] == BwrColors.WHITE
    assert BwrColors.PALETTE[2] == BwrColors.RED


def test_bwr_session_colors():
    """Test BWR session color assignment - only Race is RED, all else BLACK."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)

    assert renderer._get_session_color("Race") == BwrColors.RED
    assert renderer._get_session_color("race") == BwrColors.RED

    assert renderer._get_session_color("Qualifying") == BwrColors.BLACK
    assert renderer._get_session_color("Q1") == BwrColors.BLACK
    assert renderer._get_session_color("Q2") == BwrColors.BLACK
    assert renderer._get_session_color("Q3") == BwrColors.BLACK

    assert renderer._get_session_color("FP1") == BwrColors.BLACK
    assert renderer._get_session_color("FP2") == BwrColors.BLACK
    assert renderer._get_session_color("FP3") == BwrColors.BLACK
    assert renderer._get_session_color("Practice 1") == BwrColors.BLACK

    assert renderer._get_session_color("Sprint") == BwrColors.BLACK
    assert renderer._get_session_color("Sprint Qualifying") == BwrColors.BLACK


def test_bwr_render_calendar_with_weather():
    """Test BWR rendering calendar with weather data."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)

    race_datetime = datetime.now() + timedelta(days=3)
    race_data = {
        "race_name": "Monaco Grand Prix",
        "round": "8",
        "season": "2025",
        "circuit": {
            "circuitId": "monaco",
            "name": "Circuit de Monaco",
            "location": "Monte Carlo",
            "country": "Monaco",
        },
        "schedule": [
            {"name": "Race", "datetime": race_datetime},
        ],
    }

    weather = WeatherData(
        temperature_c=24.5,
        precipitation_probability=10,
        weather_code=0,
    )

    bmp_data = renderer.render_calendar(race_data, weather_data=weather)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_calendar_with_datetime_schedule():
    """Test BWR rendering with datetime objects in schedule."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)

    now = datetime.now(timezone.utc)
    race_data = {
        "race_name": "British Grand Prix",
        "season": "2025",
        "circuit": {
            "circuitId": "silverstone",
            "name": "Silverstone Circuit",
            "location": "Silverstone",
            "country": "UK",
        },
        "schedule": [
            {"name": "FP1", "datetime": now + timedelta(days=1)},
            {"name": "FP2", "datetime": now + timedelta(days=1, hours=4)},
            {"name": "FP3", "datetime": now + timedelta(days=2)},
            {"name": "Qualifying", "datetime": now + timedelta(days=2, hours=4)},
            {"name": "Race", "datetime": now + timedelta(days=3)},
        ],
    }

    bmp_data = renderer.render_calendar(race_data)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_calendar_las_vegas():
    """Test BWR rendering with Las Vegas circuit (tests CIRCUIT_ID_MAP)."""
    translator = get_translator("en")
    renderer = BwrRenderer(translator)

    race_data = {
        "race_name": "Las Vegas Grand Prix",
        "season": "2025",
        "circuit": {
            "circuitId": "vegas",
            "name": "Las Vegas Street Circuit",
            "location": "Las Vegas",
            "country": "USA",
        },
        "schedule": [
            {"name": "Race", "display_time": "Sun 22:00"},
        ],
    }

    bmp_data = renderer.render_calendar(race_data)

    assert bmp_data is not None
    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_bmp_uses_three_color_palette(mock_race_data):
    """Test BWR BMP palette starts with black, white, red entries."""
    renderer = BwrRenderer(get_translator("en"))
    bmp_data = renderer.render_calendar(mock_race_data)

    img = Image.open(BytesIO(bmp_data))
    palette = img.getpalette()

    assert palette is not None
    assert palette[:9] == [0, 0, 0, 255, 255, 255, 255, 0, 0]


def test_bwr_bmp_is_smaller_than_spectra6_for_same_calendar(mock_race_data):
    """BWR should use a more compact BMP encoding than Spectra 6."""
    translator = get_translator("en")
    bwr_data = BwrRenderer(translator).render_calendar(mock_race_data)
    spectra6_data = Spectra6Renderer(translator).render_calendar(mock_race_data)

    assert len(bwr_data) < len(spectra6_data)


def test_spectra6_render_teams_drivers(mock_teams_data):
    """Teams screen should render in Spectra 6 mode."""
    renderer = Spectra6Renderer(get_translator("en"))
    bmp_data = renderer.render_teams_drivers(mock_teams_data)

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwr_render_teams_drivers_uses_bwr_palette(mock_teams_data):
    """Teams screen should render in BWR mode with the expected palette prefix."""
    renderer = BwrRenderer(get_translator("en"))
    bmp_data = renderer.render_teams_drivers(mock_teams_data)

    img = Image.open(BytesIO(bmp_data))
    palette = img.getpalette()

    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert palette is not None
    assert palette[:9] == [0, 0, 0, 255, 255, 255, 255, 0, 0]


def test_bwry_render_teams_drivers_uses_bwry_palette(mock_teams_data):
    """Teams screen should render in BWRY mode with the expected palette prefix."""
    renderer = BwryRenderer(get_translator("en"))
    bmp_data = renderer.render_teams_drivers(mock_teams_data)

    img = Image.open(BytesIO(bmp_data))
    palette = img.getpalette()

    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert palette is not None
    assert palette[:12] == [0, 0, 0, 255, 255, 255, 255, 0, 0, 255, 216, 0]


def test_bwry_render_calendar_english(mock_race_data):
    """Test BWRY rendering calendar in English."""
    renderer = BwryRenderer(get_translator("en"))
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert img.getpalette() is not None


def test_bwry_render_calendar_czech(mock_race_data):
    """Test BWRY rendering calendar in Czech."""
    renderer = BwryRenderer(get_translator("cs"))
    bmp_data = renderer.render_calendar(mock_race_data)

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert img.getpalette() is not None


def test_bwry_render_error_english():
    """Test BWRY rendering error message in English."""
    renderer = BwryRenderer(get_translator("en"))
    bmp_data = renderer.render_error("Test error message")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwry_render_error_czech():
    """Test BWRY rendering error message in Czech."""
    renderer = BwryRenderer(get_translator("cs"))
    bmp_data = renderer.render_error("Chybova zprava")

    assert bmp_data is not None
    assert len(bmp_data) > 0

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_bwry_colors_palette():
    assert BwryColors.BLACK == (0x00, 0x00, 0x00)
    assert BwryColors.WHITE == (0xFF, 0xFF, 0xFF)
    assert BwryColors.RED == (0xFF, 0x00, 0x00)
    assert BwryColors.YELLOW == (0xFF, 0xD8, 0x00)

    assert len(BwryColors.PALETTE) == 4
    assert BwryColors.PALETTE[3] == BwryColors.YELLOW


def test_bwry_session_colors():
    """Test BWRY session colors reserve yellow for qualifying-style sessions."""
    renderer = BwryRenderer(get_translator("en"))

    assert renderer._get_session_color("Race") == BwryColors.RED
    assert renderer._get_session_color("Qualifying") == BwryColors.YELLOW
    assert renderer._get_session_color("Sprint") == BwryColors.YELLOW
    assert renderer._get_session_color("FP1") == BwryColors.BLACK


def test_bwry_bmp_uses_four_color_palette(mock_race_data):
    """Test BWRY BMP palette starts with black, white, red, yellow entries."""
    renderer = BwryRenderer(get_translator("en"))
    bmp_data = renderer.render_calendar(mock_race_data)

    img = Image.open(BytesIO(bmp_data))
    palette = img.getpalette()

    assert palette is not None
    assert palette[:12] == [0, 0, 0, 255, 255, 255, 255, 0, 0, 255, 216, 0]


def test_map_to_bwr_palette_keeps_grayscale_pixels_off_red():
    """Grayscale anti-aliasing should not turn black text edges red."""
    image = Image.new("RGB", (3, 1), color=(255, 255, 255))
    image.putdata([(0, 0, 0), (110, 110, 110), (255, 0, 0)])

    indexed = map_to_bwr_palette(image, BwrColors.PALETTE)
    pixels = indexed.load()

    assert pixels is not None
    assert [pixels[0, 0], pixels[1, 0], pixels[2, 0]] == [
        BwrColors.IDX_BLACK,
        BwrColors.IDX_BLACK,
        BwrColors.IDX_RED,
    ]


def test_map_to_bwr_palette_preserves_near_white_text_edges():
    """Light anti-aliased pixels in white-on-red text should stay white."""
    image = Image.new("RGB", (3, 1), color=(255, 255, 255))
    image.putdata([(255, 255, 255), (244, 204, 204), (255, 0, 0)])

    indexed = map_to_bwr_palette(image, BwrColors.PALETTE)
    pixels = indexed.load()

    assert pixels is not None
    assert [pixels[0, 0], pixels[1, 0], pixels[2, 0]] == [
        BwrColors.IDX_WHITE,
        BwrColors.IDX_WHITE,
        BwrColors.IDX_RED,
    ]


def test_map_to_bwry_palette_detects_yellow_without_bleeding_grayscale():
    """BWRY mapping should keep grayscale pixels neutral while detecting yellow."""
    image = Image.new("RGB", (4, 1), color=(255, 255, 255))
    image.putdata([(0, 0, 0), (140, 140, 140), (255, 0, 0), (244, 208, 42)])

    indexed = map_to_bwry_palette(image, BwryColors.PALETTE)
    pixels = indexed.load()

    assert pixels is not None
    assert [pixels[0, 0], pixels[1, 0], pixels[2, 0], pixels[3, 0]] == [
        BwryColors.IDX_BLACK,
        BwryColors.IDX_BLACK,
        BwryColors.IDX_RED,
        BwryColors.IDX_YELLOW,
    ]


def test_map_to_bwry_palette_preserves_near_white_edges():
    """Light anti-aliased pixels should stay white instead of turning yellow."""
    image = Image.new("RGB", (3, 1), color=(255, 255, 255))
    image.putdata([(255, 255, 255), (246, 236, 182), (244, 208, 42)])

    indexed = map_to_bwry_palette(image, BwryColors.PALETTE)
    pixels = indexed.load()

    assert pixels is not None
    assert [pixels[0, 0], pixels[1, 0], pixels[2, 0]] == [
        BwryColors.IDX_WHITE,
        BwryColors.IDX_WHITE,
        BwryColors.IDX_YELLOW,
    ]
