"""Pytest configuration and fixtures for tests."""

import atexit
import os
import shutil
import tempfile

# Set up isolated test paths BEFORE any app imports. Always replace inherited values:
# self-hosted runners may carry production storage settings in their process environment.
_test_data_dir = tempfile.mkdtemp(prefix="f1_test_")
os.environ["DATABASE_PATH"] = os.path.join(_test_data_dir, "test_f1.db")
os.environ["IMAGES_PATH"] = os.path.join(_test_data_dir, "images")


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
