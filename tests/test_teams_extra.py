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
    monkeypatch.setattr(WeatherService, "get_cached_value", cached)
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
        assert image.crop((5, 9, 260, 51)).getextrema() == (0, 1)
    assert (
        panels._countdown(context, get_translator(lang), lang)
        == get_translator(lang)["teams_extra_today"]
    )


def test_missing_flags_and_track(ctx, monkeypatch, tmp_path):
    """Missing colour flags fall back to code boxes and track failures retain text."""
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
    assert image.getpixel((5, 25)) != (255, 255, 255)
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
    assert ImageChops.invert(image.crop((5, 9, 260, 51))).getbbox() is not None


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


def test_completed_rounds_and_real_colour_flags(ctx, monkeypatch, tmp_path):
    """Render season progress and upcoming colour flags without strikes."""
    first = replace(ctx.rounds[0], done=True, iso2="xx")
    second = replace(ctx.rounds[1], iso2="xx")
    context = replace(ctx, rounds=(first, second), next_round=2)
    renderer = create_renderer("spectra6", {}, "en")
    Image.new("RGB", (30, 17), "red").save(tmp_path / "xx.bmp")
    theme = replace(renderer.theme, flags_directories=lambda: tmp_path)
    for extra in (TeamsExtra.COUNTDOWN, TeamsExtra.FLAGS):
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
    """Show three upcoming dates and fit clean artwork into a fixed map column."""
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
    assert [r.code for r in context.rounds[1:4]] == texts[::2]
    assert [r.date.strftime("%d.%m.") for r in context.rounds[1:4]] == texts[1::2]
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
    assert track.call_args.kwargs["width"] == 240
    assert track.call_args.kwargs["height"] == 106
    assert track.call_args.kwargs["options"].style == "clean"
    assert panels._date_time(context, {}) in texts


@pytest.mark.parametrize("lang", ["cs", "ja", "de"])
@pytest.mark.parametrize("display", ["1bit", "spectra6"])
@pytest.mark.parametrize("round_count", [22, 23, 24])
def test_panel_content_geometry(ctx, lang, display, monkeypatch, round_count):
    """Keep weather text centred and clear of its icon and attribution."""
    renderer = create_renderer(display, get_translator(lang), lang)
    context = replace(
        ctx,
        rounds=tuple(
            replace(ctx.rounds[0], round=i, done=i < 20) for i in range(1, round_count + 1)
        ),
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

    render(TeamsExtra.WEATHER)
    assert labels[2][1][0] - labels[1][1][2] >= 6
    body = labels[1:5]
    assert abs(body[0][1][0] + body[-1][1][2] - 394) <= 2
    assert labels[-1][0] == get_translator(lang)["teams_extra_weather_credit"]
    assert labels[-1][1][3] <= 22
    assert labels[0][1][2] + 8 <= labels[-1][1][0]
    assert labels[-1][1][2] == 389
    for _, box, _ in body:
        assert 4 <= box[0] < box[2] <= 390
        assert 23 <= box[1] < box[3] <= 58
        assert abs(box[1] + box[3] - 81) <= 2


@pytest.mark.parametrize("display", ["1bit", "bwr", "bwry", "spectra6"])
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_track_text_and_map_geometry(ctx, display, lang, monkeypatch):
    """Keep three text lines clear of the fixed, native-size map column."""
    labels = []
    pasted = []
    original_text = ImageDraw.ImageDraw.text
    original_paste = Image.Image.paste

    def text(draw, xy, value, *args, **kwargs):
        """Measure actual text ink at the final draw position."""
        labels.append((value, draw.textbbox(xy, value, font=kwargs["font"])))
        return original_text(draw, xy, value, *args, **kwargs)

    def paste(image, source, box=None, mask=None):
        """Capture map dimensions independently of panel compositing."""
        if source.size != image.size:
            pasted.append((source.size, box))
        return original_paste(image, source, box, mask)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    monkeypatch.setattr(Image.Image, "paste", paste)
    monkeypatch.setattr(
        panels, "render_track_image", Mock(return_value=Image.new("RGB", (120, 53), "black"))
    )
    image = Image.new("RGB", (394, 61), "white")
    theme = create_renderer(display, {}, lang).theme
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        ctx,
        TeamsExtra.TRACK,
        display=display,
        translator={},
        lang=lang,
    )
    assert [label for label, _ in labels] == [
        ctx.race_name,
        ctx.circuit_name,
        panels._date_time(ctx, {}),
    ]
    assert all(box[0] == 5 for _, box in labels)
    assert all(box[2] <= 262 for _, box in labels)
    assert all(4 <= box[1] < box[3] <= 59 for _, box in labels)
    (map_width, map_height), (x, y) = pasted[0]
    assert (map_width, map_height, x, y) == (120, 53, 270, 4)
    assert x >= max(box[2] for _, box in labels) + 8


@pytest.mark.parametrize("legacy", ["timeline", "combo"])
def test_legacy_panel_values_select_countdown(legacy):
    """Previously copied URLs remain valid without keeping redundant UI variants."""
    assert TeamsExtra(legacy) == TeamsExtra.COUNTDOWN
    assert {extra.value for extra in TeamsExtra} == {
        "none",
        "countdown",
        "flags",
        "track",
        "weather",
    }


@pytest.mark.parametrize("display", ["1bit", "spectra6"])
@pytest.mark.parametrize("round_count", [10, 21, 23, 24])
def test_countdown_progress_distinguishes_three_states(ctx, display, round_count):
    """Past, next and future rounds have distinct ink even without colour."""
    rounds = tuple(replace(ctx.rounds[0], round=i, done=i < 2) for i in range(1, round_count + 1))
    context = replace(ctx, rounds=rounds, next_round=2, total_rounds=round_count)
    theme = create_renderer(display, {}, "en").theme
    image = Image.new("1" if display == "1bit" else "RGB", (394, 61), "white")
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        context,
        TeamsExtra.COUNTDOWN,
        display=display,
        translator={},
        lang="en",
    )
    image = image.convert("RGB")
    past_x = 121 + 2
    next_x = 121 + 268 // round_count + 2
    future_x = 121 + 2 * 268 // round_count + 2
    assert image.getpixel((past_x, 55)) == (0, 0, 0)
    assert image.getpixel((next_x, 52)) == ((0, 0, 0) if display == "1bit" else theme.accent_fill)
    assert image.getpixel((future_x, 55)) == (255, 255, 255)
    assert image.getpixel((past_x, 51)) == (255, 255, 255)


@pytest.mark.parametrize("remaining", [1, 2, 3])
def test_upcoming_cells_keep_size_and_dates_at_season_end(ctx, remaining, monkeypatch):
    """Late-season cards stay compact and show a date for every remaining race."""
    context = replace(ctx, rounds=ctx.rounds[-remaining:], next_round=ctx.rounds[-remaining].round)
    image = Image.new("1", (394, 61), 1)
    theme = create_renderer("1bit", {}, "en").theme
    texts = []
    original = panels._text

    def record(draw, text, *args, **kwargs):
        """Retain actual drawing while recording the labels for each card."""
        texts.append(text)
        return original(draw, text, *args, **kwargs)

    monkeypatch.setattr(panels, "_text", record)
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        theme,
        context,
        TeamsExtra.FLAGS,
        display="1bit",
        translator={},
        lang="en",
    )
    assert texts == [
        value for race in context.rounds for value in (race.code, race.date.strftime("%d.%m."))
    ]
    image = image.convert("RGB")
    if remaining == 1:
        assert image.getpixel((10, 30)) == (255, 255, 255)
        assert image.getpixel((380, 30)) == (255, 255, 255)


@pytest.mark.parametrize("cached_value", [None, WeatherData(23, 1, 10)])
def test_public_weather_cache_accessor(monkeypatch, cached_value):
    """The public cache-only accessor preserves the existing lookup result and key."""
    cached = Mock(return_value=cached_value)
    monkeypatch.setattr(WeatherService, "_get_cached", cached)
    assert WeatherService.get_cached_value("race-key") is cached_value
    cached.assert_called_once_with("race-key")


@pytest.mark.parametrize(
    "lang", ["cs", "de", "en", "es", "fr", "it", "ja", "nl", "pl", "pt-BR", "sk", "tr", "zh-CN"]
)
@pytest.mark.parametrize("extra", [TeamsExtra.COUNTDOWN, TeamsExtra.FLAGS, TeamsExtra.TRACK])
@pytest.mark.parametrize("display", ["1bit", "spectra6"])
def test_redesigned_text_stays_inside_panel(ctx, lang, extra, display, monkeypatch):
    """Long names and CJK glyphs stay clear of borders, adjacent lines and maps."""
    labels = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        """Capture ink bounds after fitting, shortening and optical positioning."""
        labels.append((text, draw.textbbox(xy, text, font=kwargs["font"])))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    monkeypatch.setattr(panels, "render_track_image", Mock(return_value=None))
    context = replace(
        ctx,
        race_name=(
            "非常に長いグランプリ名称 " if lang in ("ja", "zh-CN") else "Very long Grand Prix name "
        )
        * 5,
        circuit_name="Autódromo Hermanos Rodríguez " * 4,
    )
    renderer = create_renderer(display, get_translator(lang), lang)
    image = Image.new("1" if display == "1bit" else "RGB", (394, 61), "white")
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
    for _, box in labels:
        assert 3 <= box[0] < box[2] <= 390
        assert 4 <= box[1] < box[3] <= 59
    if extra == TeamsExtra.COUNTDOWN:
        assert labels[1][1][2] <= 389
        assert labels[1][1][3] < labels[2][1][1]
        assert labels[2][0].endswith(context.start_local.strftime("%H:%M"))
        assert labels[2][1][3] <= 49
    elif extra == TeamsExtra.TRACK:
        assert all(box[2] <= 262 for _, box in labels)
        assert all(a[1][3] < b[1][1] for a, b in zip(labels, labels[1:]))
    else:
        assert len(labels) == 6
        assert all(labels[i][1][3] < labels[i + 1][1][1] for i in (0, 2, 4))


@pytest.mark.parametrize("display", ["1bit", "spectra6"])
def test_real_track_artwork_is_visible_in_fixed_map_column(ctx, display):
    """Catch fitting failures that would otherwise silently leave the map blank."""
    renderer = create_renderer(display, {}, "en")
    image = Image.new("1" if display == "1bit" else "RGB", (394, 61), "white")
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        renderer.theme,
        ctx,
        TeamsExtra.TRACK,
        display=display,
        translator={},
        lang="en",
    )
    assert ImageChops.invert(image.convert("RGB").crop((270, 4, 390, 57))).getbbox() is not None


@pytest.mark.parametrize("days_left", [0, 1, 2, 123])
@pytest.mark.parametrize("lang", ["cs", "fr", "ja", "zh-CN"])
def test_countdown_labels_fit_compact_block(ctx, days_left, lang, monkeypatch):
    """Today, plural forms and three-digit counts fit without crossing the split."""
    boxes = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        """Record final glyph bounds, including long translated today labels."""
        boxes.append(draw.textbbox(xy, text, font=kwargs["font"]))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    image = Image.new("1", (394, 61), 1)
    panels.draw_teams_extra(
        image,
        ImageDraw.Draw(image),
        create_renderer("1bit", {}, lang).theme,
        replace(ctx, days_left=days_left),
        TeamsExtra.COUNTDOWN,
        display="1bit",
        translator=get_translator(lang),
        lang=lang,
    )
    left, top, right, bottom = boxes[0]
    assert 9 <= left < right <= 107
    assert 7 <= top < bottom <= 54
    assert abs(left + right - 116) <= 1


def test_upcoming_dates_follow_display_timezone(monkeypatch):
    """A UTC evening race after local midnight uses the same date in A and C."""
    race = F1Service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.date, race.time = "2026-03-08", "23:00:00Z"
    monkeypatch.setattr(
        F1Service, "get_season_from_static", lambda year: [race] if year == 2026 else []
    )
    context = build_teams_extra_context(TeamsExtra.FLAGS, 2026, now=NOW)
    assert context.rounds[0].date == context.start_local.date()
    assert context.rounds[0].date.day == 9


@pytest.mark.parametrize("now_hour, expected_days", [(12, (3, 2)), (23, (2, 2))])
def test_context_uses_requested_timezone(monkeypatch, now_hour, expected_days):
    """Local race dates and today's date both follow the requested IANA zone."""
    race = F1Service.get_season_from_static(2026)[0].model_copy(deep=True)
    race.date, race.time = "2026-03-08", "23:00:00Z"
    monkeypatch.setattr(
        F1Service, "get_season_from_static", lambda year: [race] if year == 2026 else []
    )
    now = datetime(2026, 3, 6, now_hour, tzinfo=UTC)
    prague, la = (
        build_teams_extra_context(TeamsExtra.COUNTDOWN, 2026, now=now, timezone_name=zone)
        for zone in ("Europe/Prague", "America/Los_Angeles")
    )
    assert prague.timezone_name == "Europe/Prague"
    assert la.timezone_name == "America/Los_Angeles"
    assert prague.start_local.isoformat() == "2026-03-09T00:00:00+01:00"
    assert la.start_local.isoformat() == "2026-03-08T16:00:00-07:00"
    assert prague.rounds[0].date == prague.start_local.date()
    assert la.rounds[0].date == la.start_local.date()
    assert (prague.days_left, la.days_left) == expected_days


@pytest.mark.parametrize("extra", list(TeamsExtra))
@pytest.mark.parametrize("weather", [None, WeatherData(20, 1, 10)])
def test_timezone_cache_tokens_preserve_existing_keys(ctx, extra, weather):
    """Only drawn non-default panels add a safe timezone suffix to existing keys."""
    from app.config import config

    default = replace(ctx, weather=weather)
    explicit = replace(default, timezone_name=config.DEFAULT_TIMEZONE)
    shifted = replace(default, timezone_name="America/Los_Angeles")
    token = teams_extra_cache_token(extra, default)
    key = get_teams_image_key("en", 2026, extra=extra, extra_context=default)
    assert teams_extra_cache_token(extra, explicit) == token
    assert get_teams_image_key("en", 2026, extra=extra, extra_context=explicit) == key
    expected = "" if extra == TeamsExtra.NONE else f"_x{extra}_20260306_r1"
    if extra == TeamsExtra.WEATHER and weather is not None:
        from hashlib import sha256

        expected += "_" + sha256(repr((20, 1, weather.precip_display)).encode()).hexdigest()[:10]
    assert token == expected
    assert key == f"teams_2026_en{expected}"
    drawn = extra != TeamsExtra.NONE and (extra != TeamsExtra.WEATHER or weather is not None)
    suffix = "_America_Los_Angeles" if drawn else ""
    assert teams_extra_cache_token(extra, shifted) == token + suffix
    assert get_teams_image_key("en", 2026, extra=extra, extra_context=shifted) == key + suffix


def test_empty_weather_timezone_keeps_default_day_token(monkeypatch):
    """An undrawn weather panel keeps its legacy key even across local midnight."""
    monkeypatch.setattr(WeatherService, "get_cached_value", lambda key: None)
    monkeypatch.setattr(extras, "get_cached_circuit_weather", lambda circuit: None)
    now = datetime(2026, 3, 6, 23, 30, tzinfo=UTC)
    default = build_teams_extra_context(TeamsExtra.WEATHER, 2026, now=now)
    shifted = build_teams_extra_context(
        TeamsExtra.WEATHER, 2026, now=now, timezone_name="America/Los_Angeles"
    )
    assert default.weather is shifted.weather is None
    assert teams_extra_cache_token(TeamsExtra.WEATHER, default) == "_xweather_20260307_r1"
    assert teams_extra_cache_token(TeamsExtra.WEATHER, shifted) == "_xweather_20260307_r1"
    # Older manually constructed snapshots have no default-zone date stored.
    assert (
        teams_extra_cache_token(TeamsExtra.WEATHER, replace(default, default_today=None))
        == "_xweather_20260307_r1"
    )


def test_context_normalizes_timezone_aliases(monkeypatch):
    """Legacy IANA aliases use the same context and keys as their canonical names."""
    alias = build_teams_extra_context(
        TeamsExtra.TRACK, 2026, now=NOW, timezone_name="Asia/Calcutta"
    )
    canonical = build_teams_extra_context(
        TeamsExtra.TRACK, 2026, now=NOW, timezone_name="Asia/Kolkata"
    )
    assert alias == canonical
    assert alias.timezone_name == "Asia/Kolkata"
    monkeypatch.setattr(extras.config, "DEFAULT_TIMEZONE", "Asia/Calcutta")
    assert teams_extra_cache_token(TeamsExtra.TRACK, alias) == "_xtrack_20260306_r1"
