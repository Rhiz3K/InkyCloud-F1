"""Cached image loading and track preparation for renderers."""

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
        if is_preprocessed:
            # Pillow silently resamples mode "1" with NEAREST, which breaks thin track lines
            # into dashes. Resample in grayscale and re-threshold to keep them continuous.
            resampled = track_image.convert("L").resize(new_size, Image.Resampling.LANCZOS)
            track_image = resampled.point(lambda p: 255 if p >= 128 else 0).convert("1")
        else:
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
