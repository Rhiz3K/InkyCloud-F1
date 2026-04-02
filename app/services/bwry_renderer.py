"""Black/white/red/yellow E-Ink renderer for 800x480 calendar images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.bwr_renderer import FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR, BwrRenderer
from app.services.renderer_common import draw_results_header
from app.services.spectra6_renderer import TRACKS_DIR, logger
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwry_palette

TRACKS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "tracks_bwry"
FLAGS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "flags_bwry"


class BwryColors:
    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xFF, 0x00, 0x00)
    YELLOW = (0xFF, 0xD8, 0x00)

    PALETTE = [BLACK, WHITE, RED, YELLOW]

    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2
    IDX_YELLOW = 3


class BwryRenderer(BwrRenderer):
    """Renderer for generating black/white/red/yellow BMP images."""

    def __init__(self, translator: dict, lang_code: str = "en"):
        super().__init__(translator, lang_code)
        self.colors = BwryColors

    @classmethod
    def _load_track_image(cls, race_data: dict) -> Image.Image | None:
        return cls._load_variant_track_image(
            race_data,
            variant_suffix="bwry",
            source_tracks_dir=TRACKS_DIR,
            processed_tracks_dir=TRACKS_BWRY_DIR,
        )

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        return draw_results_header(
            draw,
            image,
            canvas_height=self.height,
            header_area_width=self.layout["results_col1_x"],
            y_start=y_start,
            season=season,
            country_name=country_name,
            year_font=self.fonts["results_year"],
            text_fill=self.colors.BLACK,
            outline_fill=self.colors.BLACK,
            country_map={},
            flags_dirs=(FLAGS_BWRY_DIR, FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR),
            prepare_flag_image=lambda opened_flag: opened_flag.convert("RGB"),
            logger=logger,
        )

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Convert RGB image to indexed 4-bit BMP optimized for BWRY displays."""
        indexed = map_to_bwry_palette(
            image,
            self.colors.PALETTE,
            black_index=self.colors.IDX_BLACK,
            white_index=self.colors.IDX_WHITE,
            red_index=self.colors.IDX_RED,
            yellow_index=self.colors.IDX_YELLOW,
        )
        return encode_indexed_bmp_4bit(indexed, self.colors.PALETTE)
