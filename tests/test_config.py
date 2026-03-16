"""Test configuration and i18n services."""

import importlib

import app.config as config_module
from app.services.i18n import get_translator


def test_config_defaults():
    """Test default configuration values."""
    config_module._reset_config_cache_for_tests()
    config = config_module.get_config()

    assert config.DISPLAY_WIDTH == 800
    assert config.DISPLAY_HEIGHT == 480
    assert config.DEFAULT_LANG in ["en", "cs"]


def test_config_invalid_env_falls_back(monkeypatch):
    """Invalid environment variables fall back to safe defaults without crashing."""

    monkeypatch.setenv("APP_PORT", "-1")
    monkeypatch.setenv("REQUEST_TIMEOUT", "0")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Not/AZone")
    monkeypatch.setenv("UMAMI_API_URL", "not-a-url")
    monkeypatch.setenv("OPEN_METEO_URL", "still-not-a-url")
    monkeypatch.setenv("OPEN_METEO_ARCHIVE_URL", "bad-archive-url")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "2")

    config_module._reset_config_cache_for_tests()
    importlib.reload(config_module)
    config = config_module.get_config()

    assert config.APP_PORT == 8000
    assert config.REQUEST_TIMEOUT == 10
    assert config.DEFAULT_TIMEZONE == "Europe/Prague"
    assert str(config.UMAMI_API_URL) == "https://analytics.example.com/api/send"
    assert str(config.OPEN_METEO_URL) == "https://api.open-meteo.com/v1/forecast"
    assert str(config.OPEN_METEO_ARCHIVE_URL) == "https://archive-api.open-meteo.com/v1/archive"
    assert config.SENTRY_TRACES_SAMPLE_RATE == 0.1


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
