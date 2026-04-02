"""Image rendering service using Pillow - FoxeeLab style layout."""

import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import ConstructorStanding, DriverStanding, HistoricalData, TeamsData
from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import (
    CJK_LANG_CODES,
    FONTS_DIR,
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
    draw_results_header,
    fit_result_text,
    format_points,
    format_schedule_session_name,
    format_team_driver_display_name,
    get_team_logo_key,
    get_text_y,
    normalize_session_name,
    normalize_team_power_unit,
    right_align_x,
    split_teams_for_columns,
    text_width,
    translate_session_name,
)
from app.services.track_assets import build_track_stem_candidates, resolve_track_source_path
from app.services.weather_service import RAINDROP_ICON, WeatherData

logger = logging.getLogger(__name__)

# Load circuit data
CIRCUITS_DATA_PATH = Path(__file__).parent.parent / "assets" / "circuits_data.json"
try:
    with open(CIRCUITS_DATA_PATH, "r", encoding="utf-8") as f:
        CIRCUITS_DATA = json.load(f)
except Exception as e:
    logger.warning("Failed to load circuit data: %s", e)
    CIRCUITS_DATA = {}

# Asset directories
ASSETS_DIR = Path(__file__).parent.parent / "assets"
TRACKS_DIR = ASSETS_DIR / "tracks"
TRACKS_PROCESSED_DIR = ASSETS_DIR / "tracks_processed"
IMAGES_DIR = ASSETS_DIR / "images"
FLAGS_DIR = ASSETS_DIR / "flags_processed"
TEAMS_COLOR_DIR = IMAGES_DIR / "teams_color"
MONOCHROME_1BIT_TEAM_LOGOS = {"ferrari", "cadillac", "red_bull"}

# Reference text for consistent text positioning across languages
# Contains characters with maximum ascent (diacritics) and descent (g, y)
# Used to ensure texts like "RACE" and "ZÁVOD" align at the same baseline
TEXT_BASELINE_REF = "ÁŽÝgy"


class Renderer:
    """Renderer for generating 1-bit BMP images in FoxeeLab style."""

    _cached_driver_photos: dict[str, Image.Image] | None = None
    _cached_driver_photos_key: str | None = None
    _cached_team_logos: dict[str, Image.Image] | None = None
    _cached_team_logos_key: tuple[str, str] | None = None

    def __init__(self, translator: dict, lang_code: str = "en"):
        """
        Initialize renderer.

        Args:
            translator: Translation dictionary for the current language
        """
        self.width = config.DISPLAY_WIDTH
        self.height = config.DISPLAY_HEIGHT
        self.translator = translator
        self.lang_code = lang_code
        self._racing_fonts = {22: self._load_racing_font(22)}

        # Load fonts - prefer TitilliumWeb, fallback to system fonts
        self.fonts = {
            "header_title": self._load_font(36, bold=True),  # Increased to 36 for main title
            "header_subtitle": self._load_font(36, bold=True),  # Match title size
            "race_name": self._load_font(20, bold=True),
            "circuit_name": self._load_font(18, bold=True),  # Keep regular data font
            "circuit_location": self._load_font(14),
            "schedule_title": self._load_font(24, bold=True),  # Increased from 20
            "schedule_row": self._load_font(20),  # Increased from 18
            "schedule_row_bold": self._load_font(20, bold=True),  # Match size, bold
            "results_title": self._load_font(18, bold=True),  # Slight increase
            "results_year": self._load_font(36, bold=True),  # Double size for year header
            "results_row": self._load_font(16),  # Increased for readability
            "footer": self._load_font(12),
            "circuit_stats": self._load_font(18),
            "circuit_stats_value": self._load_font(18, bold=True),
            "icon": self._load_icon_font(22),
            "icon_small": self._load_icon_font(22),
            "driver_number": self._racing_fonts[22],
            "weather": self._load_font(12, bold=True),
            "weather_icon": self._load_icon_font(40),
            "weather_icon_font": self._load_weather_icon_font(22),
        }

        self._driver_photos: dict[str, Image.Image] | None = None
        self._team_logos: dict[str, Image.Image] | None = None

        # Layout constants (all in pixels)
        self.layout = {
            # Header
            "header_height": 90,
            "header_split_x": 230,
            "header_padding_x": 15,
            # Main content split
            "content_y_start": 105,
            "left_column_width": 500,  # Increased to 500 to maximize map size
            "right_column_x": 510,  # Shifted right to 510
            # Track map area (left column)
            "track_padding": 10,
            "track_map_max_height": 160,
            "track_title_y_offset": 5,
            # Schedule (right column)
            "schedule_title_y": 88,
            "schedule_start_y": 127,
            "schedule_row_height": 22,
            "schedule_date_x": 510,  # Shifted +20px
            "schedule_day_x": 575,  # Shifted +20px
            "schedule_time_x": 620,  # Shifted +20px
            "schedule_name_x": 680,  # Shifted +20px
            # Historical results (footer area)
            "results_y_start": 385,  # Moved up to fit all 3 result rows (was 392)
            "results_col1_x": 109,  # Shifted left another 5px (was 114)
            "results_col2_x": 455,  # Shifted left another 5px (was 460)
            "results_time_offset": 260,  # Increased gap by another 10px (was 250)
            "results_row_height": 20,  # Reduced to 20 for tighter spacing (was 21)
            "results_title_y_offset": 5,
            "results_data_y_offset": 4,  # Reduced to fit content (was 6)
            # Circuit stats (between schedule and results)
            "circuit_stats_y": 320,  # Y position for circuit stats
            "circuit_stats_row_height": 24,  # Height per stat row
            "driver_name_padding": 4,
            # General
            "padding": 15,
            "separator_width": 2,
            # Standings layout
            "standings_header_height": 60,
            "standings_row_height": 38,
            "standings_split_x": 400,
            "standings_col_padding": 10,
            "standings_pos_width": 35,
            "standings_points_width": 60,
        }

    def render_calendar(
        self,
        race_data: dict,
        historical_data: HistoricalData | None = None,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> bytes:
        """Render the main calendar screen as a 1-bit BMP."""
        image = Image.new("1", (self.width, self.height), 1)
        draw = ImageDraw.Draw(image)

        self._draw_header(draw, image, race_data)
        self._draw_track_section(draw, image, race_data)
        schedule_bottom = self._draw_schedule_section(draw, race_data, weather_data, weather_type)
        self._draw_circuit_stats(draw, race_data, schedule_bottom)
        self._draw_results_section(draw, image, race_data, historical_data)

        return self._to_bmp(image)

    def render_standings(
        self,
        driver_standings: list[DriverStanding],
        constructor_standings: list[ConstructorStanding],
        view: str = "split",
        season: int = 2025,
        after_round: int = 0,
    ) -> bytes:
        """Render championship standings as a 1-bit BMP."""
        image = Image.new("1", (self.width, self.height), 1)
        draw = ImageDraw.Draw(image)

        self._draw_standings_header(draw, image, season, after_round)

        if view == "drivers":
            self._draw_driver_standings(draw, driver_standings, full_width=True)
        elif view == "constructors":
            self._draw_constructor_standings(draw, constructor_standings, full_width=True)
        else:
            self._draw_driver_standings(draw, driver_standings, full_width=False)
            self._draw_constructor_standings(draw, constructor_standings, full_width=False)

        return self._to_bmp(image)

    def render_teams_drivers(self, teams_data: TeamsData) -> bytes:
        """Render the teams and drivers dashboard as a 1-bit BMP."""
        self._ensure_teams_assets()
        image = Image.new("1", (self.width, self.height), 1)
        draw = ImageDraw.Draw(image)

        self._draw_teams_header(draw, image, teams_data.season)
        self._draw_teams_content(image, draw, teams_data.teams)

        return self._to_bmp(image)

    def _draw_teams_header(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, season: int
    ) -> None:
        """Draw the red-style teams screen header in monochrome form."""
        header_height = self.layout["header_height"]
        split_x = self.layout["header_split_x"]

        draw.rectangle([(0, 0), (split_x, header_height)], fill=1)
        draw.line([(0, header_height - 1), (split_x, header_height - 1)], fill=0, width=2)
        draw.rectangle([(split_x + 1, 0), (self.width, header_height)], fill=0)

        self._draw_f1_logo(image, split_x, header_height)

        title = self.translator.get("teams_drivers_title", "TEAMS & DRIVERS")
        line1 = f"{season} FIA F1 World Championship"
        line2 = title.upper()

        text_x = split_x + 15
        total_text_height = 80
        start_y = (header_height - total_text_height) // 2 - 5

        draw.text((text_x, start_y), line1, fill=1, font=self._load_brand_font(36, bold=True))
        draw.text((text_x, start_y + 40), line2, fill=1, font=self.fonts["header_subtitle"])

    def _draw_teams_content(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, teams: list
    ) -> None:
        """Lay out the team cards into two balanced columns."""
        header_height = self.layout["header_height"]
        col_padding = 5
        split_x = self.width // 2
        gap = col_padding

        left_teams, right_teams = self._split_teams_for_columns(teams)
        teams_per_col = max(len(left_teams), len(right_teams), 1)
        row_gap = 2
        available_height = self.height - header_height - 8 - (teams_per_col - 1) * row_gap
        row_height = available_height // teams_per_col

        y = header_height + 4

        for team in left_teams:
            self._draw_team_row(image, draw, col_padding, y, split_x - gap // 2, team, row_height)
            y += row_height + row_gap

        y = header_height + 4
        for team in right_teams:
            self._draw_team_row(
                image,
                draw,
                split_x + gap // 2,
                y,
                self.width - col_padding,
                team,
                row_height,
            )
            y += row_height + row_gap

    @staticmethod
    def _split_teams_for_columns(teams: list) -> tuple[list, list]:
        """Split teams into left and right columns while keeping counts balanced."""
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
        """Draw either a driver number or silhouette and return the occupied width."""
        self._ensure_teams_assets()
        surname = driver_name.split()[-1].lower() if driver_name else ""
        if surname in ("jr.", "jr"):
            parts = driver_name.split()
            surname = parts[-2].lower() if len(parts) > 1 else surname
        surname = (
            surname.replace("ü", "u")
            .replace("ö", "o")
            .replace("ä", "a")
            .replace("ß", "ss")
            .replace("é", "e")
            .replace("è", "e")
        )

        if driver_number is not None:
            num_text = str(driver_number)
            font = self._get_racing_font(size)
            bbox = draw.textbbox((0, 0), num_text, font=font)
            text_w = int(bbox[2] - bbox[0])
            text_h = int(bbox[3] - bbox[1])
            text_x = x + max(0, (size - text_w) // 2) - int(bbox[0])
            text_y = y + (size - text_h) // 2 - int(bbox[1])
            draw.text((text_x, text_y), num_text, fill=0, font=font)
            return size

        driver_img = self._driver_photos.get(surname) if self._driver_photos else None
        if driver_img is not None:
            orig_w, orig_h = driver_img.size
            scale = size / orig_h
            new_w = int(orig_w * scale)
            new_h = size
            photo_resized = driver_img.resize((new_w, new_h), Image.Resampling.NEAREST)
            image.paste(photo_resized, (x, y))
            return new_w + 2

        return 0

    def _draw_trophy(
        self, draw: ImageDraw.ImageDraw, x: int, y: int, position: int, size: int = 16
    ) -> int:
        """Draw trophy with position number inside. P1=white/outline, P2-P3=black/filled."""
        w, h = size, size
        cx = x + w // 2

        is_p1 = position == 1
        cup_fill = 1 if is_p1 else 0
        num_fill = 0 if is_p1 else 1

        cup_bottom_width = w // 2
        cup_height = h * 2 // 3
        cup_left_top = x + 1
        cup_right_top = x + w - 1
        cup_left_bottom = x + (w - cup_bottom_width) // 2
        cup_right_bottom = cup_left_bottom + cup_bottom_width

        cup_polygon = [
            (cup_left_top, y),
            (cup_right_top, y),
            (cup_right_bottom, y + cup_height),
            (cup_left_bottom, y + cup_height),
        ]
        draw.polygon(cup_polygon, fill=cup_fill, outline=0)

        handle_size = 3
        draw.arc(
            [(x - handle_size, y + 2), (x + handle_size, y + cup_height - 2)],
            start=90,
            end=270,
            fill=0,
            width=2,
        )
        draw.arc(
            [(x + w - handle_size, y + 2), (x + w + handle_size, y + cup_height - 2)],
            start=-90,
            end=90,
            fill=0,
            width=2,
        )

        stem_width = 3
        stem_left = x + (w - stem_width) // 2
        stem_top = y + cup_height
        stem_bottom = y + h - 3
        draw.rectangle(
            [(stem_left, stem_top), (stem_left + stem_width, stem_bottom)],
            fill=cup_fill,
            outline=0,
        )

        base_width = w - 4
        base_left = x + (w - base_width) // 2
        base_top = y + h - 3
        base_bottom = y + h
        draw.rectangle(
            [(base_left, base_top), (base_left + base_width, base_bottom)],
            fill=cup_fill,
            outline=0,
        )

        num_str = str(position)
        num_bbox = draw.textbbox((0, 0), num_str, font=self.fonts["circuit_stats"])
        num_w = num_bbox[2] - num_bbox[0]
        num_h = num_bbox[3] - num_bbox[1]
        top_offset = num_bbox[1]
        text_x = cx - num_w // 2
        text_y = y + (cup_height - num_h) // 2 - top_offset
        draw.text((text_x, text_y), num_str, fill=num_fill, font=self.fonts["circuit_stats"])

        return w + 4

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
        """Measure rendered text width for the active draw context."""
        return text_width(draw, text, font)

    @classmethod
    def _clamp_text(cls, draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
        """Clamp text to fit into a maximum width using an ellipsis."""
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

    @staticmethod
    def _draw_team_stats_panel_mono(
        draw: ImageDraw.ImageDraw,
        y: int,
        header_height: int,
        panel_x: int,
        panel_right_x: int,
        team_pos: str,
        team_pts: str,
        stats_font,
        points_font,
    ) -> int:
        """Draw the shared monochrome position/points panel and return its left x."""
        panel_y = y + 2
        panel_h = header_height - 4
        panel_w = panel_right_x - panel_x
        stats_gap = 4
        pos_col_w = 24
        points_col_w = panel_w - pos_col_w - stats_gap
        pos_box_x = panel_x
        points_box_x = panel_x + pos_col_w + stats_gap

        def draw_panel_stat(text: str, box_x: int, box_w: int, font, align: str = "center") -> None:
            text_bbox = draw.textbbox((0, 0), text, font=font)
            text_w = int(text_bbox[2] - text_bbox[0])
            text_h = int(text_bbox[3] - text_bbox[1])
            if align == "right":
                text_x = box_x + box_w - 4 - text_w - int(text_bbox[0])
            else:
                text_x = box_x + (box_w - text_w) // 2 - int(text_bbox[0])
            text_y = panel_y + (panel_h - text_h) // 2 - int(text_bbox[1])
            draw.text((text_x, text_y), text, fill=0, font=font)

        draw.rectangle([(panel_x, panel_y), (panel_right_x, panel_y + panel_h)], fill=1, outline=0)
        draw_panel_stat(team_pos, pos_box_x, pos_col_w, stats_font)
        draw_panel_stat(team_pts, points_box_x, points_col_w, points_font, align="right")
        return pos_box_x

    def _draw_team_driver_row_mono(
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
        """Draw a single monochrome driver row inside a team card."""
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
        draw.text((driver_name_x, driver_text_y), display_name, fill=0, font=driver_font)

        driver_pts = self._format_points(driver.points)
        pos_text = f"P{driver.position}" if driver.position else "—"
        pts_x = self._right_align_x(draw, driver_pts, pts_right_x, small_font)
        draw.text((pts_x, driver_small_y), driver_pts, fill=0, font=small_font)

        if driver.position and driver.position <= 4:
            pos_bbox = draw.textbbox((0, 0), pos_text, font=small_font)
            pos_w = pos_bbox[2] - pos_bbox[0]
            pos_h = pos_bbox[3] - pos_bbox[1]
            badge_pad_y = 3
            badge_w = int(pos_w) + badge_pad_x * 2
            badge_h = int(pos_h) + badge_pad_y * 2
            badge_x = driver_pos_x - badge_pad_x
            badge_y = driver_y + (driver_row_height - badge_h) // 2
            is_dark_badge = driver.position in {2, 3}
            draw.rectangle(
                [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
                fill=0 if is_dark_badge else 1,
                outline=0,
            )
            draw.text(
                (badge_x + badge_pad_x, badge_y + badge_pad_y - pos_bbox[1]),
                pos_text,
                fill=1 if is_dark_badge else 0,
                font=small_font,
            )
            return

        draw.text((driver_pos_x, driver_small_y), pos_text, fill=0, font=small_font)

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
        """Draw a single team card with header, drivers, points, and logo."""
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
            tech_font = self.fonts["circuit_location"]
            stats_font = self.fonts["circuit_stats_value"]
            points_font = self.fonts["circuit_stats_value"]

        header_height = 23
        box_y_end = y + row_height - 2
        draw.rectangle([(x_start, y), (x_end, box_y_end)], outline=0, width=1)
        draw.rectangle([(x_start, y), (x_end, y + header_height)], fill=0)

        header_text_y = self._get_text_y(draw, team_font, header_height, y)
        tech_text_y = self._get_text_y(draw, tech_font, header_height, y)
        team_name, meta_text, team_pos, team_pts = self._build_team_header_values(team)

        badge_pad_x = 5
        driver_pos_x = x_end - 72
        panel_x = driver_pos_x - badge_pad_x
        panel_right_x = x_end - 4
        pos_box_x = self._draw_team_stats_panel_mono(
            draw,
            y,
            header_height,
            panel_x,
            panel_right_x,
            team_pos,
            team_pts,
            stats_font,
            points_font,
        )

        name_x = x_start + 4
        draw.text((name_x, header_text_y), team_name, fill=1, font=team_font)

        name_bbox = draw.textbbox((0, 0), team_name, font=team_font)
        name_w = name_bbox[2] - name_bbox[0]
        meta_x = int(name_x + name_w + 8)
        meta_max_w = pos_box_x - meta_x - 6
        meta_text = self._clamp_text(draw, meta_text, tech_font, meta_max_w)
        if meta_text:
            draw.text((meta_x, tech_text_y), meta_text, fill=1, font=tech_font)

        driver_area_height = row_height - header_height - 4
        driver_row_height = driver_area_height // 2
        driver_y_start = y + header_height + 2
        pts_right_x = x_end - 4

        photo_size = driver_row_height - 2
        photo_x = x_start + 4

        sorted_drivers = sorted(team.drivers[:2], key=lambda d: d.position or 99)
        for i, driver in enumerate(sorted_drivers):
            driver_y = driver_y_start + i * driver_row_height
            self._draw_team_driver_row_mono(
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

        logo_container_right = driver_pos_x - 8
        driver_name_base_x = photo_x + photo_size + self.layout["driver_name_padding"] + 4
        logo_container_left = max(driver_name_base_x + 170, logo_container_right - 96)
        self._draw_team_logo(
            image,
            team,
            driver_y_start,
            driver_area_height,
            logo_container_left,
            logo_container_right,
        )

    @staticmethod
    def _get_team_logo_key(constructor: str) -> str | None:
        """Map a constructor name to the corresponding team logo asset key."""
        return get_team_logo_key(constructor)

    def _get_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Return a cached racing-style font at the requested size."""
        if size not in self._racing_fonts:
            self._racing_fonts[size] = self._load_racing_font(size)
        return self._racing_fonts[size]

    @staticmethod
    def _format_points(value: float | int | None) -> str:
        """Format points while preserving half-points for display."""
        return format_points(value)

    def _draw_team_logo(
        self,
        image: Image.Image,
        team,
        driver_area_y: int,
        driver_area_h: int,
        container_left: int,
        container_right: int,
    ) -> None:
        """Draw the team logo centered inside the reserved logo container."""
        self._ensure_teams_assets()
        if not self._team_logos:
            return

        constructor = team.constructor_name or team.entrant or ""
        logo_key = self._get_team_logo_key(constructor)
        if not logo_key:
            return

        logo = self._team_logos.get(logo_key)
        if not logo:
            return

        orig_w, orig_h = logo.size
        container_w = container_right - container_left
        if container_w <= 0:
            return
        max_w = max(1, container_w - 12)
        max_h = driver_area_h - 2

        scale_w = max_w / orig_w
        scale_h = max_h / orig_h
        scale = min(scale_w, scale_h)

        new_w = max(1, int(orig_w * scale))
        new_h = max(1, int(orig_h * scale))

        logo_resized = logo.resize((new_w, new_h), Image.Resampling.LANCZOS)
        logo_bitmap = self._logo_to_1bit(logo_resized)

        logo_x = container_left + (container_w - new_w) // 2
        logo_y = driver_area_y + (driver_area_h - new_h) // 2

        image.paste(logo_bitmap, (logo_x, logo_y))

    def _ensure_teams_assets(self) -> None:
        """Lazy-load cached driver and team assets used by the teams screen."""
        if self._driver_photos is None:
            self._driver_photos = self._get_cached_driver_photos()
        if self._team_logos is None:
            self._team_logos = self._get_cached_team_logos()

    def ensure_teams_assets(self) -> None:
        """Public warmup hook for teams assets used outside the renderer."""
        self._ensure_teams_assets()

    def _draw_standings_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        season: int,
        after_round: int,
    ) -> None:
        """Draw the shared standings screen header."""
        header_height = self.layout["header_height"]
        split_x = self.layout["header_split_x"]

        draw.rectangle([(0, 0), (split_x, header_height)], fill=1)
        draw.line([(0, header_height - 1), (split_x, header_height - 1)], fill=0, width=2)
        draw.rectangle([(split_x + 1, 0), (self.width, header_height)], fill=0)

        self._draw_f1_logo(image, split_x, header_height)

        title = self.translator.get("standings_title", "CHAMPIONSHIP STANDINGS")
        line1 = f"{season} FIA F1 World Championship"
        line2 = title.upper()

        text_x = split_x + 15
        total_text_height = 80
        start_y = (header_height - total_text_height) // 2 - 5

        draw.text((text_x, start_y), line1, fill=1, font=self.fonts["header_title"])
        draw.text((text_x, start_y + 40), line2, fill=1, font=self.fonts["header_subtitle"])

    def _draw_driver_standings(
        self,
        draw: ImageDraw.ImageDraw,
        standings: list[DriverStanding],
        full_width: bool = False,
    ) -> None:
        """Draw the driver standings in split or full-width mode."""
        header_height = self.layout["header_height"]
        col_padding = self.layout["standings_col_padding"] + 5
        pos_width = self.layout["standings_pos_width"]

        if full_width:
            # Dynamic two-column layout for all drivers (20 in 2024-25, 24 from 2026)
            total_drivers = len(standings)
            drivers_per_col = (total_drivers + 1) // 2
            available_height = self.height - header_height - 50
            row_height = min(34, available_height // max(drivers_per_col, 1))
            split_x = self.width // 2

            title_y = header_height + 10
            title = self.translator.get("standings_drivers", "DRIVERS")
            draw.text((col_padding, title_y), title, fill=0, font=self.fonts["schedule_title"])

            y = header_height + 45
            for driver in standings[:drivers_per_col]:
                pos_text = f"{driver.position}."
                draw.text(
                    (col_padding, y),
                    pos_text,
                    fill=0,
                    font=self.fonts["schedule_row_bold"],
                )

                name_x = col_padding + pos_width
                name_text = (
                    f"{driver.driver_name} ({driver.constructor_name})"
                    if driver.constructor_name
                    else driver.driver_name
                )
                draw.text((name_x, y), name_text, fill=0, font=self.fonts["schedule_row"])

                points_text = f"{int(driver.points)}"
                points_x = split_x - col_padding - 40
                draw.text(
                    (points_x, y),
                    points_text,
                    fill=0,
                    font=self.fonts["schedule_row_bold"],
                )

                y += row_height

            draw.line(
                [(split_x, header_height + 40), (split_x, self.height - 10)],
                fill=0,
                width=1,
            )

            right_x = split_x + col_padding
            y = header_height + 45
            for driver in standings[drivers_per_col:]:
                pos_text = f"{driver.position}."
                draw.text((right_x, y), pos_text, fill=0, font=self.fonts["schedule_row_bold"])

                name_x = right_x + pos_width
                name_text = (
                    f"{driver.driver_name} ({driver.constructor_name})"
                    if driver.constructor_name
                    else driver.driver_name
                )
                draw.text((name_x, y), name_text, fill=0, font=self.fonts["schedule_row"])

                points_text = f"{int(driver.points)}"
                points_x = self.width - col_padding - 40
                draw.text(
                    (points_x, y),
                    points_text,
                    fill=0,
                    font=self.fonts["schedule_row_bold"],
                )

                y += row_height
        else:
            # Single column for split view (top 10 only)
            row_height = self.layout["standings_row_height"]
            x_start = col_padding
            col_width = self.layout["standings_split_x"] - col_padding

            title_y = header_height + 10
            title = self.translator.get("standings_drivers", "DRIVERS")
            draw.text((x_start, title_y), title, fill=0, font=self.fonts["schedule_title"])

            y = header_height + 45
            for driver in standings[:10]:
                pos_text = f"{driver.position}."
                draw.text((x_start, y), pos_text, fill=0, font=self.fonts["schedule_row_bold"])

                name_x = x_start + pos_width
                draw.text(
                    (name_x, y),
                    driver.driver_name,
                    fill=0,
                    font=self.fonts["schedule_row"],
                )

                points_text = f"{int(driver.points)}"
                points_x = x_start + col_width - self.layout["standings_points_width"]
                draw.text(
                    (points_x, y),
                    points_text,
                    fill=0,
                    font=self.fonts["schedule_row_bold"],
                )

                y += row_height

            split_x = self.layout["standings_split_x"]
            draw.line(
                [(split_x, header_height), (split_x, self.height)],
                fill=0,
                width=1,
            )

    def _draw_constructor_standings(
        self,
        draw: ImageDraw.ImageDraw,
        standings: list[ConstructorStanding],
        full_width: bool = False,
    ) -> None:
        """Draw the constructor standings in split or full-width mode."""
        header_height = self.layout["header_height"]
        row_height = self.layout["standings_row_height"]
        col_padding = self.layout["standings_col_padding"] + 5
        pos_width = self.layout["standings_pos_width"]

        if full_width:
            x_start = col_padding
            col_width = self.width - (col_padding * 2)
        else:
            x_start = self.layout["standings_split_x"] + col_padding
            col_width = self.width - self.layout["standings_split_x"] - (col_padding * 2)

        title_y = header_height + 10
        title = self.translator.get("standings_constructors", "CONSTRUCTORS")
        draw.text((x_start, title_y), title, fill=0, font=self.fonts["schedule_title"])

        y = header_height + 45
        for _i, constructor in enumerate(standings[:10]):
            pos_text = f"{constructor.position}."
            draw.text((x_start, y), pos_text, fill=0, font=self.fonts["schedule_row_bold"])

            name_x = x_start + pos_width
            draw.text(
                (name_x, y),
                constructor.constructor_name,
                fill=0,
                font=self.fonts["schedule_row"],
            )

            points_text = f"{int(constructor.points)}"
            points_x = x_start + col_width - self.layout["standings_points_width"]
            draw.text((points_x, y), points_text, fill=0, font=self.fonts["schedule_row_bold"])

            y += row_height

    def render_error(self, error_message: str) -> bytes:
        """
        Render an error message as a 1-bit BMP.

        Args:
            error_message: Error message to display

        Returns:
            BMP image as bytes
        """
        image = Image.new("1", (self.width, self.height), 1)
        draw = ImageDraw.Draw(image)

        # Draw error message
        error_text = self.translator.get("error", "Error")
        padding = self.layout["padding"]
        draw.text(
            (padding, padding),
            f"{error_text}:",
            fill=0,
            font=self.fonts["schedule_title"],
        )
        draw.text(
            (padding, padding + 50),
            error_message[:60],
            fill=0,
            font=self.fonts["schedule_row"],
        )

        return self._to_bmp(image)

    # =========================================================================
    # Header Section
    # =========================================================================

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        """Draw the split header with Logo (Left) and Title (Right)."""
        header_height = self.layout["header_height"]
        split_x = self.layout["header_split_x"]

        # Left Header Box (for Logo) - White
        draw.rectangle([(0, 0), (split_x, header_height)], fill=1)

        # Draw black line under logo (bottom of header_height)
        # Extend exactly to split_x
        draw.line([(0, header_height - 1), (split_x, header_height - 1)], fill=0, width=2)

        # Right Header Box (for Title) - Black
        # Start immediately at split_x + 1 to avoid white gap
        draw.rectangle([(split_x + 1, 0), (self.width, header_height)], fill=0)

        # F1 Logo (Left side)
        self._draw_f1_logo(image, split_x, header_height)

        # Race title text (Right side)
        race_name = race_data.get("race_name", "Grand Prix")
        season = race_data.get("season", "")

        line1 = f"{season} FIA F1 World Championship"
        line2 = f"{race_name.upper()}"

        # Positioning - Shifted left due to narrower split
        text_x = split_x + 15
        # Center vertically, shift UP slightly (e.g. -5px)
        total_text_height = 80  # Two lines of 36pt approx
        start_y = (header_height - total_text_height) // 2 - 5

        draw.text((text_x, start_y), line1, fill=1, font=self.fonts["header_title"])
        draw.text((text_x, start_y + 40), line2, fill=1, font=self.fonts["header_subtitle"])

    @staticmethod
    def _draw_f1_logo(image: Image.Image, width: int, height: int) -> None:
        """
        Render the F1 logo centered in the header area.

        Loads, scales to fit, converts to 1-bit, and pastes centered. Logs
        warning if logo missing.

        Parameters:
            image: Destination image for the logo.
            width: Header area width.
            height: Header area height.
        """
        logo_path = IMAGES_DIR / "eInkF1logo.jpg"

        if not logo_path.exists():
            logger.warning("F1 logo not found at %s", logo_path)
            return

        try:
            with Image.open(logo_path) as logo_file:
                # Maximize logo size - minimal padding
                pad = 2
                target_w = width - (pad * 2)
                target_h = height - (pad * 2)

                logo_file.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)

                # Convert to 1-bit
                # Use simplified thresholding
                logo: Image.Image = logo_file.convert("L")
                # Keep black as black (0) and white as white (1) because the
                # destination background is already white.

                # Threshold
                threshold = 128
                logo = logo.point(lambda p: 255 if p > threshold else 0)  # type: ignore[arg-type]
                logo = logo.convert("1")

                # Center it
                x = (width - logo.width) // 2
                y = (height - logo.height) // 2

                image.paste(logo, (x, y))

        except Exception as e:
            logger.warning("Failed to load F1 logo: %s", e)

    # =========================================================================
    # Track Map Section (Left Column)
    # =========================================================================

    def _draw_track_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
    ) -> None:
        """Draw the left-side circuit map and circuit label block."""
        x_start = 0

        circuit = race_data.get("circuit", {})
        circuit_name = circuit.get("name", "Circuit")
        country = circuit.get("country", "").upper()
        city = circuit.get("location", "").upper()

        results_line_y = self.layout["results_y_start"]

        if city:
            label_text = f"{country}, {city} | {circuit_name}"
        else:
            label_text = f"{country} | {circuit_name}"

        label_font_key = "circuit_name"
        label_font = self.fonts[label_font_key]
        label_bbox = draw.textbbox((0, 0), label_text, font=label_font)

        label_y = results_line_y - 3 - label_bbox[3]
        text_visual_top = label_y + label_bbox[1]

        side_margin = 3
        track_top = 92

        track_bottom = text_visual_top - side_margin
        available_height = track_bottom - track_top
        available_width = self.layout["left_column_width"] - (side_margin * 2)

        track_image = self._load_track_image(race_data)

        if track_image:
            is_preprocessed = track_image.mode == "1"

            if not is_preprocessed:
                try:
                    gray = track_image.convert("L")
                    binary = gray.point(lambda p: 255 if p > 128 else 0)  # type: ignore[operator]
                    inverted = ImageOps.invert(binary)
                    bbox = inverted.getbbox()
                    if bbox:
                        track_image = track_image.crop(bbox)
                except Exception as e:
                    logger.warning("Failed to crop track image: %s", e)

            img_w, img_h = track_image.size
            ratio = min(available_width / img_w, available_height / img_h)
            new_size = (int(img_w * ratio), int(img_h * ratio))

            if new_size != (img_w, img_h):
                track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

            if not is_preprocessed:
                track_image = track_image.point(lambda p: 255 if p > 200 else 0)  # type: ignore[operator]
                track_image = track_image.convert("1")

            final_w, final_h = track_image.size
            paste_x = int(side_margin + (available_width - final_w) // 2)
            paste_y = int(track_top + (available_height - final_h) // 2)

            image.paste(track_image, (paste_x, paste_y))
        else:
            self._draw_track_placeholder(
                draw,
                x_start + side_margin,
                track_top,
                int(available_width),
                int(available_height),
            )

        label_x = self.layout["padding"]
        draw.text((label_x, label_y), label_text, fill=0, font=label_font)

    @staticmethod
    def _draw_track_placeholder(
        draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int
    ) -> None:
        """Draw a simple placeholder when track image is not available."""
        draw.rounded_rectangle(
            [(x + 20, y + 20), (x + width - 20, y + height - 20)],
            radius=20,
            outline=0,
            width=3,
        )

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        """Load track image from assets.

        First tries to load pre-processed 1-bit BMP from tracks_processed/,
        falls back to original PNG/JPG from tracks/ if not found.
        """
        circuit = race_data.get("circuit", {})
        circuit_id = str(circuit.get("circuitId", "") or "")
        location = str(circuit.get("location", "") or "")
        normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))

        track_stems = build_track_stem_candidates(normalized_id, circuit_id, location)
        if not track_stems:
            return None

        source_path = resolve_track_source_path(TRACKS_DIR, track_stems, variant_suffix="bw")
        if source_path:
            try:
                with Image.open(source_path) as track_image:
                    return track_image.copy()
            except Exception:
                pass

        # Fall back to pre-processed BMPs only when source artwork is unavailable.
        for stem in track_stems:
            track_path = TRACKS_PROCESSED_DIR / f"{stem}.bmp"
            if not track_path.exists():
                continue

            try:
                with Image.open(track_path) as track_image:
                    return track_image.copy()
            except Exception:
                continue

        # Last resort fallback
        all_processed = list(TRACKS_PROCESSED_DIR.glob("*.bmp"))
        if all_processed:
            try:
                with Image.open(all_processed[0]) as track_image:
                    return track_image.copy()
            except Exception:
                pass

        return None

    # =========================================================================
    # Schedule Section (Right Column)
    # =========================================================================

    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the weekend schedule and return the bottom of the countdown area."""
        x_start = self.layout["right_column_x"]
        y_start = self.layout["schedule_title_y"]

        schedule_title = self.translator.get("weekend_schedule", "WEEKEND SCHEDULE")
        schedule_title_font = fit_ui_font(
            draw,
            self.lang_code,
            schedule_title,
            max_width=self.width - x_start - 5,
            base_size=24,
            min_size=18,
            bold=True,
        )
        draw.text(
            (x_start, y_start),
            schedule_title,
            fill=0,
            font=schedule_title_font,
        )

        schedule = race_data.get("schedule", [])
        row_y = self.layout["schedule_start_y"]
        row_height = self.layout["schedule_row_height"]

        for event in schedule:
            self._draw_schedule_row(draw, row_y, event)
            row_y += row_height

            if row_y > self.layout["results_y_start"] - 80:
                break

        countdown_bottom = self._draw_countdown_box(
            draw, race_data, row_y + 10, weather_data, weather_type
        )

        return countdown_bottom

    def _draw_schedule_row(self, draw: ImageDraw.ImageDraw, y: int, event: dict) -> None:
        """Draw a single schedule row with bold event name."""
        dt = event.get("datetime")
        name = event.get("name", "")

        # Parse ISO string to datetime if needed
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

        # Draw columns
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

        draw.text((self.layout["schedule_date_x"], y), date_str, fill=0, font=font_reg)
        draw.text((self.layout["schedule_day_x"], y), day_str, fill=0, font=font_reg)
        draw.text((self.layout["schedule_time_x"], y), time_str, fill=0, font=font_reg)
        # Event name in BOLD
        draw.text((self.layout["schedule_name_x"], y), translated_name, fill=0, font=font_bold)

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
        """
        Draw countdown box showing time until race and optional weather.

        Parameters:
            draw: Pillow drawing context.
            race_data: Race info with "schedule" list of events.
            schedule_bottom: Y coordinate below schedule area.
            weather_data: Optional weather with icon, temp, precipitation.
            weather_type: Weather display type ("current", "race_day", etc.).

        Returns:
            Bottom Y of drawn box, or schedule_bottom if no upcoming race.
        """
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

        draw.rectangle([x_left, y_top, x_right, y_bottom], fill=0, outline=0)

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
            draw.text((text_x, text_y), status_text, fill=1, font=font)
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

            draw.text((cur_x, text_y), weather_data.icon, fill=1, font=font_weather_icon)
            cur_x += weather_icon_w + 4
            draw.text((cur_x, text_y), temp_str, fill=1, font=font)
            cur_x += temp_w
            draw.text((cur_x, text_y), RAINDROP_ICON, fill=1, font=font_weather_icon)
            cur_x += rain_icon_w + 3
            draw.text((cur_x, text_y), precip_str, fill=1, font=font)
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

        draw.text((cur_x, text_y), flag_icon, fill=1, font=font_icon)
        cur_x += flag_w + 6
        draw.text((cur_x, text_y), countdown_str, fill=1, font=font)

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

            draw.text((cur_x, text_y), weather_data.icon, fill=1, font=font_weather_icon)
            cur_x += weather_icon_w + 4
            draw.text((cur_x, text_y), temp_str, fill=1, font=font)
            cur_x += temp_w
            draw.text((cur_x, text_y), RAINDROP_ICON, fill=1, font=font_weather_icon)
            cur_x += rain_icon_w + 3
            draw.text((cur_x, text_y), precip_str, fill=1, font=font)

        return int(y_bottom)

    # =========================================================================
    # Circuit Stats Section (Between Schedule and Results)
    # =========================================================================

    def _draw_circuit_stats(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
    ) -> None:
        """
        Render circuit stats (length, laps, fastest lap, first GP) under results.

        Parameters:
            draw: Pillow drawing context.
            race_data: Race metadata with circuit.circuitId.
            schedule_bottom: Y coordinate below schedule (for layout context).
        """
        circuit_id = race_data.get("circuit", {}).get("circuitId", "")
        mapped_id = CIRCUIT_ID_MAP.get(circuit_id, circuit_id)
        circuit_data = CIRCUITS_DATA.get(mapped_id, {})

        row_height = self.layout["circuit_stats_row_height"]
        font_value = self.fonts["circuit_stats_value"]

        stats = []

        length = circuit_data.get("circuit_length")
        laps = circuit_data.get("number_of_laps")
        if length:
            line1 = f"{length}"
            if laps:
                line1 += f" | {laps} " + self.translator.get("laps", "laps")
            stats.append(("📏", line1))

        lap_time = circuit_data.get("fastest_lap_time")
        lap_driver = circuit_data.get("fastest_lap_driver")
        lap_year = circuit_data.get("fastest_lap_year")
        if lap_time:
            lap_text = f"{lap_time}"
            if lap_driver:
                last_name = lap_driver.split()[-1] if lap_driver else ""
                lap_text += f" ({last_name}"
                if lap_year:
                    lap_text += f", {lap_year})"
                else:
                    lap_text += ")"
            stats.append(("⚡", lap_text))

        first_gp = circuit_data.get("first_grand_prix")
        if first_gp:
            stats.append(("🗓", f"{self.translator.get('first_gp', 'First GP')}: {first_gp}"))

        if not stats:
            return

        results_line_y = self.layout["results_y_start"]
        total_stats_height = len(stats) * row_height
        y_start = results_line_y - 3 - total_stats_height

        font_icon = self.fonts["icon_small"]

        max_icon_width: float = 0
        for stat in stats:
            icon = stat[0]
            icon_bbox = draw.textbbox((0, 0), icon, font=font_icon)
            icon_width = icon_bbox[2] - icon_bbox[0]
            max_icon_width = max(max_icon_width, icon_width)

        max_text_width: float = 0
        for stat in stats:
            text = stat[1]
            text_bbox = draw.textbbox((0, 0), text, font=font_value)
            text_width = text_bbox[2] - text_bbox[0]
            max_text_width = max(max_text_width, text_width)

        icon_text_gap = 4
        total_block_width = max_icon_width + icon_text_gap + max_text_width

        right_margin = 5
        min_x = self.layout["right_column_x"]
        block_x = max(min_x, self.width - right_margin - total_block_width)
        text_x = block_x + max_icon_width + icon_text_gap

        y = y_start
        for stat in stats:
            icon = stat[0]
            text = stat[1]
            icon_bbox = draw.textbbox((0, 0), icon, font=font_icon)
            icon_width = icon_bbox[2] - icon_bbox[0]
            icon_x = block_x + (max_icon_width - icon_width)
            draw.text((icon_x, y), icon, fill=0, font=font_icon)
            draw.text((text_x, y), text, fill=0, font=font_value)
            y += row_height

    # =========================================================================
    # Historical Results Section (Footer)
    # =========================================================================

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
            fill=0,
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
        """Draw a centered message indicating this is a new track."""
        message = self.translator.get("new_track", "NEW TRACK")
        bbox = draw.textbbox((0, 0), message, font=self.fonts["schedule_title"])
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        y = y_start + 30
        draw.text((x, y), message, fill=0, font=self.fonts["schedule_title"])

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        """
        Render results header with centered year and optional country flag.

        Parameters:
            draw: Draw context for text/shapes.
            image: Image for flag paste.
            y_start: Y where results footer begins.
            season: Year or season label.
            country_name: Country for flag lookup.

        Returns:
            Y coordinate where results columns should align.
        """
        return draw_results_header(
            draw,
            image,
            canvas_height=self.height,
            header_area_width=self.layout["results_col1_x"],
            y_start=y_start,
            season=season,
            country_name=country_name,
            year_font=self.fonts["results_year"],
            text_fill=0,
            outline_fill=0,
            country_map=COUNTRY_MAP,
            flags_dir=FLAGS_DIR,
            prepare_flag_image=lambda opened_flag: opened_flag.copy(),
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
        """Draw a results column aligned with the Year top."""
        # Align header's visual top with Year's visual top
        font_title = self.fonts["results_title"]

        # Use reference text with diacritics for consistent baseline positioning
        # This ensures "RACE" and "ZÁVOD" align at the same vertical position
        ref_bbox = draw.textbbox((0, 0), TEXT_BASELINE_REF, font=font_title)
        header_y_anchor = visual_top - ref_bbox[1]

        # Draw title
        draw.text((x_start, header_y_anchor), title, fill=0, font=font_title)

        time_x = x_start + self.layout["results_time_offset"]

        row_height = self.layout["results_row_height"]
        font = self.fonts["results_row"]

        # Calculate proper position: data starts below headers
        # Use a consistent reference text for header height to ensure both columns align
        ref_bbox = draw.textbbox((0, 0), "Hg", font=font_title)  # Reference with ascender/descender
        header_visual_bottom = header_y_anchor + ref_bbox[3]

        row_bbox = draw.textbbox((0, 0), "1", font=font)
        # Place data below header bottom using configurable offset
        y_rows_start = header_visual_bottom + self.layout["results_data_y_offset"] - row_bbox[1]

        for i, entry in enumerate(results[:3]):
            y = y_rows_start + (i * row_height)

            pos = i + 1
            driver_name = entry.driver.display_name
            team = entry.constructor.name

            if is_qualifying:
                time_str = entry.q3_time or ""
            else:
                time_str = entry.time or ""

            # Calculate available width (offset - gap)
            max_width = self.layout["results_time_offset"] - 10

            text = self._fit_text(draw, font, max_width, pos, driver_name, team)
            draw.text((x_start, y), text, fill=0, font=font)

            if time_str:
                draw.text((time_x, y), time_str, fill=0, font=font)

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _load_font(self, size: int, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the main UI font for the active locale."""
        return load_ui_font(self.lang_code, size, bold=bold)

    @staticmethod
    def _load_brand_font(size: int, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the default Latin UI font used for non-localized text."""
        return load_brand_font(size, bold=bold)

    @staticmethod
    def _load_icon_font(size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the icon fallback font used for symbols and emoji-style glyphs."""
        symbola_path = "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf"
        try:
            return ImageFont.truetype(symbola_path, size)
        except Exception as e:
            logger.warning("Failed to load Symbola font: %s", e)
            return ImageFont.load_default()

    def _load_weather_icon_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the weather icon font with a symbol fallback."""
        font_path = FONTS_DIR / "weathericons-regular-webfont.ttf"
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception as e:
            logger.warning("Failed to load Weather Icons font: %s", e)
            return self._load_icon_font(size)

    def _load_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the stylized racing number font used for driver numbers."""
        font_path = FONTS_DIR / "RacingSansOne-Regular.ttf"
        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception as e:
                logger.warning("Failed to load Racing Sans One: %s", e)
        return self._load_font(size, bold=True)

    @staticmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """
        Load driver silhouettes from assets/drivers and convert to 1-bit masks.

        Returns:
            dict mapping lowercase filename stem to 1-bit PIL Image.
        """
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
                driver_key = photo_path.stem.lower()
                photos[driver_key] = img
            except Exception as e:
                logger.warning("Failed to load driver photo %s: %s", photo_path, e)

        return photos

    @classmethod
    def _get_cached_driver_photos(cls) -> dict[str, Image.Image]:
        """Return the process-wide cache of prepared driver silhouettes."""
        cache_key = str(IMAGES_DIR)
        if cls._cached_driver_photos is None or cls._cached_driver_photos_key != cache_key:
            cls._cached_driver_photos = cls._load_driver_photos()
            cls._cached_driver_photos_key = cache_key
        return cls._cached_driver_photos

    @classmethod
    def _load_team_logos(cls) -> dict[str, Image.Image]:
        """
        Load team logos from assets/teams as cropped source images.

        Returns:
            dict mapping lowercase filename stem to cropped PIL Image.
        """
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
                        img_file = opened_logo.convert("RGBA")
                    logos[team_key] = cls._prepare_team_logo(team_key, img_file)
                except Exception as e:
                    logger.warning("Failed to load team logo %s: %s", logo_path, e)

        return logos

    @classmethod
    def _get_cached_team_logos(cls) -> dict[str, Image.Image]:
        """Return the process-wide cache of prepared team logo source images."""
        cache_key = (str(IMAGES_DIR), str(TEAMS_COLOR_DIR))
        if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
            cls._cached_team_logos = cls._load_team_logos()
            cls._cached_team_logos_key = cache_key
        return cls._cached_team_logos

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Crop a team logo to the content area and apply team-specific trims."""
        cropped = cls._crop_to_content(img)
        if team_key == "sauber":
            return cls.normalize_sauber_logo_for_non_spectra(cropped)
        if team_key in {"audi", "cadillac"}:
            return cls._crop_primary_horizontal_band(cropped)
        return cropped

    @staticmethod
    def _crop_to_content(img: Image.Image) -> Image.Image:
        """Crop a logo to visible content, respecting transparency when present."""
        return crop_to_content(img, use_binary_mask=True)

    @staticmethod
    def _crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
        """Keep only the dominant upper band for tall stacked logo assets."""
        return crop_primary_horizontal_band(img)

    @staticmethod
    def normalize_sauber_logo_for_non_spectra(img: Image.Image) -> Image.Image:
        """Map Sauber's green accent to white while preserving its black background."""
        rgba = img.convert("RGBA")
        normalized = Image.new("RGBA", rgba.size, (0, 0, 0, 0))

        for x in range(rgba.width):
            for y in range(rgba.height):
                r, g, b, a = rgba.getpixel((x, y))
                if a == 0:
                    continue
                if r < 48 and g < 48 and b < 48:
                    normalized.putpixel((x, y), (0, 0, 0, a))
                else:
                    normalized.putpixel((x, y), (255, 255, 255, a))

        return normalized

    @staticmethod
    def _logo_to_1bit(img: Image.Image) -> Image.Image:
        """Convert a color logo into a high-contrast 1-bit bitmap."""
        flattened = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        grayscale = ImageOps.autocontrast(flattened.convert("L"))
        return grayscale.point(lambda p: 255 if p > 240 else 0).convert("1")

    @staticmethod
    def _fit_text(
        draw: ImageDraw.ImageDraw,
        font: FreeTypeFont | ImageFont.ImageFont,
        max_width: int,
        pos: int,
        driver: str,
        team: str,
    ) -> str:
        """Fit text into max_width by truncating team then driver."""
        return fit_result_text(draw, font, max_width, pos, driver, team)

    @staticmethod
    def _to_bmp(image: Image.Image) -> bytes:
        """Convert PIL Image to BMP bytes."""
        buffer = io.BytesIO()
        image.save(buffer, format="BMP")
        return buffer.getvalue()
