"""Black/white/red E-Ink renderer for 800x480 calendar images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.renderer import Renderer
from app.services.spectra6_renderer import (
    FONTS_DIR,
    IMAGES_DIR,
    TRACKS_DIR,
    Spectra6Renderer,
    logger,
)
from app.services.track_assets import build_track_stem_candidates, resolve_track_source_path
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwr_palette

TRACKS_BWR_DIR = Path(__file__).parent.parent / "assets" / "tracks_bwr"
FLAGS_BWR_DIR = Path(__file__).parent.parent / "assets" / "flags_bwr"
FLAGS_FALLBACK_DIR = Path(__file__).parent.parent / "assets" / "flags_processed"


class BwrColors:
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
        super().__init__(translator, lang_code)
        self.colors = BwrColors
        self.layout["results_flag_max_width_percent"] = 80  # type: ignore[index]
        self.layout["results_flag_gap"] = 3  # type: ignore[index]
        self.layout["results_flag_bottom_padding"] = 4  # type: ignore[index]

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Prepare team logos, using a mono-friendly Sauber variant outside Spectra 6."""
        prepared = super()._prepare_team_logo(team_key, img)
        if team_key == "sauber":
            return Renderer.normalize_sauber_logo_for_non_spectra(prepared)
        return prepared

    @staticmethod
    def _draw_f1_logo(image: Image.Image, width: int, height: int) -> None:
        logo_candidates = [IMAGES_DIR / "eInkF1logo.jpg"]

        for logo_path in logo_candidates:
            if not logo_path.exists():
                continue

            try:
                with Image.open(logo_path) as logo_file:
                    pad = 2
                    target_w = width - (pad * 2)
                    target_h = height - (pad * 2)
                    logo_file.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

                    logo = logo_file.convert("L")
                    threshold = 128
                    logo = logo.point(  # type: ignore[arg-type,operator,misc]
                        lambda p, threshold=threshold: 255 if p > threshold else 0
                    )
                    logo = logo.convert("1").convert("RGB")

                    x = (width - logo.width) // 2
                    y = (height - logo.height) // 2
                    image.paste(logo, (x, y))
                    return
            except Exception as exc:
                logger.warning("Failed to load BWR logo %s: %s", logo_path, exc)

        logger.warning("No BWR-compatible F1 logo found")

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        circuit = race_data.get("circuit", {})
        circuit_id = str(circuit.get("circuitId", "") or "")
        location = str(circuit.get("location", "") or "")

        normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
        track_stems = build_track_stem_candidates(normalized_id, circuit_id, location)
        if not track_stems:
            return None

        source_path = resolve_track_source_path(TRACKS_DIR, track_stems, variant_suffix="bwr")
        if source_path:
            try:
                with Image.open(source_path) as track_image:
                    return track_image.convert("RGB")
            except Exception as exc:
                logger.warning("Failed to load track %s: %s", source_path, exc)

        for stem in track_stems:
            track_path = TRACKS_BWR_DIR / f"{stem}.bmp"
            if not track_path.exists():
                continue

            try:
                with Image.open(track_path) as track_image:
                    return track_image.convert("RGB")
            except Exception as exc:
                logger.warning("Failed to load track %s: %s", track_path, exc)

        return None

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        year_text = str(season)
        year_font = self.fonts["results_year"]
        bbox = draw.textbbox((0, 0), year_text, font=year_font)
        text_width = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        footer_y_start = y_start
        footer_height = self.height - footer_y_start

        iso_code = COUNTRY_MAP.get(country_name, "").lower()

        flag_img: Image.Image | None = None
        if iso_code:
            flag_candidates = [
                FLAGS_BWR_DIR / f"{iso_code}.bmp",
                FLAGS_FALLBACK_DIR / f"{iso_code}.bmp",
            ]
            for flag_path in flag_candidates:
                if not flag_path.exists():
                    continue

                try:
                    with Image.open(flag_path) as opened_flag:
                        flag_img = opened_flag.convert("RGB")
                    break
                except Exception as exc:
                    logger.warning("Failed to load flag %s: %s", flag_path, exc)

        header_area_w = self.layout["results_col1_x"]
        max_flag_width = int(header_area_w * self.layout["results_flag_max_width_percent"] / 100)
        standard_gap = self.layout["results_flag_gap"]
        flag_bottom_padding = self.layout["results_flag_bottom_padding"]

        flag_h = 0
        if flag_img:
            if flag_img.width > max_flag_width:
                ratio = max_flag_width / flag_img.width
                flag_h = int(flag_img.height * ratio)
                flag_img = flag_img.resize((max_flag_width, flag_h), Image.Resampling.NEAREST)
            else:
                flag_h = flag_img.height

        total_block_h_stable = text_h + (standard_gap if flag_h > 0 else 0) + flag_h
        y_offset_stable = (footer_height - total_block_h_stable) // 2
        visual_top = footer_y_start + y_offset_stable

        year_x = (header_area_w - text_width) // 2
        text_y = visual_top - bbox[1]
        draw.text((year_x, text_y), year_text, fill=self.colors.BLACK, font=year_font)

        if flag_img:
            x = (header_area_w - flag_img.width) // 2
            flag_top_y = int(self.height - flag_img.height - flag_bottom_padding)

            image.paste(flag_img, (x, flag_top_y))

            draw.rectangle(
                [
                    x - 1,
                    flag_top_y - 1,
                    x + flag_img.width,
                    flag_top_y + flag_img.height,
                ],
                outline=self.colors.BLACK,
                width=1,
            )

        return int(visual_top)

    def _load_weather_icon_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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
