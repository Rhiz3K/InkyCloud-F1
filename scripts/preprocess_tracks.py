#!/usr/bin/env python3
"""Pre-process track images for faster rendering.

This script converts high-resolution track PNG images to optimized 1-bit BMP
files that can be loaded instantly during rendering.

Processing steps:
1. Load original PNG
2. Convert to grayscale
3. Auto-crop whitespace
4. Resize to fit max dimensions (490x280)
5. Apply threshold for 1-bit conversion
6. Save as 1-bit BMP

Usage:
    python scripts/preprocess_tracks.py
    python scripts/preprocess_tracks.py --circuits suzuka,monaco
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.track_assets import (
    discover_track_source_stems,
    resolve_track_source_path,
    strip_track_variant_suffix,
)
from app.utils.atomic_io import atomic_save_image

# Constants
MAX_WIDTH = 490  # Maximum track image width
MAX_HEIGHT = 280  # Maximum track image height
THRESHOLD = 200  # Pixel value threshold for black/white conversion

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRACKS_DIR = PROJECT_ROOT / "app" / "assets" / "tracks"
OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "tracks_processed"


def process_track_image(input_path: Path, output_path: Path) -> dict:
    """
    Process a single track image.

    Args:
        input_path: Path to input PNG file
        output_path: Path to output BMP file

    Returns:
        Dictionary with processing stats
    """
    # Load original
    original = Image.open(input_path)
    original_size = input_path.stat().st_size

    # Convert to grayscale
    gray = original.convert("L")

    # Auto-crop whitespace (invert, get bbox, crop)
    # Create binary version for bbox detection
    binary = gray.point(lambda p: 255 if p > 128 else 0)  # type: ignore[operator]
    inverted = ImageOps.invert(binary)
    bbox = inverted.getbbox()

    if bbox:
        gray = gray.crop(bbox)

    # Resize to fit max dimensions while maintaining aspect ratio
    img_w, img_h = gray.size
    ratio = min(MAX_WIDTH / img_w, MAX_HEIGHT / img_h)

    if ratio < 1:  # Only resize if larger than max
        new_size = (int(img_w * ratio), int(img_h * ratio))
        gray = gray.resize(new_size, Image.Resampling.LANCZOS)

    # Apply threshold for clean 1-bit conversion
    binary = gray.point(lambda p: 255 if p > THRESHOLD else 0)  # type: ignore[operator]

    # Convert to 1-bit
    final = binary.convert("1")

    # Save as BMP
    atomic_save_image(output_path, final, format="BMP")
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": original.size,
        "final_dimensions": final.size,
        "compression_ratio": original_size / output_size if output_size > 0 else 0,
    }


def main(circuits: list[str] | None = None) -> None:
    """Process all track images."""
    print("=" * 60)
    print(" Track Image Pre-processor")
    print("=" * 60)

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    track_stems = discover_track_source_stems(TRACKS_DIR)
    if circuits:
        wanted = {circuit.strip().lower() for circuit in circuits if circuit.strip()}
        track_stems = [stem for stem in track_stems if stem in wanted]

    track_files = [
        resolve_track_source_path(TRACKS_DIR, [track_stem], variant_suffix="bw")
        for track_stem in track_stems
    ]
    track_files = [track_path for track_path in track_files if track_path is not None]

    if not track_files:
        print(f"No track images found in {TRACKS_DIR}")
        sys.exit(1)

    print(f"Found {len(track_files)} track images")
    print("-" * 60)

    total_input_size = 0
    total_output_size = 0
    failures = 0

    for track_path in sorted(track_files):
        source_stem = strip_track_variant_suffix(track_path.stem)
        output_stem = CIRCUIT_ID_MAP.get(source_stem, source_stem)
        output_path = OUTPUT_DIR / f"{output_stem}.bmp"

        try:
            stats = process_track_image(track_path, output_path)
            total_input_size += stats["input_size"]
            total_output_size += stats["output_size"]

            print(
                f" {track_path.name:25} -> {output_path.name:25} "
                f"({stats['input_size'] / 1024:6.0f}KB -> {stats['output_size'] / 1024:5.0f}KB, "
                f"{stats['compression_ratio']:5.1f}x)"
            )
        except Exception as e:
            failures += 1
            print(f" {track_path.name:25} -> ERROR: {e}")

    print("-" * 60)
    print(f" Total: {total_input_size / 1024 / 1024:.1f}MB -> {total_output_size / 1024:.0f}KB")
    print(f" Compression: {total_input_size / total_output_size:.1f}x")
    print("=" * 60)
    print(f"\nProcessed images saved to: {OUTPUT_DIR}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-process 1-bit track images")
    parser.add_argument(
        "--circuits",
        type=str,
        default=None,
        help="Comma-separated list of circuit IDs to process",
    )
    args = parser.parse_args()
    selected_circuits = args.circuits.split(",") if args.circuits else None
    main(selected_circuits)
