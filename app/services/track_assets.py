"""Helpers for resolving display-specific track asset variants."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

TRACK_SOURCE_EXTENSIONS = (".png", ".jpg", ".jpeg")
TRACK_VARIANT_SUFFIXES = ("bw", "bwr", "bwry", "spectra6")
TRACK_BUNDLE_VARIANTS = ("generic", *TRACK_VARIANT_SUFFIXES)
TRACK_BUNDLE_SCHEMA_VERSION = 1

_CIRCUIT_ID_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class TrackBundleError(ValueError):
    """Raised when a manifest-managed track bundle is incomplete or inconsistent."""


def track_bundle_paths(tracks_dir: Path, circuit_id: str) -> dict[str, Path]:
    """Return the five deterministic PNG paths belonging to one managed track bundle."""
    if not _CIRCUIT_ID_RE.fullmatch(circuit_id):
        raise TrackBundleError(f"Invalid track bundle circuit id: {circuit_id!r}")
    return {
        variant: tracks_dir / f"{circuit_id}{'' if variant == 'generic' else f'_{variant}'}.png"
        for variant in TRACK_BUNDLE_VARIANTS
    }


def track_bundle_marker_path(tracks_dir: Path, circuit_id: str) -> Path:
    """Return the commit-marker path for one managed track artwork bundle."""
    if not _CIRCUIT_ID_RE.fullmatch(circuit_id):
        raise TrackBundleError(f"Invalid track bundle circuit id: {circuit_id!r}")
    return tracks_dir / f"{circuit_id}.bundle.json"


def encode_track_bundle_marker(
    circuit_id: str,
    source_sha256: str,
    png_bytes: Mapping[str, bytes],
) -> bytes:
    """Encode the final commit marker after all five PNG payloads have been staged."""
    expected_keys = set(TRACK_BUNDLE_VARIANTS)
    if set(png_bytes) != expected_keys:
        raise TrackBundleError(
            "Track bundle PNG payloads must contain exactly: " + ", ".join(TRACK_BUNDLE_VARIANTS)
        )
    source_digest = _require_sha256(source_sha256, "track bundle source_sha256")
    payload = {
        "schema_version": TRACK_BUNDLE_SCHEMA_VERSION,
        "circuit_id": circuit_id,
        "source_sha256": source_digest,
        "files": {
            variant: sha256(png_bytes[variant]).hexdigest() for variant in TRACK_BUNDLE_VARIANTS
        },
    }
    return (json.dumps(payload, indent=2) + "\n").encode()


def validate_track_bundle(
    tracks_dir: Path,
    circuit_id: str,
    expected_source_sha256: str,
) -> dict[str, Path]:
    """Validate a managed bundle marker and every PNG digest before consumption."""
    marker_path = track_bundle_marker_path(tracks_dir, circuit_id)
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrackBundleError(f"Track bundle marker not found: {marker_path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrackBundleError(f"Cannot read track bundle marker {marker_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise TrackBundleError(f"Track bundle marker must be a JSON object: {marker_path}")
    expected_root_keys = {"schema_version", "circuit_id", "source_sha256", "files"}
    if set(payload) != expected_root_keys:
        raise TrackBundleError(f"Track bundle marker has unexpected fields: {marker_path}")
    if (
        isinstance(payload["schema_version"], bool)
        or payload["schema_version"] != TRACK_BUNDLE_SCHEMA_VERSION
    ):
        raise TrackBundleError(
            f"Track bundle marker schema_version must be {TRACK_BUNDLE_SCHEMA_VERSION}: "
            f"{marker_path}"
        )
    if payload["circuit_id"] != circuit_id:
        raise TrackBundleError(
            f"Track bundle marker circuit_id does not match {circuit_id!r}: {marker_path}"
        )
    expected_source_digest = _require_sha256(
        expected_source_sha256, "expected track source SHA-256"
    )
    marker_source_digest = _require_sha256(payload["source_sha256"], "track bundle source_sha256")
    if marker_source_digest != expected_source_digest:
        raise TrackBundleError(
            f"Track bundle source SHA-256 mismatch for {circuit_id!r}: expected "
            f"{expected_source_digest}, got {marker_source_digest}"
        )

    file_hashes = payload["files"]
    if not isinstance(file_hashes, dict) or set(file_hashes) != set(TRACK_BUNDLE_VARIANTS):
        raise TrackBundleError(
            "Track bundle marker files must contain exactly: " + ", ".join(TRACK_BUNDLE_VARIANTS)
        )

    paths = track_bundle_paths(tracks_dir, circuit_id)
    for variant in TRACK_BUNDLE_VARIANTS:
        expected_digest = _require_sha256(file_hashes[variant], f"track bundle {variant} SHA-256")
        try:
            actual_digest = sha256(paths[variant].read_bytes()).hexdigest()
        except OSError as exc:
            raise TrackBundleError(
                f"Cannot read track bundle file {paths[variant]}: {exc}"
            ) from exc
        if actual_digest != expected_digest:
            raise TrackBundleError(
                f"Track bundle hash mismatch for {paths[variant]}: expected "
                f"{expected_digest}, got {actual_digest}"
            )
    return paths


def _require_sha256(value: Any, label: str) -> str:
    """Validate a lower-case SHA-256 digest used by a bundle marker."""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise TrackBundleError(f"{label} must be exactly 64 lower-case hexadecimal characters")
    return value


def build_track_stem_candidates(*stems: object) -> list[str]:
    """Return unique, non-empty track stem candidates preserving order."""
    candidates: list[str] = []
    for stem in stems:
        normalized = str(stem or "").strip().lower().replace(" ", "_")
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


def strip_track_variant_suffix(stem: str) -> str:
    """Strip a known display suffix from a track source stem."""
    normalized = stem.strip().lower()
    for suffix in TRACK_VARIANT_SUFFIXES:
        token = f"_{suffix}"
        if normalized.endswith(token):
            return normalized[: -len(token)]
    return normalized


def resolve_track_source_path(
    tracks_dir: Path, stems: list[str], *, variant_suffix: str | None = None
) -> Path | None:
    """Resolve a source track asset, preferring a display-specific variant first."""
    ordered_stems = [*stems]
    if variant_suffix:
        ordered_stems = [f"{stem}_{variant_suffix}" for stem in stems] + ordered_stems

    for stem in ordered_stems:
        for extension in TRACK_SOURCE_EXTENSIONS:
            candidate = tracks_dir / f"{stem}{extension}"
            if candidate.exists():
                return candidate

    return None


def discover_track_source_stems(tracks_dir: Path) -> list[str]:
    """Discover canonical track source stems from the tracks asset directory."""
    stems = {
        strip_track_variant_suffix(path.stem)
        for path in tracks_dir.iterdir()
        if path.is_file() and path.suffix.lower() in TRACK_SOURCE_EXTENSIONS
    }
    return sorted(stem for stem in stems if stem)
