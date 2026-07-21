"""Monochrome renderer and its display-specific asset preparation."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

from app.services.renderer_assets import (
    crop_primary_horizontal_band,
    crop_to_content,
    prepare_mono_track_image,
)
from app.services.renderer_base import RendererBase
from app.services.renderer_theme import make_monochrome_theme

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
TRACKS_PROCESSED_DIR = ASSETS_DIR / "tracks_processed"
IMAGES_DIR = ASSETS_DIR / "images"
FLAGS_DIR = ASSETS_DIR / "flags_processed"
TEAMS_COLOR_DIR = IMAGES_DIR / "teams_color"
MONOCHROME_1BIT_TEAM_LOGOS = {"ferrari", "cadillac", "red_bull"}


class MonoColors:
    """Logical fills for Pillow's one-bit image mode."""

    BLACK = 0
    WHITE = 1
    PALETTE = [BLACK, WHITE]


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
    def _paste_team_logo(canvas: Image.Image, logo: Image.Image, x: int, y: int) -> None:
        """Reduce a resized team logo only after scaling, then paste it."""
        canvas.paste(Renderer._logo_to_1bit(logo), (x, y))

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

    @classmethod
    def _load_team_logos(cls) -> dict[str, Image.Image]:
        """Load source logos, keeping selected hand-tuned monochrome overrides."""
        logos: dict[str, Image.Image] = {}
        for teams_dir in (TEAMS_COLOR_DIR, IMAGES_DIR / "teams"):
            if not teams_dir.exists():
                continue
            for logo_path in teams_dir.glob("*.png"):
                team_key = logo_path.stem.lower()
                if team_key in logos and not (
                    teams_dir.name == "teams" and team_key in MONOCHROME_1BIT_TEAM_LOGOS
                ):
                    continue
                try:
                    with Image.open(logo_path) as opened_logo:
                        img = opened_logo.convert("RGBA")
                    logos[team_key] = cls._prepare_team_logo(team_key, img)
                except Exception as exc:
                    logger.warning("Failed to load team logo %s: %s", logo_path, exc)
        return logos

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Crop a team logo and apply palette-specific trims."""
        cropped = crop_to_content(img, use_binary_mask=True)
        if team_key == "sauber":
            return cls.normalize_sauber_logo_for_non_spectra(cropped)
        if team_key in {"audi", "cadillac"}:
            return crop_primary_horizontal_band(cropped)
        return cropped

    @staticmethod
    def normalize_sauber_logo_for_non_spectra(img: Image.Image) -> Image.Image:
        """Map Sauber's green accent to white while preserving its black background."""
        rgba = img.convert("RGBA")
        normalized = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        for x in range(rgba.width):
            for y in range(rgba.height):
                pixel = rgba.getpixel((x, y))
                if not isinstance(pixel, tuple) or len(pixel) < 4:
                    continue
                red, green, blue, alpha = (int(channel) for channel in pixel[:4])
                if alpha == 0:
                    continue
                fill = (0, 0, 0, alpha) if max(red, green, blue) < 48 else (255, 255, 255, alpha)
                normalized.putpixel((x, y), fill)
        return normalized

    @staticmethod
    def _logo_to_1bit(img: Image.Image) -> Image.Image:
        """Convert a color logo into a high-contrast one-bit bitmap."""
        flattened = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        grayscale = ImageOps.autocontrast(flattened.convert("L"))
        return grayscale.point(lambda pixel: 255 if pixel > 240 else 0).convert("1")

    @staticmethod
    def _to_bmp(image: Image.Image) -> bytes:
        """Convert a Pillow image to BMP bytes."""
        buffer = io.BytesIO()
        image.save(buffer, format="BMP")
        return buffer.getvalue()
