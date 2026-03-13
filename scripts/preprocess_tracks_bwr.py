#!/usr/bin/env python3
"""Pre-process track images for black/white/red E-Ink rendering."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

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


def to_palette_bmp(image: Image.Image) -> Image.Image:
    palette_flat = []
    for color in PALETTE:
        palette_flat.extend(color)

    while len(palette_flat) < 768:
        palette_flat.extend([0, 0, 0])

    palette_img = Image.new("P", (1, 1))
    palette_img.putpalette(palette_flat)
    return image.quantize(colors=3, palette=palette_img, dither=Image.Dither.NONE)


def process_track_image(input_path: Path, output_path: Path) -> dict:
    original = Image.open(input_path).convert("RGB")
    original_size = input_path.stat().st_size

    gray = original.convert("L")
    binary = gray.point(lambda p: 255 if p > 128 else 0)
    bbox = ImageOps.invert(binary).getbbox()
    if bbox:
        original = original.crop(bbox)

    img_w, img_h = original.size
    ratio = min(MAX_WIDTH / img_w, MAX_HEIGHT / img_h)
    if ratio < 1:
        new_size = (int(img_w * ratio), int(img_h * ratio))
        original = original.resize(new_size, Image.Resampling.LANCZOS)

    pixels = [classify_pixel(*pixel) for pixel in original.getdata()]
    mapped = Image.new("RGB", original.size, WHITE)
    mapped.putdata(pixels)

    final = to_palette_bmp(mapped)
    final.save(output_path, format="BMP")
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": Image.open(input_path).size,
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
