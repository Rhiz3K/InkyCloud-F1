"""Monochrome renderer and its display-specific asset preparation."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import ClassVar

from PIL import Image

from app.services.renderer_assets import prepare_mono_track_image
from app.services.renderer_base import RendererBase
from app.services.renderer_theme import make_monochrome_theme

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
TRACKS_PROCESSED_DIR = ASSETS_DIR / "tracks_processed"
IMAGES_DIR = ASSETS_DIR / "images"
FLAGS_DIR = ASSETS_DIR / "flags_processed"


class MonoColors:
    """Logical fills for Pillow's one-bit image mode."""

    BLACK = 0
    WHITE = 1
    PALETTE: ClassVar[list[int]] = [BLACK, WHITE]


MONO_THEME = make_monochrome_theme(
    colors=MonoColors,
    track_directory=lambda: TRACKS_PROCESSED_DIR,
    flags_directories=lambda: FLAGS_DIR,
    images_directory=lambda: IMAGES_DIR,
    prepare_track_image=prepare_mono_track_image,
)


class Renderer(RendererBase):
    """Renderer for generating one-bit BMP images."""

    THEME = MONO_THEME

    def _new_canvas(self) -> Image.Image:
        """Create a monochrome canvas using configured display dimensions."""
        return Image.new("1", (self.width, self.height), self.colors.WHITE)

    def _encode_image(self, image: Image.Image) -> bytes:
        """Encode the monochrome canvas as BMP."""
        return self._to_bmp(image)

    @staticmethod
    def _prepare_f1_logo(image: Image.Image) -> Image.Image:
        """Convert the header logo to a sharp one-bit image."""
        return image.convert("L").point(lambda pixel: 255 if pixel > 128 else 0).convert("1")

    @staticmethod
    def _paste_driver_photo(canvas: Image.Image, photo: Image.Image, x: int, y: int) -> None:
        """Paste an already prepared one-bit driver silhouette."""
        canvas.paste(photo, (x, y))

    @staticmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """Load driver silhouettes and convert their alpha masks to one bit."""
        drivers_dir = IMAGES_DIR / "drivers"
        photos: dict[str, Image.Image] = {}
        if not drivers_dir.exists():
            return photos

        for photo_path in drivers_dir.glob("*.png"):
            try:
                with Image.open(photo_path) as img_file:
                    img: Image.Image
                    if img_file.mode in ("RGBA", "LA", "PA", "P"):
                        rgba_img = img_file.convert("RGBA")
                        result = Image.new("1", rgba_img.size, 1)
                        for y in range(rgba_img.height):
                            for x in range(rgba_img.width):
                                pixel = rgba_img.getpixel((x, y))
                                if isinstance(pixel, tuple) and len(pixel) >= 4 and pixel[3] > 128:
                                    result.putpixel((x, y), 0)
                        img = result
                    else:
                        img = img_file.convert("1")
                photos[photo_path.stem.lower()] = img
            except Exception as exc:
                logger.warning("Failed to load driver photo %s: %s", photo_path, exc)
        return photos

    @staticmethod
    def _to_bmp(image: Image.Image) -> bytes:
        """Convert a Pillow image to BMP bytes."""
        buffer = io.BytesIO()
        image.save(buffer, format="BMP")
        return buffer.getvalue()
