"""Renderer lifecycle, shared state, and display-specific hook contract."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData, TeamsData
from app.services.font_utils import (
    load_brand_font,
    load_racing_font,
    load_symbol_icon_font,
    load_ui_font,
    load_weather_icon_font,
)
from app.services.renderer_assets import ASSET_CACHE_LOCK
from app.services.renderer_theme import RenderTheme
from app.services.weather_service import WeatherData

logger = logging.getLogger(__name__)
Font = FreeTypeFont | ImageFont.ImageFont

_SESSION_KIND_BY_NAME = {
    "race": "race",
    "qualifying": "qualifying",
    "q1": "qualifying",
    "q2": "qualifying",
    "q3": "qualifying",
    "sprint qualifying": "qualifying",
    "sprint shootout": "qualifying",
    "shootout": "qualifying",
    "sq1": "qualifying",
    "sq2": "qualifying",
    "sq3": "qualifying",
    "sprint": "sprint",
}


class RendererCore(ABC):
    """Own immutable theme state, rendering entry points, fonts, and asset caches."""

    THEME: ClassVar[RenderTheme]
    _cached_driver_photos: ClassVar[dict[str, Image.Image] | None] = None
    _cached_driver_photos_key: ClassVar[tuple[str, str] | None] = None
    _cached_team_logos: ClassVar[dict[str, Image.Image] | None] = None
    _cached_team_logos_key: ClassVar[tuple[str, str] | None] = None

    width: int
    height: int
    translator: dict
    lang_code: str
    fonts: dict[str, Font]
    layout: dict[str, int]
    theme: RenderTheme
    _racing_fonts: dict[int, Font]
    _driver_photos: dict[str, Image.Image] | None
    _team_logos: dict[str, Image.Image] | None

    def __init__(self, translator: dict, lang_code: str = "en") -> None:
        """Bind this renderer's immutable theme and initialize shared state."""
        self.theme = self.THEME
        self.colors = self.theme.colors
        self._initialize_renderer(translator, lang_code)

    def _initialize_renderer(self, translator: dict, lang_code: str) -> None:
        """Initialize shared dimensions, locale state, fonts, and layout constants."""
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
        if self.theme.include_bold_circuit_location:
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
        """Render a calendar using the variant's immutable theme."""
        image = self._new_canvas()
        draw = ImageDraw.Draw(image)
        self._draw_header(draw, image, race_data)
        self._draw_track_section(draw, image, race_data)
        schedule_bottom = self._draw_schedule_section(draw, race_data, weather_data, weather_type)
        self._draw_circuit_stats(draw, race_data, schedule_bottom)
        self._draw_results_section(draw, image, race_data, historical_data)
        return self._encode_image(image)

    def render_teams_drivers(self, teams_data: TeamsData) -> bytes:
        """Render a teams dashboard using the same themed adapter set."""
        self._ensure_teams_assets()
        image = self._new_canvas()
        draw = ImageDraw.Draw(image)
        self._draw_teams_header(draw, image, teams_data.season)
        self._draw_teams_content(image, draw, teams_data.teams)
        return self._encode_image(image)

    def render_error(self, error_message: str) -> bytes:
        """Render an error placeholder in the active display palette."""
        image = self._new_canvas()
        draw = ImageDraw.Draw(image)
        error_text = self.translator.get("error", "Error")
        padding = self.layout["padding"]
        draw.text(
            (padding, padding),
            f"{error_text}:",
            fill=self.theme.error_title_fill,
            font=self.fonts["schedule_title"],
        )
        draw.text(
            (padding, padding + 50),
            error_message[:60],
            fill=self.theme.error_text_fill,
            font=self.fonts["schedule_row"],
        )
        return self._encode_image(image)

    @abstractmethod
    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
        """Draw the calendar header."""

    @abstractmethod
    def _draw_track_section(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict
    ) -> None:
        """Draw the circuit identity and track image section."""

    @abstractmethod
    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
        weather_type: str = "",
    ) -> int:
        """Draw the race-weekend schedule and return its lower boundary."""

    @abstractmethod
    def _draw_circuit_stats(
        self, draw: ImageDraw.ImageDraw, race_data: dict, schedule_bottom: int
    ) -> None:
        """Draw circuit statistics below the schedule."""

    @abstractmethod
    def _draw_results_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
        historical_data: HistoricalData | None,
    ) -> None:
        """Draw historical qualifying and race results."""

    @abstractmethod
    def _draw_teams_header(
        self, draw: ImageDraw.ImageDraw, image: Image.Image, season: int
    ) -> None:
        """Draw the teams dashboard header."""

    @abstractmethod
    def _draw_teams_content(
        self, image: Image.Image, draw: ImageDraw.ImageDraw, teams: list
    ) -> None:
        """Lay out the team cards into columns."""

    @staticmethod
    def _session_kind(session_name: str) -> str:
        """Map every known session alias through one canonical lookup."""
        normalized = session_name.strip().lower()
        if normalized.startswith("fp") or normalized.startswith("practice"):
            return "practice"
        return _SESSION_KIND_BY_NAME.get(normalized, "default")

    def _get_session_color(self, session_name: str):
        """Return the theme fill for one canonical session kind."""
        session_fills = dict(self.theme.session_fills)
        return session_fills[self._session_kind(session_name)]

    def _get_racing_font(self, size: int) -> Font:
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

    @classmethod
    def _get_cached_driver_photos(cls) -> dict[str, Image.Image]:
        """Return the process cache of prepared driver images."""
        cache_key = (cls.__qualname__, str(cls.THEME.images_directory()))
        if cls._cached_driver_photos is None or cls._cached_driver_photos_key != cache_key:
            with ASSET_CACHE_LOCK:
                if cls._cached_driver_photos is None or cls._cached_driver_photos_key != cache_key:
                    cls._cached_driver_photos = cls._load_driver_photos()
                    cls._cached_driver_photos_key = cache_key
        return cls._cached_driver_photos

    @classmethod
    def _get_cached_team_logos(cls) -> dict[str, Image.Image]:
        """Return the process cache of palette-prepared team logos."""
        cache_key = (cls.__qualname__, str(cls.THEME.images_directory()))
        if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
            with ASSET_CACHE_LOCK:
                if cls._cached_team_logos is None or cls._cached_team_logos_key != cache_key:
                    cls._cached_team_logos = cls._load_team_logos()
                    cls._cached_team_logos_key = cache_key
        return cls._cached_team_logos

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

    @abstractmethod
    def _new_canvas(self) -> Image.Image:
        """Create a blank canvas in the variant's native image mode."""

    @abstractmethod
    def _encode_image(self, image: Image.Image) -> bytes:
        """Encode a rendered canvas as the variant's BMP payload."""

    @staticmethod
    @abstractmethod
    def _load_driver_photos() -> dict[str, Image.Image]:
        """Load and prepare the variant's driver image assets."""

    @classmethod
    @abstractmethod
    def _load_team_logos(cls) -> dict[str, Image.Image]:
        """Load and prepare the variant's team logo assets."""

    @staticmethod
    @abstractmethod
    def _prepare_f1_logo(image: Image.Image) -> Image.Image:
        """Prepare the F1 header logo for the variant's canvas mode."""

    @staticmethod
    @abstractmethod
    def _paste_driver_photo(canvas: Image.Image, photo: Image.Image, x: int, y: int) -> None:
        """Paste one prepared driver image onto the canvas."""

    @staticmethod
    @abstractmethod
    def _paste_team_logo(canvas: Image.Image, logo: Image.Image, x: int, y: int) -> None:
        """Paste one prepared team logo onto the canvas."""
