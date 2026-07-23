"""Black/white/red/yellow renderer configuration and palette encoding."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PIL import Image

from app.services.bwr_renderer import FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR, BwrRenderer
from app.services.circuit_metadata import COUNTRY_MAP
from app.services.renderer_assets import prepare_color_track_image
from app.services.renderer_theme import make_color_theme
from app.services.spectra6_renderer import IMAGES_DIR
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwry_palette

__all__ = ["COUNTRY_MAP", "BwryColors", "BwryRenderer"]

TRACKS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "tracks_bwry"
FLAGS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "flags_bwry"


class BwryColors:
    """Palette colors and indices used by black/white/red/yellow renderers."""

    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xFF, 0x00, 0x00)
    YELLOW = (0xFF, 0xD8, 0x00)
    PALETTE: ClassVar[list[tuple[int, int, int]]] = [BLACK, WHITE, RED, YELLOW]
    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2
    IDX_YELLOW = 3


BWRY_THEME = make_color_theme(
    colors=BwryColors,
    track_directory=lambda: TRACKS_BWRY_DIR,
    flags_directories=lambda: (FLAGS_BWRY_DIR, FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR),
    images_directory=lambda: IMAGES_DIR,
    prepare_track_image=lambda image, width, height, _logger: prepare_color_track_image(
        image, width, height
    ),
)


class BwryRenderer(BwrRenderer):
    """Renderer for generating black/white/red/yellow indexed BMP images."""

    THEME = BWRY_THEME

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Map the RGB canvas to the strict B/W/R/Y palette and encode 4-bit BMP."""
        indexed = map_to_bwry_palette(
            image,
            self.colors.PALETTE,
            black_index=self.colors.IDX_BLACK,
            white_index=self.colors.IDX_WHITE,
            red_index=self.colors.IDX_RED,
            yellow_index=self.colors.IDX_YELLOW,
        )
        return encode_indexed_bmp_4bit(indexed, self.colors.PALETTE)
