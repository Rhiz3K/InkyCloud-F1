"""Spectra 6 Color E-Ink Renderer for 7.3" display (800x480, 6 colors)."""

import io
import json
import logging
import math
import re
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData, TeamsData
from app.services.font_utils import FONTS_DIR, fit_ui_font, load_ui_font
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

CIRCUIT_ID_MAP: dict[str, str] = {
    "vegas": "las_vegas",
}

COUNTRY_MAP = {
    "Australia": "au",
    "Austria": "at",
    "Azerbaijan": "az",
    "Bahrain": "bh",
    "Belgium": "be",
    "Brazil": "br",
    "Canada": "ca",
    "China": "cn",
    "France": "fr",
    "Germany": "de",
    "Hungary": "hu",
    "Italy": "it",
    "Japan": "jp",
    "Mexico": "mx",
    "Monaco": "mc",
    "Netherlands": "nl",
    "Portugal": "pt",
    "Qatar": "qa",
    "Russia": "ru",
    "Saudi Arabia": "sa",
    "Singapore": "sg",
    "Spain": "es",
    "Turkey": "tr",
    "UAE": "ae",
    "United Arab Emirates": "ae",
    "UK": "gb",
    "United Kingdom": "gb",
    "USA": "us",
    "United States": "us",
}

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
        header_height = self.layout["header_height"]
        split_x = self.layout["header_split_x"]

        draw.rectangle([(0, 0), (split_x, header_height)], fill=self.colors.WHITE)
        draw.line(
            [(0, header_height - 1), (split_x, header_height - 1)],
            fill=self.colors.RED,
            width=2,
        )
        draw.rectangle([(split_x + 1, 0), (self.width, header_height)], fill=self.colors.RED)

        self._draw_f1_logo(image, split_x, header_height)

        title = self.translator.get("teams_drivers_title", "TEAMS & DRIVERS")
        line1 = f"{season} FIA F1 World Championship"
        line2 = title.upper()

        text_x = split_x + 15
        total_text_height = 80
        start_y = (header_height - total_text_height) // 2 - 5

        draw.text((text_x, start_y), line1, fill=self.colors.WHITE, font=self.fonts["header_title"])
        draw.text(
            (text_x, start_y + 40),
            line2,
            fill=self.colors.WHITE,
            font=self.fonts["header_subtitle"],
        )

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
        """Split teams into balanced left and right columns."""
        if not teams:
            return [], []

        left_count = math.ceil(len(teams) / 2)
        return teams[:left_count], teams[left_count:]

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
            draw.text((text_x, text_y), num_text, fill=self.colors.BLACK, font=font)
            return size

        driver_img = self._driver_photos.get(surname) if self._driver_photos else None
        if driver_img is not None:
            orig_w, orig_h = driver_img.size
            scale = size / orig_h
            new_w = int(orig_w * scale)
            new_h = size
            photo_resized = driver_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            image.paste(photo_resized, (x, y), photo_resized)
            return new_w + 2

        return 0

    @staticmethod
    def _get_text_y(
        draw: ImageDraw.ImageDraw,
        font,
        row_h: int,
        row_y: int,
    ) -> int:
        """Align text vertically within a row using font metrics."""
        bbox = draw.textbbox((0, 0), "Ay", font=font)
        h = bbox[3] - bbox[1]
        top_off = bbox[1]
        return int(row_y + (row_h - h) // 2 - top_off)

    @staticmethod
    def _right_align_x(draw: ImageDraw.ImageDraw, text: str, right_edge: int, font) -> int:
        """Return the x-coordinate that right-aligns text to the given edge."""
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(right_edge - (bbox[2] - bbox[0]))

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
        """Measure rendered text width for the current draw context."""
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])

    @classmethod
    def _clamp_text(cls, draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
        """Clamp text to a maximum width using an ellipsis."""
        if max_width <= 0:
            return ""
        if cls._text_width(draw, text, font) <= max_width:
            return text

        ellipsis = "..."
        trimmed = text
        while trimmed and cls._text_width(draw, trimmed + ellipsis, font) > max_width:
            trimmed = trimmed[:-1]
        return (trimmed + ellipsis) if trimmed else ""

    @staticmethod
    def _build_team_header_values(team) -> tuple[str, str, str, str]:
        """Build normalized constructor header strings for a team card."""
        constructor = team.constructor_name or team.entrant or ""
        team_name = constructor.split("-")[0].replace(" Aramco", "").replace("Kick ", "").strip()
        chassis = team.chassis or ""
        power_unit = team.power_unit.replace("-AMG", "") if team.power_unit else ""
        meta_text = " | ".join(part for part in (chassis, power_unit) if part)
        team_pos = str(team.position) if team.position else "—"
        return team_name, meta_text, team_pos, Spectra6Renderer._format_points(team.points)

    @staticmethod
    def _format_team_driver_display_name(name: str) -> str:
        """Format a driver name as `Given SURNAME` for team cards."""
        name_parts = name.replace(" Jr.", "").replace(" jr.", "").split()
        if len(name_parts) >= 2:
            given = name_parts[0]
            surname = " ".join(name_parts[1:]).upper()
            return f"{given} {surname}"
        return name.upper()

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
        driver_text_y = self._get_text_y(draw, driver_font, driver_row_height, driver_y)
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
        header_fill = self.colors.BLACK

        header_height = 23
        box_y_end = y + row_height - 2
        draw.rectangle([(x_start, y), (x_end, box_y_end)], outline=self.colors.BLACK, width=1)
        draw.rectangle([(x_start, y), (x_end, y + header_height)], fill=header_fill)
        header_text_y = self._get_text_y(draw, team_font, header_height, y)
        tech_text_y = self._get_text_y(draw, tech_font, header_height, y)
        team_name, meta_text, team_pos, team_pts = self._build_team_header_values(team)

        badge_pad_x = 5
        driver_pos_x = x_end - 72
        panel_x = driver_pos_x - badge_pad_x
        panel_right_x = x_end - 4
        pos_box_x = self._draw_team_stats_panel_color(
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

        name_x = x_start + 4
        draw.text((name_x, header_text_y), team_name, fill=self.colors.WHITE, font=team_font)

        name_bbox = draw.textbbox((0, 0), team_name, font=team_font)
        name_w = name_bbox[2] - name_bbox[0]
        meta_x = int(name_x + name_w + 8)
        meta_max_w = pos_box_x - meta_x - 6
        meta_text = self._clamp_text(draw, meta_text, tech_font, meta_max_w)
        if meta_text:
            draw.text((meta_x, tech_text_y), meta_text, fill=self.colors.WHITE, font=tech_font)

        driver_area_height = row_height - header_height - 4
        driver_row_height = driver_area_height // 2
        driver_y_start = y + header_height + 2
        pts_right_x = x_end - 4

        photo_size = driver_row_height - 2
        photo_x = x_start + 4

        sorted_drivers = sorted(team.drivers[:2], key=lambda d: d.position or 99)
        for i, driver in enumerate(sorted_drivers):
            driver_y = driver_y_start + i * driver_row_height
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
        name = constructor.lower()
        if "audi" in name:
            return "audi"
        if "cadillac" in name:
            return "cadillac"
        if "mclaren" in name:
            return "mclaren"
        if "williams" in name:
            return "williams"
        if "aston martin" in name:
            return "aston_martin"
        if (
            name == "rb"
            or name.startswith("rb ")
            or " rb " in name
            or "racing bulls" in name
            or "visa" in name
        ):
            return "racing_bulls"
        if "red bull" in name:
            return "red_bull"
        if "haas" in name:
            return "haas"
        if "sauber" in name or "stake" in name or "kick" in name:
            return "sauber"
        if "alpine" in name:
            return "alpine"
        if "mercedes" in name:
            return "mercedes"
        if "ferrari" in name:
            return "ferrari"
        return None

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

        scale = min(max_w / orig_w, max_h / orig_h)
        new_w = max(1, int(orig_w * scale))
        new_h = max(1, int(orig_h * scale))

        logo_resized = logo.resize((new_w, new_h), Image.Resampling.LANCZOS)
        x = container_left + (container_w - new_w) // 2
        y = driver_area_y + (driver_area_h - new_h) // 2
        image.paste(logo_resized, (x, y), logo_resized)

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        """Draw the Spectra 6 race header with monochrome logo and red title block."""
        header_height = self.layout["header_height"]
        split_x = self.layout["header_split_x"]

        draw.rectangle([(0, 0), (split_x, header_height)], fill=self.colors.WHITE)
        draw.line(
            [(0, header_height - 1), (split_x, header_height - 1)],
            fill=self.colors.RED,
            width=2,
        )
        draw.rectangle([(split_x + 1, 0), (self.width, header_height)], fill=self.colors.RED)

        self._draw_f1_logo(image, split_x, header_height)

        race_name = race_data.get("race_name", "Grand Prix")
        season = race_data.get("season", "")

        line1 = f"{season} FIA F1 World Championship"
        line2 = f"{race_name.upper()}"

        text_x = split_x + 15
        total_text_height = 80
        start_y = (header_height - total_text_height) // 2 - 5

        draw.text((text_x, start_y), line1, fill=self.colors.WHITE, font=self.fonts["header_title"])
        draw.text(
            (text_x, start_y + 40),
            line2,
            fill=self.colors.WHITE,
            font=self.fonts["header_subtitle"],
        )

    @staticmethod
    def _draw_f1_logo(image: Image.Image, width: int, height: int) -> None:
        """Draw the shared monochrome F1 logo inside the header logo area."""
        logo_path = IMAGES_DIR / "eInkF1logo.jpg"

        if not logo_path.exists():
            logger.warning("F1 logo not found at %s", logo_path)
            return

        try:
            logo_file = Image.open(logo_path)

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
            # Resize to fit available space while maintaining aspect ratio
            img_w, img_h = track_image.size
            ratio = min(available_width / img_w, available_height / img_h)
            new_size = (int(img_w * ratio), int(img_h * ratio))

            if new_size != (img_w, img_h):
                track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

            # Center horizontally and vertically in available space
            final_w, final_h = track_image.size
            paste_x = int(side_margin + (available_width - final_w) // 2)
            paste_y = int(track_top + (available_height - final_h) // 2)

            image.paste(track_image.convert("RGB"), (paste_x, paste_y))
        else:
            self._draw_track_placeholder(
                draw,
                x_start + side_margin,
                track_top,
                int(available_width),
                int(available_height),
            )

        label_x = self.layout["padding"]
        draw.text((label_x, label_y), label_text, fill=self.colors.BLACK, font=label_font)

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
                return Image.open(source_path)
            except Exception as e:
                logger.warning("Failed to load track %s: %s", source_path, e)

        for stem in track_stems:
            track_path = TRACKS_SPECTRA6_DIR / f"{stem}.bmp"
            if not track_path.exists():
                continue

            try:
                return Image.open(track_path)
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
            fill=self.colors.BLACK,
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

        translated_name = self._translate_session_name(name)

        font_reg = self.fonts["schedule_row"]
        font_bold = fit_ui_font(
            draw,
            self.lang_code,
            translated_name,
            max_width=self.width - self.layout["schedule_name_x"] - 5,
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

    def _translate_session_name(self, name: str) -> str:
        """Translate session names while normalizing API/static variants."""
        if not name:
            return ""

        direct_key = f"session_{name.lower()}"
        if direct_key in self.translator:
            return self.translator[direct_key]

        normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
        aliases = {
            "practice1": "fp1",
            "practice2": "fp2",
            "practice3": "fp3",
            "firstpractice": "fp1",
            "secondpractice": "fp2",
            "thirdpractice": "fp3",
            "sprintqualifying": "sprintqualifying",
            "sprintshootout": "sprintqualifying",
            "shootout": "sprintqualifying",
        }
        normalized = aliases.get(normalized, normalized)
        normalized_key = f"session_{normalized}"
        return self.translator.get(normalized_key, name)

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

        max_icon_width = 0
        for stat in stats:
            icon = stat[0]
            icon_bbox = draw.textbbox((0, 0), icon, font=font_icon)
            icon_width = icon_bbox[2] - icon_bbox[0]
            max_icon_width = max(max_icon_width, icon_width)

        max_text_width = 0
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
            draw.text((icon_x, y), icon, fill=self.colors.BLACK, font=font_icon)
            draw.text((text_x, y), text, fill=self.colors.BLACK, font=font_value)
            y += row_height

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
        message = self.translator.get("new_track", "NEW TRACK")
        bbox = draw.textbbox((0, 0), message, font=self.fonts["schedule_title"])
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        y = y_start + 30
        draw.text((x, y), message, fill=self.colors.BLACK, font=self.fonts["schedule_title"])

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        """Draw the year and optional country flag for the results footer."""
        year_text = str(season)
        year_font = self.fonts["results_year"]
        bbox = draw.textbbox((0, 0), year_text, font=year_font)
        text_width = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        footer_y_start = y_start
        footer_height = self.height - footer_y_start

        iso_code = COUNTRY_MAP.get(country_name, "").lower()

        flag_img = None
        if iso_code:
            local_flag_path = FLAGS_DIR / f"{iso_code}.bmp"
            if local_flag_path.exists():
                try:
                    flag_img = Image.open(local_flag_path).convert("RGB")
                except Exception as e:
                    logger.warning("Failed to load local flag: %s", e)

        header_area_w = self.layout["results_col1_x"]

        flag_h = 0
        if flag_img:
            max_flag_width = int(header_area_w * 0.8)
            if flag_img.width > max_flag_width:
                ratio = max_flag_width / flag_img.width
                flag_h = int(flag_img.height * ratio)
                flag_img = flag_img.resize((max_flag_width, flag_h), Image.Resampling.NEAREST)
            else:
                flag_h = flag_img.height

        standard_gap = 3
        total_block_h_stable = text_h + (standard_gap if flag_h > 0 else 0) + flag_h
        y_offset_stable = (footer_height - total_block_h_stable) // 2
        visual_top = footer_y_start + y_offset_stable

        year_x = (header_area_w - text_width) // 2
        text_y = visual_top - bbox[1]
        draw.text((year_x, text_y), year_text, fill=self.colors.BLACK, font=year_font)

        if flag_img:
            x = (header_area_w - flag_img.width) // 2
            flag_top_y = int(self.height - flag_img.height - 4)

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
        font_title = self.fonts["results_title"]

        ref_bbox = draw.textbbox((0, 0), TEXT_BASELINE_REF, font=font_title)
        header_y_anchor = visual_top - ref_bbox[1]

        draw.text((x_start, header_y_anchor), title, fill=self.colors.BLACK, font=font_title)

        time_x = x_start + self.layout["results_time_offset"]

        row_height = self.layout["results_row_height"]
        font = self.fonts["results_row"]

        ref_bbox = draw.textbbox((0, 0), "Hg", font=font_title)
        header_visual_bottom = header_y_anchor + ref_bbox[3]

        row_bbox = draw.textbbox((0, 0), "1", font=font)
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

            max_width = self.layout["results_time_offset"] - 10

            text = self._fit_text(draw, font, max_width, pos, driver_name, team)

            pos_text = f"{pos}."
            draw.text((x_start, y), pos_text, fill=self.colors.BLACK, font=font)

            pos_bbox = draw.textbbox((0, 0), pos_text, font=font)
            pos_width = pos_bbox[2] - pos_bbox[0]
            rest_text = text[len(pos_text) :]
            draw.text((x_start + pos_width, y), rest_text, fill=self.colors.BLACK, font=font)

            if time_str:
                draw.text((time_x, y), time_str, fill=self.colors.BLACK, font=font)

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

        def get_width(t: str) -> int:
            """Measure a candidate text width for truncation decisions."""
            return int(draw.textbbox((0, 0), t, font=font)[2])

        full = f"{pos}. {driver} ({team})"
        if get_width(full) <= max_width:
            return full

        for i in range(len(team), 2, -1):
            short_team = team[:i] + ".."
            text = f"{pos}. {driver} ({short_team})"
            if get_width(text) <= max_width:
                return text

        short_team = team[:3] + ".."

        for i in range(len(driver), 2, -1):
            short_driver = driver[:i] + "."
            text = f"{pos}. {short_driver} ({short_team})"
            if get_width(text) <= max_width:
                return text

        return f"{pos}. {driver[:5]}.. ({team[:3]}..)"

    def _load_font(self, size: int, bold: bool = False) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the main UI font for the active locale."""
        return load_ui_font(self.lang_code, size, bold=bold)

    @staticmethod
    def _load_icon_font(size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the fallback icon font used for symbols."""
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

    def _get_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Return a cached racing-style font at the requested size."""
        if size not in self._racing_fonts:
            self._racing_fonts[size] = self._load_racing_font(size)
        return self._racing_fonts[size]

    @staticmethod
    def _format_points(value: float | int | None) -> str:
        """Format points while preserving half-points for display."""
        if value in (None, 0):
            return "0"
        value_float = float(value)
        if value_float.is_integer():
            return str(int(value_float))
        return f"{value_float:.1f}"

    @staticmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """Load driver portraits used by the teams screen."""
        drivers_dir = IMAGES_DIR / "drivers"
        photos: dict[str, Image.Image] = {}

        if not drivers_dir.exists():
            return photos

        for photo_path in drivers_dir.glob("*.png"):
            try:
                photos[photo_path.stem.lower()] = Image.open(photo_path).convert("RGBA")
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

    def _load_team_logos(self) -> dict[str, Image.Image]:
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
                    img = Image.open(logo_path).convert("RGBA")
                    logos[team_key] = self._prepare_team_logo(team_key, img)
                except Exception as e:
                    logger.warning("Failed to load team logo %s: %s", logo_path, e)

        return logos

    @classmethod
    def _get_cached_team_logos(cls) -> dict[str, Image.Image]:
        """Return the process-wide cache of prepared color team logos."""
        cache_key = (str(IMAGES_DIR), str(TEAMS_COLOR_DIR))
        if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
            temp_renderer = cls.__new__(cls)
            cls._cached_team_logos = cls._load_team_logos(temp_renderer)
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
        if "A" in img.getbands():
            alpha = img.getchannel("A")
            if alpha.getextrema()[0] < 255:
                bbox = alpha.getbbox()
                if bbox:
                    return img.crop(bbox)

        inverted = ImageOps.invert(img.convert("L"))
        bbox = inverted.getbbox()
        if bbox:
            return img.crop(bbox)
        return img

    @staticmethod
    def _crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
        """Keep only the dominant upper band for tall stacked logo assets."""
        if "A" in img.getbands() and img.getchannel("A").getextrema()[0] < 255:
            mask = img.getchannel("A")
        else:
            mask = ImageOps.invert(img.convert("L"))
        rows = []
        for y in range(mask.height):
            active = 0
            for x in range(mask.width):
                if mask.getpixel((x, y)) > 16:
                    active += 1
            rows.append(active)

        segments: list[tuple[int, int, int]] = []
        start: int | None = None
        for index, count in enumerate(rows):
            if count > 5 and start is None:
                start = index
            elif count <= 5 and start is not None:
                segment_rows = rows[start:index]
                segments.append((start, index, max(segment_rows) if segment_rows else 0))
                start = None
        if start is not None:
            segment_rows = rows[start:]
            segments.append((start, len(rows), max(segment_rows) if segment_rows else 0))

        if len(segments) < 2:
            return img

        first_start, first_end, first_peak = segments[0]
        second_start, second_end, second_peak = segments[1]
        first_height = first_end - first_start
        second_height = second_end - second_start
        gap = second_start - first_end

        min_gap = max(8, img.height // 30)
        min_primary_height = max(12, img.height // 5)
        if (
            gap < min_gap
            or first_height < min_primary_height
            or first_height < second_height
            or first_peak < second_peak
        ):
            return img

        return img.crop((0, first_start, img.width, first_end))

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
