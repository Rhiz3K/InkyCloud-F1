"""Spectra 6 Color E-Ink Renderer for 7.3" display (800x480, 6 colors)."""

import io
import json
import logging
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.models import HistoricalData
from app.services.weather_service import RAINDROP_ICON, WeatherData

logger = logging.getLogger(__name__)

CIRCUITS_DATA_PATH = Path(__file__).parent.parent / "assets" / "circuits_data.json"
TRACKS_SPECTRA6_DIR = Path(__file__).parent.parent / "assets" / "tracks_spectra6"

try:
    with open(CIRCUITS_DATA_PATH, "r", encoding="utf-8") as f:
        CIRCUITS_DATA = json.load(f)
except Exception as e:
    logger.warning(f"Failed to load circuit data: {e}")
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
FONTS_DIR = ASSETS_DIR / "fonts"
FLAGS_DIR = ASSETS_DIR / "flags_spectra6"

TEXT_BASELINE_REF = "ÁŽÝgy"


class Spectra6Colors:
    BLACK = (0x00, 0x00, 0x00)
    WHITE = (0xFF, 0xFF, 0xFF)
    RED = (0xA0, 0x20, 0x20)
    YELLOW = (0xF0, 0xE0, 0x50)
    GREEN = (0x60, 0x80, 0x50)
    BLUE = (0x50, 0x80, 0xB8)

    PALETTE = [BLACK, WHITE, RED, YELLOW, BLUE, GREEN]

    IDX_BLACK = 0
    IDX_WHITE = 1
    IDX_RED = 2
    IDX_YELLOW = 3
    IDX_BLUE = 4
    IDX_GREEN = 5


class Spectra6Renderer:
    """Renderer for generating 6-color images for Spectra 6 E-Ink displays."""

    def __init__(self, translator: dict):
        self.width = config.DISPLAY_WIDTH
        self.height = config.DISPLAY_HEIGHT
        self.translator = translator
        self.colors = Spectra6Colors

        self.fonts = {
            "header_title": self._load_font(36, bold=True),
            "header_subtitle": self._load_font(36, bold=True),
            "race_name": self._load_font(20, bold=True),
            "circuit_name": self._load_font(18, bold=True),
            "circuit_location": self._load_font(14),
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
        }

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
            "padding": 15,
            "separator_width": 2,
        }

    def render_calendar(
        self,
        race_data: dict,
        historical_data: HistoricalData | None = None,
        weather_data: WeatherData | None = None,
    ) -> bytes:
        image = Image.new("RGB", (self.width, self.height), self.colors.WHITE)
        draw = ImageDraw.Draw(image)

        self._draw_header(draw, image, race_data)
        self._draw_track_section(draw, image, race_data)
        schedule_bottom = self._draw_schedule_section(draw, race_data, weather_data)
        self._draw_circuit_stats(draw, race_data, schedule_bottom)
        self._draw_results_section(draw, image, race_data, historical_data)

        return self._to_indexed_bmp(image)

    def render_error(self, error_message: str) -> bytes:
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

    def _draw_header(self, draw: ImageDraw.ImageDraw, image: Image.Image, race_data: dict) -> None:
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

    def _draw_f1_logo(self, image: Image.Image, width: int, height: int) -> None:
        logo_path = IMAGES_DIR / "f1_spectra_6.bmp"

        if not logo_path.exists():
            logger.warning("F1 logo not found at %s", logo_path)
            return

        try:
            logo = Image.open(logo_path).convert("RGB")

            x = (width - logo.width) // 2
            y = (height - logo.height) // 2

            image.paste(logo, (x, y))

        except Exception as e:
            logger.warning("Failed to load F1 logo: %s", e)

    def _draw_track_section(
        self,
        draw: ImageDraw.ImageDraw,
        image: Image.Image,
        race_data: dict,
    ) -> None:
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
            image.paste(track_image.convert("RGB"), (side_margin, track_top))
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
        draw.rounded_rectangle(
            [(x + 20, y + 20), (x + width - 20, y + height - 20)],
            radius=20,
            outline=Spectra6Colors.BLACK,
            width=3,
        )

    def _load_track_image(self, race_data: dict) -> Image.Image | None:
        circuit = race_data.get("circuit", {})
        circuit_id = circuit.get("circuitId", "")

        if not circuit_id:
            return None

        normalized_id = CIRCUIT_ID_MAP.get(circuit_id, circuit_id)
        track_path = TRACKS_SPECTRA6_DIR / f"{normalized_id}.bmp"

        if track_path.exists():
            try:
                return Image.open(track_path)
            except Exception as e:
                logger.warning(f"Failed to load track {track_path}: {e}")

        return None

    def _draw_schedule_section(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        weather_data: WeatherData | None = None,
    ) -> int:
        x_start = self.layout["right_column_x"]
        y_start = self.layout["schedule_title_y"]

        schedule_title = self.translator.get("weekend_schedule", "WEEKEND SCHEDULE")
        draw.text(
            (x_start, y_start),
            schedule_title,
            fill=self.colors.BLACK,
            font=self.fonts["schedule_title"],
        )

        schedule = race_data.get("schedule", [])
        row_y = self.layout["schedule_start_y"]
        row_height = self.layout["schedule_row_height"]

        for event in schedule:
            self._draw_schedule_row(draw, row_y, event)
            row_y += row_height

            if row_y > self.layout["results_y_start"] - 80:
                break

        countdown_bottom = self._draw_countdown_box(draw, race_data, row_y + 10, weather_data)

        return countdown_bottom

    def _get_session_color(self, session_name: str) -> tuple[int, int, int]:
        if session_name.lower() == "race":
            return self.colors.RED
        return self.colors.BLACK

    def _draw_schedule_row(self, draw: ImageDraw.ImageDraw, y: int, event: dict) -> None:
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

        translated_name = self.translator.get(f"session_{name.lower()}", name)

        font_reg = self.fonts["schedule_row"]
        font_bold = self.fonts["schedule_row_bold"]

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

    def _draw_countdown_box(
        self,
        draw: ImageDraw.ImageDraw,
        race_data: dict,
        schedule_bottom: int,
        weather_data: WeatherData | None = None,
    ) -> int:
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

        if not race_dt:
            return schedule_bottom

        now = datetime.now(race_dt.tzinfo) if race_dt.tzinfo else datetime.now()
        delta = race_dt - now

        if delta.total_seconds() <= 0:
            return schedule_bottom

        days = delta.days
        hours = delta.seconds // 3600

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

        flag_icon = "🏁"
        countdown_str = f"{days}D {hours}H"

        cur_x = x_left + padding_x
        draw.text((cur_x, text_y), flag_icon, fill=self.colors.WHITE, font=font_icon)
        flag_bbox = draw.textbbox((0, 0), flag_icon, font=font_icon)
        cur_x += flag_bbox[2] - flag_bbox[0] + 6
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
        def get_width(t: str) -> int:
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
        font_filename = "TitilliumWeb-Bold.ttf" if bold else "TitilliumWeb-Regular.ttf"
        font_path = FONTS_DIR / font_filename

        if font_path.exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception as e:
                logger.warning("Failed to load TitilliumWeb: %s", e)

        fallback_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(fallback_name, size)
        except OSError:
            return ImageFont.load_default()

    def _load_icon_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        symbola_path = "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf"
        try:
            return ImageFont.truetype(symbola_path, size)
        except Exception as e:
            logger.warning("Failed to load Symbola font: %s", e)
            return ImageFont.load_default()

    def _load_weather_icon_font(self, size: int) -> FreeTypeFont | ImageFont.ImageFont:
        font_path = FONTS_DIR / "weathericons-regular-webfont.ttf"
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception as e:
            logger.warning("Failed to load Weather Icons font: %s", e)
            return self._load_icon_font(size)

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
