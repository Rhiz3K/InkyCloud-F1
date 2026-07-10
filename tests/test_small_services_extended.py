"""Edge-case coverage for small deterministic service helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services import i18n
from app.services.image_keys import get_configure_preview_filename, get_preview_filename
from app.services.track_assets import (
    discover_track_source_stems,
    resolve_track_source_path,
    strip_track_variant_suffix,
)


@pytest.fixture(autouse=True)
def clear_translation_cache():
    """Keep i18n fallback tests independent from process-global cached data."""
    i18n._translations_cache.clear()
    yield
    i18n._translations_cache.clear()


def _secondary_language() -> str:
    secondary = [lang for lang in i18n.VALID_LANGUAGES if lang != i18n.config.DEFAULT_LANG]
    assert secondary
    return secondary[0]


def test_translator_handles_missing_mapping_for_default(monkeypatch):
    monkeypatch.setattr(i18n, "_TRANSLATION_FILES", {})

    assert i18n.get_translator(i18n.config.DEFAULT_LANG) == {}


def test_translator_handles_missing_mapping_for_secondary_language(monkeypatch):
    default = i18n.config.DEFAULT_LANG
    secondary = _secondary_language()
    fallback = {"title": "fallback"}
    i18n._translations_cache[default] = fallback
    monkeypatch.setattr(i18n, "_TRANSLATION_FILES", {})

    assert i18n.get_translator(secondary) is fallback


def test_translator_falls_back_when_secondary_file_is_missing(tmp_path, monkeypatch):
    default = i18n.config.DEFAULT_LANG
    secondary = _secondary_language()
    fallback = {"title": "fallback"}
    i18n._translations_cache[default] = fallback
    monkeypatch.setattr(i18n, "_TRANSLATION_FILES", {secondary: tmp_path / "missing.json"})

    assert i18n.get_translator(secondary) is fallback


@pytest.mark.parametrize("language,expected", [("default", {}), ("secondary", {"ok": True})])
def test_translator_handles_unexpected_file_errors(language, expected, monkeypatch):
    default = i18n.config.DEFAULT_LANG
    secondary = _secondary_language()
    requested = default if language == "default" else secondary
    if requested != default:
        i18n._translations_cache[default] = expected

    translation_file = MagicMock(spec=Path)
    translation_file.exists.return_value = True
    monkeypatch.setattr(i18n, "_TRANSLATION_FILES", {requested: translation_file})

    with patch("builtins.open", side_effect=OSError("unreadable")):
        assert i18n.get_translator(requested) == expected


@pytest.mark.parametrize(
    ("function", "args", "message"),
    [
        (get_preview_filename, ("standings", "en"), "Unsupported screen type"),
        (get_configure_preview_filename, ("standings", "en"), "Unsupported screen type"),
    ],
)
def test_preview_filename_rejects_unknown_screen_types(function, args, message):
    with pytest.raises(ValueError, match=message):
        function(*args)


def test_teams_preview_rejects_weather_variant():
    with pytest.raises(ValueError, match="do not support weather"):
        get_configure_preview_filename("teams", "en", weather="temperature")


@pytest.mark.parametrize(
    ("stem", "expected"),
    [(" Monza_BW ", "monza"), ("MONZA", "monza")],
)
def test_strip_track_variant_suffix(stem, expected):
    assert strip_track_variant_suffix(stem) == expected


def test_resolve_track_source_path_without_variant(tmp_path):
    source = tmp_path / "monza.jpeg"
    source.write_bytes(b"track")

    assert resolve_track_source_path(tmp_path, ["spa", "monza"]) == source
    assert resolve_track_source_path(tmp_path, ["missing"]) is None


def test_discover_track_source_stems_filters_and_normalizes(tmp_path):
    (tmp_path / "monza_bw.PNG").write_bytes(b"track")
    (tmp_path / "_bw.png").write_bytes(b"empty canonical stem")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")
    (tmp_path / "directory.jpg").mkdir()

    assert discover_track_source_stems(tmp_path) == ["monza"]
