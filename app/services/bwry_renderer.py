"""Black/white/red/yellow E-Ink renderer for 800x480 calendar images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.services.bwr_renderer import BwrRenderer, FLAGS_BWR_DIR, FLAGS_FALLBACK_DIR
from app.services.spectra6_renderer import CIRCUIT_ID_MAP, COUNTRY_MAP, TRACKS_DIR, logger
from app.services.track_assets import build_track_stem_candidates, resolve_track_source_path
from app.utils.bmp import encode_indexed_bmp_4bit, map_to_bwry_palette

TRACKS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "tracks_bwry"
FLAGS_BWRY_DIR = Path(__file__).parent.parent / "assets" / "flags_bwry"


class BwryColors:
    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xD9, 0x18, 0x18)
    YELLOW = (0xF4, 0xD0, 0x2A)

    PALETTE = [BLACK, WHITE, RED, YELLOW]

    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2
    IDX_YELLOW = 3


class BwryRenderer(BwrRenderer):
    """Renderer for generating black/white/red/yellow BMP images."""

    def __init__(self, translator: dict):
        super().__init__(translator)
        self.colors = BwryColors

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        circuit = race_data.get("circuit", {})
        circuit_id = str(circuit.get("circuitId", "") or "")
        location = str(circuit.get("location", "") or "")

        normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
        track_stems = build_track_stem_candidates(normalized_id, circuit_id, location)
        if not track_stems:
            return None

        source_path = resolve_track_source_path(TRACKS_DIR, track_stems, variant_suffix="bwry")
        if source_path:
            try:
                return Image.open(source_path).convert("RGB")
            except Exception as exc:
                logger.warning("Failed to load track %s: %s", source_path, exc)

        for stem in track_stems:
            track_path = TRACKS_BWRY_DIR / f"{stem}.bmp"
            if not track_path.exists():
                continue

            try:
                return Image.open(track_path).convert("RGB")
            except Exception as exc:
                logger.warning("Failed to load track %s: %s", track_path, exc)

        return None

    def _get_session_color(self, session_name: str) -> tuple[int, int, int]:
        normalized = session_name.lower()
        if normalized == "race":
            return self.colors.RED
        if normalized in {"q1", "q2", "q3", "qualifying", "sprint", "sprint qualifying"}:
            return self.colors.YELLOW
        return self.colors.BLACK

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
                FLAGS_BWRY_DIR / f"{iso_code}.bmp",
                FLAGS_BWR_DIR / f"{iso_code}.bmp",
                FLAGS_FALLBACK_DIR / f"{iso_code}.bmp",
            ]
            for flag_path in flag_candidates:
                if not flag_path.exists():
                    continue

                try:
                    flag_img = Image.open(flag_path).convert("RGB")
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
