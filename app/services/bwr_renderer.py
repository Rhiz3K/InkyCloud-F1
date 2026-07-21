"""Black/white/red renderer configuration and palette encoding."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.circuit_metadata import COUNTRY_MAP
from app.services.renderer import Renderer
from app.services.renderer_assets import prepare_color_track_image
from app.services.renderer_theme import make_color_theme
from app.services.spectra6_renderer import IMAGES_DIR, Spectra6Renderer
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwr_palette

__all__ = ["BwrColors", "BwrRenderer", "COUNTRY_MAP"]

TRACKS_BWR_DIR = Path(__file__).parent.parent / "assets" / "tracks_bwr"
FLAGS_BWR_DIR = Path(__file__).parent.parent / "assets" / "flags_bwr"
FLAGS_FALLBACK_DIR = Path(__file__).parent.parent / "assets" / "flags_processed"


class BwrColors:
    """Palette colors and indices used by black/white/red renderers."""

    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xFF, 0x00, 0x00)
    PALETTE = [BLACK, WHITE, RED]
    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2


BWR_THEME = make_color_theme(
    colors=BwrColors,
    track_directory=lambda: TRACKS_BWR_DIR,
    flags_directories=lambda: (FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR),
    images_directory=lambda: IMAGES_DIR,
    prepare_track_image=lambda image, width, height, _logger: prepare_color_track_image(
        image, width, height
    ),
)


class BwrRenderer(Spectra6Renderer):
    """Renderer for generating black/white/red indexed BMP images."""

    THEME = BWR_THEME

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Prepare team logos with a mono-friendly Sauber variant."""
        prepared = super()._prepare_team_logo(team_key, img)
        if team_key == "sauber":
            return Renderer.normalize_sauber_logo_for_non_spectra(prepared)
        return prepared

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Map the RGB canvas to the strict B/W/R palette and encode 4-bit BMP."""
        indexed = map_to_bwr_palette(
            image,
            self.colors.PALETTE,
            black_index=self.colors.IDX_BLACK,
            white_index=self.colors.IDX_WHITE,
            red_index=self.colors.IDX_RED,
        )
        return encode_indexed_bmp_4bit(indexed, self.colors.PALETTE)
