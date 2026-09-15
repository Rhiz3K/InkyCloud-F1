"""Content-bound generation metadata shared by BMPs and their PNG previews."""

import json
import time
from pathlib import Path

from app.services.generation_freshness import PREGENERATED_MAX_AGE_SECONDS
from app.services.legal import CONTENT_POLICY_VERSION
from app.services.track_catalog import DEFAULT_TRACK_OPTIONS, TrackOptions
from app.utils.atomic_io import atomic_write_bytes
from app.utils.etag import strong_etag

CALENDAR_LAYOUT_VERSION = 2


def calendar_identity(
    race: dict | None, track_options: TrackOptions = DEFAULT_TRACK_OPTIONS
) -> str:
    """Identify a race independently of its translated or timezone-converted display."""
    if not race:
        return ""
    key = race.get("race_key") or ":".join(
        str(value or "")
        for value in (
            race.get("season"),
            race.get("round"),
            race.get("circuit", {}).get("circuitId"),
            race.get("date"),
        )
    )
    return f"calendar:v{CALENDAR_LAYOUT_VERSION}:{key}:{track_options.cache_key}"


def metadata_path(path: Path) -> Path:
    """Return the metadata sidecar for a generated image."""
    return path.with_suffix(path.suffix + ".meta.json")


def read_artifact_metadata(path: Path) -> dict | None:
    """Accept only fresh metadata bound to the current image and optional source BMP."""
    try:
        data = json.loads(metadata_path(path).read_text(encoding="utf-8"))
        stat = path.stat()
        if (
            not isinstance(data, dict)
            or data.get("schema") != 1
            or data.get("content_policy") != CONTENT_POLICY_VERSION
        ):
            return None
        if data["mtime_ns"] != stat.st_mtime_ns or data["size"] != stat.st_size:
            return None
        age = time.time() - float(data["generated_at"])
        if not 0 <= age <= PREGENERATED_MAX_AGE_SECONDS:
            return None
        if not isinstance(data["identity"], str) or not isinstance(data["etag"], str):
            return None
        source_name = data.get("source")
        if source_name is not None:
            if (
                path.suffix != ".png"
                or not isinstance(source_name, str)
                or Path(source_name).name != source_name
                or not source_name.endswith(".bmp")
            ):
                return None
            source = read_artifact_metadata(path.parent / source_name)
            if source is None or source["etag"] != data["source_etag"]:
                return None
            if source["identity"] != data["identity"]:
                return None
        return data
    except OSError, ValueError, TypeError, KeyError:
        return None


def artifact_matches(path: Path, identity: str) -> bool:
    """Return whether an artifact belongs to the requested current selection."""
    metadata = read_artifact_metadata(path)
    return bool(identity and metadata is not None and metadata["identity"] == identity)


async def write_artifact_metadata(
    path: Path, content: bytes, identity: str, *, source: tuple[Path, dict] | None = None
) -> None:
    """Publish metadata last, preserving source age when converting an existing BMP."""
    stat = path.stat()
    data = {
        "schema": 1,
        "content_policy": CONTENT_POLICY_VERSION,
        "identity": identity,
        "etag": strong_etag(content),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "generated_at": time.time(),
    }
    if source is not None:
        source_path, source_data = source
        data.update(
            generated_at=source_data["generated_at"],
            source=source_path.name,
            source_etag=source_data["etag"],
        )
    await atomic_write_bytes(metadata_path(path), json.dumps(data).encode("utf-8"))
