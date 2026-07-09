#!/usr/bin/env python3
"""Pre-process track images for Spectra 6 E-Ink rendering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from app.services.spectra6_renderer import CIRCUIT_ID_MAP, Spectra6Colors
from app.services.track_assets import (
    discover_track_source_stems,
    resolve_track_source_path,
    strip_track_variant_suffix,
)
from app.utils.atomic_io import atomic_write_bytes_sync
from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette

MAX_WIDTH = 490
MAX_HEIGHT = 280
NON_WHITE_THRESHOLD = 245

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRACKS_DIR = PROJECT_ROOT / "app" / "assets" / "tracks"
OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "tracks_spectra6"

WHITE = Spectra6Colors.WHITE
PALETTE = Spectra6Colors.PALETTE


def process_track_image(input_path: Path, output_path: Path) -> dict:
    original_file = Image.open(input_path)
    if original_file.mode in ("RGBA", "LA") or "transparency" in original_file.info:
        rgba = original_file.convert("RGBA")
        background = Image.new("RGB", rgba.size, WHITE)
        background.paste(rgba, mask=rgba.getchannel("A"))
        original = background
    elif original_file.mode == "P":
        rgba = original_file.convert("RGBA")
        background = Image.new("RGB", rgba.size, WHITE)
        background.paste(rgba, mask=rgba.getchannel("A"))
        original = background
    elif original_file.mode != "RGB":
        original = original_file.convert("RGB")
    else:
        original = original_file

    original_size = input_path.stat().st_size
    original_dimensions = original.size

    rgb_pixels = original.load()
    if rgb_pixels is None:
        raise ValueError("Failed to access track pixels")

    min_x = original.width
    min_y = original.height
    max_x = -1
    max_y = -1
    for y in range(original.height):
        for x in range(original.width):
            pixel = rgb_pixels[x, y]  # type: ignore[index]
            if isinstance(pixel, tuple):
                r, g, b = (int(channel) for channel in pixel[:3])
            else:
                r = g = b = int(pixel)

            if min(r, g, b) < NON_WHITE_THRESHOLD:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x >= 0 and max_y >= 0:
        original = original.crop((min_x, min_y, max_x + 1, max_y + 1))

    img_w, img_h = original.size
    ratio = min(MAX_WIDTH / img_w, MAX_HEIGHT / img_h)
    if ratio < 1:
        new_size = (int(img_w * ratio), int(img_h * ratio))
        original = original.resize(new_size, Image.Resampling.LANCZOS)

    final = quantize_to_palette(original, PALETTE, colors=len(PALETTE))
    atomic_write_bytes_sync(output_path, encode_indexed_bmp_4bit(final, PALETTE))
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
        "compression_ratio": original_size / output_size if output_size > 0 else 0,
    }


def main(circuits: list[str] | None = None) -> None:
    print("=" * 60)
    print(" Spectra 6 Track Image Pre-processor")
    print("=" * 60)

    if not TRACKS_DIR.exists():
        print(f"Input directory not found: {TRACKS_DIR}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    track_stems = discover_track_source_stems(TRACKS_DIR)
    if circuits:
        wanted = {circuit.strip().lower() for circuit in circuits if circuit.strip()}
        track_stems = [stem for stem in track_stems if stem in wanted]

    track_files = [
        resolve_track_source_path(TRACKS_DIR, [track_stem], variant_suffix="spectra6")
        for track_stem in track_stems
    ]
    track_files = [track_path for track_path in track_files if track_path is not None]

    if not track_files:
        print(f"No track images found in {TRACKS_DIR}")
        sys.exit(1)

    total_input_size = 0
    total_output_size = 0
    for track_path in sorted(track_files):
        source_stem = strip_track_variant_suffix(track_path.stem)
        normalized_stem = CIRCUIT_ID_MAP.get(source_stem, source_stem)
        output_path = OUTPUT_DIR / f"{normalized_stem}.bmp"
        try:
            stats = process_track_image(track_path, output_path)
            total_input_size += stats["input_size"]
            total_output_size += stats["output_size"]
            print(
                f" {track_path.name:25} -> {output_path.name:25} "
                f"({stats['input_size'] / 1024:6.0f}KB -> {stats['output_size'] / 1024:5.0f}KB)"
            )
        except Exception as exc:
            print(f" {track_path.name:25} -> ERROR: {exc}")

    print("-" * 60)
    print(f" Total: {total_input_size / 1024 / 1024:.1f}MB -> {total_output_size / 1024:.0f}KB")
    print(f"\nProcessed images saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-process Spectra 6 track images")
    parser.add_argument(
        "--circuits",
        type=str,
        default=None,
        help="Comma-separated list of circuit IDs to process",
    )
    args = parser.parse_args()
    selected_circuits = args.circuits.split(",") if args.circuits else None
    main(selected_circuits)
