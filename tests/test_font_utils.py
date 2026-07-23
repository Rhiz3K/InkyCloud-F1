"""Direct coverage for font caches, fallbacks, and fitting helpers."""

from unittest.mock import MagicMock

import pytest

from app.services import font_utils


@pytest.fixture(autouse=True)
def reset_font_state():
    font_utils._missing_font_keys.clear()
    if hasattr(font_utils._thread_local, "fonts"):
        del font_utils._thread_local.fonts
    yield
    font_utils._missing_font_keys.clear()
    if hasattr(font_utils._thread_local, "fonts"):
        del font_utils._thread_local.fonts


def test_cached_truetype_clears_full_thread_cache(monkeypatch):
    cached = {(str(index), index, 0): object() for index in range(font_utils._FONT_CACHE_MAXSIZE)}
    font_utils._thread_local.fonts = cached
    loaded = object()
    monkeypatch.setattr(font_utils.ImageFont, "truetype", MagicMock(return_value=loaded))

    assert font_utils._cached_truetype("new.ttf", 12) is loaded
    assert list(font_utils._thread_local.fonts) == [("new.ttf", 12, 0)]
    font_utils.ImageFont.truetype.assert_called_once_with(
        "new.ttf",
        12,
        index=0,
        layout_engine=font_utils.ImageFont.Layout.BASIC,
    )


def test_optional_truetype_skips_known_missing_font():
    key = ("missing.ttf", 12, 0)
    font_utils._missing_font_keys.add(key)

    assert (
        font_utils.load_optional_truetype(
            key[0], key[1], label="optional", target_logger=MagicMock()
        )
        is None
    )


def test_optional_truetype_logs_only_first_failure(monkeypatch):
    target_logger = MagicMock()
    monkeypatch.setattr(
        font_utils, "_cached_truetype", MagicMock(side_effect=OSError("missing font"))
    )

    assert (
        font_utils.load_optional_truetype(
            "missing.ttf", 12, label="optional", target_logger=target_logger
        )
        is None
    )
    assert (
        font_utils.load_optional_truetype(
            "missing.ttf", 12, label="optional", target_logger=target_logger
        )
        is None
    )
    target_logger.warning.assert_called_once()


def test_optional_truetype_handles_concurrent_failure_record(monkeypatch):
    key = ("missing.ttf", 12, 0)
    target_logger = MagicMock()

    def fail_after_other_thread_records_key(*_args, **_kwargs):
        font_utils._missing_font_keys.add(key)
        raise OSError("missing font")

    monkeypatch.setattr(font_utils, "_cached_truetype", fail_after_other_thread_records_key)

    assert (
        font_utils.load_optional_truetype(
            key[0], key[1], label="optional", target_logger=target_logger
        )
        is None
    )
    target_logger.warning.assert_not_called()


def test_load_brand_font_uses_system_fallback_when_bundled_font_fails(tmp_path, monkeypatch):
    (tmp_path / "TitilliumWeb-Regular.ttf").touch()
    fallback = object()
    loader = MagicMock(side_effect=[OSError("broken bundle"), fallback])
    monkeypatch.setattr(font_utils, "FONTS_DIR", tmp_path)
    monkeypatch.setattr(font_utils, "_cached_truetype", loader)

    assert font_utils.load_brand_font(14) is fallback


def test_load_brand_font_uses_pillow_default_when_all_fonts_fail(tmp_path, monkeypatch):
    default = object()
    monkeypatch.setattr(font_utils, "FONTS_DIR", tmp_path)
    monkeypatch.setattr(
        font_utils, "_cached_truetype", MagicMock(side_effect=OSError("unavailable"))
    )
    monkeypatch.setattr(font_utils.ImageFont, "load_default", MagicMock(return_value=default))

    assert font_utils.load_brand_font(14, bold=True) is default


def test_load_ui_font_falls_back_when_cjk_font_is_unavailable(monkeypatch):
    fallback = object()
    monkeypatch.setattr(font_utils, "_load_cjk_font", MagicMock(return_value=None))
    monkeypatch.setattr(font_utils, "load_brand_font", MagicMock(return_value=fallback))

    assert font_utils.load_ui_font("ja", 14) is fallback


def test_fit_ui_font_returns_minimum_when_nothing_fits(monkeypatch):
    font = object()
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 100, 10)
    loader = MagicMock(return_value=font)
    monkeypatch.setattr(font_utils, "load_ui_font", loader)

    assert font_utils.fit_ui_font(draw, "en", "wide", max_width=10, base_size=3, min_size=1) is font
    assert loader.call_args_list[-1].args[:2] == ("en", 1)


def test_fit_ui_font_box_checks_width_and_height(monkeypatch):
    fonts = {size: object() for size in (3, 2, 1)}
    draw = MagicMock()
    draw.textbbox.side_effect = [(0, 0, 30, 5), (0, 0, 5, 30), (0, 0, 5, 5)]
    monkeypatch.setattr(font_utils, "load_ui_font", lambda _lang, size, **_kwargs: fonts[size])

    assert (
        font_utils.fit_ui_font_box(
            draw,
            "en",
            "text",
            max_width=10,
            max_height=10,
            base_size=3,
            min_size=1,
        )
        is fonts[1]
    )


def test_fit_ui_font_box_returns_minimum_when_nothing_fits(monkeypatch):
    fallback = object()
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 30, 30)
    monkeypatch.setattr(font_utils, "load_ui_font", MagicMock(return_value=fallback))

    assert (
        font_utils.fit_ui_font_box(
            draw,
            "en",
            "text",
            max_width=10,
            max_height=10,
            base_size=2,
            min_size=1,
        )
        is fallback
    )


def test_fit_brand_font_box_returns_minimum_when_nothing_fits(monkeypatch):
    fallback = object()
    draw = MagicMock()
    draw.textbbox.return_value = (0, 0, 30, 30)
    monkeypatch.setattr(font_utils, "load_brand_font", MagicMock(return_value=fallback))

    assert (
        font_utils.fit_brand_font_box(
            draw,
            "text",
            max_width=10,
            max_height=10,
            base_size=2,
            min_size=1,
        )
        is fallback
    )


def test_load_cjk_font_skips_missing_files_and_handles_load_failure(tmp_path, monkeypatch):
    missing = tmp_path / "missing.ttc"
    broken = tmp_path / "broken.ttc"
    broken.touch()
    monkeypatch.setattr(font_utils, "_CJK_FONT_FILES", {"regular": [missing, broken]})
    monkeypatch.setattr(
        font_utils, "_cached_truetype", MagicMock(side_effect=RuntimeError("bad face"))
    )

    assert font_utils._load_cjk_font("ja", 14) is None
