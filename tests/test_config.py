"""Test configuration and i18n services."""

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import app.config as config_module
import app.services.i18n as i18n_module
from app.services.i18n import get_translator
from app.web.templates import LANGUAGE_LABELS, OG_LOCALES


def _nested_key_paths(data: object, prefix: str = "") -> set[str]:
    """Collect dot-separated key paths for nested translation structures."""
    if not isinstance(data, dict):
        return {prefix} if prefix else set()

    paths: set[str] = set()
    for key, value in data.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths.add(child_prefix)
            paths.update(_nested_key_paths(value, child_prefix))
        else:
            paths.add(child_prefix)
    return paths


def test_config_defaults():
    """Test default configuration values."""
    config_module._reset_config_cache_for_tests()
    config = config_module.get_config()

    assert config.DISPLAY_WIDTH == 800
    assert config.DISPLAY_HEIGHT == 480
    assert config.DEFAULT_LANG in config_module.LANGUAGE_CODES
    assert config.STATS_RATE_LIMIT_PER_MINUTE == 60
    assert config.STATS_RETENTION_DAYS == 400


def test_default_stats_retention_covers_longest_dashboard_range():
    """The shipped retention must preserve every selectable dashboard range."""
    default_retention = config_module.Config.model_fields["STATS_RETENTION_DAYS"].default

    assert default_retention == 0 or default_retention >= 365


@pytest.mark.parametrize("field_name", ["DATABASE_PATH", "IMAGES_PATH"])
def test_config_rejects_empty_storage_paths(field_name):
    with pytest.raises(ValidationError):
        config_module.Config(_env_file=None, **{field_name: "   "})


def test_config_treats_explicitly_empty_admin_token_as_unset():
    config = config_module.Config(_env_file=None, ADMIN_API_TOKEN="")

    assert config.ADMIN_API_TOKEN is None


@pytest.mark.parametrize("env_file", [".env.example", ".env.local.example"])
def test_shipped_example_env_files_boot(env_file, monkeypatch):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    monkeypatch.delenv("STATS_RETENTION_DAYS", raising=False)

    config = config_module.Config(_env_file=env_file)

    assert config.ADMIN_API_TOKEN is None
    assert config.STATS_RETENTION_DAYS == 400


@pytest.mark.parametrize(
    ("field_name", "value", "expected"),
    [
        ("DISPLAY_WIDTH", "801", 800),
        ("DISPLAY_HEIGHT", "481", 480),
        ("DISPLAY_WIDTH", "not-a-number", 800),
        ("DISPLAY_HEIGHT", "not-a-number", 480),
    ],
)
def test_config_ignores_display_dimension_overrides(field_name, value, expected):
    config = config_module.Config(_env_file=None, **{field_name: value})

    assert getattr(config, field_name) == expected


def test_config_invalid_env_falls_back(monkeypatch):
    """Invalid environment variables fall back to safe defaults without crashing."""

    monkeypatch.setenv("APP_PORT", "-1")
    monkeypatch.setenv("REQUEST_TIMEOUT", "0")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Not/AZone")
    monkeypatch.setenv("SITE_URL", "not-a-url")
    monkeypatch.setenv("UMAMI_API_URL", "not-a-url")
    monkeypatch.setenv("GITHUB_API_BASE_URL", "not-a-url")
    monkeypatch.setenv("OPEN_METEO_URL", "still-not-a-url")
    monkeypatch.setenv("OPEN_METEO_ARCHIVE_URL", "bad-archive-url")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "2")
    monkeypatch.setenv("DEFAULT_LANG", "xx")
    monkeypatch.setenv("STATS_RETENTION_DAYS", "-5")

    config_module._reset_config_cache_for_tests()
    importlib.reload(config_module)
    config = config_module.get_config()

    assert config.APP_PORT == 8000
    assert config.REQUEST_TIMEOUT == 10
    assert config.DEFAULT_TIMEZONE == "Europe/Prague"
    assert str(config.SITE_URL) == "http://localhost:8000"
    assert str(config.UMAMI_API_URL) == "https://analytics.example.com/api/send"
    assert str(config.GITHUB_API_BASE_URL) == "https://api.github.com"
    assert str(config.OPEN_METEO_URL) == "https://api.open-meteo.com/v1/forecast"
    assert str(config.OPEN_METEO_ARCHIVE_URL) == "https://archive-api.open-meteo.com/v1/archive"
    assert config.SENTRY_TRACES_SAMPLE_RATE == 0.1
    assert config.DEFAULT_LANG == "en"
    assert config.STATS_RETENTION_DAYS == 400


def test_translator_english():
    """Test English translations."""
    translator = get_translator("en")
    assert "next_race" in translator
    assert translator["next_race"] == "Next Race"
    assert translator["schedule"] == "Schedule"


def test_translator_czech():
    """Test Czech translations."""
    translator = get_translator("cs")
    assert "next_race" in translator
    assert translator["next_race"] == "Příští závod"
    assert translator["schedule"] == "Rozvrh"


def test_translator_fallback():
    """Test fallback for unknown language."""
    translator = get_translator("unknown")
    # Should fall back to default language
    assert "next_race" in translator


def test_corrupted_non_default_translation_falls_back_to_default(tmp_path, monkeypatch):
    broken = tmp_path / "cs.json"
    broken.write_text("{broken", encoding="utf-8")
    monkeypatch.setitem(i18n_module._TRANSLATION_FILES, "cs", broken)
    i18n_module._translations_cache.clear()

    translator = i18n_module.get_translator("cs")

    assert translator["next_race"] == "Next Race"
    assert i18n_module.get_translator("cs") is translator
    i18n_module._translations_cache.clear()


def test_all_translation_files_match_english_keys():
    """All supported locales should ship a translation file with the full key set."""
    translations_dir = Path(__file__).resolve().parent.parent / "translations"
    reference = json.loads((translations_dir / "en.json").read_text(encoding="utf-8"))
    reference_paths = _nested_key_paths(reference)

    for lang in config_module.LANGUAGE_CODES:
        data = json.loads((translations_dir / f"{lang}.json").read_text(encoding="utf-8"))
        assert _nested_key_paths(data) == reference_paths


def test_language_metadata_matches_supported_locales():
    """Template locale metadata should stay aligned with the canonical language list."""
    expected = set(config_module.LANGUAGE_CODES)
    assert set(LANGUAGE_LABELS) == expected
    assert set(OG_LOCALES) == expected


def test_config_reset_updates_module_singleton(monkeypatch):
    """Resetting cached config should refresh the module-level singleton too."""
    monkeypatch.setenv("APP_PORT", "9001")

    config_module._reset_config_cache_for_tests()

    assert config_module.config is config_module.get_config()
    assert config_module.config.APP_PORT == 9001


def test_port_alias_configures_app_port(monkeypatch):
    monkeypatch.delenv("APP_PORT", raising=False)
    monkeypatch.setenv("PORT", "9012")

    config = config_module.Config(_env_file=None)

    assert config.APP_PORT == 9012


def test_s3_secrets_are_masked_in_repr(monkeypatch):
    """S3 credentials should be stored as masked secrets in Config."""
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret-key")

    config_module._reset_config_cache_for_tests()
    config = config_module.get_config()

    assert config.S3_ACCESS_KEY_ID is not None
    assert config.S3_SECRET_ACCESS_KEY is not None
    assert str(config.S3_ACCESS_KEY_ID) == "**********"
    assert str(config.S3_SECRET_ACCESS_KEY) == "**********"
    assert config.S3_ACCESS_KEY_ID.get_secret_value() == "test-access-key"
    assert config.S3_SECRET_ACCESS_KEY.get_secret_value() == "test-secret-key"


def test_translator_missing_default_file_returns_empty_dict():
    """Missing default translations should stop at an empty fallback instead of recursing."""
    i18n_module._translations_cache.clear()

    with (
        patch.dict(i18n_module._TRANSLATION_FILES, {"en": Path("/tmp/definitely-missing-en.json")}),
        patch.object(i18n_module.config, "DEFAULT_LANG", "en"),
    ):
        assert i18n_module.get_translator("en") == {}
        assert i18n_module.get_translator("en") == {}

    i18n_module._translations_cache.clear()
