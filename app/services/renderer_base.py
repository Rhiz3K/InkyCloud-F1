"""Shared drawing adapters for monochrome and color renderers."""

from __future__ import annotations

import logging
from datetime import datetime

from PIL import Image, ImageDraw

from app.models import HistoricalData
from app.services.circuit_data import load_circuits_data
from app.services.circuit_metadata import CIRCUIT_ID_MAP, COUNTRY_MAP
from app.services.font_utils import CJK_LANG_CODES
from app.services.renderer_assets import build_track_stems, load_track_image_asset
from app.services.renderer_calendar import (
    draw_circuit_stats_block,
    draw_countdown_box,
    draw_f1_logo,
    draw_race_header,
    draw_schedule_row,
    draw_schedule_section,
    draw_track_placeholder,
    draw_track_section,
)
from app.services.renderer_core import RendererCore
from app.services.renderer_results import (
    draw_new_track_message,
    draw_results_column,
    draw_results_header,
    draw_results_section,
)
from app.services.renderer_teams import (
    draw_driver_photo,
    draw_team_driver_row,
    draw_team_logo,
    draw_team_row,
    draw_team_stats_panel,
    draw_teams_content,
    draw_teams_header,
)
from app.services.renderer_text import (
    build_team_header_values,
    clamp_text,
    fit_result_text,
    format_points,
    format_schedule_session_name,
    format_team_driver_display_name,
    get_team_logo_key,
    get_text_y,
    right_align_x,
)
from app.services.weather_service import RAINDROP_ICON, WeatherData

logger = logging.getLogger(__name__)
TEXT_BASELINE_REF = "ÁŽÝgy"


class RendererBase(RendererCore):
    """Own all display-independent drawing adapters."""

    def _draw_teams_header(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, season: int
    ) -> None:
        """Draw the shared teams screen header using theme fills."""
        draw_teams_header(
            draw,
            image,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            season=season,
            title=self.translator.get("teams_drivers_title", "TEAMS & DRIVERS"),
            left_fill=self.theme.header_left_fill,
            divider_fill=self.theme.header_divider_fill,
            right_fill=self.theme.header_right_fill,
            text_fill=self.theme.header_text_fill,
            brand_font=self._load_brand_font(36, bold=True),
            subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=self.theme.images_directory() / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=self._prepare_f1_logo,
            ),
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
            number_fill=self.theme.driver_number_fill,
            resample=self.theme.driver_resample,
            paste_photo_fn=self._paste_driver_photo,
        )

    def _draw_team_stats_panel(
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
        """Draw the position/points panel and return its left coordinate."""
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
            panel_fill=self.theme.team_panel_fill,
            panel_outline=self.theme.team_panel_outline_fill,
            team_pos_fill=self.theme.team_position_fill(team_position),
            team_pts_fill=self.theme.team_points_fill,
        )

    def _draw_team_driver_row(
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
        """Draw one driver row inside a team card."""
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
            text_fill=self.theme.text_fill,
            badge_outline_fill=self.theme.team_outline_fill,
            badge_colors_fn=self.theme.driver_badge_colors,
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
        """Draw one themed team card with standings, drivers, and logo."""
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
                if self.theme.use_bold_team_tech_font
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
            """Adapt the layout callback to the shared themed statistics renderer."""
            return self._draw_team_stats_panel(
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
            """Adapt the layout callback to the shared themed driver-row renderer."""
            self._draw_team_driver_row(
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
            """Load team artwork lazily and draw it through the shared layout callback."""
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
                paste_logo_fn=self._paste_team_logo,
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
            header_fill=self.theme.team_header_fill,
            header_text_fill=self.theme.team_header_text_fill,
            outline_fill=self.theme.team_outline_fill,
            stats_padding=5,
            driver_name_padding=self.layout["driver_name_padding"],
            get_text_y_fn=get_text_y,
            build_team_header_values_fn=lambda _team: (team_name, meta_text, team_pos, team_pts),
            clamp_text_fn=clamp_text,
            draw_team_stats_panel_fn=render_team_stats_panel,
            draw_team_driver_row_fn=render_team_driver_row,
            draw_team_logo_fn=draw_team_logo_cb,
        )

    def _draw_teams_content(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, teams: list
    ) -> None:
        """Lay out team cards into two balanced columns."""
        draw_teams_content(
            image,
            draw,
            teams,
            canvas_width=self.width,
            canvas_height=self.height,
            header_height=self.layout["header_height"],
            draw_team_row_fn=self._draw_team_row,
        )

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        """Draw the shared split race header."""
        draw_race_header(
            draw,
            image,
            race_data,
            canvas_width=self.width,
            header_height=self.layout["header_height"],
            split_x=self.layout["header_split_x"],
            left_fill=self.theme.header_left_fill,
            divider_fill=self.theme.header_divider_fill,
            right_fill=self.theme.header_right_fill,
            title_fill=self.theme.header_text_fill,
            header_title_font=self.fonts["header_title"],
            header_subtitle_font=self.fonts["header_subtitle"],
            draw_f1_logo_fn=lambda canvas, width, height: draw_f1_logo(
                canvas,
                width,
                height,
                logo_path=self.theme.images_directory() / "eInkF1logo.jpg",
                logger=logger,
                prepare_logo_fn=self._prepare_f1_logo,
            ),
        )

    def _draw_track_section(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict
    ) -> None:
        """Draw the left-side circuit map and label block."""
        draw_track_section(
            draw,
            image,
            race_data,
            left_column_width=self.layout["left_column_width"],
            results_y_start=self.layout["results_y_start"],
            padding=self.layout["padding"],
            label_font=self.fonts["circuit_name"],
            label_fill=self.theme.text_fill,
            load_track_image_fn=self._load_track_image,
            prepare_track_image_fn=lambda track_image, width, height: (
                self.theme.prepare_track_image(track_image, width, height, logger)
            ),
            paste_track_image_fn=(
                lambda canvas, prepared_image, px, py: canvas.paste(prepared_image, (px, py))
            ),
            draw_track_placeholder_fn=(
                lambda draw_ctx, x, y, width, height: draw_track_placeholder(
                    draw_ctx,
                    x,
                    y,
                    width,
                    height,
                    outline_fill=self.theme.track_placeholder_fill,
                )
            ),
        )

    @classmethod
    def _load_track_image(cls, race_data: dict) -> Image.Image | None:
        """Load a track only from the display's processed runtime directory."""
        track_image = load_track_image_asset(
            build_track_stems(race_data),
            processed_dir=cls.THEME.track_directory(),
            logger=logger,
        )
        return cls.THEME.normalize_track_image(track_image) if track_image is not None else None

    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the race-weekend schedule and return its lower boundary."""
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
            title_fill=self.theme.text_fill,
            draw_schedule_row_fn=self._draw_schedule_row,
            draw_countdown_box_fn=self._draw_countdown_box,
            weather_data=weather_data,
            weather_type=weather_type,
        )

    def _draw_schedule_row(self, draw: ImageDraw.ImageDraw, y: int, event: dict) -> None:
        """Draw a localized schedule row using the shared session mapping."""
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
            regular_text_fill=self.theme.text_fill,
            session_text_fill=self._get_session_color(normalized_session_name),
            session_text_shadow_fill=self.theme.session_shadow_fill,
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
        """Draw the countdown/status box and return its bottom coordinate."""
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
            lang_code=self.lang_code,
            datetime_cls=datetime,
            text_baseline_ref=TEXT_BASELINE_REF,
            rain_icon=RAINDROP_ICON,
            box_fill=self.theme.countdown_fill,
            box_outline=self.theme.countdown_outline_fill,
            text_fill=self.theme.countdown_text_fill,
            weather_data=weather_data,
            weather_type=weather_type,
        )

    def _draw_circuit_stats(
        self, draw: ImageDraw.ImageDraw, race_data: dict, schedule_bottom: int
    ) -> None:
        """Draw right-column circuit facts between schedule and results."""
        circuit_id = race_data.get("circuit", {}).get("circuitId", "")
        mapped_id = CIRCUIT_ID_MAP.get(circuit_id, circuit_id)
        circuit_data = load_circuits_data().get(mapped_id, {})
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
            fill=self.theme.text_fill,
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
            text_fill=self.theme.text_fill,
            outline_fill=self.theme.text_fill,
            country_map=COUNTRY_MAP,
            flags_dirs=self.theme.flags_directories(),
            prepare_flag_image=self.theme.prepare_flag_image,
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
            separator_fill=self.theme.accent_fill,
            separator_width=self.layout["separator_width"],
            y_start=self.layout["results_y_start"],
            race_data=race_data,
            historical_data=historical_data,
            results_col1_x=self.layout["results_col1_x"],
            results_col2_x=self.layout["results_col2_x"],
            qualifying_title=self.translator.get(
                self.theme.qualifying_translation_key, "QUALIFYING"
            ),
            race_title=self.translator.get(self.theme.race_translation_key, "RACE"),
            draw_new_track_message_fn=self._draw_new_track_message,
            draw_results_header_fn=self._draw_results_header,
            draw_results_column_fn=self._draw_results_column,
        )

    def _draw_new_track_message(self, draw: ImageDraw.ImageDraw, y_start: int) -> None:
        """Draw a centered message when historical data is unavailable."""
        draw_new_track_message(
            draw,
            canvas_width=self.width,
            y_start=y_start,
            message=self.translator.get("new_track", "NEW TRACK"),
            font=self.fonts["schedule_title"],
            fill=self.theme.text_fill,
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
        """Draw one historical results column aligned with its header."""
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
            text_fill=self.theme.text_fill,
            fit_result_text_fn=fit_result_text,
            split_position_prefix=self.theme.results_split_position_prefix,
        )
