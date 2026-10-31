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
                    return source.convert("RGB").resize((30, 17), Image.Resampling.NEAREST)
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
    round_label = translator.get("teams_extra_round", "Round")
    title = _race_line(ctx, translator, draw, lang, width - 10)
    two_rows = extra == TeamsExtra.TIMELINE and _timeline_two_rows(draw, ctx, lang, width)
    title_width = width - 10
    if extra == TeamsExtra.WEATHER:
        credit = translator.get("teams_extra_weather_credit", "Open-Meteo")
        bounds = draw.textbbox((0, 0), credit, font=load_ui_font(lang, 12, bold=True))
        title_width -= int(bounds[2] - bounds[0]) + 8
        title = _race_line(ctx, translator, draw, lang, title_width)
    if extra == TeamsExtra.TIMELINE:
        title = (
            f"{translator.get('teams_extra_season', 'Season')} {ctx.season} · "
            f"{round_label} {ctx.next_round}/{ctx.total_rounds}"
        )
        if two_rows:
            prefix = f"{round_label} {ctx.next_round}/{ctx.total_rounds} · "
            font = load_ui_font(lang, 12, bold=True)
            prefix_width = int(draw.textlength(prefix, font=font))
            title = prefix + _race_line(ctx, translator, draw, lang, width - 10 - prefix_width)
    elif extra == TeamsExtra.FLAGS:
        title = translator.get("teams_extra_upcoming", "Upcoming races")
    if extra not in (TeamsExtra.COUNTDOWN, TeamsExtra.COMBO, TeamsExtra.TRACK):
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
            size=12 if extra == TeamsExtra.WEATHER or two_rows else 16,
        )
    drawers = {
        TeamsExtra.TIMELINE: _draw_timeline,
        TeamsExtra.COUNTDOWN: _draw_countdown,
        TeamsExtra.COMBO: _draw_countdown,
        TeamsExtra.WEATHER: _draw_weather,
        TeamsExtra.FLAGS: _draw_flags,
        TeamsExtra.TRACK: _draw_track,
    }
    drawers[extra](
        panel, draw, theme, ctx, extra, display=display, translator=translator, lang=lang
    )
    image.paste(panel, (0, 0))


def _timeline_two_rows(
    draw: ImageDraw.ImageDraw, ctx: TeamsExtraContext, lang: str, width: int
) -> bool:
    """Keep one clear pixel between the widest round number and each one-pixel outline."""
    font = load_ui_font(lang, 12, bold=True)
    widest = max(draw.textbbox((0, 0), str(i), font=font)[2] for i in range(10, 100))
    return (width - 2) // max(1, len(ctx.rounds)) < widest + 4


def _draw_timeline(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    extra: TeamsExtra,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw the timeline card body inside the clipped panel."""
    width, height = panel.size
    count = max(1, len(ctx.rounds))
    two_rows = _timeline_two_rows(draw, ctx, lang, width)
    per_row = (count + 1) // 2 if two_rows else count
    for index, race in enumerate(ctx.rounds):
        row, column = divmod(index, per_row)
        top = 24 + row * (height - 27) // 2 if two_rows else 25
        bottom = 24 + (row + 1) * (height - 27) // 2 if two_rows else 43
        x = 1 + column * (width - 2) // per_row
        right = 1 + (column + 1) * (width - 2) // per_row
        active = race.round == ctx.next_round
        draw.rectangle(
            (x, top, right - 1, bottom),
            fill=theme.team_header_fill if active else theme.colors.WHITE,
            outline=theme.team_outline_fill,
        )
        _text(
            draw,
            str(race.round),
            x + 1,
            top,
            right - x - 2,
            theme.team_header_text_fill if active else theme.text_fill,
            lang,
            size=12,
            height=bottom - top + 1,
            centered=True,
        )
    if two_rows:
        return
    _text(
        draw,
        _race_line(ctx, translator, draw, lang, width - 10),
        5,
        47,
        width - 10,
        theme.text_fill,
        lang,
        size=12,
        height=12,
    )


def _draw_countdown(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    extra: TeamsExtra,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw the countdown card body inside the clipped panel."""
    width, height = panel.size
    combo = extra == TeamsExtra.COMBO
    split = 148
    draw.rectangle((3, 3, split, height - 4), fill=theme.team_header_fill)
    _text(
        draw,
        _countdown(ctx, translator, lang),
        9,
        10,
        split - 15,
        theme.team_header_text_fill,
        lang,
        size=28,
        height=30,
    )
    if combo:
        _text(
            draw,
            ctx.race_name,
            9,
            43,
            split - 15,
            theme.team_header_text_fill,
            lang,
            size=12,
            height=13,
        )
        done = sum(r.done for r in ctx.rounds)
        detail = (
            f"{translator.get('teams_extra_remaining', 'remaining')}: "
            f"{ctx.total_rounds - done} · {done}/{ctx.total_rounds} "
            f"{translator.get('teams_extra_done', 'completed')}"
        )
        _text(draw, detail, split + 9, 8, width - split - 15, theme.text_fill, lang, size=12)
        for i, race in enumerate([r for r in ctx.rounds if not r.done][:5]):
            x = split + 9 + i * 44
            active = race.round == ctx.next_round
            draw.rectangle(
                (x, 31, x + 37, 54),
                fill=theme.team_header_fill if active else theme.colors.WHITE,
                outline=theme.team_outline_fill,
            )
            _text(
                draw,
                str(race.round),
                x + 9,
                36,
                30,
                theme.team_header_text_fill if active else theme.text_fill,
                lang,
                size=16,
            )
    else:
        _text(
            draw,
            ctx.race_name,
            split + 9,
            9,
            width - split - 15,
            theme.text_fill,
            lang,
            size=20,
            height=23,
        )
        _text(
            draw,
            _date_time(ctx, translator),
            split + 9,
            38,
            width - split - 15,
            theme.text_fill,
            lang,
            size=14,
        )


def _draw_weather(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    extra: TeamsExtra,
    *,
    display: str,
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
    extra: TeamsExtra,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw the flags card body inside the clipped panel."""
    width, height = panel.size
    rounds = [r for r in ctx.rounds if not r.done and r.round >= ctx.next_round][:6]
    cell = (width - 8) // max(1, len(rounds))
    for i, race in enumerate(rounds):
        x = 4 + i * cell
        active = race.round == ctx.next_round
        inverted = active and display == "1bit"
        foreground = theme.team_header_text_fill if inverted else theme.text_fill
        draw.rectangle(
            (x, 25, x + cell - 3, height - 4),
            fill=theme.team_header_fill if inverted else theme.colors.WHITE,
            outline=theme.accent_fill if active else theme.team_outline_fill,
            width=2 if active else 1,
        )
        flag = _flag(theme, race.iso2) if display != "1bit" else None
        if flag is not None:
            panel.paste(flag.convert(panel.mode), (x + (cell - 2 - 30) // 2, 28))
        _text(
            draw,
            race.code,
            x + 3,
            47 if flag is not None else 28,
            cell - 8,
            foreground,
            "en",
            size=12 if flag is not None else 26,
            height=height - 51 if flag is not None else height - 35,
            centered=True,
            min_size=12 if flag is not None else 14,
        )


def _draw_track(
    panel: Image.Image,
    draw: ImageDraw.ImageDraw,
    theme: RenderTheme,
    ctx: TeamsExtraContext,
    extra: TeamsExtra,
    *,
    display: str,
    translator: dict,
    lang: str,
) -> None:
    """Draw the track card body inside the clipped panel."""
    width, height = panel.size
    lines = [(ctx.race_name, 16), (ctx.circuit_name, 12), (_date_time(ctx, translator), 12)]
    bounds = [
        draw.textbbox((0, 0), text, font=load_ui_font(lang, size, bold=True))
        for text, size in lines
    ]
    text_width = min(max(int(box[2] - box[0]) for box in bounds), width - 10 - 8 - 120)
    line_heights = [int(box[3] - box[1]) for box in bounds]
    y = (height - sum(line_heights) - 4) // 2
    for (text, size), line_height in zip(lines, line_heights, strict=True):
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
        y += line_height + 2
    left, top, right, bottom = 5 + text_width + 8, 3, width - 4, height - 4
    box_width, box_height = right - left, bottom - top
    try:
        track = render_track_image(
            {"circuit": {"circuitId": ctx.circuit_id}}, width=240, height=110, display=display
        )
        if track is not None:
            track = (
                prepare_mono_track_image(track, box_width, box_height, logger)
                if display == "1bit"
                else prepare_color_track_image(track, box_width, box_height)
            )
            panel.paste(
                track.convert(panel.mode),
                (left + (box_width - track.width) // 2, top + (box_height - track.height) // 2),
            )
    except OSError, ValueError, RuntimeError:
        logger.warning("Unable to render teams extra track %s", ctx.circuit_id, exc_info=True)
