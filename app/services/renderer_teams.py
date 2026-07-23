"""Teams-and-drivers screen section drawing."""

from __future__ import annotations

from PIL import Image, ImageDraw

from app.services.font_utils import CJK_LANG_CODES, fit_brand_font_box
from app.services.renderer_text import select_active_team_drivers, split_teams_for_columns


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
