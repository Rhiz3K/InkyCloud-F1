"""Spectra 6 Color E-Ink Renderer for 7.3" display (800x480, 6 colors)."""

import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData, TeamsData
from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import (
    CJK_LANG_CODES,
    fit_brand_font_box,
    fit_ui_font,
    load_brand_font,
    load_ui_font,
)
from app.services.renderer_common import (
    build_sprint_qualifying_label,
    build_team_header_values,
    clamp_text,
    crop_primary_horizontal_band,
    crop_to_content,
    draw_circuit_stats_block,
    draw_driver_photo,
    draw_new_track_message,
    draw_race_header,
    draw_results_column,
    draw_results_header,
    draw_schedule_section,
    draw_team_logo,
    draw_team_row,
    draw_teams_content,
    draw_teams_header,
    draw_track_section,
    fit_result_text,
    format_points,
    format_schedule_session_name,
    format_team_driver_display_name,
    get_team_logo_key,
    get_text_y,
    load_racing_font,
    load_symbol_icon_font,
    load_weather_icon_font,
    normalize_session_name,
    normalize_team_power_unit,
    prepare_color_track_image,
    right_align_x,
    split_teams_for_columns,
    text_width,
    translate_session_name,
)
from app.services.track_assets import build_track_stem_candidates, resolve_track_source_path
from app.services.weather_service import RAINDROP_ICON, WeatherData

logger = logging.getLogger(__name__)

CIRCUITS_DATA_PATH = Path(__file__).parent.parent / "assets" / "circuits_data.json"
TRACKS_SPECTRA6_DIR = Path(__file__).parent.parent / "assets" / "tracks_spectra6"

try:
    with open(CIRCUITS_DATA_PATH, "r", encoding="utf-8") as f:
        CIRCUITS_DATA = json.load(f)
except Exception as e:
    logger.warning("Failed to load circuit data: %s", e)
    CIRCUITS_DATA = {}

ASSETS_DIR = Path(__file__).parent.parent / "assets"
TRACKS_DIR = ASSETS_DIR / "tracks"
TRACKS_PROCESSED_DIR = ASSETS_DIR / "tracks_processed"
IMAGES_DIR = ASSETS_DIR / "images"
FLAGS_DIR = ASSETS_DIR / "flags_spectra6"
TEAMS_COLOR_DIR = IMAGES_DIR / "teams_color"

TEXT_BASELINE_REF = "ÁŽÝgy"


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


class Spectra6Renderer:
    """Renderer for generating 6-color images for Spectra 6 E-Ink displays."""

    _cached_driver_photos: dict[str, Image.Image] | None = None
    _cached_driver_photos_key: str | None = None
    _cached_team_logos: dict[str, Image.Image] | None = None
    _cached_team_logos_key: tuple[str, str] | None = None

    def __init__(self, translator: dict, lang_code: str = "en"):
        """Initialize the Spectra 6 renderer, fonts, and layout constants."""
        self.width = config.DISPLAY_WIDTH
        self.height = config.DISPLAY_HEIGHT
        self.translator = translator
        self.lang_code = lang_code
        self.colors = Spectra6Colors
        self._racing_fonts = {22: self._load_racing_font(22)}

        self.fonts = {
            "header_title": self._load_font(36, bold=True),
            "header_subtitle": self._load_font(36, bold=True),
            "race_name": self._load_font(20, bold=True),
            "circuit_name": self._load_font(18, bold=True),
            "circuit_location": self._load_font(14),
            "circuit_location_bold": self._load_font(14, bold=True),
            "schedule_title": self._load_font(24, bold=True),
            "schedule_row": self._load_font(20),
            "schedule_row_bold": self._load_font(20, bold=True),
            "results_title": self._load_font(18, bold=True),
            "results_year": self._load_font(36, bold=True),
            "results_row": self._load_font(16),
            "footer": self._load_font(12),
            "circuit_stats": self._load_font(18),
            "circuit_stats_value": self._load_font(18, bold=True),
            "icon": self._load_icon_font(22),
            "icon_small": self._load_icon_font(22),
            "weather": self._load_font(12, bold=True),
            "weather_icon": self._load_icon_font(40),
            "weather_icon_font": self._load_weather_icon_font(22),
            "driver_number": self._racing_fonts[22],
        }

        self._driver_photos: dict[str, Image.Image] | None = None
        self._team_logos: dict[str, Image.Image] | None = None

        self.layout = {
            "header_height": 90,
            "header_split_x": 230,
            "header_padding_x": 15,
            "content_y_start": 105,
            "left_column_width": 500,
            "right_column_x": 510,
            "track_padding": 10,
            "track_map_max_height": 160,
            "track_title_y_offset": 5,
            "schedule_title_y": 88,
            "schedule_start_y": 127,
            "schedule_row_height": 22,
            "schedule_date_x": 510,
            "schedule_day_x": 575,
            "schedule_time_x": 620,
            "schedule_name_x": 680,
            "results_y_start": 385,
            "results_col1_x": 109,
            "results_col2_x": 455,
            "results_time_offset": 260,
            "results_row_height": 20,
            "results_title_y_offset": 5,
            "results_data_y_offset": 4,
            "circuit_stats_y": 320,
            "circuit_stats_row_height": 24,
            "driver_name_padding": 4,
            "padding": 15,
            "separator_width": 2,
        }

    def render_calendar(
        self,
        race_data: dict,
        historical_data: HistoricalData | None = None,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> bytes:
        """Render the main calendar screen as a Spectra 6 BMP."""
        image = Image.new("RGB", (self.width, self.height), self.colors.WHITE)
        draw = ImageDraw.Draw(image)

        self._draw_header(draw, image, race_data)
        self._draw_track_section(draw, image, race_data)
        schedule_bottom = self._draw_schedule_section(draw, race_data, weather_data, weather_type)
        self._draw_circuit_stats(draw, race_data, schedule_bottom)
        self._draw_results_section(draw, image, race_data, historical_data)

        return self._to_indexed_bmp(image)

    def render_teams_drivers(self, teams_data: TeamsData) -> bytes:
        """Render the teams and drivers dashboard as a Spectra 6 BMP."""
        self._ensure_teams_assets()
        image = Image.new("RGB", (self.width, self.height), self.colors.WHITE)
        draw = ImageDraw.Draw(image)

        self._draw_teams_header(draw, image, teams_data.season)
        self._draw_teams_content(image, draw, teams_data.teams)

        return self._to_indexed_bmp(image)

    def render_error(self, error_message: str) -> bytes:
        """Render an error placeholder image for Spectra 6 displays."""
        image = Image.new("RGB", (self.width, self.height), self.colors.WHITE)
        draw = ImageDraw.Draw(image)

        error_text = self.translator.get("error", "Error")
        padding = self.layout["padding"]
        draw.text(
            (padding, padding),
            f"{error_text}:",
            fill=self.colors.RED,
            font=self.fonts["schedule_title"],
        )
        draw.text(
            (padding, padding + 50),
            error_message[:60],
            fill=self.colors.BLACK,
            font=self.fonts["schedule_row"],
        )

        return self._to_indexed_bmp(image)

    def _draw_teams_header(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, season: int
    ) -> None:
        """Draw the red teams screen header for the Spectra 6 layout."""
        draw_teams_header(
            draw,
            image,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            season=season,
            title=self.translator.get("teams_drivers_title", "TEAMS & DRIVERS"),
            left_fill=self.colors.WHITE,
            divider_fill=self.colors.RED,
            right_fill=self.colors.RED,
            text_fill=self.colors.WHITE,
            brand_font=self._load_brand_font(36, bold=True),
            subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=self._draw_f1_logo,
        )

    def _draw_teams_content(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, teams: list
    ) -> None:
        """Lay out the team cards into two balanced columns."""
        draw_teams_content(
            image,
            draw,
            teams,
            canvas_width=self.width,
            canvas_height=self.height,
            header_height=self.layout["header_height"],
            draw_team_row_fn=self._draw_team_row,
        )

    @staticmethod
    def _split_teams_for_columns(teams: list) -> tuple[list, list]:
        """Split teams into balanced left and right columns."""
        return split_teams_for_columns(teams)

    def _draw_driver_photo(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        x: int,
        y: int,
        driver_name: str,
        size: int = 18,
        driver_number: int | None = None,
    ) -> int:
        """Draw a driver number or portrait and return the consumed width."""
        self._ensure_teams_assets()
        return draw_driver_photo(
            draw,
            image,
            x=x,
            y=y,
            driver_name=driver_name,
            size=size,
            driver_number=driver_number,
            driver_photos=self._driver_photos,
            get_racing_font_fn=self._get_racing_font,
            number_fill=self.colors.BLACK,
            resample=Image.Resampling.LANCZOS,
            paste_photo_fn=(
                lambda canvas, photo_resized, px, py: canvas.paste(
                    photo_resized, (px, py), photo_resized
                )
            ),
        )

    @staticmethod
    def _get_text_y(
        draw: ImageDraw.ImageDraw,
        font,
        row_h: int,
        row_y: int,
        text: str = "Ay",
    ) -> int:
        """Align text vertically within a row using the provided text metrics."""
        return get_text_y(draw, font, row_h, row_y, text)

    @staticmethod
    def _right_align_x(draw: ImageDraw.ImageDraw, text: str, right_edge: int, font) -> int:
        """Return the x-coordinate that right-aligns text to the given edge."""
        return right_align_x(draw, text, right_edge, font)

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
        """Measure rendered text width for the current draw context."""
        return text_width(draw, text, font)

    @classmethod
    def _clamp_text(cls, draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
        """Clamp text to a maximum width using an ellipsis."""
        return clamp_text(draw, text, font, max_width)

    @staticmethod
    def _build_team_header_values(team) -> tuple[str, str, str, str]:
        """Build normalized constructor header strings for a team card."""
        return build_team_header_values(team)

    @staticmethod
    def _normalize_team_power_unit(constructor: str, power_unit: str | None) -> str:
        """Shorten Red Bull power-unit labels in teams headers."""
        return normalize_team_power_unit(constructor, power_unit)

    @staticmethod
    def _format_team_driver_display_name(name: str) -> str:
        """Format a driver name as `Given SURNAME` for team cards."""
        return format_team_driver_display_name(name)

    def _draw_team_stats_panel_color(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        header_height: int,
        panel_x: int,
        panel_right_x: int,
        team_pos: str,
        team_pts: str,
        stats_font,
        points_font,
        team_position: int | None,
    ) -> int:
        """Draw the shared color position/points panel and return its left x."""
        panel_y = y + 2
        panel_h = header_height - 4
        panel_w = panel_right_x - panel_x
        stats_gap = 4
        pos_col_w = 24
        points_col_w = panel_w - pos_col_w - stats_gap
        pos_box_x = panel_x
        points_box_x = panel_x + pos_col_w + stats_gap
        pos_fill = self.colors.RED if team_position == 1 else self.colors.BLACK

        def draw_panel_stat(
            text: str,
            box_x: int,
            box_w: int,
            font,
            fill: tuple[int, int, int],
            align: str = "center",
        ) -> None:
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_w = int(text_bbox[2] - text_bbox[0])
            text_h = int(text_bbox[3] - text_bbox[1])
            if align == "right":
                text_x = box_x + box_w - 4 - text_w - int(text_bbox[0])
            else:
                text_x = box_x + (box_w - text_w) // 2 - int(text_bbox[0])
            text_y = panel_y + (panel_h - text_h) // 2 - int(text_bbox[1])
            draw.text((text_x, text_y), text, fill=fill, font=font)

        draw.rectangle(
            [(panel_x, panel_y), (panel_right_x, panel_y + panel_h)],
            fill=self.colors.WHITE,
            outline=self.colors.BLACK,
        )
        draw_panel_stat(team_pos, pos_box_x, pos_col_w, stats_font, pos_fill)
        draw_panel_stat(
            team_pts,
            points_box_x,
            points_col_w,
            points_font,
            self.colors.BLACK,
            align="right",
        )
        return pos_box_x

    def _draw_team_driver_row_color(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        driver,
        driver_y: int,
        driver_row_height: int,
        photo_x: int,
        photo_size: int,
        pts_right_x: int,
        driver_pos_x: int,
        badge_pad_x: int,
        small_font,
        driver_font,
    ) -> None:
        """Draw a single color driver row inside a team card."""
        name = driver.name or f"{driver.given_name} {driver.family_name}".strip()
        if not name:
            name = driver.driver_code or "TBA"

        display_name = self._format_team_driver_display_name(name)
        center_y = driver_y + driver_row_height // 2
        driver_small_y = self._get_text_y(draw, small_font, driver_row_height, driver_y)

        photo_y = center_y - photo_size // 2
        self._draw_driver_photo(
            draw,
            image,
            photo_x,
            photo_y,
            name,
            size=photo_size,
            driver_number=driver.driver_number,
        )
        driver_name_x = photo_x + photo_size + self.layout["driver_name_padding"] + 4
        if self.lang_code in CJK_LANG_CODES:
            max_name_width = max(1, driver_pos_x - 8 - driver_name_x)
            driver_font = fit_brand_font_box(
                draw,
                display_name,
                max_width=max_name_width,
                max_height=max(1, driver_row_height - 1),
                base_size=18,
                min_size=12,
                bold=True,
            )
        driver_text_y = self._get_text_y(
            draw, driver_font, driver_row_height, driver_y, display_name
        )
        draw.text(
            (driver_name_x, driver_text_y),
            display_name,
            fill=self.colors.BLACK,
            font=driver_font,
        )

        driver_pts = self._format_points(driver.points)
        pos_text = f"P{driver.position}" if driver.position else "—"
        pts_x = self._right_align_x(draw, driver_pts, pts_right_x, small_font)
        draw.text((pts_x, driver_small_y), driver_pts, fill=self.colors.BLACK, font=small_font)

        if driver.position and driver.position <= 4:
            pos_bbox = draw.textbbox((0, 0), pos_text, font=small_font)
            pos_w = pos_bbox[2] - pos_bbox[0]
            pos_h = pos_bbox[3] - pos_bbox[1]
            badge_pad_y = 3
            badge_w = int(pos_w) + badge_pad_x * 2
            badge_h = int(pos_h) + badge_pad_y * 2
            badge_x = driver_pos_x - badge_pad_x
            badge_y = driver_y + (driver_row_height - badge_h) // 2
            badge_fill = (
                self.colors.RED
                if driver.position == 1
                else self.colors.BLACK
                if driver.position in {2, 3}
                else self.colors.WHITE
            )
            draw.rectangle(
                [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
                fill=badge_fill,
                outline=self.colors.BLACK,
            )
            draw.text(
                (badge_x + badge_pad_x, badge_y + badge_pad_y - pos_bbox[1]),
                pos_text,
                fill=self.colors.WHITE if driver.position in {1, 2, 3} else self.colors.BLACK,
                font=small_font,
            )
            return

        draw.text((driver_pos_x, driver_small_y), pos_text, fill=self.colors.BLACK, font=small_font)

    def _draw_team_row(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        x_start: int,
        y: int,
        x_end: int,
        team,
        row_height: int,
    ) -> None:
        """Draw a single Spectra 6 team card."""
        if self.lang_code in CJK_LANG_CODES:
            team_font = self._load_brand_font(18, bold=True)
            small_font = self._load_brand_font(18, bold=True)
            driver_font = self._load_brand_font(18, bold=True)
            tech_font = self._load_brand_font(14)
            stats_font = self._load_brand_font(18, bold=True)
            points_font = self._load_brand_font(18, bold=True)
        else:
            team_font = self.fonts["circuit_name"]
            small_font = self.fonts["circuit_stats_value"]
            driver_font = self.fonts["circuit_name"]
            tech_font = (
                self.fonts["circuit_location_bold"]
                if len(self.colors.PALETTE) <= 4
                else self.fonts["circuit_location"]
            )
            stats_font = self.fonts["circuit_stats_value"]
            points_font = self.fonts["circuit_stats_value"]

        team_name, meta_text, team_pos, team_pts = self._build_team_header_values(team)

        def draw_team_stats_panel(
            panel_x: int,
            panel_right_x: int,
            header_height: int,
            _badge_pad_x: int,
        ) -> int:
            return self._draw_team_stats_panel_color(
                draw,
                y,
                header_height,
                panel_x,
                panel_right_x,
                team_pos,
                team_pts,
                stats_font,
                points_font,
                team.position,
            )

        def draw_team_driver_row(
            driver,
            driver_y: int,
            driver_row_height: int,
            photo_x: int,
            photo_size: int,
            pts_right_x: int,
            driver_pos_x: int,
            badge_pad_x: int,
        ) -> None:
            self._draw_team_driver_row_color(
                draw,
                image,
                driver,
                driver_y,
                driver_row_height,
                photo_x,
                photo_size,
                pts_right_x,
                driver_pos_x,
                badge_pad_x,
                small_font,
                driver_font,
            )

        def draw_team_logo_cb(
            team_obj,
            driver_y_start: int,
            driver_area_height: int,
            logo_container_left: int,
            logo_container_right: int,
        ) -> None:
            self._draw_team_logo(
                image,
                team_obj,
                driver_y_start,
                driver_area_height,
                logo_container_left,
                logo_container_right,
            )

        draw_team_row(
            image,
            draw,
            team,
            x_start=x_start,
            y=y,
            x_end=x_end,
            row_height=row_height,
            team_font=team_font,
            tech_font=tech_font,
            header_fill=self.colors.BLACK,
            header_text_fill=self.colors.WHITE,
            outline_fill=self.colors.BLACK,
            stats_padding=5,
            driver_name_padding=self.layout["driver_name_padding"],
            get_text_y_fn=self._get_text_y,
            build_team_header_values_fn=lambda _team: (team_name, meta_text, team_pos, team_pts),
            clamp_text_fn=self._clamp_text,
            draw_team_stats_panel_fn=draw_team_stats_panel,
            draw_team_driver_row_fn=draw_team_driver_row,
            draw_team_logo_fn=draw_team_logo_cb,
        )

    @staticmethod
    def _get_team_logo_key(constructor: str) -> str | None:
        """Map a constructor name to the corresponding team logo asset key."""
        return get_team_logo_key(constructor)

    def _draw_team_logo(
        self,
        image: Image.Image,
        team,
        driver_area_y: int,
        driver_area_h: int,
        container_left: int,
        container_right: int,
    ) -> None:
        """Draw a centered team logo inside the reserved card area."""
        self._ensure_teams_assets()
        draw_team_logo(
            image,
            team,
            team_logos=self._team_logos,
            get_team_logo_key_fn=self._get_team_logo_key,
            driver_area_y=driver_area_y,
            driver_area_h=driver_area_h,
            container_left=container_left,
            container_right=container_right,
            paste_logo_fn=lambda canvas, logo_resized, x, y: canvas.paste(
                logo_resized,
                (x, y),
                logo_resized,
            ),
        )

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        """Draw the Spectra 6 race header with monochrome logo and red title block."""
        draw_race_header(
            draw,
            image,
            race_data,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            left_fill=self.colors.WHITE,
            divider_fill=self.colors.RED,
            right_fill=self.colors.RED,
            title_fill=self.colors.WHITE,
            header_title_font=self.fonts["header_title"],
            header_subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=self._draw_f1_logo,
        )

    @staticmethod
    def _draw_f1_logo(image: Image.Image, width: int, height: int) -> None:
        """Draw the shared monochrome F1 logo inside the header logo area."""
        logo_path = IMAGES_DIR / "eInkF1logo.jpg"

        if not logo_path.exists():
            logger.warning("F1 logo not found at %s", logo_path)
            return

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

        except Exception as e:
            logger.warning("Failed to load F1 logo: %s", e)

    def _ensure_teams_assets(self) -> None:
        """Lazy-load cached driver and team assets used by the teams screen."""
        if self._driver_photos is None:
            self._driver_photos = self._get_cached_driver_photos()
        if self._team_logos is None:
            self._team_logos = self._get_cached_team_logos()

    def ensure_teams_assets(self) -> None:
        """Public warmup hook for teams assets used outside the renderer."""
        self._ensure_teams_assets()

    def _draw_track_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
    ) -> None:
        """Draw the left-side track map and circuit label for Spectra 6."""
        draw_track_section(
            draw,
            image,
            race_data,
            left_column_width=self.layout["left_column_width"],
            results_y_start=self.layout["results_y_start"],
            padding=self.layout["padding"],
            label_font=self.fonts["circuit_name"],
            label_fill=self.colors.BLACK,
            load_track_image_fn=self._load_track_image,
            prepare_track_image_fn=prepare_color_track_image,
            paste_track_image_fn=(
                lambda canvas, prepared_image, px, py: canvas.paste(prepared_image, (px, py))
            ),
            draw_track_placeholder_fn=self._draw_track_placeholder,
        )

    @staticmethod
    def _draw_track_placeholder(
        draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int
    ) -> None:
        """Draw a fallback placeholder when no track image is available."""
        draw.rounded_rectangle(
            [(x + 20, y + 20), (x + width - 20, y + height - 20)],
            radius=20,
            outline=Spectra6Colors.BLACK,
            width=3,
        )

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        """Load the best available Spectra 6 track image for a race."""
        circuit = race_data.get("circuit", {})
        circuit_id = str(circuit.get("circuitId", "") or "")
        location = str(circuit.get("location", "") or "")

        normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
        track_stems = build_track_stem_candidates(normalized_id, circuit_id, location)
        if not track_stems:
            return None

        source_path = resolve_track_source_path(TRACKS_DIR, track_stems, variant_suffix="spectra6")
        if source_path:
            try:
                with Image.open(source_path) as track_image:
                    return track_image.copy()
            except Exception as e:
                logger.warning("Failed to load track %s: %s", source_path, e)

        for stem in track_stems:
            track_path = TRACKS_SPECTRA6_DIR / f"{stem}.bmp"
            if not track_path.exists():
                continue

            try:
                with Image.open(track_path) as track_image:
                    return track_image.copy()
            except Exception as e:
                logger.warning("Failed to load track %s: %s", track_path, e)

        return None

    def _session_palette_color(self, color_name: str) -> tuple[int, int, int]:
        """Return a session accent color, falling back to black when unsupported."""
        return getattr(self.colors, color_name, self.colors.BLACK)

    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the weekend schedule and return the bottom of the countdown area."""
        return draw_schedule_section(
            draw,
            race_data,
            canvas_width=self.width,
            right_column_x=self.layout["right_column_x"],
            schedule_title_y=self.layout["schedule_title_y"],
            schedule_start_y=self.layout["schedule_start_y"],
            schedule_row_height=self.layout["schedule_row_height"],
            results_y_start=self.layout["results_y_start"],
            translator=self.translator,
            lang_code=self.lang_code,
            title_fill=self.colors.BLACK,
            draw_schedule_row_fn=self._draw_schedule_row,
            draw_countdown_box_fn=self._draw_countdown_box,
            weather_data=weather_data,
            weather_type=weather_type,
        )

    def _get_session_color(self, session_name: str) -> tuple[int, int, int]:
        """Return the accent color for a schedule session in the active palette."""
        normalized = session_name.strip().lower()
        if normalized == "race":
            return self._session_palette_color("RED")
        if normalized in {
            "qualifying",
            "q1",
            "q2",
            "q3",
            "sprint qualifying",
            "sprint shootout",
            "shootout",
            "sq1",
            "sq2",
            "sq3",
        }:
            return self._session_palette_color("YELLOW")
        if normalized == "sprint":
            return self._session_palette_color("GREEN")
        if normalized.startswith("fp") or normalized.startswith("practice"):
            return self._session_palette_color("BLUE")
        return self.colors.BLACK

    def _draw_schedule_row(self, draw: ImageDraw.ImageDraw, y: int, event: dict) -> None:
        """Draw a single schedule row with localized labels and session color."""
        dt = event.get("datetime")
        name = event.get("name", "")

        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)

        if dt:
            date_str = dt.strftime("%d.%m.")
            day_key = f"day_{dt.strftime('%a').lower()}"
            day_str = self.translator.get(day_key, dt.strftime("%a"))
            time_str = dt.strftime("%H:%M")
        else:
            date_str = ""
            day_str = ""
            time_str = event.get("display_time", "")

        name_max_width = self.width - self.layout["schedule_name_x"] - 5
        translated_name = self._format_schedule_session_name(draw, name, name_max_width)

        font_reg = self.fonts["schedule_row"]
        font_bold = fit_ui_font(
            draw,
            self.lang_code,
            translated_name,
            max_width=name_max_width,
            base_size=20,
            min_size=15,
            bold=True,
        )

        draw.text(
            (self.layout["schedule_date_x"], y), date_str, fill=self.colors.BLACK, font=font_reg
        )
        draw.text(
            (self.layout["schedule_day_x"], y), day_str, fill=self.colors.BLACK, font=font_reg
        )
        draw.text(
            (self.layout["schedule_time_x"], y), time_str, fill=self.colors.BLACK, font=font_reg
        )

        session_color = self._get_session_color(name)
        draw.text(
            (self.layout["schedule_name_x"], y),
            translated_name,
            fill=session_color,
            font=font_bold,
        )

    def _format_schedule_session_name(
        self,
        draw: ImageDraw.ImageDraw,
        name: str,
        max_width: int,
    ) -> str:
        """Return the best-fitting localized schedule label for a session."""
        return format_schedule_session_name(draw, name, max_width, self.lang_code, self.translator)

    def _build_sprint_qualifying_label(self, *, abbreviated: bool) -> str:
        """Compose the sprint qualifying label from the localized sprint and qualifying text."""
        return build_sprint_qualifying_label(
            self.translator,
            self.lang_code,
            abbreviated=abbreviated,
        )

    def _abbreviate_schedule_term(self, term: str) -> str:
        """Reduce a localized schedule term to its leading letter or character."""
        stripped = term.strip()
        if not stripped:
            return term
        first_char = stripped[0]
        if self.lang_code in CJK_LANG_CODES:
            return first_char
        return f"{first_char}."

    def _translate_session_name(self, name: str) -> str:
        """Translate session names while normalizing API/static variants."""
        return translate_session_name(name, self.translator, self.lang_code)

    @staticmethod
    def _normalize_session_name(name: str) -> str:
        """Normalize API/static session variants to a stable translation key suffix."""
        return normalize_session_name(name)

    def _draw_countdown_box(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the countdown/status box and return its bottom y-coordinate."""
        is_cancelled = race_data.get("is_cancelled", False)
        schedule = race_data.get("schedule", [])
        race_dt = None
        for event in schedule:
            if event.get("name", "").lower() == "race":
                dt = event.get("datetime")
                if isinstance(dt, str):
                    race_dt = datetime.fromisoformat(dt)
                elif isinstance(dt, datetime):
                    race_dt = dt
                break

        if not is_cancelled and not race_dt:
            return schedule_bottom

        font = self.fonts["schedule_row_bold"]
        font_icon = self.fonts["icon_small"]
        font_weather_icon = self.fonts["weather_icon_font"]
        ref_bbox = draw.textbbox((0, 0), TEXT_BASELINE_REF, font=font)
        text_height = ref_bbox[3] - ref_bbox[1]

        padding_y = 3
        padding_x = 12
        box_height = text_height + 2 * padding_y

        x_left = self.layout["right_column_x"]
        x_right = self.width - 5

        stats_row_height = self.layout["circuit_stats_row_height"]
        stats_top_y = self.layout["results_y_start"] - 3 - (3 * stats_row_height)
        available_height = stats_top_y - schedule_bottom
        y_top = schedule_bottom + (available_height - box_height) // 2
        y_bottom = y_top + box_height

        draw.rectangle(
            [x_left, y_top, x_right, y_bottom],
            fill=self.colors.RED,
            outline=self.colors.RED,
        )

        text_y = y_top + padding_y - ref_bbox[1]

        status_text = None
        if is_cancelled:
            status_text = self.translator.get("cancelled", "CANCELLED")
        else:
            if race_dt is None:
                return schedule_bottom

            active_race_dt = race_dt
            now = datetime.now(active_race_dt.tzinfo) if active_race_dt.tzinfo else datetime.now()
            delta = active_race_dt - now

            if delta.total_seconds() <= 0:
                status_key = (
                    "race_ongoing"
                    if now < active_race_dt + timedelta(hours=3)
                    else "race_completed"
                )
                status_text = self.translator.get(
                    status_key,
                    "IN PROGRESS" if status_key == "race_ongoing" else "COMPLETED",
                )

        if status_text:
            show_weather = weather_data is not None and not is_cancelled
            status_bbox = draw.textbbox((0, 0), status_text, font=font)
            status_w = status_bbox[2] - status_bbox[0]
            if show_weather:
                text_x = x_left + padding_x
            else:
                text_x = x_left + ((x_right - x_left) - status_w) // 2
            draw.text((text_x, text_y), status_text, fill=self.colors.WHITE, font=font)
            if not show_weather:
                return int(y_bottom)

            temp_str = f"{weather_data.temp_display} "
            precip_str = weather_data.precip_display

            weather_icon_bbox = draw.textbbox((0, 0), weather_data.icon, font=font_weather_icon)
            weather_icon_w = weather_icon_bbox[2] - weather_icon_bbox[0]
            temp_bbox = draw.textbbox((0, 0), temp_str, font=font)
            temp_w = temp_bbox[2] - temp_bbox[0]
            rain_icon_bbox = draw.textbbox((0, 0), RAINDROP_ICON, font=font_weather_icon)
            rain_icon_w = rain_icon_bbox[2] - rain_icon_bbox[0]
            precip_bbox = draw.textbbox((0, 0), precip_str, font=font)
            precip_w = precip_bbox[2] - precip_bbox[0]

            total_w = weather_icon_w + 4 + temp_w + rain_icon_w + 3 + precip_w
            cur_x = x_right - padding_x - total_w

            draw.text(
                (cur_x, text_y), weather_data.icon, fill=self.colors.WHITE, font=font_weather_icon
            )
            cur_x += weather_icon_w + 4
            draw.text((cur_x, text_y), temp_str, fill=self.colors.WHITE, font=font)
            cur_x += temp_w
            draw.text(
                (cur_x, text_y), RAINDROP_ICON, fill=self.colors.WHITE, font=font_weather_icon
            )
            cur_x += rain_icon_w + 3
            draw.text((cur_x, text_y), precip_str, fill=self.colors.WHITE, font=font)
            return int(y_bottom)

        if race_dt is None:
            return schedule_bottom
        active_race_dt = race_dt
        now = datetime.now(active_race_dt.tzinfo) if active_race_dt.tzinfo else datetime.now()
        delta = active_race_dt - now

        if delta.total_seconds() <= 0:
            return schedule_bottom

        days = delta.days
        hours = delta.seconds // 3600

        flag_icon = "🏁"
        # Use short labels (d/h) for current and race-day aliases.
        if weather_type in ("current", "race_day", "race"):
            days_label = self.translator.get("countdown_days_short", "d")
            hours_label = self.translator.get("countdown_hours_short", "h")
        else:
            days_label = self.translator.get("countdown_days", "days")
            hours_label = self.translator.get("countdown_hours", "hours")
        countdown_str = f"{days} {days_label} {hours} {hours_label}"

        flag_bbox = draw.textbbox((0, 0), flag_icon, font=font_icon)
        flag_w = flag_bbox[2] - flag_bbox[0]
        countdown_bbox = draw.textbbox((0, 0), countdown_str, font=font)
        countdown_w = countdown_bbox[2] - countdown_bbox[0]
        total_content_w = flag_w + 6 + countdown_w

        if weather_data:
            cur_x = x_left + padding_x
        else:
            box_width = x_right - x_left
            cur_x = x_left + (box_width - total_content_w) // 2

        draw.text((cur_x, text_y), flag_icon, fill=self.colors.WHITE, font=font_icon)
        cur_x += flag_w + 6
        draw.text((cur_x, text_y), countdown_str, fill=self.colors.WHITE, font=font)

        if weather_data:
            temp_str = f"{weather_data.temp_display} "
            precip_str = weather_data.precip_display

            weather_icon_bbox = draw.textbbox((0, 0), weather_data.icon, font=font_weather_icon)
            weather_icon_w = weather_icon_bbox[2] - weather_icon_bbox[0]
            temp_bbox = draw.textbbox((0, 0), temp_str, font=font)
            temp_w = temp_bbox[2] - temp_bbox[0]
            rain_icon_bbox = draw.textbbox((0, 0), RAINDROP_ICON, font=font_weather_icon)
            rain_icon_w = rain_icon_bbox[2] - rain_icon_bbox[0]
            precip_bbox = draw.textbbox((0, 0), precip_str, font=font)
            precip_w = precip_bbox[2] - precip_bbox[0]

            total_w = weather_icon_w + 4 + temp_w + rain_icon_w + 3 + precip_w
            cur_x = x_right - padding_x - total_w

            draw.text(
                (cur_x, text_y), weather_data.icon, fill=self.colors.WHITE, font=font_weather_icon
            )
            cur_x += weather_icon_w + 4
            draw.text((cur_x, text_y), temp_str, fill=self.colors.WHITE, font=font)
            cur_x += temp_w
            draw.text(
                (cur_x, text_y), RAINDROP_ICON, fill=self.colors.WHITE, font=font_weather_icon
            )
            cur_x += rain_icon_w + 3
            draw.text((cur_x, text_y), precip_str, fill=self.colors.WHITE, font=font)

        return int(y_bottom)

    def _draw_circuit_stats(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
    ) -> None:
        """Draw the right-column circuit facts between schedule and results."""
        circuit_id = race_data.get("circuit", {}).get("circuitId", "")
        mapped_id = CIRCUIT_ID_MAP.get(circuit_id, circuit_id)
        circuit_data = CIRCUITS_DATA.get(mapped_id, {})

        draw_circuit_stats_block(
            draw,
            circuit_data,
            translator=self.translator,
            results_y_start=self.layout["results_y_start"],
            right_column_x=self.layout["right_column_x"],
            canvas_width=self.width,
            row_height=self.layout["circuit_stats_row_height"],
            font_icon=self.fonts["icon_small"],
            font_value=self.fonts["circuit_stats_value"],
            fill=self.colors.BLACK,
        )

    def _draw_results_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
        historical_data: HistoricalData | None,
    ) -> None:
        """Draw the footer historical results section."""
        y_start = self.layout["results_y_start"]

        draw.line(
            [(0, y_start), (self.width, y_start)],
            fill=self.colors.RED,
            width=self.layout["separator_width"],
        )

        if historical_data is None or historical_data.is_new_track:
            self._draw_new_track_message(draw, y_start)
            return

        season = historical_data.season or ""
        country = race_data.get("circuit", {}).get("country", "")
        visual_top = self._draw_results_header(draw, image, y_start, season, country)

        self._draw_results_column(
            draw,
            self.layout["results_col1_x"],
            visual_top,
            self.translator.get("qualifying", "QUALIFYING"),
            historical_data.qualifying_results,
            is_qualifying=True,
        )

        self._draw_results_column(
            draw,
            self.layout["results_col2_x"],
            visual_top,
            self.translator.get("race", "RACE"),
            historical_data.race_results,
            is_qualifying=False,
        )

    def _draw_new_track_message(self, draw: ImageDraw.ImageDraw, y_start: int) -> None:
        """Draw a centered new-track message when historical data is unavailable."""
        draw_new_track_message(
            draw,
            canvas_width=self.width,
            y_start=y_start,
            message=self.translator.get("new_track", "NEW TRACK"),
            font=self.fonts["schedule_title"],
            fill=self.colors.BLACK,
        )

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        """Draw the year and optional country flag for the results footer."""
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
            flags_dir=FLAGS_DIR,
            prepare_flag_image=lambda opened_flag: opened_flag.convert("RGB"),
            logger=logger,
        )

    def _draw_results_column(
        self,
        draw: ImageDraw.ImageDraw,
        x_start: int,
        visual_top: int,
        title: str,
        results: list,
        is_qualifying: bool,
    ) -> None:
        """Draw one historical results column aligned with the footer header."""
        draw_results_column(
            draw,
            x_start=x_start,
            visual_top=visual_top,
            title=title,
            results=results,
            is_qualifying=is_qualifying,
            font_title=self.fonts["results_title"],
            font_row=self.fonts["results_row"],
            time_x=x_start + self.layout["results_time_offset"],
            row_height=self.layout["results_row_height"],
            data_y_offset=self.layout["results_data_y_offset"],
            text_fill=self.colors.BLACK,
            fit_result_text_fn=self._fit_text,
            split_position_prefix=True,
        )

    @staticmethod
    def _fit_text(
        draw: ImageDraw.ImageDraw,
        font: FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
        pos: int,
        driver: str,
        team: str,
    ) -> str:
        """Fit historical results text into the available width."""
        return fit_result_text(draw, font, max_width, pos, driver, team)

    def _load_font(self, size: int, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the main UI font for the active locale."""
        return load_ui_font(self.lang_code, size, bold=bold)

    @staticmethod
    def _load_brand_font(size: int, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the default Latin UI font used for non-localized text."""
        return load_brand_font(size, bold=bold)

    @staticmethod
    def _load_icon_font(size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the fallback icon font used for symbols."""
        return load_symbol_icon_font(size, logger)

    def _load_weather_icon_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the weather icon font with a symbol fallback."""
        return load_weather_icon_font(size, logger, self._load_icon_font)

    def _load_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the stylized racing number font used for driver numbers."""
        return load_racing_font(size, logger, self._load_font)

    def _get_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Return a cached racing-style font at the requested size."""
        if size not in self._racing_fonts:
            self._racing_fonts[size] = self._load_racing_font(size)
        return self._racing_fonts[size]

    @staticmethod
    def _format_points(value: float | int | None) -> str:
        """Format points while preserving half-points for display."""
        return format_points(value)

    @staticmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """Load driver portraits used by the teams screen."""
        drivers_dir = IMAGES_DIR / "drivers"
        photos: dict[str, Image.Image] = {}

        if not drivers_dir.exists():
            return photos

        for photo_path in drivers_dir.glob("*.png"):
            try:
                with Image.open(photo_path) as opened_photo:
                    photos[photo_path.stem.lower()] = opened_photo.convert("RGBA")
            except Exception as e:
                logger.warning("Failed to load driver photo %s: %s", photo_path, e)

        return photos

    @classmethod
    def _get_cached_driver_photos(cls) -> dict[str, Image.Image]:
        """Return the process-wide cache of color driver portraits."""
        cache_key = str(IMAGES_DIR)
        if cls._cached_driver_photos is None or cls._cached_driver_photos_key != cache_key:
            cls._cached_driver_photos = cls._load_driver_photos()
            cls._cached_driver_photos_key = cache_key
        return cls._cached_driver_photos

    @classmethod
    def _load_team_logos(cls) -> dict[str, Image.Image]:
        """Load and prepare color team logos for Spectra 6 rendering."""
        logos: dict[str, Image.Image] = {}
        search_dirs = [TEAMS_COLOR_DIR, IMAGES_DIR / "teams"]

        for teams_dir in search_dirs:
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
                except Exception as e:
                    logger.warning("Failed to load team logo %s: %s", logo_path, e)

        return logos

    @classmethod
    def _get_cached_team_logos(cls) -> dict[str, Image.Image]:
        """Return the process-wide cache of prepared color team logos."""
        cache_key = (str(IMAGES_DIR), str(TEAMS_COLOR_DIR))
        if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
            cls._cached_team_logos = cls._load_team_logos()
            cls._cached_team_logos_key = cache_key
        return cls._cached_team_logos

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Crop a team logo to visible content and apply team-specific trims."""
        cropped = cls._crop_to_content(img)
        if team_key in {"audi", "cadillac"}:
            return cls._crop_primary_horizontal_band(cropped)
        return cropped

    @staticmethod
    def _crop_to_content(img: Image.Image) -> Image.Image:
        """Crop a logo to visible content, respecting transparency when present."""
        return crop_to_content(img)

    @staticmethod
    def _crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
        """Keep only the dominant upper band for tall stacked logo assets."""
        return crop_primary_horizontal_band(img)

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Convert RGB image to indexed 6-color BMP for Spectra 6 display."""
        palette_flat = []
        for color in self.colors.PALETTE:
            palette_flat.extend(color)

        while len(palette_flat) < 768:
            palette_flat.extend([0, 0, 0])

        palette_image = Image.new("P", (1, 1))
        palette_image.putpalette(palette_flat)

        indexed = image.quantize(colors=6, palette=palette_image, dither=Image.Dither.NONE)

        buffer = io.BytesIO()
        indexed.save(buffer, format="BMP")
        return buffer.getvalue()
