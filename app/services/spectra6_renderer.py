"""Spectra 6 renderer and its color asset preparation."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image

from app.services.renderer_assets import (
    crop_primary_horizontal_band,
    crop_to_content,
    prepare_color_track_image,
)
from app.services.renderer_base import RendererBase
from app.services.renderer_theme import make_color_theme
from app.utils.bmp import quantize_to_palette

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
TRACKS_SPECTRA6_DIR = ASSETS_DIR / "tracks_spectra6"
IMAGES_DIR = ASSETS_DIR / "images"
FLAGS_DIR = ASSETS_DIR / "flags_spectra6"
TEAMS_COLOR_DIR = IMAGES_DIR / "teams_color"


class Spectra6Colors:
    """Named RGB values and palette indexes for the Spectra 6 display."""

    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xFF, 0x00, 0x00)
    YELLOW = (0xFF, 0xD8, 0x00)
    GREEN = (0x00, 0xD8, 0x00)
    BLUE = (0x00, 0xA8, 0xFF)
    PALETTE = [BLACK, WHITE, RED, YELLOW, BLUE, GREEN]
    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2
    IDX_YELLOW = 3
    IDX_BLUE = 4
    IDX_GREEN = 5


SPECTRA6_THEME = make_color_theme(
    colors=Spectra6Colors,
    track_directory=lambda: TRACKS_SPECTRA6_DIR,
    flags_directories=lambda: FLAGS_DIR,
    images_directory=lambda: IMAGES_DIR,
    prepare_track_image=lambda image, width, height, _logger: prepare_color_track_image(
        image, width, height
    ),
)


class Spectra6Renderer(RendererBase):
    """Renderer for generating six-color indexed BMP images."""

    THEME = SPECTRA6_THEME

    def _new_canvas(self) -> Image.Image:
        """Create an RGB composition canvas in active palette white."""
        return Image.new("RGB", (self.width, self.height), self.colors.WHITE)

    def _encode_image(self, image: Image.Image) -> bytes:
        """Encode the RGB canvas as the target indexed BMP."""
        return self._to_indexed_bmp(image)

    @staticmethod
    def _prepare_f1_logo(image: Image.Image) -> Image.Image:
        """Convert the header logo to monochrome RGB for color canvases."""
        return (
            image.convert("L")
            .point(lambda pixel: 255 if pixel > 128 else 0)
            .convert("1")
            .convert("RGB")
        )

    @staticmethod
    def _paste_driver_photo(canvas: Image.Image, photo: Image.Image, x: int, y: int) -> None:
        """Paste an RGBA driver portrait with its alpha mask."""
        canvas.paste(photo, (x, y), photo)

    @staticmethod
    def _paste_team_logo(canvas: Image.Image, logo: Image.Image, x: int, y: int) -> None:
        """Paste an RGBA team logo with its alpha mask."""
        canvas.paste(logo, (x, y), logo)

    @staticmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """Load color driver portraits used by the teams screen."""
        drivers_dir = IMAGES_DIR / "drivers"
        photos: dict[str, Image.Image] = {}
        if not drivers_dir.exists():
            return photos
        for photo_path in drivers_dir.glob("*.png"):
            try:
                with Image.open(photo_path) as opened_photo:
                    photos[photo_path.stem.lower()] = opened_photo.convert("RGBA")
            except Exception as exc:
                logger.warning("Failed to load driver photo %s: %s", photo_path, exc)
        return photos

    @classmethod
    def _load_team_logos(cls) -> dict[str, Image.Image]:
        """Load and prepare color team logos."""
        logos: dict[str, Image.Image] = {}
        for teams_dir in (TEAMS_COLOR_DIR, IMAGES_DIR / "teams"):
            if not teams_dir.exists():
                continue
            for logo_path in teams_dir.glob("*.png"):
                team_key = logo_path.stem.lower()
                if team_key in logos:
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
        """Crop a logo to visible content and apply team-specific trims."""
        cropped = crop_to_content(img)
        if team_key in {"audi", "cadillac"}:
            return crop_primary_horizontal_band(cropped)
        return cropped

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Quantize RGB pixels through the shared fixed-palette helper."""
        indexed = quantize_to_palette(image, self.colors.PALETTE, len(self.colors.PALETTE))
        buffer = io.BytesIO()
        indexed.save(buffer, format="BMP")
        return buffer.getvalue()
