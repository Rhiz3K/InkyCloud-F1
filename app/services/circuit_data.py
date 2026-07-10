"""Paths and seeding for mutable runtime circuit data."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import config
from app.utils.atomic_io import atomic_write_bytes_sync

BUNDLED_CIRCUITS_DATA_PATH = Path(__file__).resolve().parents[1] / "assets" / "circuits_data.json"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _load_circuits_data_file(path_value: str, mtime_ns: int, size: int, inode: int) -> dict:
    """Load one immutable file version identified by stable filesystem metadata."""
    del mtime_ns, size, inode
    with open(path_value, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def runtime_circuits_data_path() -> Path:
    """Store mutable circuit history beside the configured SQLite database."""
    return Path(config.DATABASE_PATH).expanduser().parent / "circuits_data.json"


def get_circuits_data_path() -> Path:
    """Return the runtime copy when available, otherwise the bundled read-only seed."""
    runtime_path = runtime_circuits_data_path()
    return runtime_path if runtime_path.exists() else BUNDLED_CIRCUITS_DATA_PATH


def load_circuits_data() -> dict:
    """Read the current runtime-or-bundled circuit data without stale process state."""
    path = get_circuits_data_path()
    try:
        stat = path.stat()
        return _load_circuits_data_file(str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Failed to load circuit data from %s: %s", path, exc)
        return {}


def ensure_runtime_circuits_data() -> Path:
    """Seed the persistent runtime copy from bundled data exactly when it is missing."""
    runtime_path = runtime_circuits_data_path()
    if runtime_path.exists():
        return runtime_path
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes_sync(runtime_path, BUNDLED_CIRCUITS_DATA_PATH.read_bytes())
    return runtime_path
