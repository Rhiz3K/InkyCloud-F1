"""Spectra 6 Color E-Ink Renderer for 7.3" display (800x480, 6 colors)."""

import io
import json
import logging
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData, TeamsData
from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import (
    CJK_LANG_CODES,
    load_brand_font,
    load_ui_font,
)
from app.services.renderer_common import (
    ASSET_CACHE_LOCK,
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
    prepare_color_track_image,
    right_align_x,
)
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

# Shared with the 1bit Renderer hierarchy: rendering runs in a thread pool, so the lazy
# class-level asset caches can be populated concurrently from multiple threads.
_ASSET_CACHE_LOCK = ASSET_CACHE_LOCK

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
    _cached_team_logos_key: tuple[str, str, str] | None = None

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
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=IMAGES_DIR / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=lambda logo_file: (
                    logo_file.convert("L")
                    .point(lambda p, threshold=128: 255 if p > threshold else 0)
                    .convert("1")
                    .convert("RGB")
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
            panel_fill=self.colors.WHITE,
            panel_outline=self.colors.BLACK,
            team_pos_fill=self.colors.RED if team_position == 1 else self.colors.BLACK,
            team_pts_fill=self.colors.BLACK,
        )

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
            text_fill=self.colors.BLACK,
            badge_outline_fill=self.colors.BLACK,
            badge_colors_fn=lambda position: (
                (self.colors.RED, self.colors.WHITE)
                if position == 1
                else (self.colors.BLACK, self.colors.WHITE)
                if position in {2, 3}
                else (self.colors.WHITE, self.colors.BLACK)
            ),
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

        team_name, meta_text, team_pos, team_pts = build_team_header_values(team)

        def render_team_stats_panel(
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
                    logo_resized,
                    (x, y),
                    logo_resized,
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
            header_fill=self.colors.BLACK,
            header_text_fill=self.colors.WHITE,
            outline_fill=self.colors.BLACK,
            stats_padding=5,
            driver_name_padding=self.layout["driver_name_padding"],
            get_text_y_fn=get_text_y,
            build_team_header_values_fn=lambda _team: (team_name, meta_text, team_pos, team_pts),
            clamp_text_fn=clamp_text,
            draw_team_stats_panel_fn=render_team_stats_panel,
            draw_team_driver_row_fn=render_team_driver_row,
            draw_team_logo_fn=draw_team_logo_cb,
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
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=IMAGES_DIR / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=lambda logo_file: (
                    logo_file.convert("L")
                    .point(lambda p, threshold=128: 255 if p > threshold else 0)
                    .convert("1")
                    .convert("RGB")
                ),
            ),
        )

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
            draw_track_placeholder_fn=(
                lambda draw_ctx, x, y, width, height: draw_track_placeholder(
                    draw_ctx, x, y, width, height, outline_fill=Spectra6Colors.BLACK
                )
            ),
        )

    @staticmethod
    def _load_track_image(race_data: dict) -> Image.Image | None:
        """Load the best available Spectra 6 track image for a race."""
        return load_track_image_asset(
            build_track_stems(race_data),
            source_dir=TRACKS_DIR,
            variant_suffix="spectra6",
            fallback_dir=TRACKS_SPECTRA6_DIR,
            logger=logger,
        )

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
        raw_session_name = event.get("name", "")
        normalized_session_name = raw_session_name
        normalized_alias = raw_session_name.strip().lower().replace(" ", "")
        if normalized_alias in {"sprintqualifying", "sprintshootout", "shootout"}:
            normalized_session_name = "Sprint Qualifying"

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
            regular_text_fill=self.colors.BLACK,
            session_text_fill=self._get_session_color(normalized_session_name),
            session_text_shadow_fill=self.colors.BLACK,
            format_schedule_session_name_fn=(
                lambda draw_ctx, session_name, max_width: format_schedule_session_name(
                    draw_ctx, session_name, max_width, self.lang_code, self.translator
                )
            ),
        )

    def _draw_countdown_box(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the countdown/status box and return its bottom y-coordinate."""
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
            box_fill=self.colors.RED,
            box_outline=self.colors.RED,
            text_fill=self.colors.WHITE,
            weather_data=weather_data,
            weather_type=weather_type,
        )

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

    def _draw_results_header(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        y_start: int,
        season: int | str,
        country_name: str,
    ) -> int:
        """Draw the footer year/flag header for historical results."""
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
            flags_dirs=FLAGS_DIR,
            prepare_flag_image=lambda opened_flag: opened_flag.convert("RGB"),
            logger=logger,
        )

    def _draw_results_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
        historical_data: HistoricalData | None,
    ) -> None:
        """Draw the footer historical results section."""

        draw_results_section(
            draw,
            image,
            canvas_width=self.width,
            separator_fill=self.colors.RED,
            separator_width=self.layout["separator_width"],
            y_start=self.layout["results_y_start"],
            race_data=race_data,
            historical_data=historical_data,
            results_col1_x=self.layout["results_col1_x"],
            results_col2_x=self.layout["results_col2_x"],
            qualifying_title=self.translator.get("session_qualifying", "QUALIFYING"),
            race_title=self.translator.get("session_race", "RACE"),
            draw_new_track_message_fn=self._draw_new_track_message,
            draw_results_header_fn=self._draw_results_header,
            draw_results_column_fn=self._draw_results_column,
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
            fit_result_text_fn=fit_result_text,
            split_position_prefix=True,
        )

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
            with _ASSET_CACHE_LOCK:
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
        # Key by cls so subclasses (BWR/BWRY) that override _prepare_team_logo don't inherit
        # the base Spectra6 cache — otherwise their palette-specific logo prep never runs.
        cache_key = (cls.__name__, str(IMAGES_DIR), str(TEAMS_COLOR_DIR))
        if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
            with _ASSET_CACHE_LOCK:
                if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
                    cls._cached_team_logos = cls._load_team_logos()
                    cls._cached_team_logos_key = cache_key
        return cls._cached_team_logos

    @classmethod
    def _prepare_team_logo(cls, team_key: str, img: Image.Image) -> Image.Image:
        """Crop a team logo to visible content and apply team-specific trims."""
        cropped = crop_to_content(img)
        if team_key in {"audi", "cadillac"}:
            return crop_primary_horizontal_band(cropped)
        return cropped

    def _to_indexed_bmp(self, image: Image.Image) -> bytes:
        """Convert RGB image to indexed 6-color BMP for Spectra 6 display."""
        palette_flat: list[int] = []
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
