"""Narrow renderer edge cases that are not exercised by full-image snapshots."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from app.services import renderer, spectra6_renderer
from app.services.bwr_renderer import BwrRenderer
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer


def _team_row_renderer_state(lang_code: str):
    return SimpleNamespace(
        lang_code=lang_code,
        _load_brand_font=MagicMock(return_value=object()),
        layout={"driver_name_padding": 2},
        colors=SimpleNamespace(BLACK=(0, 0, 0), WHITE=(255, 255, 255), PALETTE=[]),
        fonts={},
    )


def test_bwr_track_loader_returns_none_without_identifiers(tmp_path):
    assert (
        BwrRenderer._load_variant_track_image(
            {"circuit": {}},
            variant_suffix="bwr",
            source_tracks_dir=tmp_path,
            processed_tracks_dir=tmp_path,
        )
        is None
    )


def test_bwr_weather_font_falls_back_to_generic_icon_font(monkeypatch):
    fallback = object()
    instance = BwrRenderer.__new__(BwrRenderer)
    monkeypatch.setattr(BwrRenderer, "_load_icon_font", MagicMock(return_value=fallback))
    monkeypatch.setattr(
        "app.services.bwr_renderer.ImageFont.truetype",
        MagicMock(side_effect=OSError("missing")),
    )

    assert instance._load_weather_icon_font(16) is fallback


def test_monochrome_team_row_uses_brand_fonts_for_cjk():
    state = _team_row_renderer_state("ja")
    with (
        patch("app.services.renderer.build_team_header_values", return_value=("Team", "", "", "")),
        patch("app.services.renderer.draw_team_row") as shared_draw,
    ):
        Renderer._draw_team_row(
            state,
            Image.new("1", (20, 20)),
            MagicMock(),
            0,
            0,
            20,
            SimpleNamespace(position=1),
            20,
        )

    assert state._load_brand_font.call_count == 6
    shared_draw.assert_called_once()


def test_monochrome_driver_loader_handles_alpha_and_corrupt_files(tmp_path, monkeypatch):
    drivers = tmp_path / "drivers"
    drivers.mkdir()
    portrait = Image.new("RGBA", (2, 1))
    portrait.putdata([(0, 0, 0, 255), (0, 0, 0, 0)])
    portrait.save(drivers / "driver.png")
    (drivers / "broken.png").write_bytes(b"not an image")
    monkeypatch.setattr(renderer, "IMAGES_DIR", tmp_path)

    photos = Renderer._load_driver_photos()

    assert photos["driver"].mode == "1"
    assert photos["driver"].getpixel((0, 0)) == 0
    assert photos["driver"].getpixel((1, 0)) == 1


def test_monochrome_team_logo_loader_skips_missing_dir_and_corrupt_asset(tmp_path, monkeypatch):
    teams = tmp_path / "teams"
    teams.mkdir()
    (teams / "broken.png").write_bytes(b"not an image")
    monkeypatch.setattr(renderer, "TEAMS_COLOR_DIR", tmp_path / "missing-color")
    monkeypatch.setattr(renderer, "IMAGES_DIR", tmp_path)

    assert Renderer._load_team_logos() == {}


def test_sauber_normalizer_ignores_non_tuple_pixels(monkeypatch):
    rgba = MagicMock(size=(1, 1), width=1, height=1)
    rgba.getpixel.return_value = 5
    source = MagicMock()
    source.convert.return_value = rgba
    normalized = MagicMock()
    monkeypatch.setattr(renderer.Image, "new", MagicMock(return_value=normalized))

    assert Renderer.normalize_sauber_logo_for_non_spectra(source) is normalized
    normalized.putpixel.assert_not_called()


def test_spectra_team_row_uses_brand_fonts_for_cjk():
    state = _team_row_renderer_state("ja")
    with (
        patch(
            "app.services.spectra6_renderer.build_team_header_values",
            return_value=("Team", "", "", ""),
        ),
        patch("app.services.spectra6_renderer.draw_team_row") as shared_draw,
    ):
        Spectra6Renderer._draw_team_row(
            state,
            Image.new("RGB", (20, 20)),
            MagicMock(),
            0,
            0,
            20,
            SimpleNamespace(position=1),
            20,
        )

    assert state._load_brand_font.call_count == 6
    shared_draw.assert_called_once()


def test_spectra_session_color_defaults_to_black():
    state = SimpleNamespace(colors=SimpleNamespace(BLACK=(1, 2, 3)))

    assert Spectra6Renderer._get_session_color(state, "unknown") == (1, 2, 3)


def test_spectra_schedule_row_normalizes_sprint_alias():
    state = SimpleNamespace(
        width=800,
        layout={
            "schedule_date_x": 1,
            "schedule_day_x": 2,
            "schedule_time_x": 3,
            "schedule_name_x": 4,
        },
        translator={},
        lang_code="en",
        fonts={"schedule_row": object()},
        colors=SimpleNamespace(BLACK=(0, 0, 0)),
        _get_session_color=MagicMock(return_value=(1, 2, 3)),
    )
    with patch("app.services.spectra6_renderer.draw_schedule_row") as shared_draw:
        Spectra6Renderer._draw_schedule_row(state, MagicMock(), 10, {"name": "Sprint Shootout"})

    state._get_session_color.assert_called_once_with("Sprint Qualifying")
    shared_draw.assert_called_once()


def test_spectra_driver_loader_handles_corrupt_asset(tmp_path, monkeypatch):
    drivers = tmp_path / "drivers"
    drivers.mkdir()
    (drivers / "broken.png").write_bytes(b"not an image")
    monkeypatch.setattr(spectra6_renderer, "IMAGES_DIR", tmp_path)

    assert Spectra6Renderer._load_driver_photos() == {}


def test_spectra_team_logo_loader_skips_missing_dir_and_corrupt_asset(tmp_path, monkeypatch):
    teams = tmp_path / "teams"
    teams.mkdir()
    (teams / "broken.png").write_bytes(b"not an image")
    monkeypatch.setattr(spectra6_renderer, "TEAMS_COLOR_DIR", tmp_path / "missing-color")
    monkeypatch.setattr(spectra6_renderer, "IMAGES_DIR", tmp_path)

    assert Spectra6Renderer._load_team_logos() == {}
