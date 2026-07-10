"""Shared stateful renderer pipeline for monochrome and color variants."""

from __future__ import annotations

import logging

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData, TeamsData
from app.services.font_utils import load_brand_font, load_ui_font
from app.services.renderer_common import (
    draw_teams_content,
    load_racing_font,
    load_symbol_icon_font,
    load_weather_icon_font,
)
from app.services.weather_service import WeatherData

logger = logging.getLogger(__name__)
Font = FreeTypeFont | ImageFont.ImageFont


class RendererBase:
    """Own renderer state and the display-independent render sequence."""

    width: int
    height: int
    translator: dict
    lang_code: str
    fonts: dict[str, Font]
    layout: dict[str, int]
    _racing_fonts: dict[int, Font]
    _driver_photos: dict[str, Image.Image] | None
    _team_logos: dict[str, Image.Image] | None

    def _initialize_renderer(
        self,
        translator: dict,
        lang_code: str,
        *,
        include_bold_circuit_location: bool = False,
    ) -> None:
        self.width = config.DISPLAY_WIDTH
        self.height = config.DISPLAY_HEIGHT
        self.translator = translator
        self.lang_code = lang_code
        self._racing_fonts = {22: self._load_racing_font(22)}

        fonts: dict[str, Font] = {
            "header_title": self._load_font(36, bold=True),
            "header_subtitle": self._load_font(36, bold=True),
            "race_name": self._load_font(20, bold=True),
            "circuit_name": self._load_font(18, bold=True),
            "circuit_location": self._load_font(14),
        }
        if include_bold_circuit_location:
            fonts["circuit_location_bold"] = self._load_font(14, bold=True)
        fonts.update(
            {
                "schedule_title": self._load_font(24, bold=True),
                "schedule_row": self._load_font(20),
                "schedule_row_bold": self._load_font(20, bold=True),
                "results_title": self._load_font(18, bold=True),
                "results_year": self._load_font(36, bold=True),
                "results_row": self._load_font(16),
                "footer": self._load_font(12),
                "circuit_stats": self._load_font(18),
                "circuit_stats_value": self._load_font(18, bold=True),
                "icon_small": self._load_icon_font(22),
                "driver_number": self._racing_fonts[22],
                "weather": self._load_font(12, bold=True),
                "weather_icon": self._load_icon_font(40),
                "weather_icon_font": self._load_weather_icon_font(22),
            }
        )
        self.fonts = fonts
        self._driver_photos = None
        self._team_logos = None
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
        """Render a calendar using the variant's canvas, drawing, and encoding hooks."""
        image = self._new_canvas()
        draw = ImageDraw.Draw(image)
        self._draw_header(draw, image, race_data)
        self._draw_track_section(draw, image, race_data)
        schedule_bottom = self._draw_schedule_section(draw, race_data, weather_data, weather_type)
        self._draw_circuit_stats(draw, race_data, schedule_bottom)
        self._draw_results_section(draw, image, race_data, historical_data)
        return self._encode_image(image)

    def render_teams_drivers(self, teams_data: TeamsData) -> bytes:
        """Render a teams dashboard using the variant's drawing and encoding hooks."""
        self._ensure_teams_assets()
        image = self._new_canvas()
        draw = ImageDraw.Draw(image)
        self._draw_teams_header(draw, image, teams_data.season)
        self._draw_teams_content(image, draw, teams_data.teams)
        return self._encode_image(image)

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
            draw_team_row_fn=getattr(self, "_draw_team_row"),
        )

    def _get_racing_font(self, size: int) -> Font:
        """Return a cached racing-style font at the requested size."""
        if size not in self._racing_fonts:
            self._racing_fonts[size] = self._load_racing_font(size)
        return self._racing_fonts[size]

    def _ensure_teams_assets(self) -> None:
        """Lazy-load cached driver and team assets used by the teams screen."""
        if self._driver_photos is None:
            self._driver_photos = getattr(self, "_get_cached_driver_photos")()
        if self._team_logos is None:
            self._team_logos = getattr(self, "_get_cached_team_logos")()

    def ensure_teams_assets(self) -> None:
        """Public warmup hook for teams assets used outside the renderer."""
        self._ensure_teams_assets()

    def _load_font(self, size: int, bold: bool = False) -> Font:
        """Load the main UI font for the active locale."""
        return load_ui_font(self.lang_code, size, bold=bold)

    @staticmethod
    def _load_brand_font(size: int, bold: bool = False) -> Font:
        """Load the default Latin UI font used for non-localized text."""
        return load_brand_font(size, bold=bold)

    @staticmethod
    def _load_icon_font(size: int) -> Font:
        """Load the fallback icon font used for symbols."""
        return load_symbol_icon_font(size, logger)

    def _load_weather_icon_font(self, size: int) -> Font:
        """Load the weather icon font with a symbol fallback."""
        return load_weather_icon_font(size, logger, self._load_icon_font)

    def _load_racing_font(self, size: int) -> Font:
        """Load the stylized racing number font used for driver numbers."""
        return load_racing_font(size, logger, self._load_font)

    def _new_canvas(self) -> Image.Image:
        raise NotImplementedError

    def _encode_image(self, image: Image.Image) -> bytes:
        raise NotImplementedError

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        raise NotImplementedError

    def _draw_track_section(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict
    ) -> None:
        raise NotImplementedError

    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        raise NotImplementedError

    def _draw_circuit_stats(
        self, draw: ImageDraw.ImageDraw, race_data: dict, schedule_bottom: int
    ) -> None:
        raise NotImplementedError

    def _draw_results_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
        historical_data: HistoricalData | None,
    ) -> None:
        raise NotImplementedError

    def _draw_teams_header(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, season: int
    ) -> None:
        raise NotImplementedError
