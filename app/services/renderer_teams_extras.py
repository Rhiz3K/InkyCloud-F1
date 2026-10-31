"""Design concepts for the empty bottom-right slot of the teams screen (800x480).

Every variant draws into a crop of ``SLOT`` using local coordinates and the crop is
pasted back, so nothing can ever leak outside the slot. Only theme fills are used
(no grey, no antialias reliance) and the minimum font size is 12.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from app.services.font_utils import FONTS_DIR, load_brand_font
from app.services.weather_service import RAINDROP_ICON, WEATHER_ICONS, WeatherData

SLOT = (402, 414, 795, 474)
W = SLOT[2] - SLOT[0] + 1  # 394
H = SLOT[3] - SLOT[1] + 1  # 61
HEADER_H = 23
MIN_SIZE = 12
PRAGUE = ZoneInfo("Europe/Prague")
CS_WEEKDAYS = ("po", "út", "st", "čt", "pá", "so", "ne")


@dataclass
class NextRaceContext:
    rounds: list[dict]
    next_round: int
    next_name_cs: str
    next_start_utc: dt.datetime
    now_utc: dt.datetime
    weather: WeatherData | None
    weather_is_sample: bool
    circuit_id: str
    display: str
    circuit_name: str = ""


# --------------------------------------------------------------------------- helpers


def _weather_font(size: int):
    return ImageFont.truetype(str(FONTS_DIR / "weathericons-regular-webfont.ttf"), size)


def _w(draw, text, font) -> int:
    b = draw.textbbox((0, 0), text, font=font)
    return int(b[2] - b[0])


def _fit(draw, text, max_w, max_h=None, max_size=24, bold=True):
    """Largest brand font (>= MIN_SIZE) whose glyph box fits; MIN_SIZE if nothing fits."""
    for size in range(max_size, MIN_SIZE - 1, -1):
        font = load_brand_font(size, bold=bold)
        b = draw.textbbox((0, 0), text, font=font)
        if b[2] - b[0] <= max_w and (max_h is None or b[3] - b[1] <= max_h):
            return font
    return load_brand_font(MIN_SIZE, bold=bold)


def _clamp(draw, text, font, max_w) -> str:
    if _w(draw, text, font) <= max_w:
        return text
    while text and _w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return (text.rstrip() + "…") if text else ""


def _put(draw, x, top, text, font, fill, *, h=None, align="left") -> int:
    """Draw text with its glyph box at ``top`` (or centred in ``h``); return right edge."""
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = int(b[2] - b[0]), int(b[3] - b[1])
    if align == "right":
        x -= tw
    elif align == "center":
        x -= tw // 2
    y = top + ((h - th) // 2 if h is not None else 0)
    draw.text((x - int(b[0]), y - int(b[1])), text, font=font, fill=fill)
    return x + tw


def _header(draw, theme, text) -> None:
    draw.rectangle([(0, 0), (W - 1, HEADER_H)], fill=theme.team_header_fill)
    font = _fit(draw, text, W - 10, HEADER_H - 8, max_size=15)
    _put(draw, 5, 0, _clamp(draw, text, font, W - 10), font, theme.team_header_text_fill, h=HEADER_H)


def _outline(draw, theme) -> None:
    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=theme.team_outline_fill, width=1)


def _local(ctx) -> dt.datetime:
    return ctx.next_start_utc.astimezone(PRAGUE)


def _date_cs(ctx, with_time=True, with_weekday=True) -> str:
    t = _local(ctx)
    s = f"{t.day}. {t.month}."
    if with_weekday:
        s = f"{CS_WEEKDAYS[t.weekday()]} {s}"
    return f"{s} · {t:%H:%M}" if with_time else s


def _countdown_parts(ctx) -> list[tuple[str, bool]]:
    secs = max(0, int((ctx.next_start_utc - ctx.now_utc).total_seconds()))
    days, rem = divmod(secs, 86400)
    hours, mins = rem // 3600, (rem % 3600) // 60
    if days:
        return [(str(days), True), (" d ", False), (f"{hours:02d}", True), (" h", False)]
    return [(f"{hours:02d}", True), (" h ", False), (f"{mins:02d}", True), (" min", False)]


def _draw_countdown(draw, parts, x, top, max_w, max_h, fill, max_size=48) -> tuple[int, int]:
    """Baseline-aligned big digits + small units; returns (width, glyph height)."""
    for size in range(max_size, MIN_SIZE - 1, -1):
        big = load_brand_font(size, bold=True)
        small = load_brand_font(max(MIN_SIZE, int(size * 0.45)), bold=True)
        widths = [_w(draw, t, big if is_big else small) for t, is_big in parts]
        db = draw.textbbox((0, 0), "0123456789", font=big, anchor="ls")
        if sum(widths) <= max_w and -db[1] <= max_h:
            break
    total, digit_h = sum(widths), int(-db[1])
    cx = x + (max_w - total) // 2
    baseline = top + (max_h + digit_h) // 2
    for (t, is_big), wd in zip(parts, widths):
        draw.text((cx, baseline), t, font=big if is_big else small, fill=fill, anchor="ls")
        cx += wd
    return total, digit_h


def _cells(draw, theme, rounds, ctx, x0, x1, top, h, *, gap=2, max_cell=30, big_extra=2):
    """Row of round-number cells: done = struck outline, next = inverted, future = outline."""
    n = max(1, len(rounds))
    cell = min(max_cell, (x1 - x0 - (n - 1) * gap) // n)
    x = x0 + (x1 - x0 - (n * cell + (n - 1) * gap)) // 2
    font = _fit(draw, "23", cell - 2, h - 4, max_size=18)
    for r in rounds:
        num = r["round"]
        if num == ctx.next_round:
            box = [(x - 1, top - big_extra), (x + cell, top + h - 1 + big_extra)]
            draw.rectangle(box, fill=theme.team_header_fill)
            nfont = _fit(draw, str(num), cell, h + 2 * big_extra - 4, max_size=20)
            _put(draw, x + cell // 2, top - big_extra, str(num), nfont,
                 theme.team_header_text_fill, h=h + 2 * big_extra, align="center")
        else:
            draw.rectangle([(x, top), (x + cell - 1, top + h - 1)], outline=theme.team_outline_fill)
            _put(draw, x + cell // 2, top, str(num), font, theme.text_fill, h=h, align="center")
            if num < ctx.next_round:
                draw.line([(x, top + h - 1), (x + cell - 1, top)], fill=theme.text_fill, width=1)
        x += cell + gap


def _load_flag(theme, iso2, size, display, mode):
    dirs = theme.flags_directories()
    dirs = (dirs,) if isinstance(dirs, Path) else tuple(dirs)
    for d in dirs:
        for ext in (".bmp", ".png"):
            p = d / f"{iso2}{ext}"
            if not p.exists():
                continue
            try:
                with Image.open(p) as im:
                    im.load()
                    if display == "1bit":
                        src = im if im.mode == "1" else im.convert("RGB")
                        out = src.resize(size, Image.Resampling.NEAREST)
                        out = out if out.mode == "1" else out.convert("1", dither=Image.Dither.NONE)
                    else:
                        out = im.convert("RGB").resize(size, Image.Resampling.LANCZOS)
                    return out.convert(mode)
            except Exception:  # noqa: BLE001 - concept code: missing flag -> code-only box
                continue
    return None


# --------------------------------------------------------------------------- variants


def draw_timeline(panel, draw, theme, ctx) -> None:
    total = len(ctx.rounds)
    year = ctx.next_start_utc.year
    _header(draw, theme, f"SEZÓNA {year} · KOLO {ctx.next_round}/{total}")
    _outline(draw, theme)
    _cells(draw, theme, ctx.rounds, ctx, 4, W - 4, 29, 15, gap=2, max_cell=22)
    font = load_brand_font(13, bold=True)
    text = f"Další: {ctx.next_name_cs} {_date_cs(ctx, with_time=False, with_weekday=False)}"
    _put(draw, W // 2, 47, _clamp(draw, text, font, W - 10), font, theme.text_fill, h=12, align="center")


def draw_countdown(panel, draw, theme, ctx) -> None:
    _outline(draw, theme)
    box_w = 170
    draw.rectangle([(3, 3), (box_w, H - 4)], fill=theme.accent_fill)
    _draw_countdown(draw, _countdown_parts(ctx), 8, 6, box_w - 12, H - 14,
                    theme.countdown_text_fill)
    x, max_w = box_w + 10, W - box_w - 16
    name_font = _fit(draw, ctx.next_name_cs, max_w, max_size=22)
    _put(draw, x, 9, ctx.next_name_cs, name_font, theme.text_fill)
    info_font = load_brand_font(16)
    _put(draw, x, 36, _clamp(draw, _date_cs(ctx), info_font, max_w), info_font, theme.text_fill)


def draw_weather(panel, draw, theme, ctx) -> None:
    t = _local(ctx)
    _header(draw, theme, f"Další: {ctx.next_name_cs} · {_date_cs(ctx).replace(' · ', ' ')}")
    _outline(draw, theme)
    body_top, body_h = HEADER_H + 1, H - HEADER_H - 2
    small = load_brand_font(MIN_SIZE)
    attribution = "Open-Meteo" + (" (ukázka)" if ctx.weather_is_sample else "")
    _put(draw, W - 6, H - 15, attribution, small, theme.text_fill, align="right")
    wx = ctx.weather
    if wx is None:
        _put(draw, 8, body_top, "Předpověď zatím není k dispozici", load_brand_font(14),
             theme.text_fill, h=body_h)
        return
    glyph = WEATHER_ICONS.get(int(wx.weather_code), WEATHER_ICONS[3])
    x = _put(draw, 10, body_top, glyph, _weather_font(28), theme.text_fill, h=body_h) + 12
    temp_font = _fit(draw, "26°", 120, body_h - 6, max_size=34)
    x = _put(draw, x, body_top, f"{round(wx.temperature_c)}°", temp_font, theme.accent_fill,
             h=body_h) + 22
    x = _put(draw, x, body_top, RAINDROP_ICON, _weather_font(22), theme.text_fill, h=body_h) + 6
    rain = wx.precipitation_display_override or f"{int(wx.precipitation_probability)} %"
    _put(draw, x, body_top, rain, load_brand_font(20, bold=True), theme.text_fill, h=body_h)
    _ = t


def draw_flag_timeline(panel, draw, theme, ctx) -> None:
    _header(draw, theme, "Nadcházející závody")
    _outline(draw, theme)
    idx = next((i for i, r in enumerate(ctx.rounds) if r["round"] == ctx.next_round), 0)
    prev = ctx.rounds[idx - 1] if idx > 0 else None
    upcoming = ctx.rounds[idx: idx + 6]
    font = load_brand_font(MIN_SIZE, bold=True)
    col_w, x = (W - 8) // 7, 4
    fw, fh, ftop = 30, 19, HEADER_H + 3
    cb = draw.textbbox((0, 0), "MMM", font=font)
    # glyph top >= 2 px below the flag outline row (ftop + fh), glyph bottom inside slot
    ctop = min(ftop + fh + 3, H - 3 - int(cb[3] - cb[1]))
    if prev is not None:
        right = _put(draw, x + col_w // 2, HEADER_H + 1, prev["code"], font, theme.text_fill,
                     h=H - HEADER_H - 2, align="center")
        mid = HEADER_H + 1 + (H - HEADER_H - 2) // 2
        draw.line([(right - _w(draw, prev["code"], font) - 2, mid), (right + 2, mid)],
                  fill=theme.text_fill, width=1)
    x += col_w
    for r in upcoming:
        cx, is_next = x + col_w // 2, r["round"] == ctx.next_round
        fg = theme.team_header_text_fill if is_next else theme.text_fill
        if is_next:
            draw.rectangle([(cx - fw // 2 - 3, ftop - 2), (cx + fw // 2 + 2, H - 3)],
                           fill=theme.team_header_fill)
        flag = _load_flag(theme, r["iso2"], (fw, fh), ctx.display, panel.mode)
        fx = cx - fw // 2
        if flag is not None:
            panel.paste(flag, (fx, ftop))
            if not is_next:
                draw.rectangle([(fx - 1, ftop - 1), (fx + fw, ftop + fh)], outline=theme.team_outline_fill)
        else:
            draw.rectangle([(fx, ftop), (fx + fw - 1, ftop + fh - 1)], outline=fg)
        b = draw.textbbox((0, 0), r["code"], font=font)
        draw.text((cx - int(b[2] - b[0]) // 2 - int(b[0]), ctop - int(b[1])), r["code"],
                  font=font, fill=fg)
        x += col_w


def draw_combo(panel, draw, theme, ctx) -> None:
    _outline(draw, theme)
    block = 120
    draw.rectangle([(0, 0), (block - 1, H - 1)], fill=theme.team_header_fill)
    _draw_countdown(draw, _countdown_parts(ctx), 4, 3, block - 8, 30,
                    theme.team_header_text_fill, max_size=32)
    small = load_brand_font(MIN_SIZE)
    label = _clamp(draw, f"do {ctx.next_name_cs}", small, block - 8)
    _put(draw, block // 2, 40, label, small, theme.team_header_text_fill, h=14, align="center")
    done = sum(1 for r in ctx.rounds if r["round"] < ctx.next_round)
    remaining = [r for r in ctx.rounds if r["round"] >= ctx.next_round]
    f13 = load_brand_font(13, bold=True)
    _put(draw, block + 8, 4, f"Zbývá {len(remaining)} závodů", f13, theme.text_fill, h=14)
    _put(draw, W - 6, 4, f"{done}/{len(ctx.rounds)} odjeto", f13, theme.accent_fill, h=14,
         align="right")
    _cells(draw, theme, remaining, ctx, block + 8, W - 6, 28, 22, gap=4, max_cell=34)


def draw_track(panel, draw, theme, ctx) -> None:
    _outline(draw, theme)
    import logging

    from app.services.renderer_assets import prepare_color_track_image, prepare_mono_track_image
    from app.services.track_renderer import render_track_image

    try:
        track = render_track_image({"circuit": {"circuitId": ctx.circuit_id}}, width=220,
                                   height=110, display=ctx.display)
        if ctx.display == "1bit":
            track = prepare_mono_track_image(track, 110, 55, logging.getLogger(__name__))
        else:
            track = prepare_color_track_image(track, 110, 55)
    except Exception:  # noqa: BLE001 - concept code: no map -> text only
        track = None
    if track is not None:
        panel.paste(track.convert(panel.mode), (4, (H - track.height) // 2))
    x, max_w = 122, W - 128
    name_font = _fit(draw, ctx.next_name_cs, max_w, max_size=17)
    _put(draw, x, 3, ctx.next_name_cs, name_font, theme.accent_fill)
    f13, f12 = load_brand_font(13), load_brand_font(MIN_SIZE)
    if ctx.circuit_name:
        _put(draw, x, 22, _clamp(draw, ctx.circuit_name, f13, max_w), f13, theme.text_fill)
    info = f"{_date_cs(ctx)} · kolo {ctx.next_round}"
    _put(draw, x, 36, _clamp(draw, info, f13, max_w), f13, theme.text_fill)
    _put(draw, W - 5, 49, "trať: CC BY 4.0, viz /credits", f12, theme.text_fill, align="right")


VARIANTS = {
    "A": draw_timeline,
    "B": draw_countdown,
    "C": draw_weather,
    "D": draw_flag_timeline,
    "E": draw_combo,
    "F": draw_track,
}


def draw_extra_panel(variant: str, image: Image.Image, draw: ImageDraw.ImageDraw, theme, ctx) -> None:
    """Render one concept into ``SLOT``; ``draw`` is unused because we draw on a clipped crop."""
    _ = draw
    box = (SLOT[0], SLOT[1], SLOT[2] + 1, SLOT[3] + 1)
    panel = image.crop(box)
    pdraw = ImageDraw.Draw(panel)
    pdraw.rectangle([(0, 0), (W - 1, H - 1)], fill=theme.colors.WHITE)
    VARIANTS[variant.upper()](panel, pdraw, theme, ctx)
    image.paste(panel, box[:2])
