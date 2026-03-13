"""Helpers for palette-based BMP generation."""

from __future__ import annotations

import struct
from typing import TypeAlias

from PIL import Image

RgbColor: TypeAlias = tuple[int, int, int]


def _build_palette_image(palette: list[RgbColor]) -> Image.Image:
    palette_flat: list[int] = []
    for color in palette:
        palette_flat.extend(color)

    while len(palette_flat) < 768:
        palette_flat.extend([0, 0, 0])

    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(palette_flat)
    return palette_image


def quantize_to_palette(image: Image.Image, palette: list[RgbColor], colors: int) -> Image.Image:
    """Quantize an image to a fixed RGB palette without dithering."""
    palette_image = _build_palette_image(palette)
    return image.quantize(colors=colors, palette=palette_image, dither=Image.Dither.NONE)


def map_to_bwr_palette(
    image: Image.Image,
    palette: list[RgbColor],
    *,
    black_index: int = 0,
    white_index: int = 1,
    red_index: int = 2,
    bw_threshold: int = 170,
    white_preserve_threshold: int = 208,
    black_preserve_threshold: int = 96,
    red_max_luminance: int = 200,
    red_min: int = 96,
    red_margin: int = 24,
    red_dominance: float = 1.15,
) -> Image.Image:
    """Map RGB image to a strict black/white/red palette.

    Non-red pixels are forced to black/white by luminance to avoid red fringing on
    anti-aliased black text and line art.
    """
    rgb = image.convert("RGB")
    indexed = Image.new("P", rgb.size, color=white_index)
    palette_data = _build_palette_image(palette).getpalette() or []
    indexed.putpalette(palette_data)  # type: ignore[arg-type]

    src = rgb.load()
    dst = indexed.load()
    if src is None or dst is None:
        raise ValueError("Failed to access image pixel data")

    for y in range(rgb.height):
        for x in range(rgb.width):
            pixel = src[x, y]  # type: ignore[index]
            if isinstance(pixel, tuple):
                r, g, b = pixel[:3]
            else:
                r = g = b = int(pixel)

            luminance = int(0.299 * r + 0.587 * g + 0.114 * b)

            if luminance >= white_preserve_threshold:
                dst[x, y] = white_index  # type: ignore[index]
                continue

            is_red = (
                r >= red_min
                and r - max(g, b) >= red_margin
                and r >= int(g * red_dominance)
                and r >= int(b * red_dominance)
            )

            if is_red and luminance <= red_max_luminance:
                dst[x, y] = red_index  # type: ignore[index]
                continue

            if luminance <= black_preserve_threshold:
                dst[x, y] = black_index  # type: ignore[index]
                continue

            dst[x, y] = black_index if luminance < bw_threshold else white_index  # type: ignore[index]

    return indexed


def encode_indexed_bmp_4bit(indexed: Image.Image, palette: list[RgbColor]) -> bytes:
    """Encode a palette image as an uncompressed 4-bit BMP.

    Standard BMP does not support 2-bit indexed images, so 4-bit indexed BMP is the
    smallest broadly compatible format for 3-color BWR output.
    """
    if indexed.mode != "P":
        raise ValueError(f"Expected image mode 'P', got {indexed.mode!r}")
    if len(palette) > 16:
        raise ValueError(f"4-bit BMP supports max 16 palette colors, got {len(palette)}")

    width, height = indexed.size
    row_stride = ((((width + 1) // 2) + 3) // 4) * 4

    rows: list[bytes] = []
    px = indexed.load()
    if px is None:
        raise ValueError("Failed to access indexed image pixel data")
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(0, width, 2):
            left_pixel = px[x, y]  # type: ignore[index]
            left = int(left_pixel[0]) if isinstance(left_pixel, tuple) else int(left_pixel)
            if not 0 <= left <= 15:
                raise ValueError(f"Pixel index out of 4-bit range at ({x}, {y}): {left}")

            if x + 1 < width:
                right_pixel = px[x + 1, y]  # type: ignore[index]
                right = int(right_pixel[0]) if isinstance(right_pixel, tuple) else int(right_pixel)
                if not 0 <= right <= 15:
                    raise ValueError(f"Pixel index out of 4-bit range at ({x + 1}, {y}): {right}")
            else:
                right = 0
            row.append((left << 4) | right)
        row.extend(b"\x00" * (row_stride - len(row)))
        rows.append(bytes(row))

    pixel_data = b"".join(rows)

    palette_entries = list(palette)
    filler = palette[-1] if palette else (255, 255, 255)
    while len(palette_entries) < 16:
        palette_entries.append(filler)

    palette_bytes = b"".join(
        struct.pack("<BBBB", blue, green, red, 0) for red, green, blue in palette_entries
    )

    offset = 14 + 40 + len(palette_bytes)
    file_size = offset + len(pixel_data)

    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, offset)
    info_header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        4,
        0,
        len(pixel_data),
        2835,
        2835,
        len(palette_entries),
        len(palette),
    )

    return file_header + info_header + palette_bytes + pixel_data
