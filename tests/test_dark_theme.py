"""Opt-in dark theme: ``theme=light|dark`` on the calendar, teams and auto screens (#185 F7)."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image

import app.services.renderer_base as renderer_base_module
from app.routes import images, previews
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts, teams_extra
from app.services import renderer_teams_extras as panels
from app.services.bwr_renderer import BwrColors, BwrRenderer
from app.services.bwry_renderer import BwryColors, BwryRenderer
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.renderer import MonoColors, Renderer
from app.services.renderer_teams import teams_extra_slot
from app.services.renderer_theme import ThemeMode
from app.services.renderers import create_renderer
from app.services.spectra6_renderer import Spectra6Colors, Spectra6Renderer
from app.services.teams_extra import TeamsExtra, build_teams_extra_context
from app.services.teams_service import TeamsService
from app.services.track_catalog import COLORS, TrackOptions, TrackStyle
from app.services.track_drawing import build_track_svg, inverts_inks
from app.services.track_renderer import render_track_image
from app.services.weather_service import WeatherData
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils.etag import strong_etag
from app.utils.image_conversion import ImageOutput, OutputFormat, Rotation, transform_bmp_output

DISPLAYS = ("1bit", "bwr", "bwry", "spectra6")
WHITE, BLACK, RED = (255, 255, 255), (0, 0, 0), (255, 0, 0)
PALETTES = {
    "1bit": [BLACK, WHITE],
    "bwr": BwrColors.PALETTE,
    "bwry": BwryColors.PALETTE,
    "spectra6": Spectra6Colors.PALETTE,
}
RENDERERS = {
    "1bit": Renderer,
    "bwr": BwrRenderer,
    "bwry": BwryRenderer,
    "spectra6": Spectra6Renderer,
}
# Qatar 2026 (bundled schedule): lights out 2026-11-29 16:00 UTC.
RACE_START = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
RACE_WEEK = RACE_START - timedelta(hours=20)
OFF_WEEK = datetime(2026, 11, 22, 9, 30, tzinfo=UTC)
PANEL_NOW = datetime(2026, 3, 6, 12, tzinfo=UTC)


def _qatar() -> dict:
    """Return the bundled Qatar 2026 weekend converted to Europe/Prague."""
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    return next(r for r in races if r["race_name"] == "Qatar Grand Prix")


def _rgb(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGB")


def _colors(image: Image.Image) -> set[tuple[int, int, int]]:
    return {color for _count, color in image.getcolors(1 << 20)}


def _share(image: Image.Image, color: tuple[int, int, int]) -> float:
    counts = {rgb: count for count, rgb in image.getcolors(1 << 20)}
    return counts.get(color, 0) / (image.width * image.height)


def _calendar(display: str, theme: ThemeMode, lang: str = "en", **kwargs) -> bytes:
    race = kwargs.pop("race", None) or _qatar()
    historical = F1Service.get_historical_from_static(race["circuit"]["circuitId"])
    renderer = create_renderer(display, get_translator(lang), lang, theme)
    return renderer.render_calendar(
        race, historical, WeatherData(24.5, 61, 30), "race_day", **kwargs
    )


def _teams(display: str, theme: ThemeMode, extra: TeamsExtra = TeamsExtra.NONE, lang="en") -> bytes:
    context = build_teams_extra_context(extra, 2026, now=PANEL_NOW)
    if context is not None:
        context = replace(context, weather=WeatherData(22, 3, 40))
    renderer = create_renderer(display, get_translator(lang), lang, theme)
    return renderer.render_teams_drivers(TeamsService()._load_from_json(2026), extra, context)


# --- Themes and renderers --------------------------------------------------------------------


def test_theme_values_and_light_default_keep_the_existing_policy():
    assert [theme.value for theme in ThemeMode] == ["light", "dark"]
    for display, renderer_type in RENDERERS.items():
        light = create_renderer(display, get_translator("en"), "en")
        explicit = create_renderer(display, get_translator("en"), "en", ThemeMode.LIGHT)
        dark = create_renderer(display, get_translator("en"), "en", ThemeMode.DARK)
        assert light.theme is explicit.theme is renderer_type.THEME
        assert dark.theme is renderer_type.DARK_THEME
        theme = renderer_type.THEME
        assert not theme.dark and not theme.invert_logo
        assert theme.canvas_fill == theme.colors.WHITE
        assert theme.header_rule_fill is theme.team_header_rule_fill is None
        assert theme.flag_outline_fill is None
        assert (theme.block_fill, theme.block_text_fill) == (
            theme.team_header_fill,
            theme.team_header_text_fill,
        )


def test_dark_themes_swap_black_and_white_but_keep_the_accents():
    mono = Renderer.DARK_THEME
    assert mono.canvas_fill == MonoColors.BLACK and mono.text_fill == MonoColors.WHITE
    assert mono.header_rule_fill == MonoColors.WHITE
    assert (mono.countdown_fill, mono.countdown_text_fill) == (MonoColors.WHITE, MonoColors.BLACK)
    assert mono.driver_badge_colors(2) == (MonoColors.WHITE, MonoColors.BLACK)
    assert mono.driver_badge_colors(5) == (MonoColors.BLACK, MonoColors.WHITE)
    for renderer_type in (BwrRenderer, BwryRenderer, Spectra6Renderer):
        theme = renderer_type.DARK_THEME
        assert theme.dark and theme.invert_logo
        assert theme.canvas_fill == BLACK and theme.text_fill == WHITE
        # Red is not inverted (to cyan): header, countdown, separator and P1 stay red.
        assert theme.header_right_fill == theme.countdown_fill == theme.accent_fill == RED
        assert theme.team_position_fill(1) == RED and theme.team_position_fill(2) == WHITE
        assert theme.driver_badge_colors(1) == (RED, WHITE)
        assert theme.driver_badge_colors(3) == (WHITE, BLACK)
        assert theme.session_shadow_fill is None and theme.header_rule_fill is None
        assert (theme.block_fill, theme.block_text_fill) == (WHITE, BLACK)
        assert theme.flag_outline_fill == theme.team_header_rule_fill == WHITE
    bwr = dict(BwrRenderer.DARK_THEME.session_fills)
    assert bwr == {
        "race": RED,
        "qualifying": WHITE,
        "sprint": WHITE,
        "practice": WHITE,
        "default": WHITE,
    }
    spectra = dict(Spectra6Renderer.DARK_THEME.session_fills)
    assert spectra["qualifying"] == Spectra6Colors.YELLOW
    assert spectra["practice"] == Spectra6Colors.BLUE


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("lang", ["en", "de", "ja"])
def test_dark_calendar_is_dark_with_white_text_and_only_panel_colours(display, lang):
    light = _rgb(_calendar(display, ThemeMode.LIGHT, lang))
    dark = _rgb(_calendar(display, ThemeMode.DARK, lang))
    assert dark.size == (800, 480)
    assert _colors(dark) <= set(PALETTES[display])
    # A black canvas: mostly black, little white left from forgotten fills (light is >50% white).
    assert _share(light, WHITE) > 0.5
    assert _share(dark, BLACK) > 0.6
    assert _share(dark, WHITE) < 0.3
    # Text regions (schedule, circuit facts, results) carry white glyphs on black.
    for box in ((510, 125, 800, 245), (600, 300, 800, 385), (110, 395, 800, 480)):
        region = dark.crop(box)
        assert (
            {BLACK, WHITE}
            <= _colors(region)
            <= {BLACK, WHITE, RED, Spectra6Colors.BLUE, Spectra6Colors.YELLOW, Spectra6Colors.GREEN}
        )
        assert _share(region, BLACK) > 0.6
    # The header car is white line art on black instead of a white plate.
    logo = dark.crop((0, 0, 228, 86))
    assert _share(logo, BLACK) > 0.5 and WHITE in _colors(logo)
    if display != "1bit":
        # Red stays red: the title band, the countdown box and the footer separator.
        assert _share(dark.crop((240, 0, 800, 85)), RED) > 0.6
        assert dark.getpixel((400, 386)) == RED


@pytest.mark.parametrize("display", DISPLAYS)
def test_dark_text_edges_do_not_pick_up_stray_colours(display):
    """Schedule dates, the header title and the footer stay strictly black and white."""
    dark = _rgb(_calendar(display, ThemeMode.DARK, "ja"))
    for box in ((510, 125, 670, 245), (600, 300, 800, 385), (110, 395, 800, 480)):
        assert _colors(dark.crop(box)) == {BLACK, WHITE}
    if display != "1bit":
        assert _colors(dark.crop((245, 5, 790, 85))) == {RED, WHITE}


def test_mono_dark_header_is_underlined_and_countdown_inverted():
    dark = _rgb(_calendar("1bit", ThemeMode.DARK))
    assert all(dark.getpixel((x, 89)) == WHITE for x in range(0, 800, 7))
    # The countdown box is a white plate with black text.
    box = dark.crop((515, 268, 795, 293))
    assert _share(box, WHITE) > 0.6 and BLACK in _colors(box)


@pytest.mark.parametrize("display", ["bwr", "spectra6"])
def test_flags_keep_their_colours_with_a_white_border(display):
    light = _rgb(_calendar(display, ThemeMode.LIGHT))
    dark = _rgb(_calendar(display, ThemeMode.DARK))
    # The Qatar results flag sits bottom left; its pixels are identical in both themes.
    flag_box = (14, 420, 97, 475)
    assert dark.crop(flag_box).tobytes() == light.crop(flag_box).tobytes()
    assert RED in _colors(dark.crop(flag_box))
    border = [dark.getpixel((x, 476)) for x in range(20, 90)]
    assert set(border) == {WHITE}


def test_new_track_badge_and_error_image_follow_the_dark_canvas():
    races = F1Service(timezone_name="Europe/Prague").get_all_races_from_static(2026)
    madring = next(race for race in races if race["circuit"]["circuitId"] == "madring")
    dark = _rgb(_calendar("bwr", ThemeMode.DARK, race=madring))
    badge = dark.crop((520, 905 - 484, 680, 955 - 484))
    assert _share(badge, BLACK) > 0.4 and WHITE in _colors(badge)
    for display in DISPLAYS:
        renderer = create_renderer(display, get_translator("en"), "en", ThemeMode.DARK)
        error = _rgb(renderer.render_error("Temporary rendering error"))
        assert _share(error, BLACK) > 0.95 and WHITE in _colors(error)
        if display != "1bit":
            assert RED in _colors(error.crop((0, 0, 200, 50)))


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("extra", list(TeamsExtra))
def test_dark_teams_and_every_panel_are_dark_and_palette_only(display, extra):
    dark = _rgb(_teams(display, ThemeMode.DARK, extra))
    assert _colors(dark) <= set(PALETTES[display])
    assert _share(dark, BLACK) > 0.6 and _share(dark, WHITE) < 0.3
    # Team cards: a white card outline and white driver text on black.
    card = dark.crop((4, 120, 398, 152))
    assert _share(card, BLACK) > 0.6 and WHITE in _colors(card)
    panel = dark.crop(teams_extra_slot(800, 480, 90, 6))
    assert _colors(panel) <= set(PALETTES[display])
    if extra == TeamsExtra.NONE:
        assert _colors(panel) == {BLACK}
        return
    assert WHITE in _colors(panel)
    # The panel outline is white; outside the countdown block the panel stays dark.
    assert panel.getpixel((0, 30)) == WHITE and panel.getpixel((panel.width - 1, 30)) == WHITE
    if extra != TeamsExtra.COUNTDOWN:
        assert _share(panel, BLACK) > 0.5


@pytest.mark.parametrize("lang", ["en", "de", "ja"])
def test_dark_panels_in_long_and_cjk_languages(lang):
    for extra in (TeamsExtra.COUNTDOWN, TeamsExtra.WEATHER, TeamsExtra.TRACK):
        dark = _rgb(_teams("bwry", ThemeMode.DARK, extra, lang))
        assert _colors(dark) <= set(BwryColors.PALETTE)


def test_dark_flag_panel_borders_colour_flags_and_inverts_the_1bit_active_cell():
    context = build_teams_extra_context(TeamsExtra.FLAGS, 2026, now=PANEL_NOW)
    for display in ("1bit", "spectra6"):
        renderer = create_renderer(display, get_translator("en"), "en", ThemeMode.DARK)
        panel = Image.new(renderer._new_canvas().mode, (394, 61), renderer.theme.canvas_fill)
        panels.draw_teams_extra(
            panel,
            renderer._new_draw(panel),
            renderer.theme,
            context,
            TeamsExtra.FLAGS,
            display=display,
            translator=get_translator("en"),
            lang="en",
        )
        rgb = panel.convert("RGB")
        if display == "1bit":
            # The active (next) race is a white block with black text, like a light block.
            active = rgb.crop((8, 8, 120, 52))
            assert _share(active, WHITE) > 0.5 and BLACK in _colors(active)
        else:
            # A thin white border frames each flag so its dark edge stays visible.
            assert rgb.getpixel((5 + 9, 20)) == WHITE


def test_dark_light_panel_track_and_flag_helpers_keep_light_defaults():
    theme = BwrRenderer.THEME
    assert theme.block_fill == theme.team_header_fill
    assert BwrRenderer.DARK_THEME.block_fill == WHITE


# --- Track artwork ---------------------------------------------------------------------------


def test_track_inks_swap_for_every_style_but_negative():
    for style in TrackStyle:
        assert inverts_inks(TrackOptions(style), True) is (style != TrackStyle.NEGATIVE)
        assert not inverts_inks(TrackOptions(style), False)
    light = build_track_svg("monza", TrackOptions(TrackStyle.PAPERCUT), "1bit")
    dark = build_track_svg("monza", TrackOptions(TrackStyle.PAPERCUT), "1bit", dark=True)
    paper = re.compile(rb'fill="(#[0-9A-F]{6})" data-layer="paper"')
    assert paper.search(light).group(1) == COLORS["white"].encode()
    assert paper.search(dark).group(1) == COLORS["black"].encode()
    # Luminance masks keep white = visible, black = cut, whatever the theme.
    for svg in (light, dark):
        assert re.search(rb'<mask [^>]*>\s*<rect [^>]*fill="#FFFFFF"', svg)
    negative = TrackOptions(TrackStyle.NEGATIVE)
    assert build_track_svg("monza", negative, "bwr", dark=True) == build_track_svg(
        "monza", negative, "bwr"
    )


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("style", list(TrackStyle))
def test_every_track_style_stays_visible_on_dark(display, style):
    race = {"circuit": {"circuitId": "losail"}}
    image = render_track_image(race, 494, 271, display, TrackOptions(style), dark=True)
    assert image is not None
    rgb = image.convert("RGB")
    assert _colors(rgb) <= set(PALETTES[display])
    visible = 1 - _share(rgb, BLACK)
    # Track ink and decorations show up on the black canvas (white or accent colours).
    assert visible > 0.03, (display, style, visible)
    light = render_track_image(race, 494, 271, display, TrackOptions(style)).convert("RGB")
    assert (light.tobytes() == rgb.tobytes()) is (style == TrackStyle.NEGATIVE)


def test_dark_track_cache_key_is_separate():
    race = {"circuit": {"circuitId": "monza"}}
    light = render_track_image(race, 300, 200, "spectra6", TrackOptions(TrackStyle.CLEAN))
    dark = render_track_image(race, 300, 200, "spectra6", TrackOptions(TrackStyle.CLEAN), dark=True)
    assert light.convert("RGB").getpixel((0, 0)) == WHITE
    assert dark.convert("RGB").getpixel((0, 0)) == BLACK


# --- Artifacts and caches --------------------------------------------------------------------


def test_cache_selection_and_keys():
    assert bmp_artifacts.theme_cache_suffix(ThemeMode.LIGHT) == ""
    assert bmp_artifacts.theme_cache_suffix(ThemeMode.DARK) == ":theme=dark"
    assert bmp_artifacts.theme_kwargs(ThemeMode.LIGHT) == {}
    assert bmp_artifacts.theme_kwargs(ThemeMode.DARK) == {"theme": ThemeMode.DARK}
    from app.utils.refresh_hint import CountdownMode

    assert not bmp_artifacts.is_calendar_variant(CountdownMode.HOURS)
    assert bmp_artifacts.is_calendar_variant(CountdownMode.HOURS, theme=ThemeMode.DARK)
    assert bmp_artifacts.calendar_image_cache(CountdownMode.HOURS) is get_bmp_cache()
    assert (
        bmp_artifacts.calendar_image_cache(CountdownMode.HOURS, theme=ThemeMode.DARK)
        is get_bmp_variant_cache()
    )
    assert bmp_artifacts.teams_image_cache(TeamsExtra.NONE) is get_bmp_cache()
    assert (
        bmp_artifacts.teams_image_cache(TeamsExtra.NONE, ThemeMode.DARK) is get_bmp_variant_cache()
    )
    assert bmp_artifacts.is_teams_variant(TeamsExtra.FLAGS)
    assert not bmp_artifacts.is_teams_variant(TeamsExtra.NONE)


@pytest.fixture
def clean_caches():
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()
    yield
    get_bmp_cache().clear()
    get_bmp_variant_cache().clear()
    bmp_artifacts._singleflight_tasks.clear()


@pytest.mark.asyncio
async def test_dark_error_artifact_is_shared_separately(clean_caches):
    light = await bmp_artifacts.shared_error_artifact("bwr", "en", "Temporary rendering error")
    dark = await bmp_artifacts.shared_error_artifact(
        "bwr", "en", "Temporary rendering error", ThemeMode.DARK
    )
    assert light != dark
    assert _share(_rgb(dark[0]), BLACK) > 0.95
    missing = await bmp_artifacts.render_calendar(
        race_data=None,
        lang="en",
        target_tz="UTC",
        weather_type="off",
        display="1bit",
        theme=ThemeMode.DARK,
    )
    assert missing[1:] == (None, False) and _share(_rgb(missing[0]), BLACK) > 0.95


# --- Routes ----------------------------------------------------------------------------------


class _Clock:
    now = RACE_WEEK


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def image_app(monkeypatch, clean_caches, tmp_path):
    """Mount the image and preview routes with a fixed next race, clock and teams data."""
    _Clock.now = RACE_WEEK
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    race = _qatar()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _qatar())
    record = MagicMock()
    monkeypatch.setattr(images, "record_api_call", record)
    monkeypatch.setattr(images, "enforce_rate_limit", MagicMock())
    monkeypatch.setattr(previews, "enforce_rate_limit", MagicMock())
    pregenerated_calendar = MagicMock(return_value=None)
    pregenerated_teams = MagicMock(return_value=None)
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", pregenerated_calendar)
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", pregenerated_teams)
    monkeypatch.setattr(images, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(previews, "get_default_teams_year", lambda: 2026)
    monkeypatch.setattr(images, "points_session_starts", lambda year: [RACE_START])
    monkeypatch.setattr(bmp_artifacts.config, "WEATHER_ENABLED", False)
    monkeypatch.setattr(previews.config, "IMAGES_PATH", str(tmp_path))
    build = teams_extra.build_teams_extra_context

    def context(extra, year, **kwargs):
        return build(extra, year, now=_Clock.now, **kwargs)

    monkeypatch.setattr(images, "build_teams_extra_context", context)
    monkeypatch.setattr(previews, "build_teams_extra_context", context)
    monkeypatch.setattr(bmp_artifacts, "is_teams_data_cacheable", lambda data: True)
    monkeypatch.setattr(previews, "is_teams_data_cacheable", lambda data: True)
    teams_data = TeamsService()._load_from_json(2026)
    monkeypatch.setattr(
        bmp_artifacts.TeamsService, "get_teams_and_drivers", AsyncMock(return_value=teams_data)
    )
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    app.state.record = record
    app.state.pregenerated = (pregenerated_calendar, pregenerated_teams)
    return app


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/calendar.bmp?theme=black",
        "/calendar.bmp?theme=DARK",
        "/teams.bmp?theme=",
        "/auto.bmp?theme=night",
        "/preview/configure/calendar.png?theme=invert",
        "/preview/configure/teams.png?theme=1",
    ],
)
async def test_invalid_theme_returns_422(image_app, url):
    response = await _get(image_app, url)
    assert response.status_code == 422
    image_app.state.record.assert_not_called()


@pytest.mark.asyncio
async def test_light_is_the_default_and_dark_has_its_own_variant_entry(image_app):
    default = await _get(image_app, "/calendar.bmp?display=bwr")
    explicit = await _get(image_app, "/calendar.bmp?display=bwr&theme=light")
    assert explicit.content == default.content
    assert (default.headers["x-cache"], explicit.headers["x-cache"]) == ("MISS", "HIT")
    assert len(get_bmp_cache()) == 1 and not get_bmp_variant_cache()
    assert all("theme" not in key for key in get_bmp_cache())
    pregenerated_calendar, _ = image_app.state.pregenerated
    assert pregenerated_calendar.call_count == 1

    dark = await _get(image_app, "/calendar.bmp?display=bwr&theme=dark")
    assert dark.status_code == 200 and dark.headers["x-cache"] == "MISS"
    assert dark.headers["etag"] == strong_etag(dark.content) != default.headers["etag"]
    assert dark.headers["cache-control"] == default.headers["cache-control"]
    assert dark.headers["link"] == default.headers["link"]
    assert dark.headers["x-next-refresh-seconds"] == default.headers["x-next-refresh-seconds"]
    # Never looked up in (or served from) the pregenerated files; only the variant cache.
    assert pregenerated_calendar.call_count == 1
    assert len(get_bmp_cache()) == 1
    assert [key.endswith(":theme=dark") for key in get_bmp_variant_cache()] == [True]
    assert _share(_rgb(dark.content), BLACK) > 0.6
    again = await _get(image_app, "/calendar.bmp?display=bwr&theme=dark")
    assert again.headers["x-cache"] == "HIT" and again.content == dark.content
    revalidated = await _get(
        image_app, "/calendar.bmp?display=bwr&theme=dark", {"If-None-Match": dark.headers["etag"]}
    )
    assert revalidated.status_code == 304
    stale = await _get(
        image_app,
        "/calendar.bmp?display=bwr&theme=dark",
        {"If-None-Match": default.headers["etag"]},
    )
    assert stale.status_code == 200


@pytest.mark.asyncio
async def test_dark_composes_with_png_rotation_and_other_calendar_variants(image_app):
    params = "display=spectra6&countdown=days&weekend_mode=auto&temp_unit=f&theme=dark"
    base = await _get(image_app, f"/calendar.bmp?{params}")
    variant = await _get(image_app, f"/calendar.bmp?{params}&format=png&rotate=180")
    assert variant.headers["content-type"] == "image/png"
    with Image.open(BytesIO(variant.content)) as decoded, Image.open(BytesIO(base.content)) as raw:
        assert decoded.tobytes() == raw.transpose(Image.Transpose.ROTATE_180).tobytes()
    assert variant.content == transform_bmp_output(
        base.content,
        ImageOutput(OutputFormat.PNG, Rotation.HALF_TURN),
        palette=list(Spectra6Colors.PALETTE),
    )
    assert not get_bmp_cache()
    light = await _get(image_app, f"/calendar.bmp?{params.replace('dark', 'light')}")
    assert light.content != base.content


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["none", "flags", "weather"])
async def test_dark_teams_render_on_demand_into_the_variant_cache(image_app, extra):
    light = await _get(image_app, f"/teams.bmp?display=bwry&extra={extra}")
    dark = await _get(image_app, f"/teams.bmp?display=bwry&extra={extra}&theme=dark")
    assert dark.status_code == 200 and dark.content != light.content
    assert dark.headers["link"] == light.headers["link"]
    assert dark.headers["cache-control"] == "no-cache"
    dark_keys = [key for key in get_bmp_variant_cache() if key.endswith(":theme=dark")]
    assert len(dark_keys) == 1
    assert all("theme" not in key for key in get_bmp_cache())
    _, pregenerated_teams = image_app.state.pregenerated
    assert pregenerated_teams.call_count == (1 if extra == "none" else 0)
    assert _share(_rgb(dark.content), BLACK) > 0.6
    again = await _get(image_app, f"/teams.bmp?display=bwry&extra={extra}&theme=dark")
    assert again.headers["x-cache"] == "HIT"


@pytest.mark.asyncio
async def test_auto_passes_the_theme_to_the_active_screen(image_app):
    _Clock.now = RACE_WEEK
    auto = await _get(image_app, "/auto.bmp?display=1bit&theme=dark&format=png&rotate=180")
    direct = await _get(image_app, "/calendar.bmp?display=1bit&theme=dark&format=png&rotate=180")
    assert auto.headers["x-active-screen"] == "calendar"
    assert auto.content == direct.content
    _Clock.now = OFF_WEEK
    teams = await _get(image_app, "/auto.bmp?display=bwr&theme=dark")
    plain = await _get(image_app, "/teams.bmp?display=bwr&extra=countdown&theme=dark")
    assert teams.headers["x-active-screen"] == "teams"
    assert teams.content == plain.content


@pytest.mark.asyncio
async def test_failed_dark_render_returns_a_dark_error_image(image_app, monkeypatch):
    monkeypatch.setattr(
        bmp_artifacts, "render_calendar_artifact", AsyncMock(side_effect=RuntimeError("boom"))
    )
    response = await _get(image_app, "/calendar.bmp?display=bwr&theme=dark")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert _share(_rgb(response.content), BLACK) > 0.95
    monkeypatch.setattr(
        bmp_artifacts.TeamsService,
        "get_teams_and_drivers",
        AsyncMock(side_effect=RuntimeError("upstream")),
    )
    teams = await _get(image_app, "/teams.bmp?display=1bit&theme=dark")
    assert teams.headers["cache-control"] == "no-store"
    assert _share(_rgb(teams.content), BLACK) > 0.95


@pytest.mark.asyncio
async def test_dark_configure_previews_render_on_demand(image_app):
    calendar = await _get(image_app, "/preview/configure/calendar.png?display=bwr&theme=dark")
    assert calendar.status_code == 200 and calendar.headers["content-type"] == "image/png"
    assert _share(_rgb(calendar.content), BLACK) > 0.6
    assert any(key.endswith(":theme=dark") for key in get_bmp_variant_cache())
    for query in ("display=spectra6&theme=dark", "display=1bit&extra=flags&theme=dark"):
        teams = await _get(image_app, f"/preview/configure/teams.png?{query}")
        assert teams.status_code == 200 and teams.headers["content-type"] == "image/png"
        assert 'rel="license"' in teams.headers["link"]
        assert _share(_rgb(teams.content), BLACK) > 0.6
        revalidated = await _get(
            image_app,
            f"/preview/configure/teams.png?{query}",
            {"If-None-Match": teams.headers["etag"]},
        )
        assert revalidated.status_code == 304
    preview_keys = [key for key in get_bmp_variant_cache() if key.startswith("preview:")]
    assert len(preview_keys) == 2 and all(key.endswith(":theme=dark") for key in preview_keys)


@pytest.mark.asyncio
async def test_dark_teams_preview_falls_back_to_a_plain_dark_render(image_app, monkeypatch):
    monkeypatch.setattr(
        previews, "_render_teams_extra_preview", AsyncMock(side_effect=RuntimeError("panel"))
    )
    fallback = AsyncMock(wraps=previews._render_teams_preview)
    monkeypatch.setattr(previews, "_render_teams_preview", fallback)
    response = await _get(image_app, "/preview/configure/teams.png?extra=track&theme=dark")
    assert response.status_code == 200
    assert response.headers["x-preview-fallback"] == "plain"
    assert response.headers["cache-control"] == "no-store"
    assert fallback.await_args.kwargs == {"full_size": True, "theme": ThemeMode.DARK}
    assert _share(_rgb(response.content), BLACK) > 0.6


@pytest.mark.asyncio
async def test_api_info_documents_the_theme(image_app):
    from app.routes import api

    endpoints = (await api.api_info())["endpoints"]
    for path in (
        "/calendar.bmp",
        "/teams.bmp",
        "/auto.bmp",
        "/preview/configure/{screen_type}.png",
    ):
        parameter = endpoints[path]["parameters"]["theme"]
        assert parameter["values"] == ["light", "dark"] and parameter["default"] == "light"
