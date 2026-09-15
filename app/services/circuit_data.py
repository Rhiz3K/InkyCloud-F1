"""Paths and seeding for mutable runtime circuit data."""

from __future__ import annotations

import copy
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import config
from app.utils.atomic_io import atomic_write_bytes_sync

BUNDLED_CIRCUITS_DATA_PATH = Path(__file__).resolve().parents[1] / "assets" / "circuits_data.json"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _parse_circuits_data(payload: str) -> dict:
    """Parse and cache an immutable circuit-data snapshot by its contents."""
    data = json.loads(payload)
    return data if isinstance(data, dict) else {}


def runtime_circuits_data_path() -> Path:
    """Store mutable circuit history beside the configured SQLite database."""
    return Path(config.DATABASE_PATH).expanduser().parent / "circuits_data.json"


def get_circuits_data_path() -> Path:
    """Return the runtime copy when available, otherwise the bundled read-only seed."""
    runtime_path = runtime_circuits_data_path()
    return runtime_path if runtime_path.exists() else BUNDLED_CIRCUITS_DATA_PATH


def _read_circuits_data(path: Path) -> dict:
    """Read a snapshot, retaining bundled fallback when runtime JSON is damaged."""
    try:
        return _parse_circuits_data(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Failed to load circuit data from %s: %s", path, exc)
        return {}


def _history_season(history: object) -> int:
    """Rank history with a valid season above missing or malformed history."""
    if not isinstance(history, dict):
        return -1
    season = history.get("season")
    if not isinstance(season, (str, int)):
        return -1
    try:
        return max(-1, int(season))
    except ValueError:
        return -1


def load_circuits_data(runtime_path: Path | None = None) -> dict:
    """Merge current release metadata with the newest persistent circuit history.

    Runtime-only circuits remain available for archived races. For known circuits the
    release owns metadata; equally recent history prefers the runtime refresh. Copies
    isolate callers from the JSON parse cache and allow the refresher to update its snapshot.
    """
    bundled = _read_circuits_data(BUNDLED_CIRCUITS_DATA_PATH)
    runtime = _read_circuits_data(runtime_path or get_circuits_data_path())
    merged = copy.deepcopy(runtime)
    for circuit_id, metadata in bundled.items():
        if not isinstance(metadata, dict):
            continue
        stored = runtime.get(circuit_id)
        stored = stored if isinstance(stored, dict) else {}
        merged[circuit_id] = copy.deepcopy({**stored, **metadata})
        stored_history = stored.get("historical")
        stored_season = _history_season(stored_history)
        if stored_season >= 0 and stored_season >= _history_season(metadata.get("historical")):
            merged[circuit_id]["historical"] = copy.deepcopy(stored_history)
    return merged


def ensure_runtime_circuits_data() -> Path:
    """Seed the persistent runtime copy from bundled data exactly when it is missing."""
    runtime_path = runtime_circuits_data_path()
    if runtime_path.exists():
        return runtime_path
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes_sync(runtime_path, BUNDLED_CIRCUITS_DATA_PATH.read_bytes())
    return runtime_path
