"""Shared stateless helpers used by multiple renderer variants."""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from cachetools import LRUCache
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.services.circuit_metadata import CIRCUIT_ID_MAP
from app.services.font_utils import (
    CJK_LANG_CODES,
    FONTS_DIR,
    fit_brand_font_box,
    fit_ui_font,
    load_optional_truetype,
)
from app.services.track_assets import build_track_stem_candidates, resolve_track_source_path

# Guards lazy class-level asset caches in the renderer hierarchies (team logos, driver
# photos). Rendering runs in a thread pool, so first-load population can race without it.
ASSET_CACHE_LOCK = threading.Lock()
_DECODED_IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DECODED_IMAGE_CACHE_LOCK = threading.Lock()


def _decoded_image_size(image: Image.Image) -> int:
    return image.width * image.height * len(image.getbands())


_DECODED_IMAGE_CACHE: LRUCache[tuple[str, int], Image.Image] = LRUCache(
    maxsize=_DECODED_IMAGE_CACHE_MAX_BYTES,
    getsizeof=_decoded_image_size,
)


def _load_image_file(path_value: str, mtime_ns: int) -> Image.Image:
    """Decode an immutable image asset with a byte-bounded process cache."""
    cache_key = (path_value, mtime_ns)
    with _DECODED_IMAGE_CACHE_LOCK:
        cached = _DECODED_IMAGE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with Image.open(path_value) as image_file:
        decoded = image_file.copy()

    if _decoded_image_size(decoded) <= _DECODED_IMAGE_CACHE.maxsize:
        with _DECODED_IMAGE_CACHE_LOCK:
            _DECODED_IMAGE_CACHE[cache_key] = decoded
    return decoded


def _load_image_copy(path: Path) -> Image.Image:
    """Return an independent copy of a cached image asset."""
    return _load_image_file(str(path), path.stat().st_mtime_ns).copy()


_ROUND_RANGE_RE = re.compile(r"^(\d+)(?:\s*[-–—]\s*(\d+))?$")


def _driver_round_window(rounds: str) -> tuple[int, int] | None:
    normalized = rounds.strip()
    if not normalized or normalized.lower() == "all":
        return None
    match = _ROUND_RANGE_RE.fullmatch(normalized)
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return (min(start, end), max(start, end))


def select_active_team_drivers(drivers: Sequence, limit: int = 2) -> list:
    """Select the drivers active at the latest round represented by season roster data."""
    if limit <= 0:
        return []

    windows = [
        (driver, _driver_round_window(getattr(driver, "rounds", "All"))) for driver in drivers
    ]
    latest_round = max((window[1] for _, window in windows if window is not None), default=None)
    if latest_round is None:
        active = [driver for driver, _ in windows]
    else:
        active = [
            driver
            for driver, window in windows
            if window is None or window[0] <= latest_round <= window[1]
        ]

    if len(active) < limit:
        active_ids = {id(driver) for driver in active}
        active.extend(driver for driver, _ in windows if id(driver) not in active_ids)
    return sorted(active, key=lambda driver: driver.position or 99)[:limit]


def split_teams_for_columns(teams: list) -> tuple[list, list]:
    """Split teams into balanced left and right columns."""
    if not teams:
        return [], []

    left_count = math.ceil(len(teams) / 2)
    return teams[:left_count], teams[left_count:]


def get_text_y(
    draw: ImageDraw.ImageDraw,
    font,
    row_h: int,
    row_y: int,
    text: str = "Ay",
) -> int:
    """Align text vertically within a row using the provided text metrics."""
    bbox = draw.textbbox((0, 0), text, font=font)
    height = bbox[3] - bbox[1]
    top_offset = bbox[1]
    return int(row_y + (row_h - height) // 2 - top_offset)


def right_align_x(draw: ImageDraw.ImageDraw, text: str, right_edge: int, font) -> int:
    """Return the x-coordinate that right-aligns text to the given edge."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(right_edge - bbox[2])


def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    """Measure rendered text width for the active draw context."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def clamp_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Clamp text to fit into a maximum width using an ellipsis."""
    if max_width <= 0:
        return ""
    if text_width(draw, text, font) <= max_width:
        return text

    ellipsis = "..."
    trimmed = text
    while trimmed and text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ""


def normalize_team_power_unit(constructor: str, power_unit: str | None) -> str:
    """Shorten Red Bull power-unit labels in teams headers."""
    if not power_unit:
        return ""

    normalized = power_unit.replace("-AMG", "").strip()
    constructor_name = (constructor or "").lower()
    is_red_bull_team = (
        "red bull" in constructor_name
        or "racing bulls" in constructor_name
        or constructor_name == "rb"
        or constructor_name.startswith("rb ")
    )
    if not is_red_bull_team:
        return normalized

    if normalized.startswith("Red Bull "):
        remainder = normalized.removeprefix("Red Bull ").strip()
        return f"RB {remainder}" if remainder else "RB"

    return normalized.replace("Red Bull", "RB")


def format_team_driver_display_name(name: str) -> str:
    """Format a driver name as `Given SURNAME` for team cards."""
    name_parts = name.replace(" Jr.", "").replace(" jr.", "").split()
    if len(name_parts) >= 2:
        given = name_parts[0]
        surname = " ".join(name_parts[1:]).upper()
        return f"{given} {surname}"
    return name.upper()


def format_points(value: float | int | None) -> str:
    """Format points while preserving half-points for display."""
    if value is None or value == 0:
        return "0"
    value_float = float(value)
    if value_float.is_integer():
        return str(int(value_float))
    return f"{value_float:.1f}"


def build_team_header_values(team) -> tuple[str, str, str, str]:
    """Build normalized constructor header strings for a team card."""
    constructor = team.constructor_name or team.entrant or ""
    team_name = constructor.split("-")[0].replace(" Aramco", "").replace("Kick ", "").strip()
    chassis = team.chassis or ""
    power_unit = normalize_team_power_unit(constructor, team.power_unit)
    meta_text = " | ".join(part for part in (chassis, power_unit) if part)
    team_pos = str(team.position) if team.position else "—"
    return team_name, meta_text, team_pos, format_points(team.points)


def normalize_session_name(name: str) -> str:
    """Normalize API/static session variants to a stable translation key suffix."""
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
    return aliases.get(normalized, normalized)


def abbreviate_schedule_term(term: str, lang_code: str) -> str:
    """Reduce a localized schedule term to its leading letter or character."""
    stripped = term.strip()
    if not stripped:
        return term
    first_char = stripped[0]
    if lang_code in CJK_LANG_CODES:
        return first_char
    return f"{first_char}."


def build_sprint_qualifying_label(
    translator: Mapping[str, str],
    lang_code: str,
    *,
    abbreviated: bool,
) -> str:
    """Compose the sprint qualifying label from localized sprint/qualifying text."""
    sprint_label = translator.get("session_sprint", "Sprint")
    qualifying_label = translator.get("session_qualifying", "Qualifying")
    separator = "" if lang_code in CJK_LANG_CODES else " "

    if abbreviated:
        qualifying_label = abbreviate_schedule_term(qualifying_label, lang_code)

    return f"{sprint_label}{separator}{qualifying_label}"


def get_dedicated_sprint_qualifying_label(translator: Mapping[str, str]) -> str | None:
    """Return a locale-specific sprint-qualifying label when one is defined."""
    label = translator.get("session_sprintqualifying")
    if isinstance(label, str) and label:
        return label
    return None


def translate_session_name(name: str, translator: Mapping[str, str], lang_code: str) -> str:
    """Translate session names while normalizing API/static variants."""
    if not name:
        return ""

    normalized = normalize_session_name(name)
    if normalized == "sprintqualifying":
        return get_dedicated_sprint_qualifying_label(translator) or build_sprint_qualifying_label(
            translator, lang_code, abbreviated=False
        )

    direct_key = f"session_{name.lower()}"
    if direct_key in translator:
        return translator[direct_key]

    normalized_key = f"session_{normalized}"
    return translator.get(normalized_key, name)


def format_schedule_session_name(
    draw: ImageDraw.ImageDraw,
    name: str,
    max_width: int,
    lang_code: str,
    translator: Mapping[str, str],
) -> str:
    """Return the best-fitting localized schedule label for a session."""
    if normalize_session_name(name) != "sprintqualifying":
        return translate_session_name(name, translator, lang_code)

    dedicated_label = get_dedicated_sprint_qualifying_label(translator)
    if dedicated_label:
        return dedicated_label

    full_label = build_sprint_qualifying_label(translator, lang_code, abbreviated=False)
    full_font = fit_ui_font(
        draw,
        lang_code,
        full_label,
        max_width=max_width,
        base_size=20,
        min_size=15,
        bold=True,
    )
    full_bbox = draw.textbbox((0, 0), full_label, font=full_font)
    if full_bbox[2] - full_bbox[0] <= max_width:
        return full_label

    return build_sprint_qualifying_label(translator, lang_code, abbreviated=True)


def get_team_logo_key(constructor: str) -> str | None:
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


def crop_to_content(img: Image.Image, *, use_binary_mask: bool = False) -> Image.Image:
    """Crop a logo to visible content, respecting transparency when present."""
    if "A" in img.getbands():
        alpha = img.getchannel("A")
        extrema = alpha.getextrema()
        alpha_min = extrema[0] if extrema is not None else None
        if not isinstance(alpha_min, tuple) and alpha_min is not None and alpha_min < 255:
            bbox = alpha.getbbox()
            if bbox:
                return img.crop(bbox)

    inverted = ImageOps.invert(img.convert("L"))
    if use_binary_mask:
        inverted = inverted.convert("1")
    bbox = inverted.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def _has_transparent_alpha(alpha: Image.Image | None) -> bool:
    if alpha is None:
        return False
    alpha_extrema = alpha.getextrema()
    alpha_min = alpha_extrema[0] if alpha_extrema is not None else None
    return not isinstance(alpha_min, tuple) and alpha_min is not None and alpha_min < 255


def _pixel_activity_value(pixel: object) -> float:
    if isinstance(pixel, tuple):
        return float(max(pixel)) if pixel else 0.0
    if isinstance(pixel, int | float):
        return float(pixel)
    return 0.0


def _active_pixel_counts(mask: Image.Image, *, threshold: float = 16) -> list[int]:
    rows = []
    for y in range(mask.height):
        active = 0
        for x in range(mask.width):
            if _pixel_activity_value(mask.getpixel((x, y))) > threshold:
                active += 1
        rows.append(active)
    return rows


def _find_horizontal_segments(
    rows: Sequence[int],
    *,
    threshold: int = 5,
) -> list[tuple[int, int, int]]:
    segments: list[tuple[int, int, int]] = []
    start: int | None = None
    for index, count in enumerate(rows):
        if count > threshold:
            if start is None:
                start = index
            continue
        if start is None:
            continue
        segment_rows = rows[start:index]
        segments.append((start, index, max(segment_rows) if segment_rows else 0))
        start = None
    if start is not None:
        segment_rows = rows[start:]
        segments.append((start, len(rows), max(segment_rows) if segment_rows else 0))
    return segments


def _preserves_stacked_logo(
    img: Image.Image,
    first_segment: tuple[int, int, int],
    second_segment: tuple[int, int, int],
) -> bool:
    first_start, first_end, first_peak = first_segment
    second_start, second_end, second_peak = second_segment
    first_height = first_end - first_start
    second_height = second_end - second_start
    gap = second_start - first_end

    min_gap = max(8, img.height // 30)
    min_primary_height = max(12, img.height // 5)
    return (
        gap < min_gap
        or first_height < min_primary_height
        or first_height < second_height
        or first_peak < second_peak
    )


def crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
    """Keep only the dominant upper band for tall stacked logo assets."""
    alpha = img.getchannel("A") if "A" in img.getbands() else None
    if _has_transparent_alpha(alpha) and alpha is not None:
        mask = alpha
    else:
        mask = ImageOps.invert(img.convert("L"))
    segments = _find_horizontal_segments(_active_pixel_counts(mask))

    if len(segments) < 2:
        return img

    first_start, first_end, _first_peak = segments[0]
    if _preserves_stacked_logo(img, segments[0], segments[1]):
        return img

    return img.crop((0, first_start, img.width, first_end))


def fit_result_text(
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
    pos: int,
    driver: str,
    team: str,
) -> str:
    """Fit historical results text into the available width."""

    def get_width(text: str) -> int:
        return int(draw.textbbox((0, 0), text, font=font)[2])

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


def get_country_flag_iso_code(country_name: str, country_map: dict[str, str]) -> str:
    """Resolve a country name to the local flag asset ISO code."""
    iso_code = country_map.get(country_name, "").lower()
    if iso_code:
        return iso_code

    aliases = {"UAE": "ae", "UK": "gb", "USA": "us"}
    return aliases.get(country_name, country_name[:2].lower())


def load_results_flag_image(
    country_name: str,
    country_map: dict[str, str],
    flags_dirs: Path | Sequence[Path],
    prepare_flag_image,
    logger,
) -> Image.Image | None:
    """Load and normalize the first matching local results flag image for a country."""
    iso_code = get_country_flag_iso_code(country_name, country_map)
    if not iso_code:
        return None

    directories: tuple[Path, ...]
    if isinstance(flags_dirs, Path):
        directories = (flags_dirs,)
    else:
        directories = tuple(flags_dirs)

    for directory in directories:
        local_flag_path = directory / f"{iso_code}.bmp"
        if not local_flag_path.exists():
            continue

        try:
            with Image.open(local_flag_path) as opened_flag:
                return prepare_flag_image(opened_flag)
        except Exception as exc:
            logger.warning("Failed to load local flag %s: %s", local_flag_path, exc)

    return None


def draw_results_header(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    *,
    canvas_height: int,
    header_area_width: int,
    y_start: int,
    season: int | str,
    country_name: str,
    year_font,
    text_fill,
    outline_fill,
    country_map: dict[str, str],
    flags_dirs: Path | Sequence[Path],
    prepare_flag_image,
    logger,
) -> int:
    """Render the year and optional country flag for the results footer."""
    year_text = str(season)
    bbox = draw.textbbox((0, 0), year_text, font=year_font)
    year_text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    footer_height = canvas_height - y_start
    flag_img = load_results_flag_image(
        country_name,
        country_map,
        flags_dirs,
        prepare_flag_image,
        logger,
    )

    flag_h = 0
    if flag_img:
        max_flag_width = int(header_area_width * 0.8)
        if flag_img.width > max_flag_width:
            ratio = max_flag_width / flag_img.width
            flag_h = int(flag_img.height * ratio)
            flag_img = flag_img.resize((max_flag_width, flag_h), Image.Resampling.NEAREST)
        else:
            flag_h = flag_img.height

    standard_gap = 3
    total_block_h = text_height + (standard_gap if flag_h > 0 else 0) + flag_h
    visual_top = y_start + (footer_height - total_block_h) // 2

    year_x = (header_area_width - year_text_width) // 2
    text_y = visual_top - bbox[1]
    draw.text((year_x, text_y), year_text, fill=text_fill, font=year_font)

    if flag_img:
        x = (header_area_width - flag_img.width) // 2
        flag_top_y = int(canvas_height - flag_img.height - 4)
        image.paste(flag_img, (x, flag_top_y))
        draw.rectangle(
            [
                x - 1,
                flag_top_y - 1,
                x + flag_img.width,
                flag_top_y + flag_img.height,
            ],
            outline=outline_fill,
            width=1,
        )

    return int(visual_top)


def draw_results_section(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    *,
    canvas_width: int,
    separator_fill,
    separator_width: int,
    y_start: int,
    race_data: dict,
    historical_data,
    results_col1_x: int,
    results_col2_x: int,
    qualifying_title: str,
    race_title: str,
    draw_new_track_message_fn,
    draw_results_header_fn,
    draw_results_column_fn,
) -> None:
    """Draw the shared footer flow for historical qualifying and race results."""
    draw.line(
        [(0, y_start), (canvas_width, y_start)],
        fill=separator_fill,
        width=separator_width,
    )

    if historical_data is None or historical_data.is_new_track:
        draw_new_track_message_fn(draw, y_start)
        return

    season = historical_data.season or ""
    country = race_data.get("circuit", {}).get("country", "")
    visual_top = draw_results_header_fn(draw, image, y_start, season, country)

    draw_results_column_fn(
        draw,
        results_col1_x,
        visual_top,
        qualifying_title,
        historical_data.qualifying_results,
        is_qualifying=True,
    )

    draw_results_column_fn(
        draw,
        results_col2_x,
        visual_top,
        race_title,
        historical_data.race_results,
        is_qualifying=False,
    )


def draw_new_track_message(
    draw: ImageDraw.ImageDraw,
    *,
    canvas_width: int,
    y_start: int,
    message: str,
    font,
    fill,
) -> None:
    """Draw a centered new-track message when historical data is unavailable."""
    bbox = draw.textbbox((0, 0), message, font=font)
    message_width = bbox[2] - bbox[0]
    x = (canvas_width - message_width) // 2
    y = y_start + 30
    draw.text((x, y), message, fill=fill, font=font)


def load_symbol_icon_font(size: int, logger) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the Symbola fallback icon font used for symbols and emoji-style glyphs."""
    symbola_path = "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf"
    font = load_optional_truetype(
        symbola_path,
        size,
        label="Symbola",
        target_logger=logger,
    )
    return font or ImageFont.load_default()


def load_weather_icon_font(
    size: int,
    logger,
    load_icon_font,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the weather icon font with a Symbola fallback."""
    font_path = FONTS_DIR / "weathericons-regular-webfont.ttf"
    font = load_optional_truetype(
        font_path,
        size,
        label="Weather Icons",
        target_logger=logger,
    )
    return font or load_icon_font(size)


def load_racing_font(
    size: int,
    logger,
    load_ui_font_fallback,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the stylized racing number font with a UI-font fallback."""
    font_path = FONTS_DIR / "RacingSansOne-Regular.ttf"
    font = load_optional_truetype(
        font_path,
        size,
        label="Racing Sans One",
        target_logger=logger,
    )
    if font is not None:
        return font
    return load_ui_font_fallback(size, bold=True)


def draw_results_column(
    draw: ImageDraw.ImageDraw,
    *,
    x_start: int,
    visual_top: int,
    title: str,
    results: list,
    is_qualifying: bool,
    font_title,
    font_row,
    time_x: int,
    row_height: int,
    data_y_offset: int,
    text_fill,
    fit_result_text_fn,
    split_position_prefix: bool = False,
) -> None:
    """Draw one historical results column aligned with the footer header."""
    ref_bbox = draw.textbbox((0, 0), "Ay", font=font_title)
    header_y_anchor = visual_top - ref_bbox[1]
    draw.text((x_start, header_y_anchor), title, fill=text_fill, font=font_title)

    ref_bbox = draw.textbbox((0, 0), "Hg", font=font_title)
    header_visual_bottom = header_y_anchor + ref_bbox[3]

    row_bbox = draw.textbbox((0, 0), "1", font=font_row)
    y_rows_start = header_visual_bottom + data_y_offset - row_bbox[1]

    for i, entry in enumerate(results[:3]):
        y = y_rows_start + (i * row_height)
        pos = i + 1
        driver_name = entry.driver.display_name
        team = entry.constructor.name
        time_str = entry.q3_time or "" if is_qualifying else entry.time or ""
        max_width = time_x - x_start - 10
        text = fit_result_text_fn(draw, font_row, max_width, pos, driver_name, team)

        if split_position_prefix:
            pos_text = f"{pos}."
            draw.text((x_start, y), pos_text, fill=text_fill, font=font_row)
            pos_bbox = draw.textbbox((0, 0), pos_text, font=font_row)
            pos_width = pos_bbox[2] - pos_bbox[0]
            rest_text = text[len(pos_text) :]
            draw.text((x_start + pos_width, y), rest_text, fill=text_fill, font=font_row)
        else:
            draw.text((x_start, y), text, fill=text_fill, font=font_row)

        if time_str:
            draw.text((time_x, y), time_str, fill=text_fill, font=font_row)


def draw_teams_header(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    *,
    canvas_width: int,
    header_height: int,
    split_x: int,
    season: int,
    title: str,
    left_fill,
    divider_fill,
    right_fill,
    text_fill,
    brand_font,
    subtitle_font,
    draw_f1_logo_fn,
) -> None:
    """Draw the shared teams screen header layout."""
    draw.rectangle([(0, 0), (split_x, header_height)], fill=left_fill)
    draw.line([(0, header_height - 1), (split_x, header_height - 1)], fill=divider_fill, width=2)
    draw.rectangle([(split_x + 1, 0), (canvas_width, header_height)], fill=right_fill)

    draw_f1_logo_fn(image, split_x, header_height)

    line1 = f"{season} FIA F1 World Championship"
    line2 = title.upper()
    text_x = split_x + 15
    total_text_height = 80
    start_y = (header_height - total_text_height) // 2 - 5

    draw.text((text_x, start_y), line1, fill=text_fill, font=brand_font)
    draw.text((text_x, start_y + 40), line2, fill=text_fill, font=subtitle_font)


def draw_teams_content(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    teams: list,
    *,
    canvas_width: int,
    canvas_height: int,
    header_height: int,
    draw_team_row_fn,
) -> None:
    """Lay out the shared two-column teams card grid."""
    col_padding = 5
    split_x = canvas_width // 2
    gap = col_padding

    left_teams, right_teams = split_teams_for_columns(teams)
    teams_per_col = max(len(left_teams), len(right_teams), 1)
    row_gap = 2
    available_height = canvas_height - header_height - 8 - (teams_per_col - 1) * row_gap
    row_height = available_height // teams_per_col

    y = header_height + 4
    for team in left_teams:
        draw_team_row_fn(image, draw, col_padding, y, split_x - gap // 2, team, row_height)
        y += row_height + row_gap

    y = header_height + 4
    for team in right_teams:
        draw_team_row_fn(
            image,
            draw,
            split_x + gap // 2,
            y,
            canvas_width - col_padding,
            team,
            row_height,
        )
        y += row_height + row_gap


def draw_team_stats_panel(
    draw: ImageDraw.ImageDraw,
    *,
    y: int,
    header_height: int,
    panel_x: int,
    panel_right_x: int,
    team_pos: str,
    team_pts: str,
    stats_font,
    points_font,
    panel_fill,
    panel_outline,
    team_pos_fill,
    team_pts_fill,
) -> int:
    """Draw the shared team stats panel and return the left edge of the position cell."""
    panel_y = y + 2
    panel_h = header_height - 4
    panel_w = panel_right_x - panel_x
    stats_gap = 4
    pos_col_w = 24
    points_col_w = panel_w - pos_col_w - stats_gap
    pos_box_x = panel_x
    points_box_x = panel_x + pos_col_w + stats_gap

    def draw_panel_stat(
        text: str,
        box_x: int,
        box_w: int,
        font,
        fill,
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
        fill=panel_fill,
        outline=panel_outline,
    )
    draw_panel_stat(team_pos, pos_box_x, pos_col_w, stats_font, team_pos_fill)
    draw_panel_stat(team_pts, points_box_x, points_col_w, points_font, team_pts_fill, align="right")
    return pos_box_x


def draw_team_driver_row(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    driver,
    *,
    driver_y: int,
    driver_row_height: int,
    photo_x: int,
    photo_size: int,
    pts_right_x: int,
    driver_pos_x: int,
    badge_pad_x: int,
    small_font,
    driver_font,
    driver_name_padding: int,
    lang_code: str,
    draw_driver_photo_fn,
    get_text_y_fn,
    format_team_driver_display_name_fn,
    format_points_fn,
    right_align_x_fn,
    text_fill,
    badge_outline_fill,
    badge_colors_fn,
) -> None:
    """Draw one driver row inside a team card with renderer-specific colors via callbacks."""
    name = driver.name or f"{driver.given_name} {driver.family_name}".strip()
    if not name:
        name = driver.driver_code or "TBA"

    display_name = format_team_driver_display_name_fn(name)
    center_y = driver_y + driver_row_height // 2
    driver_small_y = get_text_y_fn(draw, small_font, driver_row_height, driver_y)

    photo_y = center_y - photo_size // 2
    draw_driver_photo_fn(
        draw,
        image,
        photo_x,
        photo_y,
        name,
        size=photo_size,
        driver_number=driver.driver_number,
    )
    driver_name_x = photo_x + photo_size + driver_name_padding + 4
    if lang_code in CJK_LANG_CODES:
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
    driver_text_y = get_text_y_fn(draw, driver_font, driver_row_height, driver_y, display_name)
    draw.text((driver_name_x, driver_text_y), display_name, fill=text_fill, font=driver_font)

    driver_pts = format_points_fn(driver.points)
    pos_text = f"P{driver.position}" if driver.position else "—"
    pts_x = right_align_x_fn(draw, driver_pts, pts_right_x, small_font)
    draw.text((pts_x, driver_small_y), driver_pts, fill=text_fill, font=small_font)

    if driver.position and driver.position <= 4:
        pos_bbox = draw.textbbox((0, 0), pos_text, font=small_font)
        pos_w = pos_bbox[2] - pos_bbox[0]
        pos_h = pos_bbox[3] - pos_bbox[1]
        badge_pad_y = 3
        badge_w = int(pos_w) + badge_pad_x * 2
        badge_h = int(pos_h) + badge_pad_y * 2
        badge_x = driver_pos_x - badge_pad_x
        badge_y = driver_y + (driver_row_height - badge_h) // 2
        badge_fill, badge_text_fill = badge_colors_fn(driver.position)
        draw.rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            fill=badge_fill,
            outline=badge_outline_fill,
        )
        draw.text(
            (badge_x + badge_pad_x, badge_y + badge_pad_y - pos_bbox[1]),
            pos_text,
            fill=badge_text_fill,
            font=small_font,
        )
        return

    draw.text((driver_pos_x, driver_small_y), pos_text, fill=text_fill, font=small_font)


def draw_team_row(
    _image: Image.Image,
    draw: ImageDraw.ImageDraw,
    team,
    *,
    x_start: int,
    y: int,
    x_end: int,
    row_height: int,
    team_font,
    tech_font,
    header_fill,
    header_text_fill,
    outline_fill,
    stats_padding: int,
    driver_name_padding: int,
    get_text_y_fn,
    build_team_header_values_fn,
    clamp_text_fn,
    draw_team_stats_panel_fn,
    draw_team_driver_row_fn,
    draw_team_logo_fn,
) -> None:
    """Draw the shared team-card layout and delegate renderer-specific row details."""
    _ = stats_padding
    header_height = 23
    box_y_end = y + row_height - 2
    draw.rectangle([(x_start, y), (x_end, box_y_end)], outline=outline_fill, width=1)
    draw.rectangle([(x_start, y), (x_end, y + header_height)], fill=header_fill)

    header_text_y = get_text_y_fn(draw, team_font, header_height, y)
    tech_text_y = get_text_y_fn(draw, tech_font, header_height, y)
    team_name, meta_text, _team_pos, _team_pts = build_team_header_values_fn(team)

    badge_pad_x = 5
    driver_pos_x = x_end - 72
    panel_x = driver_pos_x - badge_pad_x
    panel_right_x = x_end - 4
    pos_box_x = draw_team_stats_panel_fn(panel_x, panel_right_x, header_height, badge_pad_x)

    name_x = x_start + 4
    draw.text((name_x, header_text_y), team_name, fill=header_text_fill, font=team_font)

    name_bbox = draw.textbbox((0, 0), team_name, font=team_font)
    name_w = name_bbox[2] - name_bbox[0]
    meta_x = int(name_x + name_w + 8)
    meta_max_w = pos_box_x - meta_x - 6
    meta_text = clamp_text_fn(draw, meta_text, tech_font, meta_max_w)
    if meta_text:
        draw.text((meta_x, tech_text_y), meta_text, fill=header_text_fill, font=tech_font)

    driver_area_height = row_height - header_height - 4
    driver_row_height = driver_area_height // 2
    driver_y_start = y + header_height + 2
    pts_right_x = x_end - 4

    photo_size = driver_row_height - 2
    photo_x = x_start + 4

    sorted_drivers = select_active_team_drivers(team.drivers)
    for i, driver in enumerate(sorted_drivers):
        driver_y = driver_y_start + i * driver_row_height
        draw_team_driver_row_fn(
            driver,
            driver_y,
            driver_row_height,
            photo_x,
            photo_size,
            pts_right_x,
            driver_pos_x,
            badge_pad_x,
        )

    logo_container_right = driver_pos_x - 8
    driver_name_base_x = photo_x + photo_size + driver_name_padding + 4
    logo_container_left = max(driver_name_base_x + 170, logo_container_right - 96)
    draw_team_logo_fn(
        team,
        driver_y_start,
        driver_area_height,
        logo_container_left,
        logo_container_right,
    )


def normalize_driver_photo_key(driver_name: str) -> str:
    """Normalize a driver surname into the local portrait asset key."""
    parts = driver_name.split()
    if not parts:
        return ""
    surname = parts[-1].lower()
    if surname in ("jr.", "jr"):
        surname = parts[-2].rstrip(",").lower() if len(parts) > 1 else surname
    return (
        surname.replace("ü", "u")
        .replace("ö", "o")
        .replace("ä", "a")
        .replace("ß", "ss")
        .replace("é", "e")
        .replace("è", "e")
    )


def draw_driver_photo(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    *,
    x: int,
    y: int,
    driver_name: str,
    size: int,
    driver_number: int | None,
    driver_photos: dict[str, Image.Image] | None,
    get_racing_font_fn,
    number_fill,
    resample,
    paste_photo_fn,
) -> int:
    """Draw a driver number or portrait and return the consumed width."""
    surname = normalize_driver_photo_key(driver_name)

    if driver_number is not None:
        num_text = str(driver_number)
        font = get_racing_font_fn(size)
        bbox = draw.textbbox((0, 0), num_text, font=font)
        text_w = int(bbox[2] - bbox[0])
        text_h = int(bbox[3] - bbox[1])
        text_x = x + max(0, (size - text_w) // 2) - int(bbox[0])
        text_y = y + (size - text_h) // 2 - int(bbox[1])
        draw.text((text_x, text_y), num_text, fill=number_fill, font=font)
        return size

    driver_img = driver_photos.get(surname) if driver_photos else None
    if driver_img is None:
        return 0

    orig_w, orig_h = driver_img.size
    scale = size / orig_h
    new_w = int(orig_w * scale)
    new_h = size
    photo_resized = driver_img.resize((new_w, new_h), resample)
    paste_photo_fn(image, photo_resized, x, y)
    return new_w + 2


def draw_race_header(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    race_data: dict,
    *,
    canvas_width: int,
    header_height: int,
    split_x: int,
    left_fill,
    divider_fill,
    right_fill,
    title_fill,
    header_title_font,
    header_subtitle_font,
    draw_f1_logo_fn,
) -> None:
    """Draw the shared race header with logo and two-line title block."""
    draw.rectangle([(0, 0), (split_x, header_height)], fill=left_fill)
    draw.line([(0, header_height - 1), (split_x, header_height - 1)], fill=divider_fill, width=2)
    draw.rectangle([(split_x + 1, 0), (canvas_width, header_height)], fill=right_fill)

    draw_f1_logo_fn(image, split_x, header_height)

    race_name = race_data.get("race_name", "Grand Prix")
    season = race_data.get("season", "")
    line1 = f"{season} FIA F1 World Championship"
    line2 = race_name.upper()

    text_x = split_x + 15
    total_text_height = 80
    start_y = (header_height - total_text_height) // 2 - 5

    draw.text((text_x, start_y), line1, fill=title_fill, font=header_title_font)
    draw.text((text_x, start_y + 40), line2, fill=title_fill, font=header_subtitle_font)


def draw_f1_logo(
    image: Image.Image,
    width: int,
    height: int,
    *,
    logo_path: Path,
    logger,
    prepare_logo_fn,
) -> None:
    """Load, fit, and center the shared F1 logo in a header area."""
    if not logo_path.exists():
        logger.warning("F1 logo not found at %s", logo_path)
        return

    try:
        logo_file = _load_image_copy(logo_path)
        pad = 2
        target_w = width - (pad * 2)
        target_h = height - (pad * 2)
        logo_file.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        logo = prepare_logo_fn(logo_file)
        x = (width - logo.width) // 2
        y = (height - logo.height) // 2
        image.paste(logo, (x, y))
    except Exception as exc:
        logger.warning("Failed to load F1 logo: %s", exc)


def draw_track_placeholder(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    outline_fill,
) -> None:
    """Draw a rounded fallback placeholder when no track asset is available."""
    draw.rounded_rectangle(
        [(x + 20, y + 20), (x + width - 20, y + height - 20)],
        radius=20,
        outline=outline_fill,
        width=3,
    )


def build_track_stems(race_data: dict) -> list[str]:
    """Build ordered candidate track asset stems from race circuit metadata."""
    circuit = race_data.get("circuit", {})
    circuit_id = str(circuit.get("circuitId", "") or "")
    location = str(circuit.get("location", "") or "")
    normalized_id = str(CIRCUIT_ID_MAP.get(circuit_id, circuit_id))
    return build_track_stem_candidates(normalized_id, circuit_id, location)


def load_track_image_asset(
    track_stems: list[str],
    *,
    source_dir: Path,
    variant_suffix: str,
    fallback_dir: Path | None = None,
    fallback_glob: str | None = None,
    logger=None,
) -> Image.Image | None:
    """Load the best available track image from source assets and optional fallback BMPs."""
    if not track_stems:
        return None

    source_path = resolve_track_source_path(source_dir, track_stems, variant_suffix=variant_suffix)
    if source_path:
        try:
            return _load_image_copy(source_path)
        except Exception as exc:
            if logger is not None:
                logger.warning("Failed to load track %s: %s", source_path, exc)

    if fallback_dir is not None:
        for stem in track_stems:
            track_path = fallback_dir / f"{stem}.bmp"
            if not track_path.exists():
                continue
            try:
                return _load_image_copy(track_path)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Failed to load track %s: %s", track_path, exc)

    if fallback_dir is not None and fallback_glob:
        all_fallback = list(fallback_dir.glob(fallback_glob))
        if all_fallback:
            try:
                return _load_image_copy(all_fallback[0])
            except Exception as exc:
                if logger is not None:
                    logger.warning("Failed to load track %s: %s", all_fallback[0], exc)

    return None


def draw_countdown_box(
    draw: ImageDraw.ImageDraw,
    race_data: dict,
    *,
    schedule_bottom: int,
    right_column_x: int,
    canvas_width: int,
    results_y_start: int,
    circuit_stats_row_height: int,
    schedule_row_bold_font,
    icon_small_font,
    weather_icon_font,
    translator: Mapping[str, str],
    lang_code: str,
    datetime_cls,
    text_baseline_ref: str,
    rain_icon: str,
    box_fill,
    box_outline,
    text_fill,
    weather_data=None,
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
                race_dt = datetime_cls.fromisoformat(dt)
            elif isinstance(dt, datetime):
                race_dt = dt
            break

    if not is_cancelled and not race_dt:
        return schedule_bottom

    ref_bbox = draw.textbbox((0, 0), text_baseline_ref, font=schedule_row_bold_font)
    text_height = ref_bbox[3] - ref_bbox[1]

    padding_y = 3
    padding_x = 12
    box_height = text_height + 2 * padding_y

    x_left = right_column_x
    x_right = canvas_width - 5

    stats_top_y = results_y_start - 3 - (3 * circuit_stats_row_height)
    available_height = stats_top_y - schedule_bottom
    y_top = schedule_bottom + (available_height - box_height) // 2
    y_bottom = y_top + box_height

    draw.rectangle([x_left, y_top, x_right, y_bottom], fill=box_fill, outline=box_outline)

    text_y = y_top + padding_y - ref_bbox[1]

    status_text = None
    if is_cancelled:
        status_text = translator.get("cancelled", "CANCELLED")
    else:
        if race_dt is None:
            return schedule_bottom

        active_race_dt = race_dt
        now = (
            datetime_cls.now(active_race_dt.tzinfo) if active_race_dt.tzinfo else datetime_cls.now()
        )
        delta = active_race_dt - now

        if delta.total_seconds() <= 0:
            status_key = (
                "race_ongoing" if now < active_race_dt + timedelta(hours=3) else "race_completed"
            )
            status_text = translator.get(
                status_key,
                "IN PROGRESS" if status_key == "race_ongoing" else "COMPLETED",
            )

    def draw_weather_block() -> None:
        temp_str = f"{weather_data.temp_display} "
        precip_str = weather_data.precip_display

        weather_icon_bbox = draw.textbbox((0, 0), weather_data.icon, font=weather_icon_font)
        weather_icon_w = weather_icon_bbox[2] - weather_icon_bbox[0]
        temp_bbox = draw.textbbox((0, 0), temp_str, font=schedule_row_bold_font)
        temp_w = temp_bbox[2] - temp_bbox[0]
        rain_icon_bbox = draw.textbbox((0, 0), rain_icon, font=weather_icon_font)
        rain_icon_w = rain_icon_bbox[2] - rain_icon_bbox[0]
        precip_bbox = draw.textbbox((0, 0), precip_str, font=schedule_row_bold_font)
        precip_w = precip_bbox[2] - precip_bbox[0]

        total_w = weather_icon_w + 4 + temp_w + rain_icon_w + 3 + precip_w
        cur_x = x_right - padding_x - total_w

        draw.text((cur_x, text_y), weather_data.icon, fill=text_fill, font=weather_icon_font)
        cur_x += weather_icon_w + 4
        draw.text((cur_x, text_y), temp_str, fill=text_fill, font=schedule_row_bold_font)
        cur_x += temp_w
        draw.text((cur_x, text_y), rain_icon, fill=text_fill, font=weather_icon_font)
        cur_x += rain_icon_w + 3
        draw.text((cur_x, text_y), precip_str, fill=text_fill, font=schedule_row_bold_font)

    if status_text:
        show_weather = weather_data is not None and not is_cancelled
        status_bbox = draw.textbbox((0, 0), status_text, font=schedule_row_bold_font)
        status_w = status_bbox[2] - status_bbox[0]
        text_x = (
            x_left + padding_x if show_weather else x_left + ((x_right - x_left) - status_w) // 2
        )
        draw.text((text_x, text_y), status_text, fill=text_fill, font=schedule_row_bold_font)
        if not show_weather:
            return int(y_bottom)
        draw_weather_block()
        return int(y_bottom)

    if race_dt is None:
        return schedule_bottom

    active_race_dt = race_dt
    now = datetime_cls.now(active_race_dt.tzinfo) if active_race_dt.tzinfo else datetime_cls.now()
    delta = active_race_dt - now
    if delta.total_seconds() <= 0:
        return schedule_bottom

    days = delta.days
    hours = delta.seconds // 3600

    flag_icon = "🏁"
    if weather_type in ("current", "race_day", "race"):
        days_label = translator.get("countdown_days_short", "d")
        hours_label = translator.get("countdown_hours_short", "h")
    else:

        def plural_category(value: int) -> str:
            if value == 1:
                return "one"
            if lang_code in {"cs", "sk"} and 2 <= value <= 4:
                return "few"
            if lang_code == "pl" and value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
                return "few"
            return "many"

        days_label = translator.get(
            f"countdown_days_{plural_category(days)}",
            translator.get("countdown_days", "days"),
        )
        hours_label = translator.get(
            f"countdown_hours_{plural_category(hours)}",
            translator.get("countdown_hours", "hours"),
        )
    countdown_str = f"{days} {days_label} {hours} {hours_label}"

    flag_bbox = draw.textbbox((0, 0), flag_icon, font=icon_small_font)
    flag_w = flag_bbox[2] - flag_bbox[0]
    countdown_bbox = draw.textbbox((0, 0), countdown_str, font=schedule_row_bold_font)
    countdown_w = countdown_bbox[2] - countdown_bbox[0]
    total_content_w = flag_w + 6 + countdown_w

    if weather_data:
        cur_x = int(x_left + padding_x)
    else:
        box_width = x_right - x_left
        cur_x = int(x_left + (box_width - total_content_w) // 2)

    draw.text((cur_x, text_y), flag_icon, fill=text_fill, font=icon_small_font)
    cur_x += int(flag_w + 6)
    draw.text((cur_x, text_y), countdown_str, fill=text_fill, font=schedule_row_bold_font)

    if weather_data:
        draw_weather_block()

    return int(y_bottom)


def draw_circuit_stats_block(
    draw: ImageDraw.ImageDraw,
    circuit_data: dict,
    *,
    translator: Mapping[str, str],
    results_y_start: int,
    right_column_x: int,
    canvas_width: int,
    row_height: int,
    font_icon,
    font_value,
    fill,
) -> None:
    """Draw the circuit facts block between the schedule and results areas."""
    stats: list[tuple[str, str]] = []

    length = circuit_data.get("circuit_length")
    laps = circuit_data.get("number_of_laps")
    if length:
        line1 = f"{length}"
        if laps:
            line1 += f" | {laps} " + translator.get("laps", "laps")
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
        stats.append(("🗓", f"{translator.get('first_gp', 'First GP')}: {first_gp}"))

    if not stats:
        return

    total_stats_height = len(stats) * row_height
    y_start = results_y_start - 3 - total_stats_height

    max_icon_width: float = 0
    for icon, _text in stats:
        icon_bbox = draw.textbbox((0, 0), icon, font=font_icon)
        icon_width = icon_bbox[2] - icon_bbox[0]
        max_icon_width = max(max_icon_width, icon_width)

    max_text_width: float = 0
    for _icon, text_value in stats:
        text_bbox = draw.textbbox((0, 0), text_value, font=font_value)
        value_text_width = text_bbox[2] - text_bbox[0]
        max_text_width = max(max_text_width, value_text_width)

    icon_text_gap = 4
    total_block_width = max_icon_width + icon_text_gap + max_text_width

    right_margin = 5
    block_x = max(right_column_x, canvas_width - right_margin - total_block_width)
    text_x = block_x + max_icon_width + icon_text_gap

    y = y_start
    for icon, text_value in stats:
        icon_bbox = draw.textbbox((0, 0), icon, font=font_icon)
        icon_width = icon_bbox[2] - icon_bbox[0]
        icon_x = block_x + (max_icon_width - icon_width)
        draw.text((icon_x, y), icon, fill=fill, font=font_icon)
        draw.text((text_x, y), text_value, fill=fill, font=font_value)
        y += row_height


def draw_track_section(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    race_data: dict,
    *,
    left_column_width: int,
    results_y_start: int,
    padding: int,
    label_font,
    label_fill,
    load_track_image_fn,
    prepare_track_image_fn,
    paste_track_image_fn,
    draw_track_placeholder_fn,
) -> None:
    """Draw the left-side track map and circuit label block."""
    circuit = race_data.get("circuit", {})
    circuit_name = circuit.get("name", "Circuit")
    country = circuit.get("country", "").upper()
    city = circuit.get("location", "").upper()

    label_text = f"{country}, {city} | {circuit_name}" if city else f"{country} | {circuit_name}"
    label_bbox = draw.textbbox((0, 0), label_text, font=label_font)
    label_y = results_y_start - 3 - label_bbox[3]
    text_visual_top = label_y + label_bbox[1]

    side_margin = 3
    track_top = 92
    track_bottom = text_visual_top - side_margin
    available_height = track_bottom - track_top
    available_width = left_column_width - (side_margin * 2)

    track_image = load_track_image_fn(race_data)
    if track_image:
        prepared_image = prepare_track_image_fn(track_image, available_width, available_height)
        final_w, final_h = prepared_image.size
        paste_x = int(side_margin + (available_width - final_w) // 2)
        paste_y = int(track_top + (available_height - final_h) // 2)
        paste_track_image_fn(image, prepared_image, paste_x, paste_y)
    else:
        draw_track_placeholder_fn(
            draw,
            side_margin,
            track_top,
            int(available_width),
            int(available_height),
        )

    draw.text((padding, label_y), label_text, fill=label_fill, font=label_font)


def prepare_mono_track_image(
    track_image: Image.Image,
    available_width: int,
    available_height: int,
    logger,
) -> Image.Image:
    """Prepare a track map for the monochrome renderer."""
    is_preprocessed = track_image.mode == "1"

    if not is_preprocessed:
        try:
            gray = track_image.convert("L")
            binary = gray.point(lambda p: 255 if p > 128 else 0)
            inverted = ImageOps.invert(binary)
            bbox = inverted.getbbox()
            if bbox:
                track_image = track_image.crop(bbox)
        except Exception as exc:
            logger.warning("Failed to crop track image: %s", exc)

    img_w, img_h = track_image.size
    ratio = min(available_width / img_w, available_height / img_h)
    new_size = (int(img_w * ratio), int(img_h * ratio))

    if new_size != (img_w, img_h):
        track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

    if not is_preprocessed:
        track_image = track_image.point(lambda p: 255 if p > 200 else 0)
        track_image = track_image.convert("1")

    return track_image


def prepare_color_track_image(
    track_image: Image.Image,
    available_width: int,
    available_height: int,
) -> Image.Image:
    """Prepare a track map for color renderers while preserving RGB output."""
    img_w, img_h = track_image.size
    ratio = min(available_width / img_w, available_height / img_h)
    new_size = (int(img_w * ratio), int(img_h * ratio))

    if new_size != (img_w, img_h):
        track_image = track_image.resize(new_size, Image.Resampling.LANCZOS)

    return track_image.convert("RGB")


def draw_schedule_row(
    draw: ImageDraw.ImageDraw,
    *,
    y: int,
    event: dict,
    canvas_width: int,
    schedule_date_x: int,
    schedule_day_x: int,
    schedule_time_x: int,
    schedule_name_x: int,
    translator: Mapping[str, str],
    lang_code: str,
    font_reg,
    regular_text_fill,
    session_text_fill,
    format_schedule_session_name_fn,
    session_text_shadow_fill=None,
    session_text_shadow_offset: tuple[int, int] = (1, 1),
) -> None:
    """Draw one localized schedule row using shared date/time/name layout."""
    dt = event.get("datetime")
    name = event.get("name", "")

    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)

    if dt:
        date_str = dt.strftime("%d.%m.")
        day_key = f"day_{dt.strftime('%a').lower()}"
        day_str = translator.get(day_key, dt.strftime("%a"))
        time_str = dt.strftime("%H:%M")
    else:
        date_str = ""
        day_str = ""
        time_str = event.get("display_time", "")

    name_max_width = canvas_width - schedule_name_x - 5
    translated_name = format_schedule_session_name_fn(draw, name, name_max_width)
    font_bold = fit_ui_font(
        draw,
        lang_code,
        translated_name,
        max_width=name_max_width,
        base_size=20,
        min_size=15,
        bold=True,
    )

    draw.text((schedule_date_x, y), date_str, fill=regular_text_fill, font=font_reg)
    draw.text((schedule_day_x, y), day_str, fill=regular_text_fill, font=font_reg)
    draw.text((schedule_time_x, y), time_str, fill=regular_text_fill, font=font_reg)
    if session_text_shadow_fill is not None and session_text_shadow_fill != session_text_fill:
        shadow_x = schedule_name_x + session_text_shadow_offset[0]
        shadow_y = y + session_text_shadow_offset[1]
        draw.text(
            (shadow_x, shadow_y),
            translated_name,
            fill=session_text_shadow_fill,
            font=font_bold,
        )
    draw.text((schedule_name_x, y), translated_name, fill=session_text_fill, font=font_bold)


def draw_schedule_section(
    draw: ImageDraw.ImageDraw,
    race_data: dict,
    *,
    canvas_width: int,
    right_column_x: int,
    schedule_title_y: int,
    schedule_start_y: int,
    schedule_row_height: int,
    results_y_start: int,
    translator: Mapping[str, str],
    lang_code: str,
    title_fill,
    draw_schedule_row_fn,
    draw_countdown_box_fn,
    weather_data,
    weather_type: str,
) -> int:
    """Draw the schedule title, rows, and countdown area."""
    schedule_title = translator.get("weekend_schedule", "WEEKEND SCHEDULE")
    schedule_title_font = fit_ui_font(
        draw,
        lang_code,
        schedule_title,
        max_width=canvas_width - right_column_x - 5,
        base_size=24,
        min_size=18,
        bold=True,
    )
    draw.text(
        (right_column_x, schedule_title_y),
        schedule_title,
        fill=title_fill,
        font=schedule_title_font,
    )

    schedule = race_data.get("schedule", [])
    row_y = schedule_start_y

    for event in schedule:
        draw_schedule_row_fn(draw, row_y, event)
        row_y += schedule_row_height
        if row_y > results_y_start - 80:
            break

    return draw_countdown_box_fn(draw, race_data, row_y + 10, weather_data, weather_type)


def draw_team_logo(
    image: Image.Image,
    team,
    *,
    team_logos: dict[str, Image.Image] | None,
    get_team_logo_key_fn,
    driver_area_y: int,
    driver_area_h: int,
    container_left: int,
    container_right: int,
    paste_logo_fn,
) -> None:
    """Draw a centered team logo inside the reserved logo container."""
    if not team_logos:
        return

    constructor = team.constructor_name or team.entrant or ""
    logo_key = get_team_logo_key_fn(constructor)
    if not logo_key:
        return

    logo = team_logos.get(logo_key)
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
    logo_x = container_left + (container_w - new_w) // 2
    logo_y = driver_area_y + (driver_area_h - new_h) // 2
    paste_logo_fn(image, logo_resized, logo_x, logo_y)
