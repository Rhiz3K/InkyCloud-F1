"""Measure visual font-top alignment used by historical results headers."""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.append(os.getcwd())
from app.services.renderer import Renderer


def get_visual_top(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> int:
    """Return the first row containing a rendered black text pixel."""
    # Create a reasonably large image
    img = Image.new("1", (200, 100), 1)  # White bg
    draw = ImageDraw.Draw(img)

    # Draw text at (0, 0)
    draw.text((0, 0), text, fill=0, font=font)

    # Find first row with a black pixel (0)
    width, height = img.size
    pixels = img.load()
    if pixels is None:
        raise RuntimeError("Failed to access rendered alignment pixels")

    for y in range(height):
        for x in range(width):
            if pixels[x, y] == 0:
                return y
    return 0


def measure_alignment() -> None:
    """Print the visual baseline difference between year and header fonts."""
    # Helper to load fonts via Renderer (mocks not needed for this)
    renderer = Renderer({})

    font_year = renderer.fonts["results_year"]  # Size 36
    font_header = renderer.fonts["results_title"]  # Size 18

    text_year = "2024"
    text_header = "QUALIFYING"

    top_year = get_visual_top(font_year, text_year)
    top_header = get_visual_top(font_header, text_header)

    print(f"Visual Top '2024' (Size 36) at draw(0,0): {top_year}px")
    print(f"Visual Top 'QUALIFYING' (Size 18) at draw(0,0): {top_header}px")

    diff = top_year - top_header
    print(f"Difference (Year - Header): {diff}px")

    if diff > 0:
        print(f"Year is visually LOWER by {diff}px. Move Year UP by {diff}px (Y - {diff})")
    elif diff < 0:
        adj = abs(diff)
        print(f"Year is visually HIGHER by {adj}px. Move Year DOWN by {adj}px (Y + {adj})")
    else:
        print("Visually aligned.")


if __name__ == "__main__":
    measure_alignment()
