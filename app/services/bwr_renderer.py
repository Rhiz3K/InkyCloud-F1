"""Black/white/red E-Ink renderer for 800x480 calendar images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import FONTS_DIR
from app.services.renderer import Renderer
from app.services.renderer_common import draw_results_header, load_track_image_asset
from app.services.spectra6_renderer import TRACKS_DIR, Spectra6Renderer, logger
from app.services.track_assets import build_track_stem_candidates
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwr_palette

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


class BwrRenderer(Spectra6Renderer):
    """Renderer for generating black/white/red BMP images."""

    def __init__(self, translator: dict, lang_code: str = "en"):
        """Initialize the shared color renderer with the BWR palette."""
        super().__init__(translator, lang_code)
        self.colors = BwrColors  # type: ignore[assignment]

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Prepare team logos, using a mono-friendly Sauber variant outside Spectra 6."""
        prepared = super()._prepare_team_logo(team_key, img)
        if team_key == "sauber":
            return Renderer.normalize_sauber_logo_for_non_spectra(prepared)
        return prepared

    @classmethod
    def _load_variant_track_image(
        cls,
        race_data: dict,
        *,
        variant_suffix: str,
        source_tracks_dir: Path,
        processed_tracks_dir: Path,
    ) -> Image.Image | None:
        """Load a track image, preferring source variants before processed BMP fallbacks."""
        circuit = race_data.get("circuit", {})
        circuit_id = str(circuit.get("circuitId", "") or "")
        location = str(circuit.get("location", "") or "")

        normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
        track_stems = build_track_stem_candidates(normalized_id, circuit_id, location)
        if not track_stems:
            return None

        return load_track_image_asset(
            track_stems,
            source_dir=source_tracks_dir,
            variant_suffix=variant_suffix,
            fallback_dir=processed_tracks_dir,
            logger=logger,
        )

    @classmethod
    def _load_track_image(cls, race_data: dict) -> Image.Image | None:
        """Load the best available BWR track asset for a race."""
        return cls._load_variant_track_image(
            race_data,
            variant_suffix="bwr",
            source_tracks_dir=TRACKS_DIR,
            processed_tracks_dir=TRACKS_BWR_DIR,
        )

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        """Draw a results header using BWR flag assets and palette colors."""
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
            country_map=COUNTRY_MAP,
            flags_dirs=(FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR),
            prepare_flag_image=lambda opened_flag: opened_flag.convert("RGB"),
            logger=logger,
        )

    def _load_weather_icon_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Load the weather icon font, falling back to the generic icon font."""
        font_path = FONTS_DIR / "weathericons-regular-webfont.ttf"
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception as exc:
            logger.warning("Failed to load Weather Icons font: %s", exc)
            return self._load_icon_font(size)

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Convert RGB image to indexed 4-bit BMP optimized for BWR displays."""
        indexed = map_to_bwr_palette(
            image,
            self.colors.PALETTE,
            black_index=self.colors.IDX_BLACK,
            white_index=self.colors.IDX_WHITE,
            red_index=self.colors.IDX_RED,
        )
        return encode_indexed_bmp_4bit(indexed, self.colors.PALETTE)
