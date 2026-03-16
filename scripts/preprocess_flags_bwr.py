#!/usr/bin/env python3
"""Pre-process flag images for black/white/red E-Ink rendering."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

from app.utils.bmp import encode_indexed_bmp_4bit, quantize_to_palette

TARGET_WIDTH = 87
TARGET_HEIGHT = 58
RED_HUE_DOMINANCE = 1.18
RED_MIN = 110
WHITE_THRESHOLD = 215
BLACK_THRESHOLD = 75

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
FLAGS_INPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_flat"
FLAGS_OUTPUT_DIR = PROJECT_ROOT / "app" / "assets" / "flags_bwr"

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
PALETTE = [BLACK, WHITE, RED]


def normalize_image(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "P"):
        background = Image.new("RGB", image.size, WHITE)
        if image.mode == "P":
            image = image.convert("RGBA")
        if image.mode == "RGBA":
            background.paste(image, mask=image.split()[3] if len(image.split()) == 4 else None)
            return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def is_red_pixel(r: int, g: int, b: int) -> bool:
    return r >= RED_MIN and r >= int(g * RED_HUE_DOMINANCE) and r >= int(b * RED_HUE_DOMINANCE)


def classify_pixel(r: int, g: int, b: int) -> tuple[int, int, int]:
    if is_red_pixel(r, g, b):
        return RED

    luminance = int(0.299 * r + 0.587 * g + 0.114 * b)
    if luminance >= WHITE_THRESHOLD:
        return WHITE
    if luminance <= BLACK_THRESHOLD:
        return BLACK
    return BLACK if luminance < 150 else WHITE


def process_flag_image(input_path: Path, output_path: Path) -> dict:
    original = normalize_image(Image.open(input_path))
    original_size = input_path.stat().st_size
    original_dimensions = original.size
    resized = original.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)

    source_pixels: list[tuple[int, int, int]] = []
    pixel_access = resized.load()
    for y in range(resized.height):
        for x in range(resized.width):
            pixel = pixel_access[x, y]
            if isinstance(pixel, tuple):
                r, g, b = pixel[:3]
            else:
                r = g = b = pixel
            source_pixels.append((int(r), int(g), int(b)))

    pixels = [classify_pixel(pixel[0], pixel[1], pixel[2]) for pixel in source_pixels]
    mapped = Image.new("RGB", resized.size, WHITE)
    mapped.putdata(pixels)

    final = quantize_to_palette(mapped, PALETTE, colors=3)
    output_path.write_bytes(encode_indexed_bmp_4bit(final, PALETTE))
    output_size = output_path.stat().st_size

    return {
        "input_size": original_size,
        "output_size": output_size,
        "original_dimensions": original_dimensions,
        "final_dimensions": final.size,
    }


def main() -> None:
    print("=" * 60)
    print(" BWR Flag Image Pre-processor")
    print("=" * 60)

    if not FLAGS_INPUT_DIR.exists():
        print(f"Input directory not found: {FLAGS_INPUT_DIR}")
        sys.exit(1)

    FLAGS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    flag_files = list(FLAGS_INPUT_DIR.glob("*.png"))

    if not flag_files:
        print(f"No flag images found in {FLAGS_INPUT_DIR}")
        sys.exit(1)

    total_input_size = 0
    total_output_size = 0
    for flag_path in sorted(flag_files):
        output_path = FLAGS_OUTPUT_DIR / f"{flag_path.stem}.bmp"
        try:
            stats = process_flag_image(flag_path, output_path)
            total_input_size += stats["input_size"]
            total_output_size += stats["output_size"]
            print(
                f" {flag_path.name:12} -> {output_path.name:12} "
                f"({stats['input_size'] / 1024:6.0f}KB -> {stats['output_size'] / 1024:5.0f}KB)"
            )
        except Exception as exc:
            print(f" {flag_path.name:12} -> ERROR: {exc}")

    print("-" * 60)
    print(f" Total: {total_input_size / 1024:.1f}KB -> {total_output_size / 1024:.1f}KB")
    print(f"\nProcessed flags saved to: {FLAGS_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
