"""Cached image loading, cropping, and track preparation for renderers."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from cachetools import LRUCache
from PIL import Image, ImageOps

from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.track_assets import build_track_stem_candidates

# Guards lazy class-level asset caches populated from the render worker pool.
ASSET_CACHE_LOCK = threading.Lock()
_DECODED_IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DECODED_IMAGE_CACHE_LOCK = threading.Lock()


def _decoded_image_size(image: Image.Image) -> int:
    """Estimate decoded image memory from dimensions and channel count."""
    return image.width * image.height * len(image.getbands())


_DECODED_IMAGE_CACHE: LRUCache[tuple[str, int], Image.Image] = LRUCache(
    maxsize=_DECODED_IMAGE_CACHE_MAX_BYTES,
    getsizeof=_decoded_image_size,
)


def _load_image_file(path_value: str, mtime_ns: int) -> Image.Image:
    """Decode an immutable image asset with a byte-bounded process cache."""
    cache_key = (path_value, mtime_ns)
    with _DECODED_IMAGE_CACHE_LOCK:
        cached = _DECODED_IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with Image.open(path_value) as image_file:
        decoded = image_file.copy()

    if _decoded_image_size(decoded) <= _DECODED_IMAGE_CACHE.maxsize:
        with _DECODED_IMAGE_CACHE_LOCK:
            _DECODED_IMAGE_CACHE[cache_key] = decoded
    return decoded


def _load_image_copy(path: Path) -> Image.Image:
    """Return an independent copy of a cached image asset."""
    return _load_image_file(str(path), path.stat().st_mtime_ns).copy()


def crop_to_content(img: Image.Image, *, use_binary_mask: bool = False) -> Image.Image:
    """Crop a logo to visible content, respecting transparency when present."""
    if "A" in img.getbands():
        alpha = img.getchannel("A")
        extrema = alpha.getextrema()
        alpha_min = extrema[0] if extrema is not None else None
        if not isinstance(alpha_min, tuple) and alpha_min is not None and alpha_min < 255:
            bbox = alpha.getbbox()
            if bbox:
                return img.crop(bbox)

    inverted = ImageOps.invert(img.convert("L"))
    if use_binary_mask:
        inverted = inverted.convert("1")
    bbox = inverted.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def _has_transparent_alpha(alpha: Image.Image | None) -> bool:
    """Return whether an alpha channel contains any non-opaque pixels."""
    if alpha is None:
        return False
    alpha_extrema = alpha.getextrema()
    alpha_min = alpha_extrema[0] if alpha_extrema is not None else None
    return not isinstance(alpha_min, tuple) and alpha_min is not None and alpha_min < 255


def _pixel_activity_value(pixel: object) -> float:
    """Reduce a scalar or tuple pixel to a comparable activity value."""
    if isinstance(pixel, tuple):
        return float(max(pixel)) if pixel else 0.0
    if isinstance(pixel, int | float):
        return float(pixel)
    return 0.0


def _active_pixel_counts(mask: Image.Image, *, threshold: float = 16) -> list[int]:
    """Count pixels above an activity threshold for every mask row."""
    rows = []
    for y in range(mask.height):
        active = 0
        for x in range(mask.width):
            if _pixel_activity_value(mask.getpixel((x, y))) > threshold:
                active += 1
        rows.append(active)
    return rows


def _find_horizontal_segments(
    rows: Sequence[int],
    *,
    threshold: int = 5,
) -> list[tuple[int, int, int]]:
    """Find contiguous active row segments and their peak widths."""
    segments: list[tuple[int, int, int]] = []
    start: int | None = None
    for index, count in enumerate(rows):
        if count > threshold:
            if start is None:
                start = index
            continue
        if start is None:
            continue
        segment_rows = rows[start:index]
        segments.append((start, index, max(segment_rows) if segment_rows else 0))
        start = None
    if start is not None:
        segment_rows = rows[start:]
        segments.append((start, len(rows), max(segment_rows) if segment_rows else 0))
    return segments


def _preserves_stacked_logo(
    img: Image.Image,
    first_segment: tuple[int, int, int],
    second_segment: tuple[int, int, int],
) -> bool:
    """Decide whether cropping would incorrectly split a stacked logo."""
    first_start, first_end, first_peak = first_segment
    second_start, second_end, second_peak = second_segment
    first_height = first_end - first_start
    second_height = second_end - second_start
    gap = second_start - first_end

    min_gap = max(8, img.height // 30)
    min_primary_height = max(12, img.height // 5)
    return (
        gap < min_gap
        or first_height < min_primary_height
        or first_height < second_height
        or first_peak < second_peak
    )


def crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
    """Keep only the dominant upper band for tall stacked logo assets."""
    alpha = img.getchannel("A") if "A" in img.getbands() else None
    if _has_transparent_alpha(alpha) and alpha is not None:
        mask = alpha
    else:
        mask = ImageOps.invert(img.convert("L"))
    segments = _find_horizontal_segments(_active_pixel_counts(mask))

    if len(segments) < 2:
        return img

    first_start, first_end, _first_peak = segments[0]
    if _preserves_stacked_logo(img, segments[0], segments[1]):
        return img

    return img.crop((0, first_start, img.width, first_end))


def get_country_flag_iso_code(country_name: str, country_map: dict[str, str]) -> str:
    """Resolve a country name to the local flag asset ISO code."""
    iso_code = country_map.get(country_name, "").lower()
    if iso_code:
        return iso_code

    aliases = {"UAE": "ae", "UK": "gb", "USA": "us"}
    return aliases.get(country_name, country_name[:2].lower())


def load_results_flag_image(
    country_name: str,
    country_map: dict[str, str],
    flags_dirs: Path | Sequence[Path],
    prepare_flag_image,
    logger,
) -> Image.Image | None:
    """Load and normalize the first matching local results flag image for a country."""
    iso_code = get_country_flag_iso_code(country_name, country_map)
    if not iso_code:
        return None

    directories: tuple[Path, ...]
    if isinstance(flags_dirs, Path):
        directories = (flags_dirs,)
    else:
        directories = tuple(flags_dirs)

    for directory in directories:
        local_flag_path = directory / f"{iso_code}.bmp"
        if not local_flag_path.exists():
            continue

        try:
            with Image.open(local_flag_path) as opened_flag:
                return prepare_flag_image(opened_flag)
        except Exception as exc:
            logger.warning("Failed to load local flag %s: %s", local_flag_path, exc)

    return None


def build_track_stems(race_data: dict) -> list[str]:
    """Build ordered candidate track asset stems from race circuit metadata."""
    circuit = race_data.get("circuit", {})
    circuit_id = str(circuit.get("circuitId", "") or "")
    location = str(circuit.get("location", "") or "")
    normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
    return build_track_stem_candidates(normalized_id, circuit_id, location)


def load_track_image_asset(
    track_stems: list[str],
    *,
    processed_dir: Path,
    fallback_glob: str | None = None,
    logger=None,
) -> Image.Image | None:
    """Load a track from one display's dedicated processed-asset directory."""
    if not track_stems:
        return None

    for stem in track_stems:
        track_path = processed_dir / f"{stem}.bmp"
        if not track_path.exists():
            continue
        try:
            return _load_image_copy(track_path)
        except Exception as exc:
            if logger is not None:
                logger.warning("Failed to load track %s: %s", track_path, exc)

    if fallback_glob:
        all_fallback = list(processed_dir.glob(fallback_glob))
        if all_fallback:
            try:
                return _load_image_copy(all_fallback[0])
            except Exception as exc:
                if logger is not None:
                    logger.warning("Failed to load track %s: %s", all_fallback[0], exc)

    return None


def prepare_mono_track_image(
    track_image: Image.Image,
    available_width: int,
    available_height: int,
    logger,
) -> Image.Image:
    """Prepare a track map for the monochrome renderer."""
    is_preprocessed = track_image.mode == "1"

    if not is_preprocessed:
        try:
            gray = track_image.convert("L")
            binary = gray.point(lambda p: 255 if p > 128 else 0)
            inverted = ImageOps.invert(binary)
            bbox = inverted.getbbox()
            if bbox:
                track_image = track_image.crop(bbox)
        except Exception as exc:
            logger.warning("Failed to crop track image: %s", exc)

    img_w, img_h = track_image.size
    ratio = min(available_width / img_w, available_height / img_h)
    new_size = (int(img_w * ratio), int(img_h * ratio))

    if new_size != (img_w, img_h):
        track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

    if not is_preprocessed:
        track_image = track_image.point(lambda p: 255 if p > 200 else 0)
        track_image = track_image.convert("1")

    return track_image


def prepare_color_track_image(
    track_image: Image.Image,
    available_width: int,
    available_height: int,
) -> Image.Image:
    """Prepare a track map for color renderers while preserving RGB output."""
    img_w, img_h = track_image.size
    ratio = min(available_width / img_w, available_height / img_h)
    new_size = (int(img_w * ratio), int(img_h * ratio))

    if new_size != (img_w, img_h):
        track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

    return track_image.convert("RGB")
