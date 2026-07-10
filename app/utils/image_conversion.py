"""Shared image format conversion helpers."""

from io import BytesIO

from PIL import Image


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
