"""Validation and atomic persistence for generated or downloaded image assets."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.utils.atomic_io import atomic_write_bytes_sync


def decode_image_bytes(content: bytes, *, expected_format: str | None = None) -> Image.Image:
    """Fully decode image bytes and return an independent image after format validation."""
    with Image.open(BytesIO(content)) as image_file:
        actual_format = image_file.format
        image_file.verify()
    if expected_format is not None and actual_format != expected_format:
        raise ValueError(f"Expected {expected_format} image, got {actual_format or 'unknown'}")
    with Image.open(BytesIO(content)) as image_file:
        image_file.load()
        return image_file.copy()


def atomic_save_image(path: Path, image: Image.Image, *, image_format: str) -> None:
    """Encode an image in memory and atomically replace the destination."""
    buffer = BytesIO()
    image.save(buffer, format=image_format)
    encoded = buffer.getvalue()
    decode_image_bytes(encoded, expected_format=image_format)
    atomic_write_bytes_sync(path, encoded)
