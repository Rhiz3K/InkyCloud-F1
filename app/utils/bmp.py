"""Helpers for palette-based BMP generation."""

from __future__ import annotations

import struct
from typing import TypeAlias

from PIL import Image, ImageMath

RgbColor: TypeAlias = tuple[int, int, int]


def _build_palette_image(palette: list[RgbColor]) -> Image.Image:
    """Build a one-pixel Pillow palette image from RGB color tuples."""
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


def _luminance_image(rgb: Image.Image) -> Image.Image:
    """Compute integer Rec. 601 luminance in Pillow's native pixel loops."""
    red, green, blue = rgb.split()
    return ImageMath.unsafe_eval(
        "convert((299 * r + 587 * g + 114 * b) / 1000, 'L')",
        r=red,
        g=green,
        b=blue,
    )


def _mask(expression: str, **bands: Image.Image) -> Image.Image:
    """Evaluate a trusted boolean ImageMath expression as an 8-bit mask."""
    return ImageMath.unsafe_eval(f"convert(255 * ({expression}), 'L')", **bands)


def _palette_image_from_indices(indices: Image.Image, palette: list[RgbColor]) -> Image.Image:
    """Attach a concrete RGB palette to an image of color indices."""
    indexed = Image.frombytes("P", indices.size, indices.tobytes())
    palette_data = _build_palette_image(palette).getpalette() or []
    indexed.putpalette(palette_data)  # type: ignore[arg-type]
    return indexed


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
    red, green, blue = rgb.split()
    luminance = _luminance_image(rgb)
    green_limit = green.point([int(value * red_dominance) for value in range(256)])
    blue_limit = blue.point([int(value * red_dominance) for value in range(256)])
    red_mask = _mask(
        f"(r >= {red_min}) * "
        f"((r - max(g, b)) >= {red_margin}) * "
        "(r >= gd) * (r >= bd) * "
        f"(lum <= {red_max_luminance}) * (lum < {white_preserve_threshold})",
        r=red,
        g=green,
        b=blue,
        gd=green_limit,
        bd=blue_limit,
        lum=luminance,
    )

    neutral_threshold = max(bw_threshold, black_preserve_threshold)
    indices = luminance.point(
        [black_index if value < neutral_threshold else white_index for value in range(256)]
    )
    indices.paste(red_index, mask=red_mask)
    return _palette_image_from_indices(indices, palette)


def map_to_bwry_palette(
    image: Image.Image,
    palette: list[RgbColor],
    *,
    black_index: int = 0,
    white_index: int = 1,
    red_index: int = 2,
    yellow_index: int = 3,
    bw_threshold: int = 172,
    white_preserve_threshold: int = 220,
    black_preserve_threshold: int = 96,
    red_max_luminance: int = 200,
    red_min: int = 96,
    red_margin: int = 24,
    red_dominance: float = 1.15,
    yellow_min: int = 112,
    yellow_margin: int = 28,
    yellow_dominance: float = 1.08,
    yellow_rg_distance: int = 74,
    yellow_max_luminance: int = 236,
) -> Image.Image:
    """Map RGB image to a strict black/white/red/yellow palette.

    Grayscale pixels are forced to black/white so anti-aliased text and line art do
    not pick up unwanted red or yellow fringes.
    """
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    luminance = _luminance_image(rgb)
    green_red_limit = green.point([int(value * red_dominance) for value in range(256)])
    blue_red_limit = blue.point([int(value * red_dominance) for value in range(256)])
    blue_yellow_limit = blue.point([int(value * yellow_dominance) for value in range(256)])

    red_mask = _mask(
        f"(r >= {red_min}) * "
        f"((r - max(g, b)) >= {red_margin}) * "
        "(r >= grd) * (r >= brd) * "
        f"(lum <= {red_max_luminance}) * (lum < {white_preserve_threshold})",
        r=red,
        g=green,
        b=blue,
        grd=green_red_limit,
        brd=blue_red_limit,
        lum=luminance,
    )
    yellow_mask = _mask(
        f"(r >= {yellow_min}) * (g >= {yellow_min}) * "
        f"((max(r, g) - min(r, g)) <= {yellow_rg_distance}) * "
        f"((min(r, g) - b) >= {yellow_margin}) * "
        "(r >= byd) * (g >= byd) * "
        f"(lum <= {yellow_max_luminance}) * (lum < {white_preserve_threshold})",
        r=red,
        g=green,
        b=blue,
        byd=blue_yellow_limit,
        lum=luminance,
    )

    neutral_threshold = max(bw_threshold, black_preserve_threshold)
    indices = luminance.point(
        [black_index if value < neutral_threshold else white_index for value in range(256)]
    )
    indices.paste(red_index, mask=red_mask)
    indices.paste(yellow_index, mask=yellow_mask)
    return _palette_image_from_indices(indices, palette)


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

    raw = indexed.tobytes()
    if len(raw) != width * height:
        raise ValueError("Failed to access indexed image pixel data")
    if raw and max(raw) > 15:
        offset = next(index for index, value in enumerate(raw) if value > 15)
        raise ValueError(
            f"Pixel index out of 4-bit range at ({offset % width}, {offset // width}): "
            f"{raw[offset]}"
        )

    # Every index fits a nibble, so the low hex digit of each byte is the pixel value;
    # joining those digits pairwise packs two pixels per byte in C instead of per-pixel Python.
    odd_width = width % 2 == 1
    padding = b"\x00" * (row_stride - (width + 1) // 2)
    rows: list[bytes] = []
    for y in range(height - 1, -1, -1):
        row = raw[y * width : (y + 1) * width]
        if odd_width:
            row += b"\x00"
        rows.append(bytes.fromhex(row.hex()[1::2]) + padding)

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
