"""Tests for scheduler image variant helpers."""

from app.services.scheduler import _get_image_key


def test_get_image_key_uses_bwr_suffix():
    assert _get_image_key("en", display="bwr") == "calendar_en_bwr"


def test_get_image_key_uses_bwr_suffix_with_timezone_and_weather():
    assert (
        _get_image_key("cs", tz="America/New_York", display="bwr", weather="race")
        == "calendar_cs_America_New_York_bwr_weather_race"
    )
