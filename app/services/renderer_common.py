"""Shared stateless helpers used by multiple renderer variants."""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.services.font_utils import CJK_LANG_CODES, FONTS_DIR, fit_ui_font


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
    return int(right_edge - (bbox[2] - bbox[0]))


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
    if value in (None, 0):
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
    translator: dict[str, str] | object,
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


def translate_session_name(name: str, translator: dict[str, str] | object, lang_code: str) -> str:
    """Translate session names while normalizing API/static variants."""
    if not name:
        return ""

    normalized = normalize_session_name(name)
    if normalized == "sprintqualifying":
        return build_sprint_qualifying_label(translator, lang_code, abbreviated=False)

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
    translator: dict[str, str] | object,
) -> str:
    """Return the best-fitting localized schedule label for a session."""
    if normalize_session_name(name) != "sprintqualifying":
        return translate_session_name(name, translator, lang_code)

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
        if alpha.getextrema()[0] < 255:
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


def crop_primary_horizontal_band(img: Image.Image) -> Image.Image:
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
    flags_dir: Path,
    prepare_flag_image,
    logger,
) -> Image.Image | None:
    """Load and normalize the local results flag image for a country."""
    iso_code = get_country_flag_iso_code(country_name, country_map)
    if not iso_code:
        return None

    local_flag_path = flags_dir / f"{iso_code}.bmp"
    if not local_flag_path.exists():
        return None

    try:
        with Image.open(local_flag_path) as opened_flag:
            return prepare_flag_image(opened_flag)
    except Exception as exc:
        logger.warning("Failed to load local flag: %s", exc)
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
    flags_dir: Path,
    prepare_flag_image,
    logger,
) -> int:
    """Render the year and optional country flag for the results footer."""
    year_text = str(season)
    bbox = draw.textbbox((0, 0), year_text, font=year_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    footer_height = canvas_height - y_start
    flag_img = load_results_flag_image(
        country_name,
        country_map,
        flags_dir,
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

    year_x = (header_area_width - text_width) // 2
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
    text_width = bbox[2] - bbox[0]
    x = (canvas_width - text_width) // 2
    y = y_start + 30
    draw.text((x, y), message, fill=fill, font=font)

def load_symbol_icon_font(size: int, logger) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the Symbola fallback icon font used for symbols and emoji-style glyphs."""
    symbola_path = "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf"
    try:
        return ImageFont.truetype(symbola_path, size)
    except Exception as exc:
        logger.warning("Failed to load Symbola font: %s", exc)
        return ImageFont.load_default()


def load_weather_icon_font(
    size: int,
    logger,
    load_icon_font,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the weather icon font with a Symbola fallback."""
    font_path = FONTS_DIR / "weathericons-regular-webfont.ttf"
    try:
        return ImageFont.truetype(str(font_path), size)
    except Exception as exc:
        logger.warning("Failed to load Weather Icons font: %s", exc)
        return load_icon_font(size)


def load_racing_font(
    size: int,
    logger,
    load_ui_font_fallback,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the stylized racing number font with a UI-font fallback."""
    font_path = FONTS_DIR / "RacingSansOne-Regular.ttf"
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception as exc:
            logger.warning("Failed to load Racing Sans One: %s", exc)
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

