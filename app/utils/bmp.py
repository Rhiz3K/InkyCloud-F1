"""Helpers for palette-based BMP generation."""

from __future__ import annotations

import struct
from typing import TypeAlias

from PIL import Image

RgbColor: TypeAlias = tuple[int, int, int]


def quantize_to_palette(image: Image.Image, palette: list[RgbColor], colors: int) -> Image.Image:
    """Quantize an image to a fixed RGB palette without dithering."""
    palette_flat: list[int] = []
    for color in palette:
        palette_flat.extend(color)

    while len(palette_flat) < 768:
        palette_flat.extend([0, 0, 0])

    palette_image = Image.new("P", (1, 1))
    palette_image.putpalette(palette_flat)
    return image.quantize(colors=colors, palette=palette_image, dither=Image.Dither.NONE)


def encode_indexed_bmp_4bit(indexed: Image.Image, palette: list[RgbColor]) -> bytes:
    """Encode a palette image as an uncompressed 4-bit BMP.

    Standard BMP does not support 2-bit indexed images, so 4-bit indexed BMP is the
    smallest broadly compatible format for 3-color BWR output.
    """
    if indexed.mode != "P":
        raise ValueError(f"Expected image mode 'P', got {indexed.mode!r}")

    width, height = indexed.size
    row_stride = ((((width + 1) // 2) + 3) // 4) * 4

    rows: list[bytes] = []
    px = indexed.load()
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(0, width, 2):
            left = int(px[x, y]) & 0x0F
            right = int(px[x + 1, y]) & 0x0F if x + 1 < width else 0
            row.append((left << 4) | right)
        row.extend(b"\x00" * (row_stride - len(row)))
        rows.append(bytes(row))

    pixel_data = b"".join(rows)

    palette_entries = list(palette[:16])
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
