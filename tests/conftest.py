"""Pytest configuration and fixtures for tests."""

import asyncio
import atexit
import os
import shutil
import tempfile

import pytest

# Exercise the optional legacy statistics mode in existing feature tests.
# Dedicated minimal-data tests explicitly enable and verify the shipped default.
os.environ["MINIMAL_DATA_MODE"] = "false"
os.environ["AGGREGATE_STATS_ONLY"] = "false"

# Set up isolated test paths BEFORE any app imports. Always replace inherited values:
# self-hosted runners may carry production storage settings in their process environment.
_test_data_dir = tempfile.mkdtemp(prefix="f1_test_")
os.environ["DATABASE_PATH"] = os.path.join(_test_data_dir, "test_f1.db")
os.environ["IMAGES_PATH"] = os.path.join(_test_data_dir, "images")


@pytest.fixture(scope="session", autouse=True)
def close_test_database_connections():
    """Close connections opened by direct service tests without an ASGI lifespan."""
    yield

    from app.services.database import Database

    # aiosqlite workers are non-daemon threads. Release them before interpreter
    # shutdown: atexit callbacks run after Python waits for these threads.
    asyncio.run(Database.close_all())


def _cleanup_test_dir():
    """Clean up test data directory on exit."""
    try:
        if os.path.exists(_test_data_dir):
            shutil.rmtree(_test_data_dir)
    except Exception:
        # Silently ignore cleanup failures
        pass


# Register cleanup to run when tests finish
atexit.register(_cleanup_test_dir)


@pytest.fixture(autouse=True)
def _reset_image_render_guards():
    """Keep image caches and render slots from leaking between tests."""
    from app.state import get_bmp_cache, get_bmp_variant_cache
    from app.utils.render_limit import reset_render_limits_for_tests

    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    reset_render_limits_for_tests()
    yield
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    reset_render_limits_for_tests()


@pytest.fixture
def bundled_calendars(tmp_path, monkeypatch):
    """Point season detection at an empty calendar directory the test fills itself.

    Years in SEASON_START_DATES keep their maintained start dates, so tests of later seasons
    do not depend on which future calendars the repository happens to bundle.
    """
    from app.utils import f1_season

    monkeypatch.setattr(f1_season, "SEASONS_DIR", tmp_path)
    f1_season.bundled_season_start.cache_clear()
    yield tmp_path
    f1_season.bundled_season_start.cache_clear()


@pytest.fixture
def sepang_2017_payload():
    """Historical Jolpica race with dates but no session times."""
    return {
        "season": "2017",
        "round": "15",
        "raceName": "Malaysian Grand Prix",
        "Circuit": {
            "circuitId": "sepang",
            "circuitName": "Sepang International Circuit",
            "Location": {"locality": "Kuala Lumpur", "country": "Malaysia"},
        },
        "date": "2017-10-01",
        "time": "07:00:00Z",
        "FirstPractice": {"date": "2017-09-29"},
        "SecondPractice": {"date": "2017-09-29"},
        "ThirdPractice": {"date": "2017-09-30"},
        "Qualifying": {"date": "2017-09-30"},
    }
