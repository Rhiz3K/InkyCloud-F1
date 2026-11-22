"""Minor review cleanups from issue #185 (B2, B11, E12, A10, C10, D11)."""

import asyncio
import io
import mimetypes
from datetime import UTC, datetime

import pytest
from PIL import Image, ImageDraw

from app.services import bmp_artifacts, weather_service
from app.services.circuit_metadata import COUNTRY_MAP
from app.services.i18n import get_translator
from app.services.renderer_assets import get_country_flag_iso_code
from app.services.renderer_calendar import _shrink_font_to_width
from app.services.renderer_text import text_width, wrap_text
from app.services.renderers import create_renderer
from app.services.weather_service import WeatherData

# --- B2: provider and local circuit IDs share one weather cache entry ---------------------------


@pytest.fixture
def empty_circuit_weather():
    weather_service.clear_circuit_weather_cache()
    yield
    weather_service.clear_circuit_weather_cache()


def test_circuit_weather_written_as_vegas_is_read_as_las_vegas(empty_circuit_weather):
    data = WeatherData(temperature_c=18.0, weather_code=1, precipitation_probability=5)
    weather_service.set_cached_circuit_weather("vegas", data)
    assert weather_service.get_cached_circuit_weather("las_vegas") is data
    assert weather_service.get_cached_circuit_weather("vegas") is data


def test_persisted_circuit_weather_loads_under_the_local_id(empty_circuit_weather):
    loaded = weather_service.load_circuit_weather_to_cache(
        {"vegas": {"temperature_c": 18.0, "fetched_at": datetime.now(UTC).isoformat()}}
    )
    assert loaded == 1
    assert weather_service.get_cached_circuit_weather("las_vegas").temperature_c == 18.0


# --- B11: no flag is guessed from a country name's first letters ---------------------------------


def test_flag_codes_come_only_from_the_country_map():
    assert get_country_flag_iso_code("Malaysia", COUNTRY_MAP) == "my"
    assert get_country_flag_iso_code("United Kingdom", COUNTRY_MAP) == "gb"
    # "be" would have drawn Belgium's flag for Belarus.
    assert get_country_flag_iso_code("Belarus", COUNTRY_MAP) == ""


# --- E12: error text wraps at word boundaries --------------------------------------------------


@pytest.fixture(scope="module")
def font_and_draw():
    renderer = create_renderer("1bit", get_translator("en"), "en")
    return renderer.fonts["schedule_row"], ImageDraw.Draw(Image.new("1", (800, 480)))


def test_wrap_text_breaks_at_spaces_and_marks_overflow(font_and_draw):
    font, draw = font_and_draw
    assert wrap_text(draw, "Temporary rendering error", font, 760, 3) == [
        "Temporary rendering error"
    ]
    assert wrap_text(draw, "", font, 760, 3) == []

    lines = wrap_text(draw, "word " * 200, font, 300, 2)
    assert len(lines) == 2
    assert lines[0].split() == ["word"] * len(lines[0].split())
    assert lines[1].endswith("...")
    assert all(text_width(draw, line, font) <= 300 for line in lines)

    (long_word,) = wrap_text(draw, "x" * 300, font, 300, 3)
    assert long_word.endswith("...") and text_width(draw, long_word, font) <= 300


def test_error_bmp_draws_a_long_message_on_several_lines():
    renderer = create_renderer("1bit", get_translator("en"), "en")
    message = "The race calendar could not be loaded because the provider timed out " * 3
    image = Image.open(io.BytesIO(renderer.render_error(message))).convert("L")
    padding = renderer.layout["padding"]

    def has_ink(top: int, bottom: int) -> bool:
        return image.crop((0, top, 800, bottom)).getextrema()[0] < 128

    # Lines start at padding + 50 and are 28 px apart; the old code drew one 60-char line.
    assert has_ink(padding + 50, padding + 78)
    assert has_ink(padding + 78, padding + 106)
    assert has_ink(padding + 106, padding + 134)


# --- A10: the header's first line stays inside the right margin -------------------------------


@pytest.mark.parametrize(("lang", "shrinks"), [("en", False), ("ja", True)])
def test_header_title_fits_the_available_width(lang, shrinks):
    renderer = create_renderer("1bit", get_translator(lang), lang)
    draw = ImageDraw.Draw(Image.new("1", (800, 480)))
    title = "2026 FIA F1 World Championship"
    available = renderer.width - (renderer.layout["header_split_x"] + 15) - 15
    base = renderer.fonts["header_title"]

    fitted = _shrink_font_to_width(draw, title, base, available, 24)

    assert text_width(draw, title, fitted) <= available
    assert (fitted.size < base.size) is shrinks
    assert fitted.path == base.path


def test_header_title_never_shrinks_below_the_minimum():
    renderer = create_renderer("1bit", get_translator("en"), "en")
    draw = ImageDraw.Draw(Image.new("1", (800, 480)))
    fitted = _shrink_font_to_width(draw, "x" * 200, renderer.fonts["header_title"], 100, 24)
    assert fitted.size == 24


# --- C10: shared renders are drained on shutdown -----------------------------------------------


@pytest.mark.asyncio
async def test_drain_singleflight_waits_for_and_cancels_in_flight_renders(caplog):
    release = asyncio.Event()

    async def quick():
        await release.wait()
        return (b"bmp", '"etag"')

    async def stuck():
        await asyncio.Event().wait()

    assert await bmp_artifacts.drain_singleflight() is None

    finished = asyncio.ensure_future(bmp_artifacts.singleflight("quick", quick))
    abandoned = asyncio.ensure_future(bmp_artifacts.singleflight("stuck", stuck))
    await asyncio.sleep(0)
    abandoned.cancel()  # the waiter disconnects; the shielded producer keeps running
    tasks = dict(bmp_artifacts._singleflight_tasks)
    loop = asyncio.get_running_loop()
    loop.call_later(0.01, release.set)

    await bmp_artifacts.drain_singleflight(timeout=0.1)

    assert await finished == (b"bmp", '"etag"')
    assert tasks["stuck"].cancelled()
    assert bmp_artifacts._singleflight_tasks == {}
    assert "Cancelling shared render bmp-singleflight:stuck" in caplog.text


# --- D11: font MIME types are registered once, by the app -------------------------------------


def test_font_mime_types_are_registered_by_the_app():
    import app.main  # noqa: F401

    assert mimetypes.guess_type("font.woff2")[0] == "font/woff2"
    assert mimetypes.guess_type("font.ttf")[0] == "font/ttf"
