"""Focused tests for shared renderer helpers."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cachetools import LRUCache
from PIL import Image, ImageDraw, ImageFont

from app.models import TeamDriverEntry
from app.services import (
    font_utils,
    renderer_assets,
    renderer_calendar,
    renderer_results,
    renderer_teams,
    renderer_text,
    renderer_theme,
)
from app.services.bwr_renderer import BwrColors, BwrRenderer
from app.services.bwry_renderer import BwryColors, BwryRenderer
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Colors, Spectra6Renderer


def test_select_active_team_drivers_uses_latest_roster_range():
    drivers = [
        TeamDriverEntry(name="Jack Doohan", rounds="1–6"),
        TeamDriverEntry(name="Franco Colapinto", rounds="7–24"),
        TeamDriverEntry(name="Pierre Gasly", rounds="All"),
    ]

    selected = renderer_text.select_active_team_drivers(drivers)

    assert [driver.name for driver in selected] == ["Franco Colapinto", "Pierre Gasly"]


def test_decoded_image_cache_evicts_by_bytes(tmp_path, monkeypatch):
    cache = LRUCache(maxsize=100, getsizeof=renderer_assets._decoded_image_size)
    monkeypatch.setattr(renderer_assets, "_DECODED_IMAGE_CACHE", cache)

    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    Image.new("RGB", (5, 5), "white").save(first_path)
    Image.new("RGB", (5, 5), "black").save(second_path)

    renderer_assets._load_image_file(str(first_path), first_path.stat().st_mtime_ns)
    renderer_assets._load_image_file(str(second_path), second_path.stat().st_mtime_ns)

    assert cache.currsize <= cache.maxsize
    assert len(cache) == 1


# Focused edge-case coverage for shared renderer primitives.


def test_decoded_image_larger_than_cache_budget_is_not_cached(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (1, 1), "white").save(source)
    cache_key = (str(source), source.stat().st_mtime_ns)
    renderer_assets._DECODED_IMAGE_CACHE.clear()
    monkeypatch.setattr(
        renderer_assets,
        "_decoded_image_size",
        lambda _image: renderer_assets._DECODED_IMAGE_CACHE.maxsize + 1,
    )

    assert renderer_assets._load_image_file(*cache_key).size == (1, 1)
    assert cache_key not in renderer_assets._DECODED_IMAGE_CACHE


def test_driver_round_window_rejects_invalid_label():
    assert renderer_text._driver_round_window("not-a-round") is None


def test_select_active_team_drivers_accepts_zero_limit():
    assert renderer_text.select_active_team_drivers([object()], limit=0) == []


def test_clamp_text_accepts_non_positive_width():
    assert renderer_text.clamp_text(MagicMock(), "text", object(), 0) == ""


def test_single_driver_name_is_uppercased():
    assert renderer_text.format_team_driver_display_name("Senna") == "SENNA"


def test_non_abbreviated_sprint_qualifying_label():
    assert (
        renderer_text.build_sprint_qualifying_label(
            {"session_sprint": "Sprint", "session_qualifying": "Qualifying"},
            "en",
            abbreviated=False,
        )
        == "Sprint Qualifying"
    )


def test_empty_schedule_term_is_preserved():
    assert renderer_text.abbreviate_schedule_term("", "en") == ""


@pytest.mark.parametrize("label", [None, "", 7])
def test_dedicated_sprint_label_rejects_invalid_values(label):
    assert (
        renderer_text.get_dedicated_sprint_qualifying_label({"session_sprintqualifying": label})
        is None
    )


def test_session_translation_handles_empty_and_direct_names():
    assert renderer_text.translate_session_name("", {}, "en") == ""
    assert (
        renderer_text.translate_session_name(
            "Practice 1", {"session_practice 1": "Direct practice"}, "en"
        )
        == "Direct practice"
    )
    assert (
        renderer_text.translate_session_name(
            "Practice 1", {"session_fp1": "Normalized practice"}, "en"
        )
        == "Normalized practice"
    )


@pytest.mark.parametrize(
    ("measured_width", "expected"),
    [(10, "Sprint Qualifying"), (100, "Sprint Q.")],
)
def test_sprint_schedule_label_chooses_full_or_abbreviated(measured_width, expected, monkeypatch):
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, measured_width, 10)
    monkeypatch.setattr(renderer_text, "fit_ui_font", MagicMock(return_value=object()))
    translator = {"session_sprint": "Sprint", "session_qualifying": "Qualifying"}

    assert (
        renderer_text.format_schedule_session_name(draw, "Sprint Shootout", 50, "en", translator)
        == expected
    )


def _width_draw(fitting_text: str | None):
    draw = MagicMock()

    def textbbox(_position, text, **_kwargs):
        return (0, 0, 1 if text == fitting_text else 100, 10)

    draw.textbbox.side_effect = textbbox
    return draw


@pytest.mark.parametrize(
    ("fitting_text", "expected"),
    [
        ("1. Driver (LongTea..)", "1. Driver (LongTea..)"),
        ("1. Driv. (Lon..)", "1. Driv. (Lon..)"),
        (None, "1. Drive.. (Lon..)"),
    ],
)
def test_fit_result_text_uses_team_driver_and_final_fallbacks(fitting_text, expected):
    assert (
        renderer_text.fit_result_text(
            _width_draw(fitting_text), object(), 10, 1, "Driver", "LongTeam"
        )
        == expected
    )


def test_results_flag_loader_returns_none_without_iso_mapping(tmp_path):
    assert (
        renderer_assets.load_results_flag_image(
            "Unknown", {}, tmp_path, lambda image: image.copy(), MagicMock()
        )
        is None
    )


def test_results_flag_loader_returns_none_for_empty_country(tmp_path):
    assert (
        renderer_assets.load_results_flag_image(
            "", {}, tmp_path, lambda image: image.copy(), MagicMock()
        )
        is None
    )


def test_results_flag_loader_logs_corrupt_local_flag(tmp_path):
    (tmp_path / "xx.bmp").write_bytes(b"broken")
    logger = MagicMock()

    assert (
        renderer_assets.load_results_flag_image(
            "Test", {"Test": "xx"}, tmp_path, lambda image: image.copy(), logger
        )
        is None
    )
    logger.warning.assert_called_once()


def test_results_header_resizes_wide_flag(monkeypatch):
    image = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(image)
    monkeypatch.setattr(
        renderer_results,
        "load_results_flag_image",
        MagicMock(return_value=Image.new("RGB", (100, 20))),
    )

    visual_top = renderer_results.draw_results_header(
        draw,
        image,
        canvas_height=100,
        header_area_width=50,
        y_start=50,
        season=2026,
        country_name="Test",
        year_font=ImageFont.load_default(),
        text_fill="black",
        outline_fill="black",
        country_map={},
        flags_dirs=font_utils.FONTS_DIR,
        prepare_flag_image=lambda flag: flag,
        logger=MagicMock(),
    )

    assert isinstance(visual_top, int)


def test_racing_font_uses_ui_fallback(monkeypatch):
    fallback = object()
    loader = MagicMock(return_value=fallback)
    monkeypatch.setattr(font_utils, "load_optional_truetype", MagicMock(return_value=None))

    assert font_utils.load_racing_font(12, MagicMock(), loader) is fallback
    loader.assert_called_once_with(12, bold=True)


def test_results_column_handles_entry_without_time():
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 10, 10)
    entry = SimpleNamespace(
        driver=SimpleNamespace(display_name="Driver"),
        constructor=SimpleNamespace(name="Team"),
        q3_time=None,
        time=None,
    )

    renderer_results.draw_results_column(
        draw,
        x_start=0,
        visual_top=0,
        title="RACE",
        results=[entry],
        is_qualifying=False,
        font_title=object(),
        font_row=object(),
        time_x=100,
        row_height=10,
        data_y_offset=0,
        text_fill="black",
        fit_result_text_fn=lambda *_args: "1. Driver (Team)",
    )

    assert draw.text.call_count == 2


def test_team_driver_row_uses_code_and_cjk_font_fallback(monkeypatch):
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 5, 5)
    fitted_font = object()
    monkeypatch.setattr(renderer_teams, "fit_brand_font_box", MagicMock(return_value=fitted_font))
    driver = SimpleNamespace(
        name="",
        given_name="",
        family_name="",
        driver_code="TST",
        driver_number=None,
        points=0,
        position=None,
    )

    renderer_teams.draw_team_driver_row(
        draw,
        Image.new("RGB", (20, 20)),
        driver,
        driver_y=0,
        driver_row_height=20,
        photo_x=0,
        photo_size=5,
        pts_right_x=15,
        driver_pos_x=18,
        badge_pad_x=1,
        small_font=object(),
        driver_font=object(),
        driver_name_padding=1,
        lang_code="ja",
        draw_driver_photo_fn=MagicMock(),
        get_text_y_fn=lambda *_args: 0,
        format_team_driver_display_name_fn=lambda name: name,
        format_points_fn=str,
        right_align_x_fn=lambda *_args: 10,
        text_fill="black",
        badge_outline_fill="black",
        badge_colors_fn=lambda _position: ("white", "black"),
    )

    assert any(call.args[1] == "TST" for call in draw.text.call_args_list)


def test_normalize_driver_photo_key_accepts_empty_name():
    assert renderer_teams.normalize_driver_photo_key("") == ""


def test_draw_driver_photo_scales_and_pastes_portrait():
    paste = MagicMock()
    photo = Image.new("RGB", (10, 20))

    width = renderer_teams.draw_driver_photo(
        MagicMock(),
        Image.new("RGB", (20, 20)),
        x=1,
        y=2,
        driver_name="Test Driver",
        size=10,
        driver_number=None,
        driver_photos={"driver": photo},
        get_racing_font_fn=MagicMock(),
        number_fill="black",
        resample=Image.Resampling.NEAREST,
        paste_photo_fn=paste,
    )

    assert width == 7
    assert paste.call_args.args[1].size == (5, 10)


@pytest.mark.parametrize(
    ("race_name", "should_shrink", "should_clamp"),
    [
        ("Monaco Grand Prix", False, False),
        ("Bahrain Grand Prix in Malaysia", True, False),
        ("An exceptionally long replacement Grand Prix name " * 4, True, True),
    ],
)
def test_draw_race_header_fits_long_race_name_without_changing_short_names(
    race_name, should_shrink, should_clamp
):
    image = Image.new("RGB", (800, 90), "white")
    real_draw = ImageDraw.Draw(image)
    draw = MagicMock(wraps=real_draw)
    title_font = font_utils.load_ui_font("en", 36, bold=True)
    subtitle_font = font_utils.load_ui_font("en", 36, bold=True)

    renderer_calendar.draw_race_header(
        draw,
        image,
        {"race_name": race_name, "season": 2026},
        canvas_width=800,
        header_height=90,
        split_x=230,
        left_fill="white",
        divider_fill="black",
        right_fill="white",
        title_fill="black",
        header_title_font=title_font,
        header_subtitle_font=subtitle_font,
        draw_f1_logo_fn=MagicMock(),
    )

    subtitle_call = draw.text.call_args_list[-1]
    rendered_text = subtitle_call.args[1]
    rendered_font = subtitle_call.kwargs["font"]
    rendered_bbox = real_draw.textbbox(subtitle_call.args[0], rendered_text, font=rendered_font)

    assert rendered_bbox[2] <= 800 - 15
    assert (rendered_font is not subtitle_font) is should_shrink
    assert rendered_text.endswith("...") is should_clamp
    assert (rendered_text != race_name.upper()) is should_clamp


def test_draw_f1_logo_logs_missing_file(tmp_path):
    logger = MagicMock()

    renderer_calendar.draw_f1_logo(
        Image.new("RGB", (20, 20)),
        20,
        20,
        logo_path=tmp_path / "missing.png",
        logger=logger,
        prepare_logo_fn=lambda logo: logo,
    )

    logger.warning.assert_called_once()


def test_draw_f1_logo_logs_decode_error(tmp_path):
    logo = tmp_path / "broken.png"
    logo.write_bytes(b"broken")
    logger = MagicMock()

    renderer_calendar.draw_f1_logo(
        Image.new("RGB", (20, 20)),
        20,
        20,
        logo_path=logo,
        logger=logger,
        prepare_logo_fn=lambda image: image,
    )

    logger.warning.assert_called_once()


def test_track_asset_loader_accepts_empty_stem_list(tmp_path):
    assert renderer_assets.load_track_image_asset([], processed_dir=tmp_path) is None


@pytest.mark.parametrize("with_logger", [False, True])
def test_track_asset_loader_handles_processed_decode_error(tmp_path, monkeypatch, with_logger):
    (tmp_path / "track.bmp").write_bytes(b"broken")
    logger = MagicMock() if with_logger else None
    monkeypatch.setattr(
        renderer_assets, "_load_image_copy", MagicMock(side_effect=OSError("broken"))
    )

    assert (
        renderer_assets.load_track_image_asset(["track"], processed_dir=tmp_path, logger=logger)
        is None
    )
    if logger is not None:
        logger.warning.assert_called_once()


@pytest.mark.parametrize("with_logger", [False, True])
def test_track_asset_loader_handles_wildcard_decode_error(tmp_path, monkeypatch, with_logger):
    (tmp_path / "other.bmp").write_bytes(b"broken")
    logger = MagicMock() if with_logger else None
    monkeypatch.setattr(
        renderer_assets, "_load_image_copy", MagicMock(side_effect=OSError("broken"))
    )

    assert (
        renderer_assets.load_track_image_asset(
            ["track"],
            processed_dir=tmp_path,
            fallback_glob="*.bmp",
            logger=logger,
        )
        is None
    )
    if logger is not None:
        logger.warning.assert_called_once()


def test_track_asset_loader_handles_empty_wildcard(tmp_path):
    assert (
        renderer_assets.load_track_image_asset(
            ["track"],
            processed_dir=tmp_path,
            fallback_glob="*.bmp",
        )
        is None
    )


def test_find_race_datetime_returns_none_without_race():
    assert renderer_calendar._find_race_datetime([{"name": "Practice"}], datetime) is None


def test_resolve_countdown_status_accepts_missing_race_datetime():
    assert renderer_calendar._resolve_countdown_status(
        is_cancelled=False, race_dt=None, datetime_cls=datetime, translator={}
    ) == (None, None)


def _countdown_kwargs(draw):
    return {
        "schedule_bottom": 100,
        "right_column_x": 400,
        "canvas_width": 800,
        "results_y_start": 400,
        "circuit_stats_row_height": 20,
        "schedule_row_bold_font": object(),
        "icon_small_font": object(),
        "weather_icon_font": object(),
        "translator": {},
        "lang_code": "en",
        "datetime_cls": datetime,
        "text_baseline_ref": "Hg",
        "rain_icon": "rain",
        "box_fill": "white",
        "box_outline": "black",
        "text_fill": "black",
    }


def test_countdown_status_draws_weather(monkeypatch):
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 10, 10)
    weather = object()
    weather_draw = MagicMock()
    monkeypatch.setattr(renderer_calendar, "_find_race_datetime", lambda *_args: object())
    monkeypatch.setattr(
        renderer_calendar,
        "_resolve_countdown_status",
        lambda **_kwargs: ("ONGOING", timedelta(hours=-1)),
    )
    monkeypatch.setattr(renderer_calendar, "_draw_countdown_weather", weather_draw)

    result = renderer_calendar.draw_countdown_box(
        draw, {"schedule": []}, weather_data=weather, **_countdown_kwargs(draw)
    )

    assert result > 100
    weather_draw.assert_called_once()


def test_countdown_without_status_or_delta_keeps_schedule_bottom(monkeypatch):
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 10, 10)
    monkeypatch.setattr(renderer_calendar, "_find_race_datetime", lambda *_args: object())
    monkeypatch.setattr(
        renderer_calendar, "_resolve_countdown_status", lambda **_kwargs: (None, None)
    )

    assert (
        renderer_calendar.draw_countdown_box(draw, {"schedule": []}, **_countdown_kwargs(draw))
        == 100
    )


@pytest.mark.parametrize(
    "circuit_data",
    [
        {
            "circuit_length": "5 km",
            "fastest_lap_time": "1:20",
            "fastest_lap_driver": "Test Driver",
        },
        {"fastest_lap_time": "1:20"},
    ],
)
def test_circuit_stats_supports_missing_laps_driver_and_year(circuit_data):
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 10, 10)

    renderer_calendar.draw_circuit_stats_block(
        draw,
        circuit_data,
        translator={},
        results_y_start=400,
        right_column_x=400,
        canvas_width=800,
        row_height=20,
        font_icon=object(),
        font_value=object(),
        fill="black",
    )

    assert draw.text.called


def test_prepare_mono_track_handles_blank_and_preprocessed_images():
    blank = Image.new("RGB", (2, 2), "white")
    preprocessed = Image.new("1", (2, 2), 1)

    assert renderer_assets.prepare_mono_track_image(blank, 2, 2, MagicMock()).mode == "1"
    assert renderer_assets.prepare_mono_track_image(preprocessed, 2, 2, MagicMock()) is preprocessed


def test_prepare_mono_track_resamples_preprocessed_maps_in_grayscale():
    track = Image.new("1", (40, 40), 1)
    ImageDraw.Draw(track).rectangle((0, 18, 39, 21), fill=0)

    resized = renderer_assets.prepare_mono_track_image(track, 20, 20, MagicMock())

    assert resized.mode == "1"
    assert resized.size == (20, 20)
    # NEAREST would leave a dashed or missing line; grayscale resampling keeps a solid row.
    assert any(all(resized.getpixel((x, y)) == 0 for x in range(20)) for y in range(20))


def test_prepare_mono_track_logs_crop_failure():
    track = MagicMock(mode="RGB", size=(2, 2))
    track.convert.side_effect = OSError("cannot convert")
    converted = object()
    track.point.return_value.convert.return_value = converted
    logger = MagicMock()

    assert renderer_assets.prepare_mono_track_image(track, 2, 2, logger) is converted
    logger.warning.assert_called_once()


def test_prepare_color_track_skips_resize_when_already_fitted():
    track = Image.new("RGB", (2, 2), "white")

    result = renderer_assets.prepare_color_track_image(track, 2, 2)

    assert result.size == (2, 2)


def test_schedule_section_stops_before_results_area(monkeypatch):
    draw = MagicMock()
    schedule_row = MagicMock()
    countdown = MagicMock(return_value=123)
    monkeypatch.setattr(renderer_calendar, "fit_ui_font", MagicMock(return_value=object()))

    result = renderer_calendar.draw_schedule_section(
        draw,
        {"schedule": [{"name": "Practice"}, {"name": "Race"}]},
        canvas_width=800,
        right_column_x=400,
        schedule_title_y=10,
        schedule_start_y=100,
        schedule_row_height=20,
        results_y_start=190,
        translator={},
        lang_code="en",
        title_fill="black",
        draw_schedule_row_fn=schedule_row,
        draw_countdown_box_fn=countdown,
        weather_data=None,
        weather_type="",
    )

    assert result == 123
    schedule_row.assert_called_once()


def test_team_identities_cover_all_teams():
    """Every known team should map to a key, abbreviation, and valid RGB identity color."""
    constructors = {
        "Alpine F1 Team": ("alpine", "ALP"),
        "Aston Martin": ("aston_martin", "AST"),
        "Audi": ("audi", "AUD"),
        "Cadillac Formula 1 Team": ("cadillac", "CAD"),
        "Ferrari": ("ferrari", "FER"),
        "Haas F1 Team": ("haas", "HAA"),
        "McLaren": ("mclaren", "MCL"),
        "Mercedes": ("mercedes", "MER"),
        "RB": ("racing_bulls", "RB"),
        "Red Bull": ("red_bull", "RBR"),
        "Kick Sauber": ("sauber", "SAU"),
        "Williams": ("williams", "WIL"),
    }

    assert set(renderer_text.TEAM_IDENTITIES) == {key for key, _ in constructors.values()}
    for constructor, (team_key, abbreviation) in constructors.items():
        assert renderer_text.get_team_key(constructor) == team_key
        badge_text, identity = renderer_text.get_team_badge(constructor)
        assert badge_text == abbreviation
        assert identity is not None
        assert len(identity) == 3
        assert all(0 <= channel <= 255 for channel in identity)


@pytest.mark.parametrize(
    ("constructor", "expected"),
    [("Andretti Global", "AND"), ("7 Up", "UP"), ("", "")],
)
def test_get_team_badge_falls_back_to_neutral_initials(constructor, expected):
    """Unknown teams should get up to three uppercase letters and no identity color."""
    assert renderer_text.get_team_badge(constructor) == (expected, None)


@pytest.mark.parametrize(
    ("renderer_cls", "team_key", "fill_name", "text_name"),
    [
        (Spectra6Renderer, "ferrari", "RED", "WHITE"),
        (Spectra6Renderer, "mclaren", "YELLOW", "BLACK"),
        (Spectra6Renderer, "mercedes", "BLUE", "WHITE"),
        (Spectra6Renderer, "aston_martin", "GREEN", "WHITE"),
        (Spectra6Renderer, "haas", "WHITE", "BLACK"),
        (Spectra6Renderer, "cadillac", "BLACK", "WHITE"),
        (BwrRenderer, "mclaren", "RED", "WHITE"),
        (BwrRenderer, "mercedes", "WHITE", "BLACK"),
        (BwrRenderer, "williams", "BLACK", "WHITE"),
        (BwryRenderer, "mclaren", "YELLOW", "BLACK"),
        (Renderer, "ferrari", "BLACK", "WHITE"),
        (Renderer, "haas", "BLACK", "WHITE"),
    ],
)
def test_team_badge_colors_use_nearest_palette_fill_with_contrast(
    renderer_cls, team_key, fill_name, text_name
):
    """Badge fills should snap to the nearest display color with readable text."""
    colors = renderer_cls.THEME.colors
    identity = renderer_text.TEAM_IDENTITIES[team_key][1]

    assert renderer_cls.THEME.team_badge_colors(identity) == (
        getattr(colors, fill_name),
        getattr(colors, text_name),
    )


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer, BwrRenderer, BwryRenderer])
def test_team_badge_colors_are_neutral_for_unknown_teams(renderer_cls):
    """Teams without an identity color should get a black badge with white text."""
    colors = renderer_cls.THEME.colors

    assert renderer_cls.THEME.team_badge_colors(None) == (colors.BLACK, colors.WHITE)


def test_nearest_palette_color_picks_closest_entry():
    """Nearest-color lookup should pick the palette entry with the smallest RGB distance."""
    assert renderer_theme.nearest_palette_color((250, 10, 10), BwrColors.PALETTE) == BwrColors.RED
    assert (
        renderer_theme.nearest_palette_color((240, 200, 20), Spectra6Colors.PALETTE)
        == Spectra6Colors.YELLOW
    )


def _draw_badge(image, renderer_cls, constructor, *, container_right=196):
    """Draw a team badge into a fixed test slot using the renderer's theme."""
    renderer_teams.draw_team_badge(
        ImageDraw.Draw(image),
        SimpleNamespace(constructor_name=constructor, entrant=""),
        driver_area_y=0,
        driver_area_h=35,
        container_left=100,
        container_right=container_right,
        badge_colors_fn=renderer_cls.THEME.team_badge_colors,
        outline_fill=renderer_cls.THEME.team_outline_fill,
    )


@pytest.mark.parametrize("constructor", ["Ferrari", "Andretti Global"])
def test_monochrome_team_badge_is_black_with_crisp_white_text(constructor):
    """1-bit badges should be black plates with white text and only pure black/white pixels."""
    image = Image.new("1", (220, 40), 1)

    _draw_badge(image, Renderer, constructor)

    badge = image.convert("L").crop((112, 2, 184, 32))
    counts = {value: count for count, value in badge.getcolors()}
    assert set(counts) == {0, 255}
    assert counts[0] > counts[255]
    assert image.getpixel((113, 17)) == 0


@pytest.mark.parametrize(
    ("renderer_cls", "constructor", "fill"),
    [
        (Spectra6Renderer, "Ferrari", Spectra6Colors.RED),
        (Spectra6Renderer, "Mercedes", Spectra6Colors.BLUE),
        (Spectra6Renderer, "Andretti Global", Spectra6Colors.BLACK),
        (BwrRenderer, "Ferrari", BwrColors.RED),
        (BwryRenderer, "McLaren", BwryColors.YELLOW),
    ],
)
def test_color_team_badge_fills_slot_with_palette_color(renderer_cls, constructor, fill):
    """Color badges should use the mapped palette fill inside a themed outline."""
    image = Image.new("RGB", (220, 40), (255, 255, 255))

    _draw_badge(image, renderer_cls, constructor)

    assert image.getpixel((113, 17)) == fill
    assert image.getpixel((112, 17)) == renderer_cls.THEME.team_outline_fill


def test_team_badge_skips_empty_slot_or_name():
    """No badge should be drawn for a zero-width slot or an empty team name."""
    image = Image.new("1", (220, 40), 1)

    _draw_badge(image, Renderer, "Ferrari", container_right=100)
    _draw_badge(image, Renderer, "")

    assert image.convert("L").getextrema() == (255, 255)
