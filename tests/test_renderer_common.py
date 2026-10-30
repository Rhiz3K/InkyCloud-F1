"""Focused tests for shared renderer helpers."""

from datetime import datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cachetools import LRUCache
from PIL import Image, ImageDraw, ImageFont

from app.models import TeamDriverEntry, TeamEntry, TeamsData
from app.services import (
    font_utils,
    renderer_assets,
    renderer_calendar,
    renderer_results,
    renderer_teams,
    renderer_text,
)
from app.services.bwr_renderer import BwrRenderer
from app.services.bwry_renderer import BwryRenderer
from app.services.i18n import get_translator
from app.services.renderer import Renderer
from app.services.spectra6_renderer import Spectra6Renderer
from app.services.teams_service import TeamsService


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


def test_team_driver_row_falls_back_to_driver_code():
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 5, 5)
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
        Image.new("RGB", (80, 20)),
        driver,
        driver_y=0,
        driver_row_height=20,
        photo_x=0,
        photo_size=5,
        pts_right_x=75,
        driver_pos_x=60,
        badge_pad_x=1,
        small_font=object(),
        driver_name_padding=1,
        draw_driver_photo_fn=MagicMock(),
        get_text_y_fn=lambda *_args: 0,
        format_team_driver_display_name_fn=lambda name: name,
        format_points_fn=str,
        right_align_x_fn=lambda *_args: 10,
        text_fill="black",
        badge_outline_fill="black",
        badge_colors_fn=lambda _position: ("white", "black"),
        lang_code="en",
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


def test_team_colors_cover_all_teams():
    """Every known team should map to a key and a valid RGB identity color."""
    constructors = {
        "Alpine F1 Team": "alpine",
        "Aston Martin": "aston_martin",
        "Audi": "audi",
        "Cadillac Formula 1 Team": "cadillac",
        "Ferrari": "ferrari",
        "Haas F1 Team": "haas",
        "McLaren": "mclaren",
        "Mercedes": "mercedes",
        "RB": "racing_bulls",
        "Red Bull": "red_bull",
        "Kick Sauber": "sauber",
        "Williams": "williams",
    }

    assert set(renderer_text.TEAM_COLORS) == set(constructors.values())
    for constructor, team_key in constructors.items():
        assert renderer_text.get_team_key(constructor) == team_key
        identity = renderer_text.TEAM_COLORS[team_key]
        assert len(identity) == 3
        assert all(0 <= channel <= 255 for channel in identity)


@pytest.mark.parametrize(
    ("constructor", "expected"),
    [("McLaren", "#ff8000"), ("Alpine F1 Team", "#0093cc"), ("Andretti Global", None)],
)
def test_get_team_color_hex_formats_team_color(constructor, expected):
    """Web swatches should use the team identity color as lowercase ``#rrggbb``."""
    assert renderer_text.get_team_color_hex(constructor) == expected


def test_fit_team_driver_name_uses_largest_size_for_typical_name():
    """A typical driver name should get the maximum 18px driver font."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))

    name, font = renderer_teams.fit_team_driver_name(draw, "George RUSSELL", max_width=230)

    assert name == "George RUSSELL"
    assert font.size == 18


def test_fit_team_driver_name_shrinks_until_name_fits_width():
    """A name that fits at 16px but not at 18px should shrink to 16px without ellipsis."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    name = "Nico HÜLKENBERG"
    width_16 = draw.textbbox((0, 0), name, font=renderer_teams.load_brand_font(16, bold=True))[2]
    width_18 = draw.textbbox((0, 0), name, font=renderer_teams.load_brand_font(18, bold=True))[2]
    assert width_16 < width_18

    fitted, font = renderer_teams.fit_team_driver_name(draw, name, max_width=int(width_16))

    assert fitted == name
    assert font.size == 16


def test_team_driver_row_centres_fitted_name_in_driver_row():
    """The fitted driver name should be vertically centred in its row via ``get_text_y``."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    for name in ("George RUSSELL", "Nico HÜLKENBERG", "Sergio PÉREZ"):
        spy = MagicMock(wraps=draw)
        driver = SimpleNamespace(
            name=name, driver_number=None, points=0, position=None, driver_code=""
        )
        renderer_teams.draw_team_driver_row(
            spy,
            Image.new("RGB", (400, 60)),
            driver,
            driver_y=10,
            driver_row_height=22,
            photo_x=0,
            photo_size=20,
            pts_right_x=390,
            driver_pos_x=320,
            badge_pad_x=5,
            small_font=renderer_teams.load_brand_font(12),
            driver_name_padding=1,
            draw_driver_photo_fn=MagicMock(),
            get_text_y_fn=renderer_text.get_text_y,
            format_team_driver_display_name_fn=lambda value: value,
            format_points_fn=str,
            right_align_x_fn=lambda *_args: 350,
            text_fill="black",
            badge_outline_fill="black",
            badge_colors_fn=lambda _position: ("white", "black"),
            lang_code="en",
        )
        name_call = next(call for call in spy.text.call_args_list if call.args[1] == name)
        font = name_call.kwargs["font"]
        assert "anchor" not in name_call.kwargs
        assert name_call.args[0][1] == renderer_text.get_text_y(draw, font, 22, 10, name)


def test_fit_team_driver_name_ellipsizes_overlong_name_within_slot():
    """An overlong accented name should be ellipsized so its right edge stays in the slot."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    long_name = "Gabriel BORTOLETO ANÇÃ-ØSTERGÅRD"

    name, font = renderer_teams.fit_team_driver_name(draw, long_name, max_width=180)

    assert name != long_name
    assert name.endswith("...")
    assert font.size == renderer_teams.DRIVER_NAME_MIN_SIZE
    assert draw.textbbox((0, 0), name, font=font)[2] <= 180


def test_fit_team_driver_name_with_max_height_shrinks_to_fit_row_height():
    """With ``max_height`` the driver font should shrink until the name fits the row height."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    name = "George RUSSELL"
    bbox_14 = draw.textbbox((0, 0), name, font=renderer_teams.load_brand_font(14, bold=True))
    max_height = int(bbox_14[3] - bbox_14[1])

    fitted, font = renderer_teams.fit_team_driver_name(
        draw, name, max_width=230, max_height=max_height
    )

    bbox = draw.textbbox((0, 0), fitted, font=font)
    assert fitted == name
    assert font.size < renderer_teams.DRIVER_NAME_MAX_SIZE
    assert bbox[3] - bbox[1] <= max_height


def test_fit_team_driver_name_with_max_height_ellipsizes_at_cjk_minimum_size():
    """A name too wide at the CJK minimum size should be ellipsized within the slot."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    long_name = "Gabriel BORTOLETO ANÇÃ-ØSTERGÅRD"

    name, font = renderer_teams.fit_team_driver_name(draw, long_name, max_width=100, max_height=100)

    assert name != long_name
    assert name.endswith("...")
    assert font.size == renderer_teams.CJK_DRIVER_NAME_MIN_SIZE
    assert draw.textbbox((0, 0), name, font=font)[2] <= 100


@pytest.mark.parametrize(
    ("lang_code", "expected_max_height"), [("ja", 21), ("zh-CN", 21), ("en", None)]
)
def test_team_driver_row_limits_name_height_only_for_cjk(
    monkeypatch, lang_code, expected_max_height
):
    """CJK locales should fit the driver name to the row height; others only to the width."""
    draw = ImageDraw.Draw(Image.new("RGB", (400, 60), "white"))
    fit_spy = MagicMock(wraps=renderer_teams.fit_team_driver_name)
    monkeypatch.setattr(renderer_teams, "fit_team_driver_name", fit_spy)
    driver = SimpleNamespace(
        name="George RUSSELL", driver_number=None, points=0, position=None, driver_code=""
    )

    renderer_teams.draw_team_driver_row(
        draw,
        Image.new("RGB", (400, 60)),
        driver,
        driver_y=10,
        driver_row_height=22,
        photo_x=0,
        photo_size=20,
        pts_right_x=390,
        driver_pos_x=320,
        badge_pad_x=5,
        small_font=renderer_teams.load_brand_font(12),
        driver_name_padding=1,
        draw_driver_photo_fn=MagicMock(),
        get_text_y_fn=renderer_text.get_text_y,
        format_team_driver_display_name_fn=lambda value: value,
        format_points_fn=str,
        right_align_x_fn=lambda *_args: 350,
        text_fill="black",
        badge_outline_fill="black",
        badge_colors_fn=lambda _position: ("white", "black"),
        lang_code=lang_code,
    )

    fit_spy.assert_called_once()
    assert fit_spy.call_args.kwargs["max_width"] == 320 - 8 - (0 + 20 + 1 + 4)
    assert fit_spy.call_args.kwargs["max_height"] == expected_max_height


@pytest.mark.parametrize("renderer_cls", [Renderer, BwrRenderer, BwryRenderer, Spectra6Renderer])
def test_render_teams_drivers_succeeds_without_team_badges(renderer_cls):
    """Every display should render the teams screen without any team badge helper."""
    teams_data = TeamsData(
        season=2026,
        teams=[
            TeamEntry(
                constructor_name="Ferrari",
                chassis="SF-26",
                power_unit="Ferrari 067/6",
                position=1,
                points=27.0,
                drivers=[
                    TeamDriverEntry(name="Charles Leclerc", driver_number=16, position=2),
                    TeamDriverEntry(name="Lewis Hamilton", driver_number=44, position=5),
                ],
            )
        ],
    )

    bmp_data = renderer_cls(get_translator("en")).render_teams_drivers(teams_data)

    assert not hasattr(renderer_teams, "draw_team_badge")
    assert Image.open(BytesIO(bmp_data)).size == (800, 480)


# Badge abbreviations from the removed TEAM_IDENTITIES / get_team_badge on origin/main.
TEAM_BADGE_ABBREVIATIONS = {
    "ALP",
    "AST",
    "AUD",
    "CAD",
    "FER",
    "HAA",
    "MCL",
    "MER",
    "RB",
    "RBR",
    "SAU",
    "WIL",
}


@pytest.mark.parametrize("renderer_cls", [Renderer, Spectra6Renderer])
@pytest.mark.parametrize("lang_code", ["en", "ja"])
def test_render_full_2026_teams_screen_draws_no_badges(monkeypatch, renderer_cls, lang_code):
    """The full 2026 teams screen draws no team abbreviation badges.

    Every ``ImageDraw.text`` call is recorded. No recorded string may be a former
    team abbreviation badge, and every driver name (identified by the text/font
    pair returned from ``fit_team_driver_name``) must end left of its card's
    position column, i.e. before ``x_end - 72 - 8``.
    """
    teams_data = TeamsService()._load_from_json(2026)
    assert teams_data is not None and teams_data.teams

    drawn = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        drawn.append((self, xy, text, kwargs.get("font")))
        return original_text(self, xy, text, *args, **kwargs)

    fitted_names = []
    original_fit = renderer_teams.fit_team_driver_name

    def spy_fit(*args, **kwargs):
        result = original_fit(*args, **kwargs)
        fitted_names.append(result)
        return result

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)
    monkeypatch.setattr(renderer_teams, "fit_team_driver_name", spy_fit)

    renderer_cls(get_translator(lang_code)).render_teams_drivers(teams_data)

    drawn_strings = {str(text).strip() for _draw, _xy, text, _font in drawn}
    assert not drawn_strings & TEAM_BADGE_ABBREVIATIONS

    driver_texts = [
        (draw, xy, text, font)
        for draw, xy, text, font in drawn
        if any(text == name and font is name_font for name, name_font in fitted_names)
    ]
    assert len(driver_texts) == len(fitted_names) > 0

    for draw, xy, text, font in driver_texts:
        width = draw.im.size[0]
        x_end = width // 2 - 2 if xy[0] < width // 2 else width - 5
        right_edge = draw.textbbox(xy, text, font=font)[2]
        assert right_edge <= x_end - 72 - 8, (text, xy, right_edge)
