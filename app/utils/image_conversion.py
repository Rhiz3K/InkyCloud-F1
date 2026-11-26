"""Shared image format conversion helpers."""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

from PIL import Image

from app.utils.bmp import RgbColor, encode_indexed_bmp_4bit

# zlib level for palette-exact PNG screens. Measured on the 800x480 calendars, level 9 is
# 1.5-3 % smaller than level 8 and takes 27-45 ms instead of 10-16 ms. The cost is paid once
# per cached variant on the render executor, while the smaller download saves radio time on
# every battery-powered refresh. optimize=True only adds a second pass that was never smaller.
PNG_COMPRESS_LEVEL = 9


def bmp_to_png(
    bmp_data: bytes,
    width: int = 400,
    *,
    full_size: bool = False,
    preserve_color: bool = False,
) -> bytes:
    """Convert BMP bytes to an optionally resized PNG preview."""
    with Image.open(BytesIO(bmp_data)) as image_file:
        image = image_file.convert("RGB" if preserve_color else "L")

    if not full_size:
        ratio = width / image.width
        image = image.resize(
            (width, int(image.height * ratio)),
            Image.Resampling.LANCZOS,
        )

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


class OutputFormat(StrEnum):
    """Encoding of a served screen; ``bmp`` (default) keeps the original bytes."""

    BMP = "bmp"
    PNG = "png"


class Rotation(StrEnum):
    """Lossless rotation of a served screen in degrees.

    Only ``0`` and ``180`` keep the fixed 800x480 landscape canvas; 90 and 270 would need a
    480x800 portrait layout, so they are deliberately not offered.
    """

    NONE = "0"
    HALF_TURN = "180"


@dataclass(frozen=True, slots=True)
class ImageOutput:
    """How a rendered screen BMP is delivered: its encoding and rotation."""

    format: OutputFormat = OutputFormat.BMP
    rotate: Rotation = Rotation.NONE

    @property
    def is_default(self) -> bool:
        """Return whether the default BMP bytes are served unchanged."""
        return self.format == OutputFormat.BMP and self.rotate == Rotation.NONE

    @property
    def media_type(self) -> str:
        """Return the response Content-Type."""
        return f"image/{self.format}"

    @property
    def cache_token(self) -> str:
        """Return the cache-key part that tells output variants apart."""
        return f"format={self.format}:rotate={self.rotate}"

    def filename(self, stem: str) -> str:
        """Return the Content-Disposition filename, e.g. ``calendar.png``."""
        return f"{stem}.{self.format}"


DEFAULT_IMAGE_OUTPUT = ImageOutput()


def _bmp_header_fields(bmp_data: bytes) -> tuple[int, int, int]:
    """Return the pixel-data offset, bits per pixel and important colours of a BMP."""
    (offset,) = struct.unpack_from("<I", bmp_data, 10)
    (bits_per_pixel,) = struct.unpack_from("<H", bmp_data, 28)
    (important_colors,) = struct.unpack_from("<I", bmp_data, 50)
    return offset, bits_per_pixel, important_colors


def _bmp_palette(bmp_data: bytes, count: int) -> list[RgbColor]:
    """Read the first ``count`` BGRX colour-table entries of a BMP as RGB tuples."""
    entries = struct.iter_unpack("<BBBx", bmp_data[54 : 54 + 4 * count])
    return [(red, green, blue) for blue, green, red in entries]


def _encode_like_source(image: Image.Image, source: bytes) -> bytes:
    """Encode ``image`` as a BMP with exactly the header and bit depth of ``source``.

    The renderers write 1-bit and 8-bit (Spectra 6) BMPs with Pillow and 4-bit (BWR/BWRY)
    BMPs with ``encode_indexed_bmp_4bit``; the same encoder is used here, so only the pixel
    data of a transformed screen differs from the default one.
    """
    offset, bits_per_pixel, important_colors = _bmp_header_fields(source)
    if bits_per_pixel == 4:
        palette = _bmp_palette(source, important_colors or 16)
        encoded = encode_indexed_bmp_4bit(image, palette)
    else:
        buffer = BytesIO()
        image.save(buffer, format="BMP")
        encoded = buffer.getvalue()
    if len(encoded) != len(source) or encoded[:offset] != source[:offset]:
        raise ValueError("Re-encoded BMP does not match the source layout")
    return encoded


def _encode_exact_png(image: Image.Image, palette: Sequence[RgbColor] | None) -> bytes:
    """Encode a decoded screen as PNG with the same pixel indices and panel palette.

    1-bit screens stay in Pillow mode ``"1"`` (1-bit greyscale PNG); colour screens stay in
    mode ``"P"`` with exactly the panel palette in PLTE, so no colour is converted, dithered
    or added and Pillow picks the smallest bit depth for it (2 bits for BWR/BWRY, 4 bits for
    Spectra 6).
    """
    if image.mode == "P":
        if not palette:
            raise ValueError("A palette screen needs its panel palette")
        flat = [channel for color in palette for channel in color]
        source_palette = image.getpalette() or []
        max_index = image.getextrema()[1]
        if source_palette[: len(flat)] != flat or not isinstance(max_index, int):
            raise ValueError("Screen palette does not match the panel palette")
        if max_index >= len(palette):
            raise ValueError(f"Pixel index {max_index} is outside the panel palette")
        image.putpalette(flat)
    elif image.mode != "1":
        raise ValueError(f"Unsupported screen image mode {image.mode!r}")
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
    return buffer.getvalue()


def transform_bmp_output(
    bmp_data: bytes,
    output: ImageOutput = DEFAULT_IMAGE_OUTPUT,
    *,
    palette: Sequence[RgbColor] | None = None,
) -> bytes:
    """Rotate and/or re-encode a rendered screen BMP without changing a single pixel value.

    ``rotate=180`` is a lossless ``transpose(ROTATE_180)`` of the indexed image (pixel
    ``(x, y)`` moves to ``(w-1-x, h-1-y)``, the size stays 800x480). ``format=png`` keeps the
    exact indices and palette; ``format=bmp`` keeps the source bit depth and header. The
    default output returns ``bmp_data`` itself. ``palette`` is the panel palette of a colour
    display (``None`` for 1-bit).
    """
    if output.is_default:
        return bmp_data
    with Image.open(BytesIO(bmp_data), formats=["BMP"]) as image_file:
        image = image_file.copy()
    if output.rotate == Rotation.HALF_TURN:
        image = image.transpose(Image.Transpose.ROTATE_180)
    if output.format == OutputFormat.PNG:
        return _encode_exact_png(image, palette)
    return _encode_like_source(image, bmp_data)
