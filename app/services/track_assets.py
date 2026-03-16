"""Helpers for resolving display-specific track asset variants."""

from __future__ import annotations

from pathlib import Path

TRACK_SOURCE_EXTENSIONS = (".png", ".jpg", ".jpeg")
TRACK_VARIANT_SUFFIXES = ("bw", "bwr", "bwry", "spectra6")


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
