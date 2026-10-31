"""Offline context and clipped teams-panel regression tests."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import Mock

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw

from app.services import renderer_teams_extras as panels
from app.services import teams_extra as extras
from app.services.f1_service import F1Service
from app.services.i18n import get_translator
from app.services.image_keys import get_teams_image_key
from app.services.renderer_teams import teams_extra_slot
from app.services.renderers import create_renderer
from app.services.teams_extra import TeamsExtra, build_teams_extra_context, teams_extra_cache_token
from app.services.teams_service import TeamsService
from app.services.weather_service import WeatherData, WeatherService

NOW = datetime(2026, 3, 6, 12, tzinfo=UTC)


@pytest.fixture
def ctx():
    """Build a real bundled season context at a deterministic instant."""
    value = build_teams_extra_context(TeamsExtra.TRACK, 2026, now=NOW)
    assert value is not None
    return value


def test_context(ctx):
    """Use canonical identities and skip cancelled rounds in a frozen snapshot."""
    assert ctx.next_round == 1
    assert ctx.race_name == "Australian GP"
    assert ctx.rounds[0].code == "AUS"
    assert ctx.rounds[0].iso2 == "au"
    assert ctx.days_left == 2
    assert len(ctx.rounds) == sum(bool(r.round) for r in F1Service.get_season_from_static(2026))
    assert ctx.track_credit.startswith("Track: ")
    with pytest.raises(FrozenInstanceError):
        ctx.rounds[0].done = True


def test_absent_context():
    """Default, missing, historical, finished and premature seasons have no panel."""
    assert build_teams_extra_context(TeamsExtra.NONE, 2026, now=NOW) is None
    assert build_teams_extra_context(TeamsExtra.TRACK, 2001, now=NOW) is None
    assert build_teams_extra_context(TeamsExtra.TRACK, 2025, now=NOW) is None
    assert (
        build_teams_extra_context(TeamsExtra.TRACK, 2026, now=datetime(2026, 12, 31, tzinfo=UTC))
        is None
    )
    assert (
        build_teams_extra_context(TeamsExtra.TRACK, 2026, now=datetime(2025, 10, 1, tzinfo=UTC))
        is None
    )


def test_midnight_and_token(ctx):
    """Local midnight changes day counts and keys; sub-day changes do not."""
    before = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=datetime(2026, 3, 6, 22, 59, tzinfo=UTC)
    )
    after = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=datetime(2026, 3, 6, 23, 1, tzinfo=UTC)
    )
    assert before.days_left == 2
    assert after.days_left == 1
    token = teams_extra_cache_token(TeamsExtra.COUNTDOWN, ctx)
    assert token == teams_extra_cache_token(TeamsExtra.COUNTDOWN, before)
    assert token != teams_extra_cache_token(TeamsExtra.COUNTDOWN, after)
    assert token != teams_extra_cache_token(TeamsExtra.COUNTDOWN, replace(ctx, next_round=2))
    assert teams_extra_cache_token(TeamsExtra.NONE, ctx) == ""
    assert teams_extra_cache_token(TeamsExtra.TRACK, None) == ""
    assert get_teams_image_key("en", 2026, extra=TeamsExtra.COUNTDOWN, extra_context=ctx).endswith(
        token
    )
    wx = replace(ctx, weather=WeatherData(20.1, 3, 40))
    key = teams_extra_cache_token(TeamsExtra.WEATHER, wx)
    assert key == teams_extra_cache_token(
        TeamsExtra.WEATHER, replace(wx, weather=WeatherData(20.2, 3, 40))
    )
    for weather in (
        WeatherData(21, 3, 40),
        WeatherData(20, 2, 40),
        WeatherData(20, 3, 41),
        WeatherData(20, 3, 40, "2 mm"),
    ):
        assert key != teams_extra_cache_token(TeamsExtra.WEATHER, replace(ctx, weather=weather))


def test_weather_cache_only(monkeypatch):
    """Forecast and fallback access never invoke HTTP or weather fetchers."""
    fail = Mock(side_effect=AssertionError("network forbidden"))
    monkeypatch.setattr(httpx, "AsyncClient", fail)
    for name in dir(WeatherService):
        if "fetch" in name:
            monkeypatch.setattr(WeatherService, name, fail)
    weather = WeatherData(23, 1, 10)
    cached = Mock(return_value=weather)
    fallback = Mock(return_value=weather)
    monkeypatch.setattr(WeatherService, "_get_cached", cached)
    monkeypatch.setattr(extras, "get_cached_circuit_weather", fallback)
    assert build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=NOW).weather == weather
    fallback.assert_not_called()
    cached.return_value = None
    assert build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=NOW).weather == weather
    fallback.return_value = None
    assert build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=NOW).weather is None
    cached.reset_mock()
    build_teams_extra_context(TeamsExtra.COUNTDOWN, 2026, now=NOW)
    cached.assert_not_called()


@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
@pytest.mark.parametrize("extra", list(TeamsExtra))
def test_render_clipping(ctx, display, extra):
    """All variants preserve every pixel outside the shared final-card box."""
    renderer = create_renderer(display, get_translator("en"), "en")
    teams = TeamsService()._load_from_json(2026)
    default = renderer.render_teams_drivers(teams)
    assert default == renderer.render_teams_drivers(teams, TeamsExtra.NONE, ctx)
    assert default == renderer.render_teams_drivers(teams, extra, None)
    context = replace(ctx, weather=WeatherData(22, 3, 40))
    result = renderer.render_teams_drivers(teams, extra, context)
    original = Image.open(BytesIO(default)).convert("RGB")
    actual = Image.open(BytesIO(result)).convert("RGB")
    box = teams_extra_slot(800, 480, 90, 6)
    assert box == (402, 414, 796, 475)
    diff = ImageChops.difference(original, actual)
    diff.paste((0, 0, 0), box)
    assert diff.getbbox() is None
    if extra != TeamsExtra.NONE:
        assert ImageChops.difference(original.crop(box), actual.crop(box)).getbbox() is not None
    else:
        assert default == result
    if extra == TeamsExtra.WEATHER:
        assert default == renderer.render_teams_drivers(teams, extra, replace(ctx, weather=None))
    even_teams = teams.model_copy(update={"teams": teams.teams[:-1]})
    assert renderer.render_teams_drivers(even_teams) == renderer.render_teams_drivers(
        even_teams, extra, context
    )


@pytest.mark.parametrize("lang", ["en", "cs", "ja", "zh-CN"])
@pytest.mark.parametrize("extra", list(TeamsExtra))
def test_panel_languages_and_credits(ctx, lang, extra, monkeypatch):
    """Locale-aware panels tolerate unavailable flags and track artwork."""
    renderer = create_renderer("1bit", get_translator(lang), lang)
    image = Image.new("1", (394, 61), 1)
    context = replace(
        ctx,
        days_left=0,
        next_round=2,
        weather=WeatherData(20, 1, 20),
        circuit_name="a very long circuit name " * 8,
    )
    monkeypatch.setattr(panels, "render_track_image", Mock(return_value=None))
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        renderer.theme,
        context,
        extra,
        display="1bit",
        translator=get_translator(lang),
        lang=lang,
    )
    if extra == TeamsExtra.TRACK:
        assert image.crop((4, 46, 389, 59)).getextrema() == (0, 1)
        credit = panels._credit(context, get_translator(lang), ImageDraw.Draw(image), lang, 384)
        assert "CC BY" in credit
    assert (
        panels._countdown(context, get_translator(lang), lang)
        == get_translator(lang)["teams_extra_today"]
    )


def test_missing_flags_and_track(ctx, monkeypatch, tmp_path):
    """Missing colour flags fall back to code boxes and track failures retain credit."""
    renderer = create_renderer("spectra6", {}, "en")
    theme = replace(renderer.theme, flags_directories=lambda: tmp_path)
    assert panels._flag(theme, None) is None
    assert panels._flag(theme, "xx") is None
    (tmp_path / "xx.bmp").write_bytes(b"broken")
    assert panels._flag(theme, "xx") is None
    Image.new("RGB", (30, 17), "red").save(tmp_path / "xx.png")
    assert panels._flag(theme, "xx") is not None
    theme = replace(theme, flags_directories=lambda: (tmp_path,))
    image = Image.new("RGB", (394, 61), "white")
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        ctx,
        TeamsExtra.FLAGS,
        display="spectra6",
        translator={},
        lang="en",
    )
    assert image.getpixel((4, 25)) != (255, 255, 255)
    monkeypatch.setattr(panels, "render_track_image", Mock(side_effect=ValueError("bad map")))
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        ctx,
        TeamsExtra.TRACK,
        display="spectra6",
        translator={},
        lang="en",
    )
    assert ImageChops.invert(image.crop((5, 46, 389, 59))).getbbox() is not None
    assert "/credits" in panels._credit(
        replace(ctx, track_credit="Track: " + "x" * 200 + ", CC BY 4.0"),
        {},
        ImageDraw.Draw(image),
        "en",
        384,
    )
    assert panels._credit(replace(ctx, track_credit=None), {}, ImageDraw.Draw(image), "en", 384)


def test_cancelled_and_fallback_codes(monkeypatch):
    """Cancelled entries never enter the sequence and unknown tracks use country codes."""
    races = F1Service.get_season_from_static(2026)
    race = races[0].model_copy(deep=True)
    race.Circuit.circuitId = "unknown"
    race.Circuit.Location.country = "Malaysia"
    race.Circuit.Location.lat = None
    cancelled = race.model_copy(update={"round": None})
    monkeypatch.setattr(
        F1Service, "get_season_from_static", lambda year: [cancelled, race] if year == 2026 else []
    )
    context = build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=NOW)
    assert context.total_rounds == 1
    assert context.rounds[0].code == "MAL"
    assert context.track_credit is None


def test_completed_rounds_and_real_colour_flags(ctx, monkeypatch, tmp_path):
    """Render completed timeline digits and upcoming colour flags without strikes."""
    first = replace(ctx.rounds[0], done=True, iso2="xx")
    second = replace(ctx.rounds[1], iso2="xx")
    context = replace(ctx, rounds=(first, second), next_round=2)
    renderer = create_renderer("spectra6", {}, "en")
    Image.new("RGB", (30, 17), "red").save(tmp_path / "xx.bmp")
    theme = replace(renderer.theme, flags_directories=lambda: tmp_path)
    for extra in (TeamsExtra.TIMELINE, TeamsExtra.FLAGS, TeamsExtra.COMBO):
        image = Image.new("RGB", (394, 61), "white")
        panels.draw_teams_extra(
            image,
            ImageDraw.Draw(image),
            theme,
            context,
            extra,
            display="spectra6",
            translator={},
            lang="en",
        )
        assert ImageChops.invert(image).getbbox() is not None
    # A long localized race name exercises clamping without smaller fonts.
    image = Image.new("RGB", (394, 61), "white")
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        replace(context, race_name="Grand Prix " * 100),
        TeamsExtra.COUNTDOWN,
        display="spectra6",
        translator={},
        lang="en",
    )


def test_render_helpers_forward_options(ctx, monkeypatch):
    """Worker adapters forward the same optional panel arguments."""
    from app.routes import images, previews
    from app.services import scheduler_generation

    renderer = Mock()
    renderer.render_teams_drivers.return_value = b"bitmap"
    teams = object()
    for module, invoke in (
        (
            images,
            lambda: images._render_teams_bytes(
                "1bit", "en", teams, extra=TeamsExtra.TRACK, extra_context=ctx
            ),
        ),
        (
            previews,
            lambda: previews._build_teams_preview_png(
                "en", "1bit", teams, False, extra=TeamsExtra.TRACK, extra_context=ctx
            ),
        ),
        (
            scheduler_generation,
            lambda: scheduler_generation._render_teams_variant_bytes(
                "en", "1bit", teams, extra=TeamsExtra.TRACK, extra_context=ctx
            ),
        ),
    ):
        monkeypatch.setattr(module, "create_renderer", Mock(return_value=renderer))
        if module is previews:
            monkeypatch.setattr(module, "bmp_to_png", Mock(return_value=b"png"))
        invoke()
        renderer.render_teams_drivers.assert_called_with(teams, TeamsExtra.TRACK, ctx)


@pytest.mark.parametrize(
    "lang", ["cs", "de", "en", "es", "fr", "it", "ja", "nl", "pl", "pt-BR", "sk", "tr", "zh-CN"]
)
def test_local_start_and_long_race_line(ctx, lang):
    """Keep the translated weekday and local time even when the race needs ellipsis."""
    translator = get_translator(lang)
    start = ctx.start_local.replace(hour=21, minute=7)
    context = replace(ctx, start_local=start, race_name="Long Grand Prix " * 30)
    date_time = panels._date_time(context, translator)
    assert date_time.startswith(translator[f"day_{start.strftime('%a').lower()}"])
    assert date_time.endswith("21:07")
    draw = ImageDraw.Draw(Image.new("1", (394, 61), 1))
    line = panels._race_line(context, translator, draw, lang, 384)
    assert "…" in line
    assert line.endswith(date_time)
    font = panels.load_ui_font(lang, 12, bold=True)
    assert draw.textbbox((0, 0), line, font=font)[2] <= 384


@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
def test_upcoming_flags_and_track_resolution(ctx, display, monkeypatch):
    """Exclude completed flags and request track artwork at twice the fitted size."""
    renderer = create_renderer(display, {}, "en")
    context = replace(
        ctx, next_round=2, rounds=(replace(ctx.rounds[0], done=True), *ctx.rounds[1:])
    )
    texts = []
    original = panels._text

    def record_text(draw, text, *args, **kwargs):
        """Record visible labels while retaining the real drawing path."""
        texts.append(text)
        return original(draw, text, *args, **kwargs)

    monkeypatch.setattr(panels, "_text", record_text)
    image = Image.new("RGB", (394, 61), "white")
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        renderer.theme,
        context,
        TeamsExtra.FLAGS,
        display=display,
        translator={},
        lang="en",
    )
    assert ctx.rounds[0].code not in texts
    assert [r.code for r in context.rounds[1:7]] == texts[1:]
    track = Mock(return_value=Image.new("RGB", (220, 100), "white"))
    monkeypatch.setattr(panels, "render_track_image", track)
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        renderer.theme,
        context,
        TeamsExtra.TRACK,
        display=display,
        translator={},
        lang="en",
    )
    assert track.call_args.kwargs["width"] >= 212
    assert track.call_args.kwargs["height"] >= 100
    assert panels._date_time(context, {}) in texts


@pytest.mark.parametrize("lang", ["cs", "ja", "de"])
@pytest.mark.parametrize("display", ["1bit", "spectra6"])
def test_panel_content_geometry(ctx, lang, display, monkeypatch):
    """Keep digits separated, flags centred, and weather text clear of its icon."""
    renderer = create_renderer(display, get_translator(lang), lang)
    context = replace(
        ctx,
        rounds=tuple(replace(ctx.rounds[0], round=i, done=i < 20) for i in range(1, 25)),
        next_round=20,
        weather=WeatherData(26, 2, 1),
    )
    labels = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        """Capture the actual ink bounds and font used by Pillow."""
        labels.append((text, draw.textbbox(xy, text, font=kwargs["font"]), kwargs["font"]))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    monkeypatch.setattr(panels, "_flag", lambda *args: Image.new("RGB", (30, 17), "red"))

    def render(extra):
        """Render a standard slot and return its measured text and pixels."""
        labels.clear()
        image = Image.new("RGB", (394, 61), "white")
        panels.draw_teams_extra(
            image,
            ImageDraw.Draw(image),
            renderer.theme,
            context,
            extra,
            display=display,
            translator=get_translator(lang),
            lang=lang,
        )
        return image

    render(TeamsExtra.TIMELINE)
    digits = labels[1:25]
    for index, (text, box, font) in enumerate(digits):
        left = 1 + index * 392 // 24
        right = 1 + (index + 1) * 392 // 24
        assert text == str(index + 1)
        assert font.size >= 12
        assert abs((box[0] + box[2]) - (left + right)) <= 1
        if index:
            assert box[0] - digits[index - 1][1][2] >= 2

    image = render(TeamsExtra.FLAGS)
    cell = 386 // 5
    for index, (_, box, font) in enumerate(labels[1:]):
        left = 4 + index * cell
        assert abs((box[0] + box[2]) - (2 * left + cell - 2)) <= 1
        assert box[3] <= 56
        if display == "1bit":
            assert font.size >= 14
            assert abs(box[1] + box[3] - 82) <= 1
        else:
            assert box[1] >= 47
            flag_left = left + (cell - 2 - 30) // 2
            assert image.getpixel((flag_left, 28)) == (255, 0, 0)
            assert image.getpixel((flag_left + 29, 44)) == (255, 0, 0)
    if display == "spectra6":
        assert image.getpixel((5, 26)) == renderer.theme.accent_fill
        assert image.getpixel((6, 27)) == (255, 255, 255)

    render(TeamsExtra.WEATHER)
    assert labels[3][1][0] - labels[1][1][2] >= 6
