"""Teams-and-drivers screen section drawing."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from app.services.font_utils import CJK_LANG_CODES, fit_brand_font_box, load_brand_font
from app.services.renderer_text import (
    clamp_text,
    select_active_team_drivers,
    split_teams_for_columns,
)

DRIVER_NAME_MAX_SIZE = 18
DRIVER_NAME_MIN_SIZE = 15
CJK_DRIVER_NAME_MIN_SIZE = 12


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
        """Draw one aligned value inside a team statistics panel cell."""
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


def fit_team_driver_name(
    draw: ImageDraw.ImageDraw,
    display_name: str,
    *,
    max_width: int,
    max_height: int | None = None,
) -> tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    """Return the driver name and the largest bold brand font that fits ``max_width``.

    Without ``max_height`` only the width is checked between the regular driver sizes.
    With ``max_height`` (used for CJK locales, whose fallback glyphs are taller) the font
    must fit both width and height and may shrink down to ``CJK_DRIVER_NAME_MIN_SIZE``.
    In both cases a name still too wide at the minimum size is ellipsized so its right
    edge stays within ``max_width``.
    """
    if max_height is not None:
        font = fit_brand_font_box(
            draw,
            display_name,
            max_width=max_width,
            max_height=max_height,
            base_size=DRIVER_NAME_MAX_SIZE,
            min_size=CJK_DRIVER_NAME_MIN_SIZE,
            bold=True,
        )
    else:
        for size in range(DRIVER_NAME_MAX_SIZE, DRIVER_NAME_MIN_SIZE - 1, -1):
            font = load_brand_font(size, bold=True)
            if int(draw.textbbox((0, 0), display_name, font=font)[2]) <= max_width:
                return display_name, font
        font = load_brand_font(DRIVER_NAME_MIN_SIZE, bold=True)
    left_bearing = int(draw.textbbox((0, 0), display_name, font=font)[0])
    return clamp_text(draw, display_name, font, max_width - left_bearing), font


def _centre_driver_glyphs(bbox, driver_y: int, driver_row_height: int) -> tuple[int, int, int]:
    """Return text y, box top and box height centred on the glyphs within a driver row."""
    glyph_h = bbox[3] - bbox[1]
    pad_y = max(0, min(3, (driver_row_height - 1 - glyph_h) // 2))
    box_h = int(glyph_h) + 2 * pad_y
    box_top = driver_y + (driver_row_height - 1 - box_h) // 2
    return box_top + pad_y - bbox[1], box_top, box_h


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
    driver_name_padding: int,
    draw_driver_photo_fn,
    get_text_y_fn,
    format_team_driver_display_name_fn,
    format_points_fn,
    right_align_x_fn,
    text_fill,
    badge_outline_fill,
    badge_colors_fn,
    lang_code: str,
) -> None:
    """Draw one driver row inside a team card with renderer-specific colors via callbacks."""
    name = driver.name or f"{driver.given_name} {driver.family_name}".strip()
    if not name:
        name = driver.driver_code or "TBA"

    display_name = format_team_driver_display_name_fn(name)
    center_y = driver_y + driver_row_height // 2

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
    display_name, driver_font = fit_team_driver_name(
        draw,
        display_name,
        max_width=driver_pos_x - 8 - driver_name_x,
        max_height=max(1, driver_row_height - 1) if lang_code in CJK_LANG_CODES else None,
    )
    driver_text_y = get_text_y_fn(draw, driver_font, driver_row_height, driver_y, display_name)
    draw.text((driver_name_x, driver_text_y), display_name, fill=text_fill, font=driver_font)

    driver_pts = format_points_fn(driver.points)
    pos_text = f"P{driver.position}" if driver.position else "—"
    pts_x = right_align_x_fn(draw, driver_pts, pts_right_x, small_font)
    pts_bbox = draw.textbbox((0, 0), driver_pts, font=small_font)
    pts_y, _, _ = _centre_driver_glyphs(pts_bbox, driver_y, driver_row_height)
    draw.text((pts_x, pts_y), driver_pts, fill=text_fill, font=small_font)

    pos_bbox = draw.textbbox((0, 0), pos_text, font=small_font)
    pos_y, badge_y, badge_h = _centre_driver_glyphs(pos_bbox, driver_y, driver_row_height)

    if driver.position and driver.position <= 3:
        pos_w = pos_bbox[2] - pos_bbox[0]
        badge_w = int(pos_w) + badge_pad_x * 2
        badge_x = driver_pos_x - badge_pad_x
        badge_fill, badge_text_fill = badge_colors_fn(driver.position)
        draw.rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h - 1)],
            fill=badge_fill,
            outline=badge_outline_fill,
        )
        draw.text(
            (badge_x + badge_pad_x, pos_y),
            pos_text,
            fill=badge_text_fill,
            font=small_font,
        )
        return

    draw.text((driver_pos_x, pos_y), pos_text, fill=text_fill, font=small_font)


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
    get_text_y_fn,
    build_team_header_values_fn,
    clamp_text_fn,
    draw_team_stats_panel_fn,
    draw_team_driver_row_fn,
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
