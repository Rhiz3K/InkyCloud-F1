"""Tests for palette-mapped team card header colors on the teams screen."""

from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from app.services import renderer_base, renderer_text, renderer_theme
from app.services.bwr_renderer import BwrColors, BwrRenderer
from app.services.bwry_renderer import BwryColors, BwryRenderer
from app.services.i18n import get_translator
from app.services.renderer import MONO_THEME, Renderer
from app.services.spectra6_renderer import Spectra6Colors, Spectra6Renderer
from app.services.teams_service import TeamsService

BLACK = (0x00, 0x00, 0x00)
WHITE = (0xFF, 0xFF, 0xFF)
RED = (0xFF, 0x00, 0x00)
YELLOW = (0xFF, 0xD8, 0x00)
GREEN = (0x00, 0xD8, 0x00)
BLUE = (0x00, 0xA8, 0xFF)

# Nearest palette entry per team by redmean distance (before the white fallback).
EXPECTED_NEAREST = {
    "bwr": {
        "alpine": BLACK,
        "aston_martin": BLACK,
        "audi": RED,
        "cadillac": BLACK,
        "ferrari": RED,
        "haas": WHITE,
        "mclaren": RED,
        "mercedes": WHITE,
        "racing_bulls": WHITE,
        "red_bull": BLACK,
        "sauber": WHITE,
        "williams": BLACK,
    },
    "bwry": {
        "alpine": BLACK,
        "aston_martin": BLACK,
        "audi": RED,
        "cadillac": BLACK,
        "ferrari": RED,
        "haas": WHITE,
        "mclaren": YELLOW,
        "mercedes": WHITE,
        "racing_bulls": WHITE,
        "red_bull": BLACK,
        "sauber": YELLOW,
        "williams": BLACK,
    },
    "spectra6": {
        "alpine": BLUE,
        "aston_martin": GREEN,
        "audi": RED,
        "cadillac": BLACK,
        "ferrari": RED,
        "haas": WHITE,
        "mclaren": YELLOW,
        "mercedes": BLUE,
        "racing_bulls": BLUE,
        "red_bull": BLUE,
        "sauber": GREEN,
        "williams": BLUE,
    },
}
PALETTES = {"bwr": BwrColors, "bwry": BwryColors, "spectra6": Spectra6Colors}
RENDERERS = {"bwr": BwrRenderer, "bwry": BwryRenderer, "spectra6": Spectra6Renderer}
# Text picked by contrast for each possible non-white fill.
EXPECTED_TEXT = {BLACK: WHITE, RED: BLACK, YELLOW: BLACK, GREEN: BLACK, BLUE: BLACK}


def expected_header(display: str, team_key: str | None) -> tuple:
    """Return the expected ``(fill, text)`` pair including both fallbacks."""
    nearest = EXPECTED_NEAREST[display].get(team_key) if team_key else None
    if nearest is None or nearest == WHITE:
        return BLACK, WHITE
    return nearest, EXPECTED_TEXT[nearest]


@pytest.mark.parametrize("display", sorted(PALETTES))
def test_nearest_palette_color_for_every_team(display):
    palette = PALETTES[display].PALETTE

    assert set(EXPECTED_NEAREST[display]) == set(renderer_text.TEAM_COLORS)
    for team_key, team_color in renderer_text.TEAM_COLORS.items():
        nearest = renderer_theme.nearest_palette_color(team_color, palette)
        assert nearest == EXPECTED_NEAREST[display][team_key], team_key


def test_nearest_palette_color_prefers_first_entry_on_ties_and_rejects_empty():
    # (0, 5, 0) is exactly equidistant from (0, 0, 0) and (0, 10, 0).
    assert renderer_theme.nearest_palette_color((0, 5, 0), [BLACK, (0, 10, 0)]) == BLACK
    assert renderer_theme.nearest_palette_color((0, 5, 0), [(0, 10, 0), BLACK]) == (0, 10, 0)
    assert renderer_theme.nearest_palette_color(BLACK, [WHITE, BLACK]) == BLACK
    with pytest.raises(ValueError):
        renderer_theme.nearest_palette_color(BLACK, [])


def test_redmean_distance_is_symmetric_and_zero_on_identity():
    assert renderer_theme.redmean_distance_sq(RED, RED) == 0
    assert renderer_theme.redmean_distance_sq(RED, BLUE) == pytest.approx(
        renderer_theme.redmean_distance_sq(BLUE, RED)
    )


def test_relative_luminance_and_contrast_ratio_match_wcag():
    assert renderer_theme.relative_luminance(BLACK) == 0
    assert renderer_theme.relative_luminance(WHITE) == pytest.approx(1)
    assert renderer_theme.relative_luminance((1, 1, 1)) == pytest.approx(1 / 255 / 12.92)
    assert renderer_theme.relative_luminance(RED) == pytest.approx(0.2126)
    assert renderer_theme.contrast_ratio(BLACK, WHITE) == pytest.approx(21)
    assert renderer_theme.contrast_ratio(WHITE, BLACK) == pytest.approx(21)
    assert renderer_theme.contrast_ratio(RED, BLACK) == pytest.approx(5.252)
    assert renderer_theme.contrast_ratio(RED, WHITE) == pytest.approx(3.998, abs=1e-3)


@pytest.mark.parametrize("display", sorted(PALETTES))
def test_team_header_colors_pick_higher_contrast_text(display):
    colors = PALETTES[display]

    for team_key, team_color in renderer_text.TEAM_COLORS.items():
        fill, text = renderer_theme.team_header_colors(
            team_color,
            colors.PALETTE,
            background=colors.WHITE,
            default_fill=colors.BLACK,
            default_text=colors.WHITE,
        )
        assert (fill, text) == expected_header(display, team_key), team_key
        other = BLACK if text == WHITE else WHITE
        assert renderer_theme.contrast_ratio(fill, text) >= renderer_theme.contrast_ratio(
            fill, other
        )
        assert renderer_theme.contrast_ratio(fill, text) >= 3.0


def test_team_header_colors_tie_prefers_light_text(monkeypatch):
    monkeypatch.setattr(renderer_theme, "contrast_ratio", lambda _first, _second: 4.5)

    assert renderer_theme.team_header_colors(
        RED, [BLACK, WHITE, RED], background=WHITE, default_fill=BLACK, default_text=WHITE
    ) == (RED, WHITE)


def test_team_header_colors_fall_back_for_white_and_missing_colors():
    palette = [BLACK, WHITE, RED]
    kwargs = {"background": WHITE, "default_fill": "default", "default_text": "text"}
    haas_grey = (0xB6, 0xBA, 0xBD)

    assert renderer_theme.team_header_colors(haas_grey, palette, **kwargs) == ("default", "text")
    assert renderer_theme.team_header_colors(None, palette, **kwargs) == ("default", "text")


@pytest.mark.parametrize("display", sorted(PALETTES))
def test_color_theme_lookup_by_constructor_name(display):
    theme = RENDERERS[display].THEME

    assert theme.team_header_colors_for("Haas F1 Team") == (BLACK, WHITE)
    assert theme.team_header_colors_for("Andretti Global") == (BLACK, WHITE)
    assert theme.team_header_colors_for("") == (BLACK, WHITE)
    assert theme.team_header_colors_for("Ferrari") == (RED, BLACK)


def test_monochrome_theme_always_uses_black_header_with_white_text():
    for constructor in ("Ferrari", "McLaren", "Haas F1 Team", "Mercedes", "Unknown", ""):
        assert MONO_THEME.team_header_colors_for(constructor) == (
            MONO_THEME.colors.BLACK,
            MONO_THEME.colors.WHITE,
        )


def render_2026_teams(monkeypatch, renderer) -> tuple[Image.Image, list]:
    """Render the bundled 2026 teams screen and record every drawn card."""
    teams_data = TeamsService()._load_from_json(2026)
    assert teams_data is not None and teams_data.teams

    cards = []
    original_draw_team_row = renderer_base.draw_team_row

    def spy_draw_team_row(image, draw, team, **kwargs):
        cards.append((team, kwargs["x_start"], kwargs["y"], kwargs["x_end"]))
        return original_draw_team_row(image, draw, team, **kwargs)

    monkeypatch.setattr(renderer_base, "draw_team_row", spy_draw_team_row)
    bmp_data = renderer.render_teams_drivers(teams_data)
    monkeypatch.setattr(renderer_base, "draw_team_row", original_draw_team_row)

    assert len(cards) == len(teams_data.teams)
    return Image.open(BytesIO(bmp_data)), cards


@pytest.mark.parametrize("display", sorted(RENDERERS))
def test_full_2026_teams_screen_uses_mapped_header_fills(monkeypatch, display):
    image, cards = render_2026_teams(monkeypatch, RENDERERS[display](get_translator("en")))
    rgb = image.convert("RGB")

    for team, x_start, y, x_end in cards:
        team_key = renderer_text.get_team_key(team.constructor_name or team.entrant)
        assert team_key is not None, team.constructor_name
        expected_fill, _text = expected_header(display, team_key)
        assert rgb.getpixel((x_start + 1, y + 1)) == expected_fill, team_key
        # The stats panel stays white between its position and points cells.
        panel_x = x_end - 72 - 5
        assert rgb.getpixel((panel_x + 26, y + 12)) == WHITE, team_key


def test_full_2026_teams_screen_is_unchanged_on_one_bit(monkeypatch):
    renderer = Renderer(get_translator("en"))
    image, cards = render_2026_teams(monkeypatch, renderer)

    for _team, x_start, y, _x_end in cards:
        assert image.convert("1").getpixel((x_start + 1, y + 1)) == 0

    renderer.theme = replace(
        MONO_THEME,
        team_header_colors_for=lambda _name: (
            MONO_THEME.team_header_fill,
            MONO_THEME.team_header_text_fill,
        ),
    )
    default_image, _cards = render_2026_teams(monkeypatch, renderer)

    assert image.tobytes() == default_image.tobytes()
