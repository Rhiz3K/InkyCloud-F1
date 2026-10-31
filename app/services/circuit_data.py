"""Paths and seeding for mutable runtime circuit data."""

from __future__ import annotations

import copy
import json
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from app.config import config
from app.utils.atomic_io import atomic_write_bytes_sync

BUNDLED_JOLPICA_PATH = Path(__file__).resolve().parents[1] / "assets" / "circuits_jolpica.json"
BUNDLED_WIKIPEDIA_PATH = Path(__file__).resolve().parents[1] / "assets" / "circuits_wikipedia.json"
WIKIPEDIA_FIELDS = ("circuit_length", "fastest_lap_time", "fastest_lap_driver", "fastest_lap_year")
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _parse_circuits_data(payload: str) -> dict:
    """Parse and cache an immutable circuit-data snapshot by its contents."""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Circuit data must be a JSON object")
    return data


def runtime_circuits_data_path() -> Path:
    """Store mutable circuit history beside the configured SQLite database."""
    return Path(config.DATABASE_PATH).expanduser().parent / "circuits_jolpica.json"


def get_circuits_data_path() -> Path:
    """Return the runtime copy when available, otherwise the bundled read-only seed."""
    runtime_path = runtime_circuits_data_path()
    return runtime_path if runtime_path.exists() else BUNDLED_JOLPICA_PATH


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


def load_jolpica_data(runtime_path: Path | None = None) -> dict:
    """Merge bundled metadata with only the newest runtime history, isolating callers."""
    bundled = _read_circuits_data(BUNDLED_JOLPICA_PATH)
    runtime = _read_circuits_data(runtime_path or get_circuits_data_path())
    merged = {
        key: copy.deepcopy(value) for key, value in bundled.items() if isinstance(value, dict)
    }
    for circuit_id, stored in runtime.items():
        if not isinstance(stored, dict):
            continue
        record = merged.setdefault(circuit_id, {})
        history = stored.get("historical")
        season = _history_season(history)
        if "historical" in stored and season >= _history_season(record.get("historical")):
            record["historical"] = copy.deepcopy(history)
    return merged


def load_wikipedia_data() -> dict:
    """Return only licensed Wikipedia facts and their attribution, as independent copies."""
    return {
        key: copy.deepcopy(
            {
                field: value
                for field, value in record.items()
                if (field in WIKIPEDIA_FIELDS and isinstance(value, str)) or field == "_provenance"
            }
        )
        for key, record in _read_circuits_data(BUNDLED_WIKIPEDIA_PATH).items()
        if isinstance(record, dict)
    }


def load_circuits_data(runtime_path: Path | None = None) -> dict:
    """Combine separately licensed snapshots only for rendering, preserving attribution."""
    merged = load_jolpica_data(runtime_path)
    wikipedia = load_wikipedia_data()
    for circuit_id, wiki in wikipedia.items():
        record = merged.setdefault(circuit_id, {})
        record.update({key: value for key, value in wiki.items() if key != "_provenance"})
    for circuit_id, record in merged.items():
        provenance = {}
        if "_provenance" in record:
            provenance["jolpica"] = record.pop("_provenance")
        if "_provenance" in wikipedia.get(circuit_id, {}):
            provenance["wikipedia"] = wikipedia[circuit_id]["_provenance"]
        if provenance:
            record["_provenance"] = provenance
    return merged


def wikipedia_circuit_credits() -> list[dict]:
    """List unique pinned Wikipedia revisions with links to their contributor history."""
    revisions = {}
    for record in load_wikipedia_data().values():
        provenance = record.get("_provenance")
        if not isinstance(provenance, dict) or not isinstance(provenance.get("fields"), dict):
            continue
        for field in provenance["fields"].values():
            if not isinstance(field, dict):
                continue
            title, revid, url = field.get("title"), field.get("revid"), field.get("source")
            if (
                not isinstance(title, str)
                or type(revid) is not int
                or not isinstance(url, str)
                or not url.startswith("https://en.wikipedia.org/w/index.php?title=")
            ):
                continue
            revisions[title, revid] = {
                "title": title,
                "revid": revid,
                "url": url,
                "history_url": (
                    "https://en.wikipedia.org/w/index.php?title="
                    f"{quote(title, safe='')}&action=history"
                ),
            }
    return sorted(revisions.values(), key=lambda credit: (credit["title"], credit["revid"]))


def ensure_runtime_circuits_data() -> Path:
    """Seed Jolpica data once, migrating only history from a legacy runtime snapshot."""
    runtime_path = runtime_circuits_data_path()
    if runtime_path.exists():
        return runtime_path
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = runtime_path.with_name("circuits_data.json")
    if legacy.exists():
        data = load_jolpica_data(legacy)
        atomic_write_bytes_sync(runtime_path, (json.dumps(data, indent=2) + "\n").encode())
        logger.info("Migrated legacy circuit history to circuits_jolpica.json")
    else:
        data = _read_circuits_data(BUNDLED_JOLPICA_PATH)
        atomic_write_bytes_sync(runtime_path, (json.dumps(data, indent=2) + "\n").encode())
    return runtime_path
