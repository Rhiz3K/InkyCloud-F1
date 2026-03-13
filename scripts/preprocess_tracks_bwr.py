#!/usr/bin/env python3
"""Pre-process track images for black/white/red E-Ink rendering."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette

MAX_WIDTH = 490
MAX_HEIGHT = 280
WHITE_THRESHOLD = 210
BLACK_THRESHOLD = 90
RED_MIN = 120
RED_DOMINANCE = 1.15

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRACKS_DIR = PROJECT_ROOT / "app" / "assets" / "tracks"
OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "tracks_bwr"

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (160, 32, 32)
PALETTE = [BLACK, WHITE, RED]


def is_red_pixel(r: int, g: int, b: int) -> bool:
    return r >= RED_MIN and r >= int(g * RED_DOMINANCE) and r >= int(b * RED_DOMINANCE)


def classify_pixel(r: int, g: int, b: int) -> tuple[int, int, int]:
    if is_red_pixel(r, g, b):
        return RED

    luminance = int(0.299 * r + 0.587 * g + 0.114 * b)
    if luminance >= WHITE_THRESHOLD:
        return WHITE
    if luminance <= BLACK_THRESHOLD:
        return BLACK
    return BLACK


def process_track_image(input_path: Path, output_path: Path) -> dict:
    original = Image.open(input_path).convert("RGB")
    original_size = input_path.stat().st_size
    original_dimensions = original.size

    gray = original.convert("L")
    gray_pixels = gray.load()
    min_x = gray.width
    min_y = gray.height
    max_x = -1
    max_y = -1
    for y in range(gray.height):
        for x in range(gray.width):
            pixel = gray_pixels[x, y]
            value = int(pixel if isinstance(pixel, int) else pixel[0])
            if value <= 128:
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

    source_pixels: list[tuple[int, int, int]] = []
    pixel_access = original.load()
    for y in range(original.height):
        for x in range(original.width):
            pixel = pixel_access[x, y]
            if isinstance(pixel, tuple):
                r, g, b = pixel[:3]
            else:
                r = g = b = pixel
            source_pixels.append((int(r), int(g), int(b)))

    pixels = [classify_pixel(pixel[0], pixel[1], pixel[2]) for pixel in source_pixels]
    mapped = Image.new("RGB", original.size, WHITE)
    mapped.putdata(pixels)

    final = quantize_to_palette(mapped, PALETTE, colors=3)
    output_path.write_bytes(encode_indexed_bmp_4bit(final, PALETTE))
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
        "compression_ratio": original_size / output_size if output_size > 0 else 0,
    }


def main() -> None:
    print("=" * 60)
    print(" BWR Track Image Pre-processor")
    print("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    track_files = list(TRACKS_DIR.glob("*.png")) + list(TRACKS_DIR.glob("*.jpg"))

    if not track_files:
        print(f"No track images found in {TRACKS_DIR}")
        sys.exit(1)

    total_input_size = 0
    total_output_size = 0
    for track_path in sorted(track_files):
        output_path = OUTPUT_DIR / f"{track_path.stem}.bmp"
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
    main()
