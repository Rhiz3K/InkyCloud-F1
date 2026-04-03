"""Image rendering service using Pillow - FoxeeLab style layout."""

import io
import json
import logging
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import ConstructorStanding, DriverStanding, HistoricalData, TeamsData
from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import (
    CJK_LANG_CODES,
    load_brand_font,
    load_ui_font,
)
from app.services.renderer_common import (
    build_team_header_values,
    build_track_stems,
    clamp_text,
    crop_primary_horizontal_band,
    crop_to_content,
    draw_circuit_stats_block,
    draw_countdown_box,
    draw_driver_photo,
    draw_f1_logo,
    draw_new_track_message,
    draw_race_header,
    draw_results_column,
    draw_results_header,
    draw_results_section,
    draw_schedule_row,
    draw_schedule_section,
    draw_team_driver_row,
    draw_team_logo,
    draw_team_row,
    draw_team_stats_panel,
    draw_teams_content,
    draw_teams_header,
    draw_track_placeholder,
    draw_track_section,
    fit_result_text,
    format_points,
    format_schedule_session_name,
    format_team_driver_display_name,
    get_team_logo_key,
    get_text_y,
    load_racing_font,
    load_symbol_icon_font,
    load_track_image_asset,
    load_weather_icon_font,
    prepare_mono_track_image,
    right_align_x,
)
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
        draw_teams_header(
            draw,
            image,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            season=season,
            title=self.translator.get("teams_drivers_title", "TEAMS & DRIVERS"),
            left_fill=1,
            divider_fill=0,
            right_fill=0,
            text_fill=1,
            brand_font=self._load_brand_font(36, bold=True),
            subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=IMAGES_DIR / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=lambda logo_file: (
                    logo_file.convert("L").point(lambda p: 255 if p > 128 else 0).convert("1")
                ),
            ),
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
            number_fill=0,
            resample=Image.Resampling.NEAREST,
            paste_photo_fn=(
                lambda canvas, photo_resized, px, py: canvas.paste(photo_resized, (px, py))
            ),
        )

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
        return draw_team_stats_panel(
            draw,
            y=y,
            header_height=header_height,
            panel_x=panel_x,
            panel_right_x=panel_right_x,
            team_pos=team_pos,
            team_pts=team_pts,
            stats_font=stats_font,
            points_font=points_font,
            panel_fill=1,
            panel_outline=0,
            team_pos_fill=0,
            team_pts_fill=0,
        )

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
        draw_team_driver_row(
            draw,
            image,
            driver,
            driver_y=driver_y,
            driver_row_height=driver_row_height,
            photo_x=photo_x,
            photo_size=photo_size,
            pts_right_x=pts_right_x,
            driver_pos_x=driver_pos_x,
            badge_pad_x=badge_pad_x,
            small_font=small_font,
            driver_font=driver_font,
            driver_name_padding=self.layout["driver_name_padding"],
            lang_code=self.lang_code,
            draw_driver_photo_fn=self._draw_driver_photo,
            get_text_y_fn=get_text_y,
            format_team_driver_display_name_fn=format_team_driver_display_name,
            format_points_fn=format_points,
            right_align_x_fn=right_align_x,
            text_fill=0,
            badge_outline_fill=0,
            badge_colors_fn=lambda position: (0, 1) if position in {2, 3} else (1, 0),
        )

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

        team_name, meta_text, team_pos, team_pts = build_team_header_values(team)

        def render_team_stats_panel(
            panel_x: int,
            panel_right_x: int,
            header_height: int,
            _badge_pad_x: int,
        ) -> int:
            return self._draw_team_stats_panel_mono(
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

        def render_team_driver_row(
            driver,
            driver_y: int,
            driver_row_height: int,
            photo_x: int,
            photo_size: int,
            pts_right_x: int,
            driver_pos_x: int,
            badge_pad_x: int,
        ) -> None:
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

        def draw_team_logo_cb(
            team_obj,
            driver_y_start: int,
            driver_area_height: int,
            logo_container_left: int,
            logo_container_right: int,
        ) -> None:
            self._ensure_teams_assets()
            draw_team_logo(
                image,
                team_obj,
                team_logos=self._team_logos,
                get_team_logo_key_fn=get_team_logo_key,
                driver_area_y=driver_y_start,
                driver_area_h=driver_area_height,
                container_left=logo_container_left,
                container_right=logo_container_right,
                paste_logo_fn=lambda canvas, logo_resized, x, y: canvas.paste(
                    self._logo_to_1bit(logo_resized),
                    (x, y),
                ),
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
            header_fill=0,
            header_text_fill=1,
            outline_fill=0,
            stats_padding=5,
            driver_name_padding=self.layout["driver_name_padding"],
            get_text_y_fn=get_text_y,
            build_team_header_values_fn=lambda _team: (team_name, meta_text, team_pos, team_pts),
            clamp_text_fn=clamp_text,
            draw_team_stats_panel_fn=render_team_stats_panel,
            draw_team_driver_row_fn=render_team_driver_row,
            draw_team_logo_fn=draw_team_logo_cb,
        )

    def _get_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Return a cached racing-style font at the requested size."""
        if size not in self._racing_fonts:
            self._racing_fonts[size] = self._load_racing_font(size)
        return self._racing_fonts[size]

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

        draw_f1_logo(
            image,
            split_x,
            header_height,
            logo_path=IMAGES_DIR / "eInkF1logo.jpg",
            logger=logger,
            prepare_logo_fn=lambda logo_file: (
                logo_file.convert("L").point(lambda p: 255 if p > 128 else 0).convert("1")
            ),
        )

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
        draw_race_header(
            draw,
            image,
            race_data,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            left_fill=1,
            divider_fill=0,
            right_fill=0,
            title_fill=1,
            header_title_font=self.fonts["header_title"],
            header_subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=IMAGES_DIR / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=lambda logo_file: (
                    logo_file.convert("L").point(lambda p: 255 if p > 128 else 0).convert("1")
                ),
            ),
        )

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
        draw_track_section(
            draw,
            image,
            race_data,
            left_column_width=self.layout["left_column_width"],
            results_y_start=self.layout["results_y_start"],
            padding=self.layout["padding"],
            label_font=self.fonts["circuit_name"],
            label_fill=0,
            load_track_image_fn=self._load_track_image,
            prepare_track_image_fn=(
                lambda track_image, available_width, available_height: prepare_mono_track_image(
                    track_image,
                    available_width,
                    available_height,
                    logger,
                )
            ),
            paste_track_image_fn=(
                lambda canvas, prepared_image, px, py: canvas.paste(prepared_image, (px, py))
            ),
            draw_track_placeholder_fn=(
                lambda draw_ctx, x, y, width, height: draw_track_placeholder(
                    draw_ctx, x, y, width, height, outline_fill=0
                )
            ),
        )

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        """Load track image from assets.

        First tries source artwork, then pre-processed monochrome BMP fallbacks.
        """
        return load_track_image_asset(
            build_track_stems(race_data),
            source_dir=TRACKS_DIR,
            variant_suffix="bw",
            fallback_dir=TRACKS_PROCESSED_DIR,
        )

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
            title_fill=0,
            draw_schedule_row_fn=self._draw_schedule_row,
            draw_countdown_box_fn=self._draw_countdown_box,
            weather_data=weather_data,
            weather_type=weather_type,
        )

    def _draw_schedule_row(self, draw: ImageDraw.ImageDraw, y: int, event: dict) -> None:
        """Draw a single schedule row with bold event name."""
        draw_schedule_row(
            draw,
            y=y,
            event=event,
            canvas_width=self.width,
            schedule_date_x=self.layout["schedule_date_x"],
            schedule_day_x=self.layout["schedule_day_x"],
            schedule_time_x=self.layout["schedule_time_x"],
            schedule_name_x=self.layout["schedule_name_x"],
            translator=self.translator,
            lang_code=self.lang_code,
            font_reg=self.fonts["schedule_row"],
            regular_text_fill=0,
            session_text_fill=0,
            format_schedule_session_name_fn=(
                lambda draw_ctx, session_name, max_width: format_schedule_session_name(
                    draw_ctx, session_name, max_width, self.lang_code, self.translator
                )
            ),
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

    def _draw_countdown_box(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw countdown box showing time until race and optional weather."""
        return draw_countdown_box(
            draw,
            race_data,
            schedule_bottom=schedule_bottom,
            right_column_x=self.layout["right_column_x"],
            canvas_width=self.width,
            results_y_start=self.layout["results_y_start"],
            circuit_stats_row_height=self.layout["circuit_stats_row_height"],
            schedule_row_bold_font=self.fonts["schedule_row_bold"],
            icon_small_font=self.fonts["icon_small"],
            weather_icon_font=self.fonts["weather_icon_font"],
            translator=self.translator,
            datetime_cls=datetime,
            text_baseline_ref=TEXT_BASELINE_REF,
            rain_icon=RAINDROP_ICON,
            box_fill=0,
            box_outline=0,
            text_fill=1,
            weather_data=weather_data,
            weather_type=weather_type,
        )

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
            fill=0,
        )

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

        def draw_results_header_cb(
            draw_ctx: ImageDraw.ImageDraw,
            image_ctx: Image.Image,
            y_start: int,
            season: int | str,
            country_name: str,
        ) -> int:
            return draw_results_header(
                draw_ctx,
                image_ctx,
                canvas_height=self.height,
                header_area_width=self.layout["results_col1_x"],
                y_start=y_start,
                season=season,
                country_name=country_name,
                year_font=self.fonts["results_year"],
                text_fill=0,
                outline_fill=0,
                country_map=COUNTRY_MAP,
                flags_dirs=FLAGS_DIR,
                prepare_flag_image=lambda opened_flag: opened_flag.copy(),
                logger=logger,
            )

        draw_results_section(
            draw,
            image,
            canvas_width=self.width,
            separator_fill=0,
            separator_width=self.layout["separator_width"],
            y_start=self.layout["results_y_start"],
            race_data=race_data,
            historical_data=historical_data,
            results_col1_x=self.layout["results_col1_x"],
            results_col2_x=self.layout["results_col2_x"],
            qualifying_title=self.translator.get("qualifying", "QUALIFYING"),
            race_title=self.translator.get("race", "RACE"),
            draw_new_track_message_fn=self._draw_new_track_message,
            draw_results_header_fn=draw_results_header_cb,
            draw_results_column_fn=self._draw_results_column,
        )

    def _draw_new_track_message(self, draw: ImageDraw.ImageDraw, y_start: int) -> None:
        """Draw a centered message indicating this is a new track."""
        draw_new_track_message(
            draw,
            canvas_width=self.width,
            y_start=y_start,
            message=self.translator.get("new_track", "NEW TRACK"),
            font=self.fonts["schedule_title"],
            fill=0,
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
            text_fill=0,
            fit_result_text_fn=fit_result_text,
            split_position_prefix=False,
        )

    # =========================================================================
    # Utility Methods
    # =========================================================================
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
        return load_symbol_icon_font(size, logger)

    def _load_weather_icon_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the weather icon font with a symbol fallback."""
        return load_weather_icon_font(size, logger, self._load_icon_font)

    def _load_racing_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        """Load the stylized racing number font used for driver numbers."""
        return load_racing_font(size, logger, self._load_font)

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
        cropped = crop_to_content(img, use_binary_mask=True)
        if team_key == "sauber":
            return cls.normalize_sauber_logo_for_non_spectra(cropped)
        if team_key in {"audi", "cadillac"}:
            return crop_primary_horizontal_band(cropped)
        return cropped

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
    def _to_bmp(image: Image.Image) -> bytes:
        """Convert PIL Image to BMP bytes."""
        buffer = io.BytesIO()
        image.save(buffer, format="BMP")
        return buffer.getvalue()
