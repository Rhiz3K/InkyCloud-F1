"""Clipped, palette-aware optional teams cards."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw

from app.services.font_utils import (
    fit_ui_font_box,
    load_brand_font,
    load_ui_font,
    load_weather_icon_font,
)
from app.services.renderer_assets import prepare_color_track_image, prepare_mono_track_image
from app.services.renderer_calendar import _countdown_plural_category
from app.services.renderer_theme import Fill, RenderTheme
from app.services.teams_extra import TeamsExtra, TeamsExtraContext
from app.services.track_catalog import TrackOptions, TrackStyle
from app.services.track_renderer import render_track_image
from app.services.weather_service import RAINDROP_ICON

logger = logging.getLogger(__name__)


def _text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    width: int,
    fill: Fill,
    lang: str,
    *,
    height: int = 18,
    size: int = 16,
    centered: bool = False,
    min_size: int = 12,
) -> None:
    """Fit locale-aware text inside its allotted line, with a twelve-pixel minimum."""
    font = fit_ui_font_box(
        draw,
        lang,
        text,
        max_width=width,
        max_height=height,
        base_size=size,
        min_size=min_size,
        bold=True,
    )
    if draw.textbbox((0, 0), text, font=font)[2] > width:
        while text and draw.textbbox((0, 0), text + "…", font=font)[2] > width:
            text = text[:-1]
        text += "…"
    bounds = draw.textbbox((0, 0), text, font=font)
    if centered:
        x += (width - int(bounds[2] - bounds[0])) // 2
        y += (height - int(bounds[3] - bounds[1])) // 2
    draw.text((x - bounds[0], y - bounds[1]), text, font=font, fill=fill)


def _date_time(ctx: TeamsExtraContext, translator: dict) -> str:
    """Follow calendar session formatting with translated weekdays and local time."""
    dt = ctx.start_local
    day = translator.get(f"day_{dt.strftime('%a').lower()}", dt.strftime("%a"))
    return f"{day} {dt:%d.%m.} · {dt:%H:%M}"


def _race_line(
    ctx: TeamsExtraContext, translator: dict, draw: ImageDraw.ImageDraw, lang: str, width: int
) -> str:
    """Shorten only the race name so the complete local start remains visible."""
    prefix = translator.get("teams_extra_next", "Next") + ": "
    suffix = " · " + _date_time(ctx, translator)
    name = ctx.race_name
    font = load_ui_font(lang, 12, bold=True)
    if draw.textbbox((0, 0), prefix + name + suffix, font=font)[2] > width:
        while name and draw.textbbox((0, 0), prefix + name + "…" + suffix, font=font)[2] > width:
            name = name[:-1]
        name += "…"
    return prefix + name + suffix


def _countdown(ctx: TeamsExtraContext, translator: dict, lang: str) -> str:
    """Return calendar days only, including a translated race-day label."""
    if ctx.days_left == 0:
        return translator.get("teams_extra_today", "today")
    category = _countdown_plural_category(ctx.days_left, lang)
    unit = translator.get(f"countdown_days_{category}", translator.get("countdown_days_short", "d"))
    return f"{ctx.days_left} {unit}"


def _flag(theme: RenderTheme, iso2: str | None) -> Image.Image | None:
    """Load a local colour flag, returning no image for absent or broken assets."""
    if not iso2:
        return None
    directories = theme.flags_directories()
    for directory in (directories,) if isinstance(directories, Path) else directories:
        for extension in ("bmp", "png"):
            try:
                with Image.open(directory / f"{iso2}.{extension}") as source:
                    return source.convert("RGB").resize((36, 22), Image.Resampling.BOX)
            except OSError, ValueError:
                continue
    return None


def draw_teams_extra(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    extra: TeamsExtra,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw an optional card into an already cropped teams-grid slot."""
    if extra == TeamsExtra.NONE or (extra == TeamsExtra.WEATHER and ctx.weather is None):
        return
    # Render into a separate crop even when invoked directly with a panel-sized image.
    panel = image.copy()
    draw = ImageDraw.Draw(panel)
    draw.fontmode = "1"
    width, height = panel.size
    draw.rectangle(
        (0, 0, width - 1, height - 1), fill=theme.colors.WHITE, outline=theme.team_outline_fill
    )
    if extra == TeamsExtra.WEATHER:
        credit = translator.get("teams_extra_weather_credit", "Open-Meteo")
        bounds = draw.textbbox((0, 0), credit, font=load_ui_font(lang, 12, bold=True))
        title_width = width - 18 - int(bounds[2] - bounds[0])
        title = _race_line(ctx, translator, draw, lang, title_width)
        draw.rectangle((1, 1, width - 2, 22), fill=theme.team_header_fill)
        _text(
            draw,
            title,
            5,
            4,
            title_width,
            theme.team_header_text_fill,
            lang,
            height=17,
            size=12,
        )
        _draw_weather(panel, draw, theme, ctx, translator=translator, lang=lang)
    elif extra == TeamsExtra.COUNTDOWN:
        _draw_countdown(panel, draw, theme, ctx, translator=translator, lang=lang)
    elif extra == TeamsExtra.FLAGS:
        _draw_flags(panel, draw, theme, ctx, display=display)
    elif extra == TeamsExtra.TRACK:
        _draw_track(panel, draw, theme, ctx, display=display, translator=translator, lang=lang)
    image.paste(panel, (0, 0))


def _draw_countdown(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    *,
    translator: dict,
    lang: str,
) -> None:
    """Prioritise the next race and start alongside a compact day countdown."""
    width, height = panel.size
    split = 112
    draw.rectangle((4, 4, split, height - 5), fill=theme.team_header_fill)
    _text(
        draw,
        _countdown(ctx, translator, lang),
        9,
        7,
        split - 14,
        theme.team_header_text_fill,
        lang,
        size=28,
        min_size=14,
        height=height - 14,
        centered=True,
    )
    left = split + 9
    text_width = width - left - 5
    _text(
        draw,
        ctx.race_name,
        left,
        5,
        text_width,
        theme.text_fill,
        lang,
        size=20,
        min_size=16,
        height=22,
    )
    _text(
        draw,
        _date_time(ctx, translator),
        left,
        30,
        text_width,
        theme.text_fill,
        lang,
        size=16,
        min_size=14,
        height=18,
    )
    # Distinct monochrome states: filled past, taller next, outlined future.
    count = max(1, len(ctx.rounds))
    for index, race in enumerate(ctx.rounds):
        x = left + index * text_width // count
        right = left + (index + 1) * text_width // count - 3
        active = race.round == ctx.next_round
        draw.rectangle(
            (x, 50 if active else 53, max(x, right), 57),
            fill=theme.accent_fill
            if active
            else (theme.text_fill if race.done else theme.colors.WHITE),
            outline=theme.team_outline_fill,
        )


def _draw_weather(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    *,
    translator: dict,
    lang: str,
) -> None:
    """Draw the weather card body inside the clipped panel."""
    width, height = panel.size
    weather = ctx.weather
    assert weather is not None
    icon_font = load_weather_icon_font(28, logger, load_brand_font)
    text_font = load_ui_font(lang, 25, bold=True)
    parts = [
        (weather.icon, icon_font, theme.text_fill, 6),
        (weather.temp_display, text_font, theme.accent_fill, 16),
        (RAINDROP_ICON, icon_font, theme.text_fill, 4),
        (weather.precip_display, text_font, theme.text_fill, 0),
    ]
    boxes = [draw.textbbox((0, 0), text, font=font) for text, font, _, _ in parts]
    group_width = sum(box[2] - box[0] + part[3] for part, box in zip(parts, boxes, strict=True))
    x = (width - group_width) // 2
    for (text, font, fill, gap), box in zip(parts, boxes, strict=True):
        y = 23 + (height - 3 - 23 - (box[3] - box[1])) // 2
        draw.text((x - box[0], y - box[1]), text, font=font, fill=fill)
        x += box[2] - box[0] + gap
    credit = translator.get("teams_extra_weather_credit", "Open-Meteo")
    box = draw.textbbox((0, 0), credit, font=load_ui_font(lang, 12, bold=True))
    credit_width = int(box[2] - box[0])
    _text(
        draw,
        credit,
        width - credit_width - 5,
        4,
        credit_width,
        theme.team_header_text_fill,
        lang,
        size=12,
        height=17,
    )


def _draw_flags(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    *,
    display: str,
) -> None:
    """Draw the flags card body inside the clipped panel."""
    width, height = panel.size
    rounds = [r for r in ctx.rounds if not r.done and r.round >= ctx.next_round][:3]
    # Keep the same cell size at the end of the season instead of stretching one flag.
    cell = (width - 8) // 3
    start = (width - len(rounds) * cell) // 2
    for i, race in enumerate(rounds):
        x = start + i * cell
        active = race.round == ctx.next_round
        inverted = active and display == "1bit"
        foreground = theme.team_header_text_fill if inverted else theme.text_fill
        draw.rectangle(
            (x, 4, x + cell - 4, height - 5),
            fill=theme.team_header_fill if inverted else theme.colors.WHITE,
            outline=theme.accent_fill if active else theme.team_outline_fill,
            width=2 if active else 1,
        )
        flag = _flag(theme, race.iso2) if display != "1bit" else None
        if flag is not None:
            panel.paste(flag.convert(panel.mode), (x + 10, 8))
        code_left = x + 52 if flag is not None else x + 4
        code_width = cell - 60 if flag is not None else cell - 12
        _text(
            draw,
            race.code,
            code_left,
            7,
            code_width,
            foreground,
            "en",
            size=22 if display != "1bit" else 26,
            min_size=22,
            height=24,
            centered=True,
        )
        _text(
            draw,
            race.date.strftime("%d.%m."),
            x + 4,
            35,
            cell - 12,
            foreground,
            "en",
            size=16,
            min_size=16,
            height=19,
            centered=True,
        )


def _draw_track(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw the track card body inside the clipped panel."""
    width, height = panel.size
    # Stable columns and baselines keep the map from moving with each circuit name.
    map_width, map_height = 120, height - 8
    map_left = width - map_width - 4
    text_width = map_left - 13
    for text, size, y, line_height in (
        (ctx.race_name, 18, 5, 20),
        (ctx.circuit_name, 14, 25, 17),
        (_date_time(ctx, translator), 14, 43, 15),
    ):
        _text(
            draw,
            text,
            5,
            y,
            text_width,
            theme.text_fill,
            lang,
            size=size,
            min_size=size,
            height=line_height,
        )
    try:
        # The artwork renderer requires at least 64px in both axes; use a clean
        # outline at 2x and the shared stroke-preserving downsampler for this slot.
        track = render_track_image(
            {"circuit": {"circuitId": ctx.circuit_id}},
            width=map_width * 2,
            height=map_height * 2,
            display=display,
            options=TrackOptions(style=TrackStyle.CLEAN),
        )
        if track is not None:
            track = (
                prepare_mono_track_image(track, map_width, map_height, logger)
                if display == "1bit"
                else prepare_color_track_image(track, map_width, map_height)
            )
            panel.paste(track.convert(panel.mode), (map_left, 4))
    except OSError, ValueError, RuntimeError:
        logger.warning("Unable to render teams extra track %s", ctx.circuit_id, exc_info=True)
