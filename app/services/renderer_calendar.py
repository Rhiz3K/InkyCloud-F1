"""Calendar-screen header, schedule, countdown, track, and stats drawing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont

from app.config import config
from app.services.font_utils import fit_ui_font
from app.services.renderer_assets import _load_image_copy
from app.services.renderer_text import clamp_text, normalize_session_name, text_width
from app.utils.race_times import today_in_timezone
from app.utils.refresh_hint import RACE_LIVE_WINDOW, CountdownMode, countdown_shows_days
from app.utils.timezones import get_timezone

if TYPE_CHECKING:
    from app.services.weekend_phase import WeekendPhase

# Schedule row marks drawn by weekend_mode=auto in the 10 px gap left of the date column.
MARK_PAST = "past"
MARK_CURRENT = "current"


def _shrink_font_to_width(
    draw: ImageDraw.ImageDraw, text: str, font, max_width: int, min_size: int
):
    """Step a TrueType font down in size (same face) until the text fits ``max_width``."""
    while (
        isinstance(font, FreeTypeFont)
        and font.size > min_size
        and text_width(draw, text, font) > max_width
    ):
        font = font.font_variant(size=font.size - 1)
    return font


def _countdown_plural_category(value: int, lang_code: str) -> str:
    """Return the translation plural category used by countdown labels."""
    if value == 1 or (lang_code in {"fr", "pt-BR"} and value == 0):
        return "one"
    if lang_code in {"cs", "sk"} and 2 <= value <= 4:
        return "few"
    if lang_code == "pl" and value % 10 in {2, 3, 4} and value % 100 not in {12, 13, 14}:
        return "few"
    return "many"


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
    text_right_padding = 15
    total_text_height = 80
    start_y = (header_height - total_text_height) // 2 - 5
    max_text_width = canvas_width - text_x - text_right_padding

    # The CJK face draws Latin wider than Titillium; keep its first line inside the margin.
    title_font = _shrink_font_to_width(draw, line1, header_title_font, max_text_width, 24)
    line1 = clamp_text(draw, line1, title_font, max_text_width)

    subtitle_font = header_subtitle_font
    subtitle_bbox = draw.textbbox((0, 0), line2, font=subtitle_font)
    if subtitle_bbox[2] - subtitle_bbox[0] > canvas_width - text_x - text_right_padding:
        subtitle_font = fit_ui_font(
            draw,
            "en",
            line2,
            max_width=canvas_width - text_x - text_right_padding,
            base_size=36,
            min_size=20,
            bold=True,
        )
    line2 = clamp_text(
        draw,
        line2,
        subtitle_font,
        canvas_width - text_x - text_right_padding,
    )

    draw.text((text_x, start_y), line1, fill=title_fill, font=title_font)
    draw.text((text_x, start_y + 40), line2, fill=title_fill, font=subtitle_font)


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


def _find_race_datetime(schedule: Sequence, datetime_cls):
    """Return the race session datetime from a normalized weekend schedule."""
    for event in schedule:
        if event.get("name", "").lower() != "race":
            continue
        value = event.get("datetime")
        if isinstance(value, str):
            return (
                date.fromisoformat(value) if len(value) == 10 else datetime_cls.fromisoformat(value)
            )
        if isinstance(value, datetime):
            return value
        return None
    return None


def _resolve_countdown_status(
    *,
    is_cancelled: bool,
    race_dt,
    datetime_cls,
    translator: Mapping[str, str],
    timezone_name: str | None = None,
) -> tuple[str | None, timedelta | None]:
    """Resolve a cancelled/live/completed label or a future-race delta."""
    if is_cancelled:
        return translator.get("cancelled", "CANCELLED"), None
    if race_dt is None:
        return None, None

    if isinstance(race_dt, date) and not isinstance(race_dt, datetime):
        days = (race_dt - today_in_timezone(timezone_name)).days
        if days < 0:
            return translator.get("race_completed", "COMPLETED"), None
        if days == 0:
            return "–", None
        return None, timedelta(days=days)

    now = datetime_cls.now(race_dt.tzinfo) if race_dt.tzinfo else datetime_cls.now()
    delta = race_dt - now
    if delta.total_seconds() > 0:
        return None, delta

    status_key = "race_ongoing" if now < race_dt + RACE_LIVE_WINDOW else "race_completed"
    fallback = "IN PROGRESS" if status_key == "race_ongoing" else "COMPLETED"
    return translator.get(status_key, fallback), delta


# Minimum horizontal gap between the left status/countdown text and the weather block.
_COUNTDOWN_WEATHER_GAP = 8
# Smallest scale applied to the status font before falling back to an ellipsis.
_MIN_STATUS_FONT_RATIO = 0.7


def _countdown_weather_width(
    draw: ImageDraw.ImageDraw,
    weather_data,
    *,
    rain_icon: str,
    weather_icon_font,
    text_font,
) -> int:
    """Measure the weather block drawn by _draw_countdown_weather."""
    parts = (
        (weather_data.icon, weather_icon_font),
        (f"{weather_data.temp_display} ", text_font),
        (rain_icon, weather_icon_font),
        (weather_data.precip_display, text_font),
    )
    widths = [draw.textbbox((0, 0), text, font=font) for text, font in parts]
    return int(sum(bbox[2] - bbox[0] for bbox in widths) + 4 + 3)


def _fit_left_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> tuple[str, Any]:
    """Shrink (then ellipsize) text so it ends before the weather block instead of under it."""
    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text, font
    base_size = getattr(font, "size", None)
    if base_size and hasattr(font, "font_variant"):
        min_size = max(1, int(base_size * _MIN_STATUS_FONT_RATIO))
        for size in range(int(base_size) - 1, min_size - 1, -1):
            candidate = font.font_variant(size=size)
            bbox = draw.textbbox((0, 0), text, font=candidate)
            if bbox[2] - bbox[0] <= max_width:
                return text, candidate
        font = font.font_variant(size=min_size)
    return clamp_text(draw, text, font, int(max_width)), font


def _draw_countdown_weather(
    draw: ImageDraw.ImageDraw,
    weather_data,
    *,
    x_right: int,
    text_y: float,
    padding_x: int,
    rain_icon: str,
    weather_icon_font,
    text_font,
    text_fill,
) -> None:
    """Right-align the weather summary inside a countdown/status box."""
    temperature = f"{weather_data.temp_display} "
    precipitation = weather_data.precip_display
    weather_icon_bbox = draw.textbbox((0, 0), weather_data.icon, font=weather_icon_font)
    temperature_bbox = draw.textbbox((0, 0), temperature, font=text_font)
    rain_icon_bbox = draw.textbbox((0, 0), rain_icon, font=weather_icon_font)
    precipitation_bbox = draw.textbbox((0, 0), precipitation, font=text_font)
    weather_icon_width = weather_icon_bbox[2] - weather_icon_bbox[0]
    temperature_width = temperature_bbox[2] - temperature_bbox[0]
    rain_icon_width = rain_icon_bbox[2] - rain_icon_bbox[0]
    precipitation_width = precipitation_bbox[2] - precipitation_bbox[0]
    total_width = (
        weather_icon_width + 4 + temperature_width + rain_icon_width + 3 + precipitation_width
    )
    current_x = x_right - padding_x - total_width
    draw.text((current_x, text_y), weather_data.icon, fill=text_fill, font=weather_icon_font)
    current_x += weather_icon_width + 4
    draw.text((current_x, text_y), temperature, fill=text_fill, font=text_font)
    current_x += temperature_width
    draw.text((current_x, text_y), rain_icon, fill=text_fill, font=weather_icon_font)
    current_x += rain_icon_width + 3
    draw.text((current_x, text_y), precipitation, fill=text_fill, font=text_font)


def _countdown_unit_labels(
    days: int,
    hours: int,
    *,
    translator: Mapping[str, str],
    lang_code: str,
    weather_type: str,
) -> tuple[str, str]:
    """Return short weather-mode labels or locale-aware plural labels."""
    if weather_type in {"current", "race_day", "race"}:
        return (
            translator.get("countdown_days_short", "d"),
            translator.get("countdown_hours_short", "h"),
        )
    return (
        translator.get(
            f"countdown_days_{_countdown_plural_category(days, lang_code)}",
            translator.get("countdown_days", "days"),
        ),
        translator.get(
            f"countdown_hours_{_countdown_plural_category(hours, lang_code)}",
            translator.get("countdown_hours", "hours"),
        ),
    )


def _race_date_label(race_dt, translator: Mapping[str, str]) -> str:
    """Return the race day and local start (``countdown=off``), as the schedule rows show it."""
    day = translator.get(f"day_{race_dt.strftime('%a').lower()}", race_dt.strftime("%a"))
    label = f"{day} {race_dt:%d.%m.}"
    if isinstance(race_dt, datetime):
        label += f" {race_dt:%H:%M}"
    return label


def _countdown_text(
    race_dt,
    delta: timedelta,
    *,
    countdown_mode: CountdownMode,
    timezone_name: str | None,
    datetime_cls,
    translator: Mapping[str, str],
    lang_code: str,
    weather_type: str,
) -> str:
    """Return the countdown for a future race in the requested ``countdown`` mode."""
    if countdown_mode == CountdownMode.OFF:
        return _race_date_label(race_dt, translator)
    date_only = isinstance(race_dt, date) and not isinstance(race_dt, datetime)
    days = delta.days
    hours = delta.seconds // 3600
    if not date_only and countdown_shows_days(countdown_mode, delta):
        # Whole calendar days in the requested timezone, as on the teams countdown panel.
        zone = get_timezone(timezone_name or config.DEFAULT_TIMEZONE)
        days = (race_dt.astimezone(zone).date() - datetime_cls.now(zone).date()).days
        if days <= 0:
            return translator.get("countdown_today", "Today")
        date_only = True
    days_label, hours_label = _countdown_unit_labels(
        days,
        hours,
        translator=translator,
        lang_code=lang_code,
        weather_type=weather_type,
    )
    return f"{days} {days_label}" if date_only else f"{days} {days_label} {hours} {hours_label}"


def _session_countdown_text(
    weekend: WeekendPhase,
    *,
    countdown_mode: CountdownMode,
    timezone_name: str | None,
    datetime_cls,
    translator: Mapping[str, str],
    lang_code: str,
    weather_type: str,
) -> tuple[str, bool]:
    """Return ``LABEL: countdown`` for the next non-race session and whether it is running.

    The countdown follows ``countdown_mode`` like the race countdown; a leading zero day count
    is left out (``Q: 3 h``) and ``off`` shows the local day and start time (``Q: Sat 15:00``;
    the date is in the schedule above). A running session shows ``LABEL: LIVE``.
    """
    name = weekend.countdown_session or ""
    label = translator.get(f"session_short_{normalize_session_name(name)}", name.upper())
    if weekend.live:
        return f"{label}: {translator.get('session_live', 'LIVE')}", True
    zone = get_timezone(timezone_name or config.DEFAULT_TIMEZONE)
    start = weekend.countdown_start
    assert start is not None
    start = start.astimezone(zone)
    if countdown_mode == CountdownMode.OFF:
        day = translator.get(f"day_{start.strftime('%a').lower()}", start.strftime("%a"))
        return f"{label}: {day} {start:%H:%M}", False
    delta = start - datetime_cls.now(zone)
    text = _countdown_text(
        start,
        delta,
        countdown_mode=countdown_mode,
        timezone_name=timezone_name,
        datetime_cls=datetime_cls,
        translator=translator,
        lang_code=lang_code,
        weather_type=weather_type,
    )
    days_label, _ = _countdown_unit_labels(
        0, 0, translator=translator, lang_code=lang_code, weather_type=weather_type
    )
    text = text.removeprefix(f"0 {days_label} ")
    return f"{label}: {text}", False


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
    countdown_mode: CountdownMode = CountdownMode.HOURS,
    weekend: WeekendPhase | None = None,
) -> int:
    """Draw the countdown/status box and return its bottom y-coordinate.

    ``countdown_mode`` picks days and hours (default), whole days, days until 48 hours before
    the start (``auto``) or the race date instead of a countdown (``off``); race statuses and
    weather are drawn the same way in every mode. With a ``weekend`` phase whose next session
    is not the race, the box counts down to that session with its short label instead.
    """
    is_cancelled = race_data.get("is_cancelled", False)
    race_dt = _find_race_datetime(race_data.get("schedule", []), datetime_cls)

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

    status_text, delta = _resolve_countdown_status(
        is_cancelled=is_cancelled,
        race_dt=race_dt,
        datetime_cls=datetime_cls,
        translator=translator,
        timezone_name=race_data.get("timezone"),
    )
    session_text: str | None = None
    if weekend is not None and weekend.countdown_session and not is_cancelled:
        session_text, session_live = _session_countdown_text(
            weekend,
            countdown_mode=countdown_mode,
            timezone_name=race_data.get("timezone"),
            datetime_cls=datetime_cls,
            translator=translator,
            lang_code=lang_code,
            weather_type=weather_type,
        )
        # The session replaces the race countdown or a date-only race-day placeholder.
        status_text = session_text if session_live else None
        if session_live:
            session_text = None

    if status_text:
        show_weather = weather_data is not None and not is_cancelled
        status_font = schedule_row_bold_font
        if show_weather:
            weather_w = _countdown_weather_width(
                draw,
                weather_data,
                rain_icon=rain_icon,
                weather_icon_font=weather_icon_font,
                text_font=schedule_row_bold_font,
            )
            # Long localized statuses (e.g. "ABGESCHLOSSEN") ran under the weather icon.
            status_text, status_font = _fit_left_text(
                draw,
                status_text,
                schedule_row_bold_font,
                (x_right - x_left) - 2 * padding_x - weather_w - _COUNTDOWN_WEATHER_GAP,
            )
        status_bbox = draw.textbbox((0, 0), status_text, font=status_font)
        status_w = status_bbox[2] - status_bbox[0]
        text_x = (
            x_left + padding_x if show_weather else x_left + ((x_right - x_left) - status_w) // 2
        )
        draw.text((text_x, text_y), status_text, fill=text_fill, font=status_font)
        if not show_weather:
            return int(y_bottom)
        _draw_countdown_weather(
            draw,
            weather_data,
            x_right=x_right,
            text_y=text_y,
            padding_x=padding_x,
            rain_icon=rain_icon,
            weather_icon_font=weather_icon_font,
            text_font=schedule_row_bold_font,
            text_fill=text_fill,
        )
        return int(y_bottom)

    if session_text is not None:
        countdown_str = session_text
    elif delta is None or delta.total_seconds() <= 0:
        return schedule_bottom
    else:
        countdown_str = _countdown_text(
            race_dt,
            delta,
            countdown_mode=countdown_mode,
            timezone_name=race_data.get("timezone"),
            datetime_cls=datetime_cls,
            translator=translator,
            lang_code=lang_code,
            weather_type=weather_type,
        )

    flag_icon = "🏁"

    flag_bbox = draw.textbbox((0, 0), flag_icon, font=icon_small_font)
    flag_w = flag_bbox[2] - flag_bbox[0]
    countdown_font = schedule_row_bold_font
    if weather_data:
        weather_w = _countdown_weather_width(
            draw,
            weather_data,
            rain_icon=rain_icon,
            weather_icon_font=weather_icon_font,
            text_font=schedule_row_bold_font,
        )
        countdown_str, countdown_font = _fit_left_text(
            draw,
            countdown_str,
            schedule_row_bold_font,
            (x_right - x_left) - 2 * padding_x - weather_w - _COUNTDOWN_WEATHER_GAP - flag_w - 6,
        )
    countdown_bbox = draw.textbbox((0, 0), countdown_str, font=countdown_font)
    countdown_w = countdown_bbox[2] - countdown_bbox[0]
    total_content_w = flag_w + 6 + countdown_w

    if weather_data:
        cur_x = int(x_left + padding_x)
    else:
        box_width = x_right - x_left
        cur_x = int(x_left + (box_width - total_content_w) // 2)

    draw.text((cur_x, text_y), flag_icon, fill=text_fill, font=icon_small_font)
    cur_x += int(flag_w + 6)
    draw.text((cur_x, text_y), countdown_str, fill=text_fill, font=countdown_font)

    if weather_data:
        _draw_countdown_weather(
            draw,
            weather_data,
            x_right=x_right,
            text_y=text_y,
            padding_x=padding_x,
            rain_icon=rain_icon,
            weather_icon_font=weather_icon_font,
            text_font=schedule_row_bold_font,
            text_fill=text_fill,
        )

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
    render_track_image_fn=None,
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

    track_image = (
        render_track_image_fn(race_data, available_width, available_height)
        if render_track_image_fn
        else load_track_image_fn(race_data)
    )
    if track_image:
        prepared_image = (
            track_image
            if render_track_image_fn
            else prepare_track_image_fn(track_image, available_width, available_height)
        )
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


def _draw_schedule_mark(
    draw: ImageDraw.ImageDraw,
    mark: str,
    *,
    right_x: int,
    digits_bbox: tuple[float, float, float, float],
    fill,
) -> None:
    """Draw a check mark or a triangle, at most 8 px wide, ending at ``right_x``.

    Shapes instead of glyphs: they look the same in every locale font and palette.
    """
    center_y = round((digits_bbox[1] + digits_bbox[3]) / 2)
    if mark == MARK_PAST:
        draw.line(
            [(right_x - 7, center_y), (right_x - 4, center_y + 3), (right_x, center_y - 4)],
            fill=fill,
            width=2,
        )
        return
    draw.polygon(
        [(right_x - 6, center_y - 5), (right_x - 6, center_y + 5), (right_x, center_y)],
        fill=fill,
    )


def schedule_mark(event: Mapping[str, Any], weekend: WeekendPhase | None) -> str | None:
    """Return the weekend mark of one schedule row, None without a weekend phase."""
    if weekend is None:
        return None
    name = event.get("name")
    if name in weekend.past:
        return MARK_PAST
    if name == weekend.current:
        return MARK_CURRENT
    return None


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
    mark: str | None = None,
    mark_fill=None,
) -> None:
    """Draw one localized schedule row using shared date/time/name layout.

    ``mark`` (``weekend_mode=auto``) draws a finished session with a check mark and a plain
    regular-weight name, and the current or next session with a filled ``mark_fill``
    triangle; both marks sit in the gap left of ``schedule_date_x``.
    """
    dt = event.get("datetime")
    name = event.get("name", "")

    unknown_time = isinstance(dt, str) and len(dt) == 10
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)

    if dt:
        date_str = dt.strftime("%d.%m.")
        day_key = f"day_{dt.strftime('%a').lower()}"
        day_str = translator.get(day_key, dt.strftime("%a"))
        time_str = "–" if unknown_time else dt.strftime("%H:%M")
    else:
        date_str = ""
        day_str = ""
        time_str = event.get("display_time", "")

    name_max_width = canvas_width - schedule_name_x - 5
    translated_name = format_schedule_session_name_fn(draw, name, name_max_width)
    past = mark == MARK_PAST
    font_bold = fit_ui_font(
        draw,
        lang_code,
        translated_name,
        max_width=name_max_width,
        base_size=20,
        min_size=15,
        bold=not past,
    )

    draw.text((schedule_date_x, y), date_str, fill=regular_text_fill, font=font_reg)
    draw.text((schedule_day_x, y), day_str, fill=regular_text_fill, font=font_reg)
    draw.text((schedule_time_x, y), time_str, fill=regular_text_fill, font=font_reg)
    if mark is not None:
        _draw_schedule_mark(
            draw,
            mark,
            right_x=schedule_date_x - 2,
            digits_bbox=draw.textbbox((schedule_date_x, y), "0", font=font_reg),
            fill=regular_text_fill if past else (mark_fill or regular_text_fill),
        )
    if past:
        # Finished sessions lose their session colour and shadow, so upcoming ones stand out.
        session_text_fill = regular_text_fill
        session_text_shadow_fill = None
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
    countdown_mode: CountdownMode = CountdownMode.HOURS,
    weekend: WeekendPhase | None = None,
) -> int:
    """Draw the schedule title, rows, and countdown area (with weekend marks when given)."""
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
        draw_schedule_row_fn(draw, row_y, event, mark=schedule_mark(event, weekend))
        row_y += schedule_row_height
        if row_y > results_y_start - 80:
            break

    return draw_countdown_box_fn(
        draw,
        race_data,
        row_y + 10,
        weather_data,
        weather_type,
        countdown_mode=countdown_mode,
        weekend=weekend,
    )
