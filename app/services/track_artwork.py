"""Validated, local-only import pipeline for official F1 track artwork."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageDraw

from app.services.bwr_renderer import BwrColors
from app.services.bwry_renderer import BwryColors
from app.services.spectra6_renderer import Spectra6Colors
from app.services.track_assets import (
    TRACK_BUNDLE_VARIANTS,
    encode_track_bundle_marker,
    track_bundle_marker_path,
    track_bundle_paths,
)
from app.utils.atomic_io import atomic_write_bytes_sync

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACK_MANIFEST = PROJECT_ROOT / "artwork" / "tracks" / "sources.json"
DEFAULT_TRACK_OUTPUT_DIR = PROJECT_ROOT / "artwork" / "tracks"
TRACK_SOURCE_MAX_HEIGHT = 704
TRACK_SOURCE_PROFILES = ("legacy", "modern")

_CIRCUIT_ID_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*\Z")
_RACE_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class TrackArtworkError(ValueError):
    """Raised when track provenance or source artwork validation fails."""


@dataclass(frozen=True, slots=True)
class SectorBoundary:
    """Normalized location and orientation for a sector separator."""

    at: tuple[float, float]
    normal_degrees: float


@dataclass(frozen=True, slots=True)
class TrackSourceEntry:
    """Validated provenance and transformation metadata for one circuit."""

    circuit_id: str
    season: int
    race_slug: str
    source_page: str
    source_url: str
    source_sha256: str
    source_dimensions: tuple[int, int]
    source_profile: str
    sector_boundaries: tuple[SectorBoundary, ...]
    rights_review_required: bool


@dataclass(frozen=True, slots=True)
class TrackImportResult:
    """Summary of a successfully imported source and its derived variants."""

    circuit_id: str
    source_sha256: str
    source_dimensions: tuple[int, int]
    output_dimensions: tuple[int, int]
    output_paths: tuple[Path, ...]
    sector_pixels: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _HueRule:
    """HSV thresholds identifying one source-profile sector color."""

    hue: int
    tolerance: int
    minimum_saturation: int
    minimum_value: int


# Pillow represents hue on 0..255. These references correspond to the sector colors used by
# official Formula 1 circuit artwork, with enough tolerance to include antialiased pixels.
_SOURCE_SECTOR_RULES: dict[str, tuple[_HueRule, _HueRule, _HueRule]] = {
    "legacy": (
        _HueRule(213, 10, 80, 50),  # S1 #ff00ff
        _HueRule(35, 9, 80, 50),  # S2 #ffd300
        _HueRule(136, 12, 70, 50),  # S3 #00b2e3
    ),
    "modern": (
        _HueRule(235, 12, 70, 50),  # S1 #e51073 / #e61073
        _HueRule(35, 9, 80, 50),  # S2 #ffd100
        _HueRule(146, 12, 60, 50),  # S3 #4098d9
    ),
}

_VARIANT_SECTOR_COLORS: dict[str, tuple[tuple[int, int, int], ...]] = {
    "bw": (BwrColors.WHITE, BwrColors.WHITE, BwrColors.WHITE),
    "bwr": (BwrColors.WHITE, BwrColors.WHITE, BwrColors.WHITE),
    "bwry": (BwryColors.RED, BwryColors.WHITE, BwryColors.YELLOW),
    "spectra6": (Spectra6Colors.RED, Spectra6Colors.BLUE, Spectra6Colors.YELLOW),
}


def load_track_source_manifest(path: Path = DEFAULT_TRACK_MANIFEST) -> dict[str, TrackSourceEntry]:
    """Load and strictly validate the versioned track-source provenance manifest."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TrackArtworkError(f"Track source manifest not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrackArtworkError(f"Cannot read track source manifest {path}: {exc}") from exc

    root = _require_mapping(payload, "manifest")
    if root.get("schema_version") != 1:
        raise TrackArtworkError("Track source manifest schema_version must be 1")
    tracks = _require_mapping(root.get("tracks"), "manifest.tracks")
    if not tracks:
        raise TrackArtworkError("Track source manifest must contain at least one track")

    result: dict[str, TrackSourceEntry] = {}
    for circuit_id, raw_entry in tracks.items():
        if not isinstance(circuit_id, str) or not _CIRCUIT_ID_RE.fullmatch(circuit_id):
            raise TrackArtworkError(f"Invalid circuit id in manifest: {circuit_id!r}")
        result[circuit_id] = _parse_manifest_entry(circuit_id, raw_entry)
    return result


def import_track_artwork(
    source_path: Path,
    circuit_id: str,
    *,
    manifest_path: Path = DEFAULT_TRACK_MANIFEST,
    output_dir: Path = DEFAULT_TRACK_OUTPUT_DIR,
    expected_sha256: str | None = None,
) -> TrackImportResult:
    """Validate one local PNG and publish a hash-committed palette bundle."""
    entries = load_track_source_manifest(manifest_path)
    normalized_id = circuit_id.strip().lower()
    if normalized_id not in entries:
        raise TrackArtworkError(f"Circuit {normalized_id!r} is not present in {manifest_path}")
    entry = entries[normalized_id]

    explicit_sha = _validate_sha256(expected_sha256, "expected SHA-256")
    if explicit_sha is not None and explicit_sha != entry.source_sha256:
        raise TrackArtworkError(
            "Expected SHA-256 does not match the selected manifest entry "
            f"({explicit_sha} != {entry.source_sha256})"
        )

    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise TrackArtworkError(f"Cannot read track source {source_path}: {exc}") from exc
    actual_sha = sha256(source_bytes).hexdigest()
    if actual_sha != entry.source_sha256:
        raise TrackArtworkError(
            f"SHA-256 mismatch for {source_path}: expected {entry.source_sha256}, got {actual_sha}"
        )

    source = _decode_source_png(source_bytes, source_path, entry.source_dimensions)
    sector_masks = _detect_sector_masks(source, entry.source_profile)
    normalized = _fit_source_height(source)

    images: dict[str, Image.Image] = {"generic": normalized}
    for suffix, colors in _VARIANT_SECTOR_COLORS.items():
        variant_image = _recolor_sectors(source, sector_masks, colors)
        if suffix in {"bw", "bwr"}:
            separator_color = BwrColors.WHITE if suffix == "bw" else BwrColors.RED
            _draw_sector_separators(variant_image, entry.sector_boundaries, separator_color)
        images[suffix] = _fit_source_height(variant_image)

    # Stage every byte and its final marker before publishing any part of the bundle.
    png_bytes = {variant: _encode_png(images[variant]) for variant in TRACK_BUNDLE_VARIANTS}
    marker_bytes = encode_track_bundle_marker(normalized_id, actual_sha, png_bytes)
    output_paths_by_variant = track_bundle_paths(output_dir, normalized_id)
    marker_path = track_bundle_marker_path(output_dir, normalized_id)

    output_dir.mkdir(parents=True, exist_ok=True)
    # A missing marker makes interrupted publication fail closed for every consumer.
    marker_path.unlink(missing_ok=True)
    for variant in TRACK_BUNDLE_VARIANTS:
        atomic_write_bytes_sync(output_paths_by_variant[variant], png_bytes[variant])
    atomic_write_bytes_sync(marker_path, marker_bytes)

    output_paths = tuple(output_paths_by_variant[variant] for variant in TRACK_BUNDLE_VARIANTS)
    sector_pixels = (
        _mask_pixel_count(sector_masks[0]),
        _mask_pixel_count(sector_masks[1]),
        _mask_pixel_count(sector_masks[2]),
    )

    return TrackImportResult(
        circuit_id=normalized_id,
        source_sha256=actual_sha,
        source_dimensions=entry.source_dimensions,
        output_dimensions=normalized.size,
        output_paths=output_paths,
        sector_pixels=sector_pixels,
    )


def _parse_manifest_entry(circuit_id: str, raw_entry: Any) -> TrackSourceEntry:
    """Validate and materialize one manifest track entry."""
    entry = _require_mapping(raw_entry, f"tracks.{circuit_id}")
    season = entry.get("season")
    if isinstance(season, bool) or not isinstance(season, int) or season < 1950:
        raise TrackArtworkError(f"tracks.{circuit_id}.season must be an F1 season year")
    race_slug = entry.get("race_slug")
    if not isinstance(race_slug, str) or not _RACE_SLUG_RE.fullmatch(race_slug):
        raise TrackArtworkError(f"tracks.{circuit_id}.race_slug is invalid")
    source_page = _validate_formula1_url(entry.get("source_page"), "source_page", circuit_id)
    source_url = _validate_formula1_url(entry.get("source_url"), "source_url", circuit_id)
    source_sha = _validate_sha256(entry.get("source_sha256"), "manifest source_sha256")
    if source_sha is None:  # pragma: no cover - narrowed by the validator
        raise TrackArtworkError(f"tracks.{circuit_id}.source_sha256 is required")
    dimensions = _parse_dimensions(entry.get("source_dimensions"), circuit_id)
    profile = entry.get("source_profile")
    if profile not in TRACK_SOURCE_PROFILES:
        choices = ", ".join(TRACK_SOURCE_PROFILES)
        raise TrackArtworkError(f"tracks.{circuit_id}.source_profile must be one of: {choices}")
    boundaries = _parse_boundaries(entry.get("sector_boundaries"), circuit_id)
    rights_review = entry.get("rights_review_required", True)
    if not isinstance(rights_review, bool):
        raise TrackArtworkError(f"tracks.{circuit_id}.rights_review_required must be boolean")
    return TrackSourceEntry(
        circuit_id=circuit_id,
        season=season,
        race_slug=race_slug,
        source_page=source_page,
        source_url=source_url,
        source_sha256=source_sha,
        source_dimensions=dimensions,
        source_profile=profile,
        sector_boundaries=boundaries,
        rights_review_required=rights_review,
    )


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    """Require a JSON object while keeping validation errors actionable."""
    if not isinstance(value, dict):
        raise TrackArtworkError(f"{label} must be a JSON object")
    return value


def _validate_sha256(value: Any, label: str) -> str | None:
    """Normalize and validate a SHA-256 hex digest."""
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value.lower()):
        raise TrackArtworkError(f"{label} must be exactly 64 hexadecimal characters")
    return value.lower()


def _validate_formula1_url(value: Any, field: str, circuit_id: str) -> str:
    """Require provenance URLs to use HTTPS on an official Formula 1 host."""
    if not isinstance(value, str):
        raise TrackArtworkError(f"tracks.{circuit_id}.{field} must be a URL")
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    official = host in {"f1.com", "formula1.com"} or host.endswith((".f1.com", ".formula1.com"))
    if parsed.scheme != "https" or not official:
        raise TrackArtworkError(
            f"tracks.{circuit_id}.{field} must use HTTPS on an official Formula 1 host"
        )
    return value


def _parse_dimensions(value: Any, circuit_id: str) -> tuple[int, int]:
    """Validate the exact dimensions expected for the downloaded original."""
    if not isinstance(value, list) or len(value) != 2:
        raise TrackArtworkError(f"tracks.{circuit_id}.source_dimensions must be [width, height]")
    width, height = value
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
        raise TrackArtworkError(f"tracks.{circuit_id}.source_dimensions must be positive integers")
    return width, height


def _parse_boundaries(value: Any, circuit_id: str) -> tuple[SectorBoundary, ...]:
    """Validate the two normalized sector-boundary positions and orientations."""
    if not isinstance(value, list) or len(value) != 2:
        raise TrackArtworkError(f"tracks.{circuit_id}.sector_boundaries must contain two entries")
    boundaries: list[SectorBoundary] = []
    for index, raw_boundary in enumerate(value):
        label = f"tracks.{circuit_id}.sector_boundaries[{index}]"
        boundary = _require_mapping(raw_boundary, label)
        at = boundary.get("at")
        if not isinstance(at, list) or len(at) != 2:
            raise TrackArtworkError(f"{label}.at must be [normalized_x, normalized_y]")
        if any(
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(coordinate)
            or not 0.0 <= coordinate <= 1.0
            for coordinate in at
        ):
            raise TrackArtworkError(f"{label}.at coordinates must be finite values from 0 to 1")
        angle = boundary.get("normal_degrees")
        if (
            isinstance(angle, bool)
            or not isinstance(angle, (int, float))
            or not math.isfinite(angle)
        ):
            raise TrackArtworkError(f"{label}.normal_degrees must be finite")
        boundaries.append(SectorBoundary((float(at[0]), float(at[1])), float(angle) % 180.0))
    return tuple(boundaries)


def _decode_source_png(
    source_bytes: bytes, source_path: Path, expected_dimensions: tuple[int, int]
) -> Image.Image:
    """Decode a verified PNG, flattening any transparency onto white."""
    try:
        with Image.open(BytesIO(source_bytes)) as opened:
            detected_format = opened.format
            opened.verify()
        with Image.open(BytesIO(source_bytes)) as opened:
            opened.load()
            actual_dimensions = opened.size
            rgba = opened.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise TrackArtworkError(f"Invalid PNG source {source_path}: {exc}") from exc

    if detected_format != "PNG":
        raise TrackArtworkError(f"Track source must be a PNG file: {source_path}")
    if actual_dimensions != expected_dimensions:
        raise TrackArtworkError(
            f"PNG dimensions mismatch for {source_path}: expected "
            f"{expected_dimensions[0]}x{expected_dimensions[1]}, got "
            f"{actual_dimensions[0]}x{actual_dimensions[1]}"
        )

    white = Image.new("RGBA", rgba.size, (*BwrColors.WHITE, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def _encode_png(image: Image.Image) -> bytes:
    """Encode one derived image fully before bundle publication starts."""
    buffer = BytesIO()
    try:
        image.save(buffer, format="PNG")
    except (OSError, ValueError) as exc:
        raise TrackArtworkError(f"Cannot encode track artwork PNG: {exc}") from exc
    return buffer.getvalue()


def _detect_sector_masks(image: Image.Image, profile: str) -> tuple[Image.Image, ...]:
    """Detect all three antialiased sector colors for an official source profile."""
    hsv = image.convert("HSV")
    hue, saturation, value = hsv.split()
    masks: list[Image.Image] = []
    minimum_pixels = max(16, image.width * image.height // 50_000)
    for sector, rule in enumerate(_SOURCE_SECTOR_RULES[profile], start=1):
        hue_mask = hue.point(
            lambda sample, center=rule.hue, tolerance=rule.tolerance: (
                255 if min(abs(sample - center), 256 - abs(sample - center)) <= tolerance else 0
            )
        )
        saturation_mask = saturation.point(
            lambda sample, minimum=rule.minimum_saturation: 255 if sample >= minimum else 0
        )
        value_mask = value.point(
            lambda sample, minimum=rule.minimum_value: 255 if sample >= minimum else 0
        )
        mask = ImageChops.multiply(ImageChops.multiply(hue_mask, saturation_mask), value_mask)
        pixel_count = _mask_pixel_count(mask)
        if pixel_count < minimum_pixels:
            raise TrackArtworkError(
                f"Source profile {profile!r} did not identify enough pixels for sector {sector} "
                f"({pixel_count} found, {minimum_pixels} required)"
            )
        masks.append(mask)
    return tuple(masks)


def _mask_pixel_count(mask: Image.Image) -> int:
    """Count enabled pixels in a binary Pillow mask."""
    histogram = mask.histogram()
    return histogram[255]


def _recolor_sectors(
    source: Image.Image,
    masks: tuple[Image.Image, ...],
    colors: tuple[tuple[int, int, int], ...],
) -> Image.Image:
    """Replace source sector colors while leaving every non-sector detail intact."""
    output = source.copy()
    for mask, color in zip(masks, colors, strict=True):
        output.paste(color, mask=mask)
    return output


def _draw_sector_separators(
    image: Image.Image,
    boundaries: tuple[SectorBoundary, ...],
    color: tuple[int, int, int],
) -> None:
    """Draw short normal lines that keep same-color sector joins visible."""
    scale = min(image.size)
    half_length = max(8, round(scale * 0.022))
    line_width = max(3, round(scale * 0.007))
    draw = ImageDraw.Draw(image)
    for boundary in boundaries:
        center_x = boundary.at[0] * (image.width - 1)
        center_y = boundary.at[1] * (image.height - 1)
        radians = math.radians(boundary.normal_degrees)
        offset_x = math.cos(radians) * half_length
        offset_y = math.sin(radians) * half_length
        draw.line(
            (center_x - offset_x, center_y - offset_y, center_x + offset_x, center_y + offset_y),
            fill=color,
            width=line_width,
        )


def _fit_source_height(image: Image.Image) -> Image.Image:
    """Downscale source art to the editable-source height cap without upscaling."""
    if image.height <= TRACK_SOURCE_MAX_HEIGHT:
        return image.copy()
    ratio = TRACK_SOURCE_MAX_HEIGHT / image.height
    width = max(1, round(image.width * ratio))
    return image.resize((width, TRACK_SOURCE_MAX_HEIGHT), Image.Resampling.LANCZOS)
