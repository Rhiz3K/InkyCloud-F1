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
