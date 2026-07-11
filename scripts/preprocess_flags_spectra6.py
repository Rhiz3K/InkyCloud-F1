#!/usr/bin/env python3
"""Pre-process flag images for Spectra 6 E-Ink rendering."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

from PIL import Image

from app.services.spectra6_renderer import Spectra6Colors
from app.utils.atomic_io import atomic_write_bytes_sync
from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette

TARGET_WIDTH = 87
TARGET_HEIGHT = 58
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
FLAGS_INPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_flat"
FLAGS_OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_spectra6"
PALETTE = Spectra6Colors.PALETTE


class FlagStats(TypedDict):
    """Sizes and dimensions reported for one processed flag."""

    input_size: int
    output_size: int
    original_dimensions: tuple[int, int]
    final_dimensions: tuple[int, int]


def normalize_image(image: Image.Image) -> Image.Image:
    """Flatten transparent flag pixels onto the display's white background."""
    if image.mode in ("RGBA", "LA", "P") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, Spectra6Colors.WHITE)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def process_flag_image(input_path: Path, output_path: Path) -> FlagStats:
    """Convert one source flag into a palette-constrained Spectra 6 BMP."""
    with Image.open(input_path) as input_image:
        original = normalize_image(input_image)
    original_size = input_path.stat().st_size
    original_dimensions = original.size
    resized = original.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
    final = quantize_to_palette(resized, PALETTE, colors=len(PALETTE))
    atomic_write_bytes_sync(output_path, encode_indexed_bmp_4bit(final, PALETTE))
    return {
        "input_size": original_size,
        "output_size": output_path.stat().st_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
    }


def main() -> None:
    """Process all source flags into Spectra 6 assets and fail on errors."""
    if not FLAGS_INPUT_DIR.exists():
        print(f"Input directory not found: {FLAGS_INPUT_DIR}")
        raise SystemExit(1)

    FLAGS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    flag_files = sorted(FLAGS_INPUT_DIR.glob("*.png"))
    if not flag_files:
        print(f"No flag images found in {FLAGS_INPUT_DIR}")
        raise SystemExit(1)

    failures = 0
    for flag_path in flag_files:
        output_path = FLAGS_OUTPUT_DIR / f"{flag_path.stem}.bmp"
        try:
            stats = process_flag_image(flag_path, output_path)
            print(
                f" {flag_path.name:12} -> {output_path.name:12} "
                f"({stats['input_size'] / 1024:6.0f}KB -> "
                f"{stats['output_size'] / 1024:5.0f}KB)"
            )
        except Exception as exc:
            failures += 1
            print(f" {flag_path.name:12} -> ERROR: {exc}", file=sys.stderr)

    if failures:
        raise SystemExit(1)
    print(f"Processed {len(flag_files)} flags into {FLAGS_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
