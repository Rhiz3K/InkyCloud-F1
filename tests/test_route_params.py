"""Shared route parameter rules and the BMP/preview artifact layer (issue #185, D4)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config import config
from app.routes import images, previews
from app.routes.params import (
    calendar_cache_key,
    canonical_weather_type,
    normalize_display,
    normalize_lang,
    validate_calendar_selection,
    validate_timezone,
    weather_file_key,
)
from app.services import bmp_artifacts
from app.services.track_catalog import DEFAULT_TRACK_OPTIONS
from app.state import get_bmp_cache
from tests.test_routes_images import _request


@pytest.mark.parametrize(
    ("weather", "weather_type", "default", "expected"),
    [
        (True, "race_day", "race_day", "race_day"),
        (True, "race", "race_day", "race_day"),
        (True, "current", "race_day", "current"),
        (True, "off", "race_day", "off"),
        (False, "race_day", "race_day", "off"),
        (False, "current", "race_day", "off"),
        (True, "../../etc", "race_day", "race_day"),
        (True, "../../etc", "off", "off"),
    ],
)
def test_canonical_weather_type(weather, weather_type, default, expected):
    assert canonical_weather_type(weather_type, weather=weather, default=default) == expected


def test_weather_file_key_matches_scheduler_filenames():
    assert [weather_file_key(w) for w in ("off", "current", "race_day")] == [
        "off",
        "current",
        "race",
    ]


def test_lang_display_and_selection_rules():
    assert normalize_lang("cs") == "cs"
    assert normalize_lang("xx") == config.DEFAULT_LANG
    assert normalize_display("bwry") == "bwry"
    assert normalize_display("crt") == "1bit"
    validate_calendar_selection(2026, "monza")
    validate_calendar_selection(None, None)
    with pytest.raises(HTTPException) as error:
        validate_calendar_selection(None, "monza")
    assert error.value.status_code == 400


def test_validate_timezone_normalizes_and_treats_empty_as_unset():
    assert validate_timezone(None) is None
    assert validate_timezone("") is None
    assert validate_timezone("Asia/Calcutta") == "Asia/Kolkata"
    with pytest.raises(HTTPException) as error:
        validate_timezone("Bad/Zone")
    assert error.value.status_code == 400


def test_calendar_cache_key_has_one_entry_per_canonical_variant():
    off = canonical_weather_type("race_day", weather=False)
    assert calendar_cache_key("en", None, None, None, None, off, "1bit") == calendar_cache_key(
        "en", None, None, None, None, canonical_weather_type("off"), "1bit"
    )
    assert calendar_cache_key(
        "en", None, None, None, None, canonical_weather_type("race"), "1bit"
    ) == calendar_cache_key("en", None, None, None, None, "race_day", "1bit")


async def _calendar_render_call(**query):
    """Run /calendar.bmp on a miss and return the singleflight key and render keywords."""
    race = {"season": 2026, "round": 1, "circuit": {"circuitId": "monza"}}
    f1 = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[]),
    )
    calls = []

    async def rendered(key, producer):
        calls.append((key, producer.keywords))
        return (b"bmp", '"etag"')

    get_bmp_cache().clear()
    params = {
        "lang": "en",
        "year": None,
        "race_round": None,
        "race_key": None,
        "tz": None,
        "weather": True,
        "weather_type": "race_day",
        "display": "1bit",
    } | query
    with (
        patch("app.routes.images.enforce_rate_limit"),
        patch("app.routes.images._get_pregenerated_calendar_path", return_value=None),
        patch("app.services.bmp_artifacts.singleflight", side_effect=rendered),
        patch("app.routes.images._record_calendar_api_call"),
    ):
        await images.get_calendar_bmp(_request(), f1_service=f1, **params)
    get_bmp_cache().clear()
    return calls[0]


@pytest.mark.asyncio
async def test_weather_false_renders_and_caches_the_off_variant():
    """A4: weather=false kept race_day for the renderer (compact countdown in an empty box)."""
    disabled_key, disabled = await _calendar_render_call(weather=False)
    off_key, off = await _calendar_render_call(weather_type="off")
    assert disabled["weather_type"] == off["weather_type"] == "off"
    assert disabled_key == off_key


@pytest.mark.asyncio
async def test_race_alias_shares_the_race_day_variant():
    race_key, race = await _calendar_render_call(weather_type="race")
    day_key, day = await _calendar_render_call(weather_type="race_day")
    assert race["weather_type"] == day["weather_type"] == "race_day"
    assert race_key == day_key


@pytest.mark.asyncio
async def test_configure_preview_reuses_the_calendar_bmp_cache_entry():
    """The configurator preview and /calendar.bmp build the same key for the same variant."""
    bmp_key, _ = await _calendar_render_call(weather_type="race")
    race = {"season": 2026, "round": 1, "circuit": {"circuitId": "monza"}}
    seen = []

    async def rendered(key, producer):
        seen.append((key, producer.keywords))
        return (b"bmp", '"etag"')

    get_bmp_cache().clear()
    with (
        patch.object(previews.F1Service, "get_next_race_from_static", return_value=race),
        patch.object(bmp_artifacts, "singleflight", side_effect=rendered),
        patch.object(previews, "bmp_to_png", return_value=b"png"),
        patch.object(previews, "run_render", new=AsyncMock(return_value=b"png")),
    ):
        await previews._render_configure_calendar(
            _request(), "en", "1bit", "race_day", DEFAULT_TRACK_OPTIONS
        )
    assert seen[0][0] == bmp_key
    assert seen[0][1]["race_data"] == race
