"""Historical qualifying and race result section drawing."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.renderer_assets import load_results_flag_image


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
