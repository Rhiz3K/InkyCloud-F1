"""Test renderer service."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageOps

from app.models import (
    ConstructorInfo,
    DriverInfo,
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
from app.services.font_utils import fit_brand_font_box
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.renderer_common import (
    build_sprint_qualifying_label,
    build_team_header_values,
    crop_to_content,
    draw_team_logo,
    format_schedule_session_name,
    format_team_driver_display_name,
    get_team_logo_key,
    get_text_y,
    split_teams_for_columns,
    translate_session_name,
)
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
def mock_ranked_teams_data():
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


@pytest.mark.parametrize(
    ("lang", "sample_text"),
    [
        ("ja", "週末スケジュール"),
        ("zh-CN", "周末赛程"),
    ],
)
def test_renderer_loads_cjk_font_glyphs(lang, sample_text):
    """Locale-aware font loading should render visible CJK glyphs on 1-bit canvases."""
    renderer = Renderer(get_translator(lang), lang)
    font = renderer._load_font(24, bold=True)
    image = Image.new("1", (240, 80), 1)
    draw = ImageDraw.Draw(image)

    draw.text((8, 8), sample_text, font=font, fill=0)

    black_pixels = image.convert("L").histogram()[0]
    assert black_pixels > 0


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


@pytest.mark.parametrize(
    ("lang", "delta", "expected"),
    [
        ("en", timedelta(days=1, hours=1, minutes=5), "1 day 1 hour"),
        ("de", timedelta(days=1, hours=1, minutes=5), "1 Tag 1 Stunde"),
        ("cs", timedelta(days=3, hours=3, minutes=5), "3 dny 3 hodiny"),
        ("pl", timedelta(days=22, hours=2, minutes=5), "22 dni 2 godziny"),
        ("fr", timedelta(hours=1, minutes=5), "0 jour 1 heure"),
        ("pt-BR", timedelta(hours=1, minutes=5), "0 dia 1 hora"),
    ],
)
def test_countdown_uses_locale_plural_forms(monkeypatch, lang, delta, expected):
    renderer = Renderer(get_translator(lang), lang)
    image = Image.new("1", (800, 480), 1)
    draw = ImageDraw.Draw(image)
    captured_text: list[str] = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(str(text))
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy_text)
    race_time = datetime.now(timezone.utc) + delta

    renderer._draw_countdown_box(
        draw,
        {"schedule": [{"name": "Race", "datetime": race_time.isoformat()}]},
        220,
    )

    assert expected in captured_text


def test_monochrome_canvas_ignores_mutated_instance_dimensions():
    """The display contract stays 800x480 even if instance state is corrupted."""
    renderer = Renderer(get_translator("en"), "en")
    renderer.width = 1
    renderer.height = 1

    assert renderer._new_canvas().size == (800, 480)


@pytest.mark.parametrize("lang", ["cs", "sk", "pl", "en", "de", "ja", "zh-CN", "pt-BR"])
@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_translate_session_name_prefers_dedicated_sprint_qualifying_key(renderer_cls, lang):
    """Sprint qualifying aliases should prefer the dedicated locale key when present."""
    renderer = renderer_cls(get_translator(lang), lang)
    expected = renderer.translator["session_sprintqualifying"]
    assert (
        translate_session_name("SprintQualifying", renderer.translator, renderer.lang_code)
        == expected
    )
    assert (
        translate_session_name("Sprint Qualifying", renderer.translator, renderer.lang_code)
        == expected
    )
    assert (
        translate_session_name("Sprint Shootout", renderer.translator, renderer.lang_code)
        == expected
    )


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", "Sprint Q."),
        ("cs", "Sprint K."),
        ("pt-BR", "Sprint C."),
        ("ja", "スプリント予"),
        ("zh-CN", "冲刺赛排"),
    ],
)
@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_build_sprint_qualifying_label_uses_localized_abbreviation(renderer_cls, lang, expected):
    """Abbreviated sprint qualifying labels should use each locale's qualifying initial."""
    renderer = renderer_cls(get_translator(lang), lang)
    assert (
        build_sprint_qualifying_label(renderer.translator, renderer.lang_code, abbreviated=True)
        == expected
    )


@pytest.mark.parametrize("lang", ["en", "cs", "pt-BR", "ja", "zh-CN"])
@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_format_schedule_session_name_prefers_dedicated_label(renderer_cls, lang):
    """Schedule rows should use the dedicated sprint-qualifying translation when available."""
    renderer = renderer_cls(get_translator(lang), lang)
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    assert (
        format_schedule_session_name(
            draw, "Sprint Qualifying", 180, renderer.lang_code, renderer.translator
        )
        == renderer.translator["session_sprintqualifying"]
    )


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
    assert get_team_logo_key("Audi") == "audi"
    assert get_team_logo_key("Cadillac Formula 1 Team") == "cadillac"


def test_split_teams_for_columns_keeps_11th_team_visible():
    teams = list(range(11))

    left, right = split_teams_for_columns(teams)

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


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
@pytest.mark.parametrize("lang", ["ja", "zh-CN"])
def test_cjk_team_driver_names_do_not_touch_card_header(renderer_cls, lang):
    """CJK locale driver names should keep clear of the team-card header line."""
    teams_data = TeamsService()._load_from_json(2026)
    assert teams_data is not None

    renderer = renderer_cls(get_translator(lang), lang)
    background = 1 if renderer_cls is Renderer else renderer.colors.WHITE
    image_mode = "1" if renderer_cls is Renderer else "RGB"
    image = Image.new(image_mode, (renderer.width, renderer.height), background)
    draw = ImageDraw.Draw(image)

    left_teams, right_teams = split_teams_for_columns(teams_data.teams)
    teams_per_col = max(len(left_teams), len(right_teams), 1)
    row_gap = 2
    row_height = (
        renderer.height - renderer.layout["header_height"] - 8 - (teams_per_col - 1) * row_gap
    ) // teams_per_col
    card_header_height = 23
    card_header_gap = 1
    photo_x = 9
    y_start = renderer.layout["header_height"] + 4

    for column_index, column_teams in enumerate((left_teams, right_teams)):
        y = y_start
        x_end = renderer.width // 2 - 2 if column_index == 0 else renderer.width - 5
        for team in column_teams:
            driver_area_height = row_height - card_header_height - 4
            driver_row_height = driver_area_height // 2
            driver_y_start = y + card_header_height + 2
            photo_size = driver_row_height - 2
            driver_pos_x = x_end - 72

            for i, driver in enumerate(sorted(team.drivers[:2], key=lambda d: d.position or 99)):
                name = driver.name or f"{driver.given_name} {driver.family_name}".strip()
                if not name:
                    name = driver.driver_code or "TBA"
                display_name = format_team_driver_display_name(name)
                driver_y = driver_y_start + i * driver_row_height
                driver_name_x = photo_x + photo_size + renderer.layout["driver_name_padding"] + 4
                max_name_width = max(1, driver_pos_x - 8 - driver_name_x)
                driver_font = fit_brand_font_box(
                    draw,
                    display_name,
                    max_width=max_name_width,
                    max_height=max(1, driver_row_height - 1),
                    base_size=18,
                    min_size=12,
                    bold=True,
                )
                driver_text_y = get_text_y(
                    draw, driver_font, driver_row_height, driver_y, display_name
                )
                bbox = draw.textbbox((driver_name_x, driver_text_y), display_name, font=driver_font)
                assert bbox[1] >= y + card_header_height + card_header_gap

            y += row_height + row_gap


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_team_header_values_shorten_red_bull_power_units(renderer_cls):
    """Red Bull teams should use the short RB label in the power-unit meta text."""
    team = TeamEntry(
        constructor_name="Red Bull Racing-Red Bull Ford",
        chassis="RB22",
        power_unit="Red Bull Ford DM01",
        position=1,
        points=38.0,
        drivers=[],
    )

    team_name, meta_text, team_pos, team_pts = build_team_header_values(team)

    assert team_name == "Red Bull Racing"
    assert meta_text == "RB22 | RB Ford DM01"
    assert team_pos == "1"
    assert team_pts == "38"


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
@pytest.mark.parametrize("lang", ["ja", "zh-CN"])
def test_cjk_teams_header_uses_brand_font_for_line1_and_cjk_font_for_line2(renderer_cls, lang):
    """JA/CN teams header should keep the Latin font for line1 and CJK font for translated line2."""
    renderer = renderer_cls(get_translator(lang), lang)
    image_mode = "1" if renderer_cls is Renderer else "RGB"
    background = 1 if renderer_cls is Renderer else renderer.colors.WHITE
    image = Image.new(image_mode, (renderer.width, renderer.height), background)
    draw = ImageDraw.Draw(image)
    captured = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured.append((text, kwargs.get("font")))
        return original_text(xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(draw, "text", spy_text)
    try:
        renderer._draw_teams_header(draw, image, 2026)
    finally:
        monkeypatch.undo()

    line1_text = "2026 FIA F1 World Championship"
    line2_text = renderer.translator.get("teams_drivers_title", "TEAMS & DRIVERS").upper()
    captured_map = {text: font for text, font in captured if text in {line1_text, line2_text}}

    line1_font = captured_map[line1_text]
    line2_font = captured_map[line2_text]

    assert getattr(line1_font, "path", "").endswith("TitilliumWeb-Bold.ttf")
    assert getattr(line2_font, "path", "").endswith("NotoSansCJK-Regular.ttc")


def test_draw_driver_photo_prefers_explicit_number_over_asset():
    translator = get_translator("en")
    renderer = Renderer(translator)
    image = Image.new("1", (120, 24), 1)
    draw = ImageDraw.Draw(image)
    renderer._driver_photos = {"verstappen": Image.new("1", (100, 20), 0)}

    width = renderer._draw_driver_photo(
        draw,
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
    """Test Spectra 6 session color assignment by session type."""
    translator = get_translator("en")
    renderer = Spectra6Renderer(translator)

    assert renderer._get_session_color("Race") == Spectra6Colors.RED
    assert renderer._get_session_color("race") == Spectra6Colors.RED

    assert renderer._get_session_color("Qualifying") == Spectra6Colors.YELLOW
    assert renderer._get_session_color("Q1") == Spectra6Colors.YELLOW
    assert renderer._get_session_color("Q2") == Spectra6Colors.YELLOW
    assert renderer._get_session_color("Q3") == Spectra6Colors.YELLOW

    assert renderer._get_session_color("FP1") == Spectra6Colors.BLUE
    assert renderer._get_session_color("FP2") == Spectra6Colors.BLUE
    assert renderer._get_session_color("FP3") == Spectra6Colors.BLUE
    assert renderer._get_session_color("Practice 1") == Spectra6Colors.BLUE

    assert renderer._get_session_color("Sprint") == Spectra6Colors.GREEN
    assert renderer._get_session_color("Sprint Qualifying") == Spectra6Colors.YELLOW


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


@pytest.mark.parametrize("lang", ["en", "cs"])
def test_spectra6_render_teams_drivers(mock_ranked_teams_data, lang):
    """Teams screen should render in Spectra 6 mode."""
    renderer = Spectra6Renderer(get_translator(lang))
    bmp_data = renderer.render_teams_drivers(mock_ranked_teams_data)

    img = Image.open(BytesIO(bmp_data))
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_spectra6_team_row_header_uses_black_fill():
    """Teams card headers should stay black while the screen header remains red."""
    renderer = Spectra6Renderer(get_translator("en"))
    image = Image.new("RGB", (renderer.width, renderer.height), renderer.colors.WHITE)
    draw = ImageDraw.Draw(image)
    team = TeamEntry(
        constructor_name="Audi",
        chassis="",
        power_unit="",
        drivers=[],
    )

    renderer._draw_team_row(image, draw, 5, 100, 395, team, 80)

    assert image.getpixel((200, 110)) == renderer.colors.BLACK


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_team_row_header_draws_shared_stats_panel(renderer_cls):
    """Teams card header should use a shared white panel for position and points."""
    renderer = renderer_cls(get_translator("en"))
    background = 1 if renderer_cls is Renderer else renderer.colors.WHITE
    image_mode = "1" if renderer_cls is Renderer else "RGB"
    image = Image.new(image_mode, (800, 480), background)
    draw = ImageDraw.Draw(image)
    team = TeamEntry(
        constructor_name="Oracle Red Bull Racing",
        chassis="RB22",
        power_unit="Honda RBPT",
        position=1,
        points=38.0,
        drivers=[],
    )

    renderer._draw_team_row(image, draw, 5, 100, 395, team, 80)

    panel_left_edge_pixel = image.getpixel((317, 104))
    panel_fill_pixel = image.getpixel((319, 104))
    if renderer_cls is Renderer:
        assert panel_left_edge_pixel == 0
        assert panel_fill_pixel == 1
    else:
        assert panel_left_edge_pixel == renderer.colors.BLACK
        assert panel_fill_pixel == renderer.colors.WHITE


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_team_row_aligns_driver_names_to_fixed_column(renderer_cls):
    """Driver names should start at the same x even when photo assets have different widths."""
    renderer = renderer_cls(get_translator("en"))
    background = 1 if renderer_cls is Renderer else renderer.colors.WHITE
    image_mode = "1" if renderer_cls is Renderer else "RGB"
    image = Image.new(image_mode, (800, 480), background)
    draw = ImageDraw.Draw(image)
    team = TeamEntry(
        constructor_name="McLaren",
        chassis="MCL40",
        power_unit="Mercedes-AMG F1 M17",
        position=1,
        points=38.0,
        drivers=[
            TeamDriverEntry(name="Lando Norris", position=1, points=25.0, driver_number=4),
            TeamDriverEntry(name="Oscar Piastri", position=2, points=18.0, driver_number=81),
        ],
    )

    original_text = draw.text
    captured_name_x: dict[str, int] = {}

    def fake_draw_driver_photo(*args, **kwargs):
        driver_name = args[4]
        return 8 if "Lando" in driver_name else 20

    def spy_text(xy, text, *args, **kwargs):
        if text in {"Lando NORRIS", "Oscar PIASTRI"}:
            captured_name_x[text] = int(xy[0])
        return original_text(xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(renderer, "_draw_driver_photo", fake_draw_driver_photo)
    monkeypatch.setattr(draw, "text", spy_text)
    try:
        renderer._draw_team_row(image, draw, 5, 100, 395, team, 80)
    finally:
        monkeypatch.undo()

    assert captured_name_x["Lando NORRIS"] == captured_name_x["Oscar PIASTRI"]


def test_spectra6_team_row_highlights_first_team_position_in_red():
    """Color team headers should highlight the leading constructor position in red."""
    renderer = Spectra6Renderer(get_translator("en"))
    image = Image.new("RGB", (renderer.width, renderer.height), renderer.colors.WHITE)
    draw = ImageDraw.Draw(image)
    captured = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured.append((text, kwargs.get("fill")))
        return original_text(xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(draw, "text", spy_text)
    try:
        team = TeamEntry(
            constructor_name="McLaren",
            chassis="MCL40",
            power_unit="Mercedes-AMG F1 M17",
            position=1,
            points=38.0,
            drivers=[],
        )
        renderer._draw_team_row(image, draw, 5, 100, 395, team, 80)
    finally:
        monkeypatch.undo()

    assert ("1", renderer.colors.RED) in captured


def test_spectra6_renderer_prefers_color_team_logo_assets(tmp_path, monkeypatch):
    """Color renderers should prefer teams_color assets over monochrome teams assets."""
    images_dir = tmp_path / "images"
    teams_dir = images_dir / "teams"
    teams_color_dir = images_dir / "teams_color"
    teams_dir.mkdir(parents=True)
    teams_color_dir.mkdir(parents=True)

    Image.new("RGBA", (4, 4), color=(0, 0, 0, 255)).save(teams_dir / "mclaren.png", "PNG")
    Image.new("RGBA", (4, 4), color=(255, 135, 0, 255)).save(teams_color_dir / "mclaren.png", "PNG")

    monkeypatch.setattr(spectra6_renderer_module, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(spectra6_renderer_module, "TEAMS_COLOR_DIR", teams_color_dir)

    renderer = Spectra6Renderer(get_translator("en"))
    renderer._ensure_teams_assets()

    assert renderer._team_logos["mclaren"].getpixel((0, 0))[:3] == (255, 135, 0)


def test_spectra6_renderer_uses_monochrome_f1_logo_asset(tmp_path, monkeypatch, mock_race_data):
    """Spectra 6 should select the shared monochrome F1 logo asset during rendering."""
    images_dir = tmp_path / "images"
    images_dir.mkdir(parents=True)

    mono_logo = Image.new("RGB", (40, 20), (255, 255, 255))
    mono_draw = ImageDraw.Draw(mono_logo)
    mono_draw.rectangle((5, 5, 35, 15), fill=(0, 0, 0))
    mono_logo.save(images_dir / "eInkF1logo.jpg", "JPEG")

    color_logo = Image.new("RGB", (40, 20), (255, 0, 0))
    color_logo.save(images_dir / "f1_spectra_6.bmp", "BMP")

    monkeypatch.setattr(spectra6_renderer_module, "IMAGES_DIR", images_dir)

    captured: dict[str, object] = {}
    original_draw_f1_logo = spectra6_renderer_module.draw_f1_logo

    def spy_draw_f1_logo(*args, **kwargs):
        captured["logo_path"] = kwargs["logo_path"]
        return original_draw_f1_logo(*args, **kwargs)

    monkeypatch.setattr(spectra6_renderer_module, "draw_f1_logo", spy_draw_f1_logo)

    renderer = Spectra6Renderer(get_translator("en"))
    bmp_data = renderer.render_calendar(mock_race_data)
    img = Image.open(BytesIO(bmp_data))

    assert captured["logo_path"].name == "eInkF1logo.jpg"
    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"


def test_renderer_prefers_color_team_logo_assets_for_1bit_sizing(tmp_path, monkeypatch):
    """1-bit renderer should use teams_color assets first so crop/size matches color renders."""
    images_dir = tmp_path / "images"
    teams_dir = images_dir / "teams"
    teams_color_dir = images_dir / "teams_color"
    teams_dir.mkdir(parents=True)
    teams_color_dir.mkdir(parents=True)

    Image.new("RGBA", (4, 4), color=(0, 0, 0, 255)).save(teams_dir / "mclaren.png", "PNG")
    color_logo = Image.new("RGBA", (10, 6), color=(0, 0, 0, 0))
    for x in range(1, 9):
        for y in range(1, 5):
            color_logo.putpixel((x, y), (255, 135, 0, 255))
    color_logo.save(teams_color_dir / "mclaren.png", "PNG")

    monkeypatch.setattr(renderer_module, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(renderer_module, "TEAMS_COLOR_DIR", teams_color_dir)

    renderer = Renderer(get_translator("en"))
    renderer._ensure_teams_assets()

    assert renderer._team_logos["mclaren"].size == (8, 4)


def test_spectra6_renderer_crops_audi_wordmark_to_primary_band():
    """Audi logo should keep the rings band so it renders larger in the teams card."""
    logo = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((10, 10, 110, 55), fill=(0, 0, 0, 255))
    draw.rectangle((30, 80, 90, 100), fill=(0, 0, 0, 255))

    prepared = Spectra6Renderer._prepare_team_logo("audi", logo)

    assert prepared.size == (101, 46)


def test_renderer_crops_audi_wordmark_to_primary_band_before_1bit_conversion():
    """1-bit teams logos should use the same Audi crop as color renderers."""
    logo = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((10, 10, 110, 55), fill=(0, 0, 0, 255))
    draw.rectangle((30, 80, 90, 100), fill=(0, 0, 0, 255))

    prepared = Renderer._prepare_team_logo("audi", logo)

    assert prepared.size == (101, 46)


def test_spectra6_renderer_crops_cadillac_wordmark_to_primary_band():
    """Cadillac logo should keep only the crest without the lower wordmark."""
    logo = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((10, 10, 110, 55), fill=(0, 0, 0, 255))
    draw.rectangle((30, 80, 90, 100), fill=(0, 0, 0, 255))

    prepared = Spectra6Renderer._prepare_team_logo("cadillac", logo)

    assert prepared.size == (101, 46)


def test_renderer_crops_cadillac_wordmark_to_primary_band_before_1bit_conversion():
    """1-bit teams logos should remove the Cadillac wordmark before conversion."""
    logo = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((10, 10, 110, 55), fill=(0, 0, 0, 255))
    draw.rectangle((30, 80, 90, 100), fill=(0, 0, 0, 255))

    prepared = Renderer._prepare_team_logo("cadillac", logo)

    assert prepared.size == (101, 46)


def test_renderer_maps_sauber_green_to_white_for_non_spectra_variants():
    """1-bit logo prep should convert Sauber's green mark into a white-on-black logo."""
    logo = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    logo.putpixel((1, 0), (0, 255, 0, 255))

    prepared = Renderer.normalize_sauber_logo_for_non_spectra(logo)

    assert prepared.getpixel((0, 0))[:3] == (0, 0, 0)
    assert prepared.getpixel((1, 0))[:3] == (255, 255, 255)


def test_bwr_renderer_maps_sauber_green_to_white_for_non_spectra_variants():
    """BWR/BWRY logo prep should use the same white-on-black Sauber treatment."""
    logo = Image.new("RGBA", (4, 1), (0, 0, 0, 0))
    logo.putpixel((1, 0), (0, 0, 0, 255))
    logo.putpixel((2, 0), (0, 255, 0, 255))

    prepared = BwrRenderer._prepare_team_logo("sauber", logo)

    assert prepared.getpixel((0, 0))[:3] == (0, 0, 0)
    assert prepared.getpixel((1, 0))[:3] == (255, 255, 255)


def test_spectra6_renderer_preserves_sauber_green_logo():
    """Spectra 6 should keep Sauber's green accent instead of forcing mono output."""
    logo = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    logo.putpixel((1, 0), (0, 255, 0, 255))

    prepared = Spectra6Renderer._prepare_team_logo("sauber", logo)

    assert prepared.getpixel((1, 0))[:3] == (0, 255, 0)


def test_renderer_crop_to_content_ignores_fully_opaque_alpha_for_white_background():
    """Opaque logos on white backgrounds should crop by visible content, not full alpha bounds."""
    logo = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((10, 10, 110, 55), fill=(0, 0, 0, 255))

    prepared = crop_to_content(logo, use_binary_mask=True)

    assert prepared.size == (101, 46)


def test_renderer_draw_team_logo_centers_logo_in_1bit_mode():
    """1-bit teams logos should be centered in the logo container like color renderers."""
    renderer = Renderer(get_translator("en"))
    renderer._team_logos = {"audi": Image.new("RGBA", (40, 20), (0, 0, 0, 255))}
    image = Image.new("1", (220, 80), 1)
    team = TeamEntry(constructor_name="Audi", chassis="", power_unit="", drivers=[])

    draw_team_logo(
        image,
        team,
        team_logos=renderer._team_logos,
        get_team_logo_key_fn=get_team_logo_key,
        driver_area_y=10,
        driver_area_h=30,
        container_left=100,
        container_right=180,
        paste_logo_fn=lambda canvas, logo_resized, x, y: canvas.paste(
            renderer._logo_to_1bit(logo_resized),
            (x, y),
        ),
    )

    bbox = ImageOps.invert(image.convert("L")).getbbox()

    assert bbox is not None
    assert (bbox[0] + bbox[2]) // 2 == 140


def test_renderer_uses_monochrome_override_for_ferrari_in_1bit(tmp_path, monkeypatch):
    """Ferrari should use the dedicated mono asset in 1-bit mode to preserve shield detail."""
    images_dir = tmp_path / "images"
    teams_dir = images_dir / "teams"
    teams_color_dir = images_dir / "teams_color"
    teams_dir.mkdir(parents=True)
    teams_color_dir.mkdir(parents=True)

    Image.new("RGBA", (8, 8), color=(255, 220, 0, 255)).save(teams_color_dir / "ferrari.png", "PNG")
    mono_logo = Image.new("1", (8, 8), 1)
    mono_logo.putpixel((0, 0), 0)
    mono_logo.save(teams_dir / "ferrari.png", "PNG")

    monkeypatch.setattr(renderer_module, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(renderer_module, "TEAMS_COLOR_DIR", teams_color_dir)

    renderer = Renderer(get_translator("en"))
    renderer._ensure_teams_assets()

    assert renderer._team_logos["ferrari"].getpixel((0, 0))[:3] == (0, 0, 0)


def test_renderer_uses_monochrome_override_for_red_bull_in_1bit(tmp_path, monkeypatch):
    """Red Bull should use the dedicated mono asset in 1-bit mode."""
    images_dir = tmp_path / "images"
    teams_dir = images_dir / "teams"
    teams_color_dir = images_dir / "teams_color"
    teams_dir.mkdir(parents=True)
    teams_color_dir.mkdir(parents=True)

    Image.new("RGBA", (8, 8), color=(255, 0, 0, 255)).save(teams_color_dir / "red_bull.png", "PNG")
    mono_logo = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    mono_logo.putpixel((0, 0), (0, 0, 0, 255))
    mono_logo.save(teams_dir / "red_bull.png", "PNG")

    monkeypatch.setattr(renderer_module, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(renderer_module, "TEAMS_COLOR_DIR", teams_color_dir)

    renderer = Renderer(get_translator("en"))
    renderer._ensure_teams_assets()

    assert renderer._team_logos["red_bull"].getpixel((0, 0))[:3] == (0, 0, 0)


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_renderer_preserves_half_points_in_team_rows(renderer_cls):
    """Teams renderers should preserve half-points instead of truncating them."""
    renderer = renderer_cls(get_translator("en"))
    background = 1 if renderer_cls is Renderer else renderer.colors.WHITE
    image_mode = "1" if renderer_cls is Renderer else "RGB"
    image = Image.new(image_mode, (800, 480), background)
    draw = ImageDraw.Draw(image)
    captured_text = []
    original_text = draw.text

    def spy_text(xy, text, *args, **kwargs):
        captured_text.append(text)
        return original_text(xy, text, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(draw, "text", spy_text)
    try:
        team = TeamEntry(
            constructor_name="Ferrari",
            chassis="SF-26",
            power_unit="Ferrari 067/6",
            position=2,
            points=27.5,
            drivers=[
                TeamDriverEntry(name="Charles Leclerc", driver_number=16, position=2, points=18.5),
                TeamDriverEntry(name="Lewis Hamilton", driver_number=44, position=5, points=9.0),
            ],
        )

        renderer._draw_team_row(image, draw, 5, 100, 395, team, 80)
    finally:
        monkeypatch.undo()

    assert "27.5" in captured_text
    assert "18.5" in captured_text


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
def test_renderer_maps_exact_rb_constructor_name(renderer_cls):
    """The RB constructor alias should resolve to the Racing Bulls logo key."""
    assert get_team_logo_key("RB") == "racing_bulls"


@pytest.mark.parametrize("lang", ["en", "cs"])
def test_bwr_render_teams_drivers_uses_bwr_palette(mock_ranked_teams_data, lang):
    """Teams screen should render in BWR mode with the expected palette prefix."""
    renderer = BwrRenderer(get_translator(lang))
    bmp_data = renderer.render_teams_drivers(mock_ranked_teams_data)

    img = Image.open(BytesIO(bmp_data))
    palette = img.getpalette()

    assert img.format == "BMP"
    assert img.size == (800, 480)
    assert img.mode == "P"
    assert palette is not None
    assert palette[:9] == [0, 0, 0, 255, 255, 255, 255, 0, 0]


@pytest.mark.parametrize("lang", ["en", "cs"])
def test_bwry_render_teams_drivers_uses_bwry_palette(mock_ranked_teams_data, lang):
    """Teams screen should render in BWRY mode with the expected palette prefix."""
    renderer = BwryRenderer(get_translator(lang))
    bmp_data = renderer.render_teams_drivers(mock_ranked_teams_data)

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
    """Test BWRY session colors fall back to black for unsupported accents."""
    renderer = BwryRenderer(get_translator("en"))

    assert renderer._get_session_color("Race") == BwryColors.RED
    assert renderer._get_session_color("Qualifying") == BwryColors.YELLOW
    assert renderer._get_session_color("Sprint") == BwryColors.BLACK
    assert renderer._get_session_color("Sprint Qualifying") == BwryColors.YELLOW
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


def test_spectra6_renderer_load_track_image_does_not_use_wildcard_fallback(
    monkeypatch, mock_race_data
):
    captured = {}

    def fake_load_track_image_asset(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        spectra6_renderer_module, "load_track_image_asset", fake_load_track_image_asset
    )

    Spectra6Renderer._load_track_image(mock_race_data)

    assert "fallback_glob" not in captured


@pytest.mark.parametrize(
    ("renderer_cls", "renderer_module", "expected_country_map"),
    [
        (BwrRenderer, bwr_renderer_module, bwr_renderer_module.COUNTRY_MAP),
        (BwryRenderer, bwry_renderer_module, bwry_renderer_module.COUNTRY_MAP),
    ],
)
def test_results_section_uses_renderer_results_header_hook(
    monkeypatch,
    mock_race_data,
    mock_historical_data,
    renderer_cls,
    renderer_module,
    expected_country_map,
):
    renderer = renderer_cls(get_translator("en"))
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    captured = {}

    def fake_draw_results_section(draw_ctx, image_ctx, **kwargs):
        header_fn = kwargs["draw_results_header_fn"]
        captured["is_bound_method"] = getattr(header_fn, "__self__", None) is renderer
        captured["visual_top"] = header_fn(
            draw_ctx,
            image_ctx,
            kwargs["y_start"],
            kwargs["historical_data"].season or "",
            kwargs["race_data"]["circuit"]["country"],
        )

    def fake_draw_results_header(*args, **kwargs):
        captured["country_map"] = kwargs["country_map"]
        return 0

    monkeypatch.setattr(spectra6_renderer_module, "draw_results_section", fake_draw_results_section)
    monkeypatch.setattr(renderer_module, "draw_results_header", fake_draw_results_header)

    renderer._draw_results_section(draw, image, mock_race_data, mock_historical_data)

    assert captured["is_bound_method"] is True
    assert captured["country_map"] == expected_country_map
    assert captured["visual_top"] == 0
