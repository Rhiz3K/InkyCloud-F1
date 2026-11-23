"""Weekend-phase calendar content: ``weekend_mode=auto`` (#185 F5)."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageChops, ImageDraw

import app.services.renderer_base as renderer_base_module
from app.config import LANGUAGE_CODES
from app.models import (
    ConstructorInfo,
    DriverInfo,
    HistoricalData,
    QualifyingResultEntry,
    RaceResultEntry,
)
from app.routes import images, previews
from app.routes.deps import get_f1_service
from app.services import bmp_artifacts, renderer_calendar, renderer_results
from app.services import weekend_results as wr
from app.services.database import DATABASE_SCHEMA_VERSION, Database
from app.services.f1_service import NEXT_RACE_GRACE, F1Service
from app.services.i18n import get_translator
from app.services.renderers import create_renderer
from app.services.weather_service import WeatherData
from app.services.weekend_phase import (
    SESSION_DURATIONS,
    WEEKEND_LEAD,
    WeekendPhase,
    phase_cache_token,
    session_duration,
    weekend_changes,
    weekend_phase,
)
from app.state import get_bmp_cache, get_bmp_variant_cache
from app.utils import refresh_hint as rh
from app.utils.refresh_hint import CountdownMode

PRAGUE = ZoneInfo("Europe/Prague")
TOKYO = ZoneInfo("Asia/Tokyo")
LOS_ANGELES = ZoneInfo("America/Los_Angeles")
DISPLAYS = ("1bit", "bwr", "bwry", "spectra6")

# Qatar 2026 (bundled, normal weekend), UTC.
QA_FP1 = datetime(2026, 11, 27, 13, 30, tzinfo=UTC)
QA_FP2 = datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
QA_FP3 = datetime(2026, 11, 28, 14, 30, tzinfo=UTC)
QA_Q = datetime(2026, 11, 28, 18, 0, tzinfo=UTC)
QA_RACE = datetime(2026, 11, 29, 16, 0, tzinfo=UTC)
# Singapore 2026 (bundled, sprint weekend), UTC.
SG_FP1 = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)
SG_SQ = datetime(2026, 10, 9, 12, 30, tzinfo=UTC)
SG_SPRINT = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
SG_Q = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)
SG_RACE = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
# Friday afternoon in Qatar, between FP1 and FP2.
FRIDAY = QA_FP2 - timedelta(hours=2)
MINUTE = timedelta(minutes=1)


def _races(tz: str = "Europe/Prague") -> list[dict]:
    return F1Service(timezone_name=tz).get_all_races_from_static(2026)


def _race_named(name: str, tz: str) -> dict:
    race = next((race for race in _races(tz) if race["race_name"] == name), None)
    assert race is not None, name
    return race


def _qatar(tz: str = "Europe/Prague") -> dict:
    return _race_named("Qatar Grand Prix", tz)


def _singapore(tz: str = "Europe/Prague") -> dict:
    return _race_named("Singapore Grand Prix", tz)


def _phase(race, now, zone=PRAGUE):
    return weekend_phase(race, now=now, zone=zone)


def _podium(kind: str, names=("Norris", "Piastri", "Verstappen")) -> list[dict]:
    return [
        {
            "position": str(index),
            "Driver": {"code": name[:3].upper(), "familyName": name},
            "Constructor": {"name": "Team"},
            **({"Q3": f"1:2{index}.000"} if kind == "qualifying" else {}),
            **({"Time": {"time": f"+{index}.000"}} if kind == "race" else {}),
        }
        for index, name in enumerate(names, start=1)
    ]


def _payload(kind: str, rows, season="2026", round_num="22") -> dict:
    key = "QualifyingResults" if kind == "qualifying" else "Results"
    race = {"season": season, "round": round_num, key: rows}
    return {"MRData": {"RaceTable": {"Races": [race]}}}


def _stored(kind: str, names=("Norris", "Piastri", "Verstappen")) -> tuple[dict, ...]:
    return tuple(
        {"pos": index, "code": name[:3].upper(), "name": name, "team": "Team", "time": "1:20.0"}
        for index, name in enumerate(names, start=1)
    )


@pytest.fixture(autouse=True)
def _clean_weekend_results():
    wr._reset_for_tests()
    yield
    wr._reset_for_tests()


class _Clock:
    now = FRIDAY


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.replace(tzinfo=None) if tz is None else _Clock.now.astimezone(tz)


@pytest.fixture
def frozen(monkeypatch):
    """Freeze the renderer, the routes and the preview clock; tests move _Clock.now."""
    _Clock.now = FRIDAY
    monkeypatch.setattr(renderer_base_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(images, "_utc_now", lambda: _Clock.now)
    monkeypatch.setattr(previews, "datetime", _FixedDatetime)
    return _Clock


# --- Phase detection ------------------------------------------------------------------------


def test_outside_the_weekend_window_there_is_no_phase():
    race = _qatar()
    window_start = QA_FP1 - WEEKEND_LEAD
    window_end = QA_RACE + NEXT_RACE_GRACE
    assert _phase(race, window_start - MINUTE) is None
    assert _phase(race, window_start) == WeekendPhase(
        current="FP1", countdown_session="FP1", countdown_start=QA_FP1
    )
    assert _phase(race, window_end - MINUTE) == WeekendPhase(
        past=frozenset({"FP1", "FP2", "FP3", "Qualifying", "Race"})
    )
    assert _phase(race, window_end) is None
    assert _phase(None, FRIDAY) is None
    assert _phase({**race, "is_cancelled": True}, FRIDAY) is None


def test_friday_marks_practice_by_start_plus_conservative_duration():
    race = _qatar()
    live = _phase(race, QA_FP1 + SESSION_DURATIONS["practice"] - MINUTE)
    assert live == WeekendPhase(
        current="FP1", live=True, countdown_session="FP1", countdown_start=QA_FP1
    )
    between = _phase(race, QA_FP1 + SESSION_DURATIONS["practice"])
    assert between == WeekendPhase(
        past=frozenset({"FP1"}), current="FP2", countdown_session="FP2", countdown_start=QA_FP2
    )
    assert live.token != between.token


def test_saturday_and_sunday_of_a_normal_weekend():
    race = _qatar()
    qualifying = _phase(race, QA_Q + timedelta(minutes=30))
    assert qualifying.current == "Qualifying" and qualifying.live
    assert qualifying.past == {"FP1", "FP2", "FP3"}
    evening = _phase(race, QA_Q + SESSION_DURATIONS["qualifying"])
    # The race itself keeps the default race countdown box.
    assert evening == WeekendPhase(
        past=frozenset({"FP1", "FP2", "FP3", "Qualifying"}), current="Race"
    )
    race_live = _phase(race, QA_RACE + timedelta(hours=1))
    assert race_live.current == "Race" and race_live.live and race_live.countdown_session is None
    assert _phase(race, QA_RACE + rh.RACE_LIVE_WINDOW).current is None


def test_sprint_weekend_saturday_counts_down_to_qualifying_then_the_race():
    race = _singapore()
    friday_evening = _phase(race, SG_SQ + SESSION_DURATIONS["sprintqualifying"])
    assert friday_evening.past == {"FP1", "SprintQualifying"}
    assert friday_evening.countdown_session == "Sprint"
    sprint_live = _phase(race, SG_SPRINT + timedelta(minutes=20))
    assert sprint_live.live and sprint_live.current == "Sprint"
    before_q = _phase(race, SG_Q - timedelta(hours=2))
    assert before_q == WeekendPhase(
        past=frozenset({"FP1", "SprintQualifying", "Sprint"}),
        current="Qualifying",
        countdown_session="Qualifying",
        countdown_start=SG_Q,
    )
    saturday_evening = _phase(race, datetime(2026, 10, 10, 18, 30, tzinfo=UTC))
    assert saturday_evening == WeekendPhase(
        past=frozenset({"FP1", "SprintQualifying", "Sprint", "Qualifying"}), current="Race"
    )


@pytest.mark.parametrize("tz", ["Europe/Prague", "Asia/Tokyo", "America/Los_Angeles"])
def test_timed_sessions_do_not_depend_on_the_display_timezone(tz):
    race = _singapore(tz)
    for now in (SG_FP1 - WEEKEND_LEAD, SG_SPRINT + MINUTE, SG_Q + timedelta(hours=2)):
        assert _phase(race, now, ZoneInfo(tz)) == _phase(_singapore(), now)


def test_date_only_sessions_end_at_local_midnight_and_are_never_counted_down():
    race = {
        "season": "2026",
        "round": "22",
        "schedule": [
            {"name": "FP1", "datetime": "2026-11-27"},
            {"name": "Qualifying", "datetime": "2026-11-28T18:00:00+00:00"},
            {"name": "Race", "datetime": "2026-11-29"},
        ],
    }
    # 2026-11-27 15:30 UTC is already Saturday in Tokyo, still Friday in Los Angeles.
    now = datetime(2026, 11, 27, 15, 30, tzinfo=UTC)
    assert _phase(race, now, TOKYO) == WeekendPhase(
        past=frozenset({"FP1"}),
        current="Qualifying",
        countdown_session="Qualifying",
        countdown_start=QA_Q,
    )
    # A TBA session cannot be counted down to, and the later timed one is not skipped to.
    assert _phase(race, now, LOS_ANGELES) == WeekendPhase()
    after_q = datetime(2026, 11, 28, 20, 0, tzinfo=UTC)
    assert _phase(race, after_q, LOS_ANGELES) == WeekendPhase(past=frozenset({"FP1", "Qualifying"}))


def test_malformed_entries_are_ignored_and_unknown_kinds_use_the_default_duration():
    race = _qatar()
    race["schedule"] = [
        *race["schedule"],
        {"name": "Parade", "datetime": "not-a-date"},
        {"name": "", "datetime": "2026-11-28T10:00:00+00:00"},
        {"name": "Shakedown", "datetime": None},
    ]
    assert _phase(race, FRIDAY) == _phase(_qatar(), FRIDAY)
    assert session_duration("Shakedown") == timedelta(minutes=75)
    assert session_duration("Sprint Shootout") == SESSION_DURATIONS["sprintqualifying"]
    assert phase_cache_token(None) == "off"
    assert phase_cache_token(WeekendPhase()) == "0:-:next"


def test_weekend_changes_cover_window_edges_session_boundaries_and_countdown_ticks():
    race = _qatar()
    window = (QA_FP1 - WEEKEND_LEAD, QA_RACE + NEXT_RACE_GRACE)
    monday = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    assert weekend_changes(race, now=monday, zone=PRAGUE, countdown=CountdownMode.HOURS) == list(
        window
    )
    assert weekend_changes(race, now=window[1], zone=PRAGUE, countdown=CountdownMode.HOURS) == []
    assert weekend_changes(None, now=FRIDAY, zone=PRAGUE, countdown=CountdownMode.HOURS) == []

    now = QA_FP2 - timedelta(hours=1, minutes=20)
    hours = weekend_changes(race, now=now, zone=PRAGUE, countdown=CountdownMode.HOURS)
    assert min(hours) == now + timedelta(minutes=20)  # the next hour tick of "FP2: 1 h"
    assert QA_FP2 in hours and QA_FP2 + session_duration("FP2") in hours
    assert QA_FP1 not in hours and all(change > now for change in hours)
    days = weekend_changes(race, now=now, zone=PRAGUE, countdown=CountdownMode.DAYS)
    assert rh.next_local_midnight(now, PRAGUE) in days
    off = weekend_changes(race, now=now, zone=PRAGUE, countdown=CountdownMode.OFF)
    assert min(off) == QA_FP2


def test_unfinished_date_only_session_wakes_the_display_at_its_local_midnight():
    # FP1 has no published hour; Saturday qualifying and the race are timed.
    race = {
        "season": "2026",
        "round": "22",
        "schedule": [
            {"name": "FP1", "datetime": "2026-11-27"},
            {"name": "Qualifying", "datetime": "2026-11-28T18:00:00+00:00"},
            {"name": "Race", "datetime": "2026-11-29T16:00:00+00:00"},
        ],
    }
    friday_night = datetime(2026, 11, 27, 21, 0, tzinfo=UTC)  # 22:00 in Prague
    saturday = rh.local_midnight(date(2026, 11, 28), PRAGUE)
    assert _phase(race, friday_night) == WeekendPhase()
    changes = weekend_changes(race, now=friday_night, zone=PRAGUE, countdown=CountdownMode.OFF)
    assert saturday in changes
    assert _phase(race, saturday) == WeekendPhase(
        past=frozenset({"FP1"}),
        current="Qualifying",
        countdown_session="Qualifying",
        countdown_start=QA_Q,
    )

    # With countdown=off and no weather the default hint alone would sleep until qualifying.
    hint = rh.calendar_refresh_hint(
        race,
        now=friday_night,
        timezone_name="Europe/Prague",
        countdown=CountdownMode.OFF,
        weather_type="off",
        weather_enabled=False,
        selection_grace=NEXT_RACE_GRACE,
    ).with_changes(changes)
    assert hint.next_change == saturday
    assert hint.seconds() == int((saturday - friday_night).total_seconds()) + rh.SETTLE_SECONDS


# --- Rendering ------------------------------------------------------------------------------


def _render(race, phase=None, *, display="1bit", lang="en", historical=None, weather=None):
    renderer = create_renderer(display, get_translator(lang), lang)
    kwargs = {} if phase is None else {"weekend": phase}
    weather_type = "race_day" if weather else "off"
    return renderer.render_calendar(race, historical, weather, weather_type, **kwargs)


def _image(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data)).convert("RGB")


def test_default_render_is_byte_identical(frozen):
    race = _qatar()
    historical = F1Service.get_historical_from_static("losail")
    assert _render(race, historical=historical) == bmp_artifacts.render_calendar_bytes(
        "1bit", "en", race, historical, None, "off", weekend=None
    )


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("lang", ["en", "de", "ja"])
@pytest.mark.parametrize(
    "race_name, now",
    [
        ("singapore", SG_Q - timedelta(hours=2)),  # sprint Saturday, Q countdown
        ("singapore", datetime(2026, 10, 10, 18, 30, tzinfo=UTC)),  # sprint Saturday evening
        ("qatar", QA_Q + timedelta(minutes=30)),  # qualifying running
    ],
)
def test_weekend_marks_stay_in_the_schedule_column_and_its_gap(
    frozen, display, lang, race_name, now
):
    frozen.now = now
    race = _singapore() if race_name == "singapore" else _qatar()
    phase = _phase(race, now)
    weather = WeatherData(27.4, 1, 10)
    plain = _image(_render(race, display=display, lang=lang, weather=weather))
    marked = _image(_render(race, phase, display=display, lang=lang, weather=weather))
    left, top, right, bottom = ImageChops.difference(plain, marked).getbbox()
    # Nothing moves on the track map, the header or the results footer.
    assert left >= 500 and top >= 90 and bottom <= 385 and right <= 800
    gap = ImageChops.difference(plain.crop((0, 0, 510, 480)), marked.crop((0, 0, 510, 480)))
    gap_left, _gap_top, gap_right, _gap_bottom = gap.getbbox()
    # The marks sit in the 10 px gap between the map (x < 500) and the date column (x 510).
    assert gap_left >= 500 and gap_right <= 510


def _box_texts(race, phase, monkeypatch, *, lang="en", weather_type="race_day", mode=None):
    renderer = create_renderer("1bit", get_translator(lang), lang)
    draw = ImageDraw.Draw(Image.new("1", (800, 480), 1))
    texts: list[str] = []
    original = draw.text

    def spy(xy, text, *args, **kwargs):
        texts.append(text)
        return original(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", spy)
    weather = WeatherData(27.4, 1, 10) if weather_type != "off" else None
    renderer._draw_countdown_box(
        draw,
        race,
        220,
        weather,
        weather_type,
        countdown_mode=mode or CountdownMode.HOURS,
        weekend=phase,
    )
    return texts


@pytest.mark.parametrize(
    "lang, expected",
    [("en", "Q: 2 h"), ("de", "Q: 2 Std"), ("ja", "予選: 2 時間"), ("cs", "K: 2 h")],
)
def test_next_session_countdown_labels(frozen, monkeypatch, lang, expected):
    frozen.now = SG_Q - timedelta(hours=2, minutes=10)
    race = _singapore()
    assert expected in _box_texts(race, _phase(race, frozen.now), monkeypatch, lang=lang)


def test_session_countdown_follows_the_countdown_mode(frozen, monkeypatch):
    race = _singapore()
    frozen.now = SG_SPRINT - timedelta(hours=19)
    phase = _phase(race, frozen.now)
    assert phase.countdown_session == "Sprint"
    # The leading "0 d" is left out of a session countdown.
    assert "SPRINT: 19 h" in _box_texts(race, phase, monkeypatch)
    assert "SPRINT: 19 hours" in _box_texts(race, phase, monkeypatch, weather_type="off")
    assert "SPRINT: 1 d" in _box_texts(race, phase, monkeypatch, mode=CountdownMode.DAYS)
    assert "SPRINT: Sat 11:00" in _box_texts(race, phase, monkeypatch, mode=CountdownMode.OFF)
    frozen.now = SG_SPRINT + timedelta(minutes=10)
    live = _box_texts(race, _phase(race, frozen.now), monkeypatch)
    assert "SPRINT: LIVE" in live
    assert "🏁" not in live


@pytest.mark.parametrize("lang", LANGUAGE_CODES)
def test_session_countdown_fits_next_to_the_weather_in_every_locale(frozen, monkeypatch, lang):
    race = _singapore()
    moments = (
        SG_FP1 - timedelta(hours=9),
        SG_SQ - timedelta(hours=2),
        SG_SQ + timedelta(minutes=10),
        SG_SPRINT - timedelta(hours=13),
        SG_SPRINT + timedelta(minutes=10),
        SG_Q - timedelta(hours=1),
    )
    for now in moments:
        frozen.now = now
        phase = _phase(race, now)
        for mode in CountdownMode:
            texts = _box_texts(race, phase, monkeypatch, lang=lang, mode=mode)
            assert not any("..." in text for text in texts), (lang, now, mode, texts)


def test_race_target_keeps_the_default_box(frozen, monkeypatch):
    race = _qatar()
    frozen.now = QA_Q + timedelta(hours=3)
    phase = _phase(race, frozen.now)
    assert _box_texts(race, phase, monkeypatch) == _box_texts(race, None, monkeypatch)


def test_session_countdown_replaces_a_date_only_race_day_placeholder(frozen, monkeypatch):
    race = {
        "season": "2026",
        "round": "22",
        "timezone": "Europe/Prague",
        "schedule": [
            {"name": "Qualifying", "datetime": "2026-11-29T08:00:00+00:00"},
            {"name": "Race", "datetime": "2026-11-29"},
        ],
    }
    frozen.now = datetime(2026, 11, 29, 5, 0, tzinfo=UTC)
    monkeypatch.setattr(renderer_calendar, "today_in_timezone", lambda _tz: frozen.now.date())
    phase = _phase(race, frozen.now)
    assert "–" in _box_texts(race, None, monkeypatch, weather_type="off")
    assert "Q: 3 h" in _box_texts(race, phase, monkeypatch)


def test_cancelled_race_keeps_its_status(frozen, monkeypatch):
    race = {**_qatar(), "is_cancelled": True}
    phase = WeekendPhase(current="FP2", countdown_session="FP2", countdown_start=QA_FP2)
    assert "CANCELLED" in _box_texts(race, phase, monkeypatch)


# --- Per-column season labels ---------------------------------------------------------------


def _entry(position, name, *, qualifying):
    driver = DriverInfo(code=name[:3].upper(), given_name="", family_name=name)
    team = ConstructorInfo(name="Team")
    if qualifying:
        return QualifyingResultEntry(position=position, driver=driver, constructor=team)
    return RaceResultEntry(position=position, driver=driver, constructor=team)


def _footer(historical):
    """Return the header season, column titles and whether the new-track badge is drawn."""
    calls = {"titles": [], "season": None, "badge": False, "rows": []}

    def header(_draw, _image, _y, season, _country):
        calls["season"] = season
        return 400

    def column(_draw, _x, _top, title, results, is_qualifying):
        calls["titles"].append(title)
        calls["rows"].append(results)

    def badge(_draw, _y):
        calls["badge"] = True

    renderer_results.draw_results_section(
        MagicMock(),
        MagicMock(),
        canvas_width=800,
        separator_fill=0,
        separator_width=2,
        y_start=385,
        race_data={"circuit": {"country": "Qatar"}},
        historical_data=historical,
        results_col1_x=109,
        results_col2_x=455,
        qualifying_title="QUALIFYING",
        race_title="RACE",
        draw_new_track_message_fn=badge,
        draw_results_header_fn=header,
        draw_results_column_fn=column,
    )
    return calls


def _history(season=2025, new_track=False):
    if new_track:
        return HistoricalData(is_new_track=True)
    return HistoricalData(
        season=season,
        qualifying_results=[_entry(1, "Old", qualifying=True)],
        race_results=[_entry(1, "Old", qualifying=False)],
    )


def test_footer_columns_carry_their_own_season():
    default = _footer(_history())
    assert default["season"] == 2025 and default["titles"] == ["QUALIFYING", "RACE"]
    assert _footer(None)["season"] == "" and _footer(None)["badge"]

    race = {"season": "2026", "round": "22"}
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), FRIDAY)
    saturday = _footer(wr.apply_weekend_results(_history(), race))
    assert saturday["season"] == ""
    assert saturday["titles"] == ["2026 QUALIFYING", "2025 RACE"]
    assert saturday["rows"][0][0].driver.family_name == "Norris"
    assert saturday["rows"][1][0].driver.family_name == "Old"

    wr._store((2026, 22, wr.RACE), _stored("race", ("Piastri", "Norris", "Russell")), FRIDAY)
    sunday = _footer(wr.apply_weekend_results(_history(), race))
    assert sunday["season"] == 2026 and sunday["titles"] == ["QUALIFYING", "RACE"]
    assert sunday["rows"][1][0].driver.family_name == "Piastri"


def test_new_track_footer_with_this_weekends_podiums():
    race = {"season": "2026", "round": "22"}
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), FRIDAY)
    saturday = _footer(wr.apply_weekend_results(_history(new_track=True), race))
    assert saturday["titles"] == ["2026 QUALIFYING", "RACE"]
    assert saturday["season"] == "" and saturday["badge"]
    assert saturday["rows"][1] == [None, None, None]
    wr._store((2026, 22, wr.RACE), _stored("race"), FRIDAY)
    sunday = _footer(wr.apply_weekend_results(None, race))
    assert sunday["season"] == 2026 and sunday["titles"] == ["QUALIFYING", "RACE"]
    assert not sunday["badge"]


def test_apply_weekend_results_without_podiums_returns_the_input():
    history = _history()
    assert wr.apply_weekend_results(history, {"season": "2026", "round": "22"}) is history
    assert wr.apply_weekend_results(history, {"season": "2026"}) is history
    assert wr.apply_weekend_results(history, None) is history


@pytest.mark.parametrize("display", DISPLAYS)
@pytest.mark.parametrize("lang", ["en", "de", "ja"])
def test_per_column_titles_fit_their_columns(frozen, display, lang):
    race = _qatar()
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), FRIDAY)
    history = wr.apply_weekend_results(F1Service.get_historical_from_static("losail"), race)
    plain = _image(_render(race, display=display, lang=lang))
    split = _image(_render(race, display=display, lang=lang, historical=history))
    left, top, right, bottom = ImageChops.difference(plain, split).getbbox()
    assert top >= 385 and bottom <= 480 and right <= 800 and left >= 0
    # The qualifying column title still ends before the race column starts.
    title_band = ImageChops.difference(
        plain.crop((109, 386, 455, 405)), split.crop((109, 386, 455, 405))
    ).getbbox()
    assert title_band is not None and title_band[2] < 455 - 109 - 5


# --- Scheduler window logic and Jolpica fetches ---------------------------------------------


def test_due_fetches_only_inside_the_windows():
    races = [_qatar()]
    q_from = QA_Q + wr.FETCH_DELAYS[wr.QUALIFYING]
    assert wr.due_fetches(races, q_from - MINUTE) == []
    assert wr.due_fetches(races, q_from) == [(2026, 22, wr.QUALIFYING)]
    assert wr.due_fetches(races, QA_Q + wr.FETCH_CUTOFF) == []
    race_from = QA_RACE + wr.FETCH_DELAYS[wr.RACE]
    assert wr.due_fetches(races, race_from) == [(2026, 22, wr.RACE)]
    assert wr.due_fetches(races, FRIDAY) == []
    assert wr.due_fetches([{**_qatar(), "is_cancelled": True}], q_from) == []
    assert wr.due_fetches([{**_qatar(), "round": None}], q_from) == []
    tba = {**_qatar(), "schedule": [{"name": "Qualifying", "datetime": "2026-11-28"}]}
    assert wr.due_fetches([tba], q_from) == []
    broken = {**_qatar(), "schedule": [{"name": "Race", "datetime": "garbage"}]}
    assert wr.fetch_windows(broken) == []


def test_negative_cache_and_complete_podiums_stop_fetching():
    races = [_qatar()]
    now = QA_Q + timedelta(hours=2)
    wr._store((2026, 22, wr.QUALIFYING), None, now)
    assert wr.due_fetches(races, now + wr.RETRY_INTERVAL - MINUTE) == []
    assert wr.due_fetches(races, now + wr.RETRY_INTERVAL) == [(2026, 22, wr.QUALIFYING)]
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), now)
    assert wr.due_fetches(races, now + timedelta(hours=5)) == []
    # A later miss never replaces a stored podium.
    wr._store((2026, 22, wr.QUALIFYING), None, now + timedelta(hours=1))
    assert wr.stored_podium(2026, 22, wr.QUALIFYING) == _stored("qualifying")


def test_results_pending_and_token():
    race = _qatar()
    in_window = QA_Q + timedelta(hours=2)
    assert wr.results_pending(race, in_window)
    assert not wr.results_pending(race, FRIDAY)
    assert not wr.results_pending(None, in_window)
    assert not wr.results_pending({**race, "is_cancelled": True}, in_window)
    assert wr.results_token(race) == "none"
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), in_window)
    token = wr.results_token(race)
    assert token != "none" and not wr.results_pending(race, in_window)
    wr._store((2026, 22, wr.RACE), _stored("race"), in_window)
    assert wr.results_token(race) not in {"none", token}
    assert wr.results_token({"season": "x"}) == "none"


@pytest.fixture
def jolpica(monkeypatch, tmp_path):
    """Mock Jolpica, the bundled races and a fresh database for the refresh job."""
    monkeypatch.setattr(wr, "_candidate_races", lambda now: [_qatar()])
    monkeypatch.setattr(wr.config, "DATABASE_PATH", str(tmp_path / "weekend.db"))
    responses: dict[str, object] = {}
    calls: list[str] = []

    async def fake_fetch(_client, url, **_kwargs):
        calls.append(url)
        answer = next((value for key, value in responses.items() if key in url), None)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json=answer, request=httpx.Request("GET", url))

    monkeypatch.setattr(wr, "fetch_with_retry", fake_fetch)
    return SimpleNamespace(responses=responses, calls=calls)


@pytest.mark.asyncio
async def test_refresh_makes_no_calls_outside_the_windows(jolpica):
    assert await wr.refresh_weekend_results(FRIDAY) == []
    assert await wr.refresh_weekend_results(QA_Q + timedelta(minutes=30)) == []
    assert jolpica.calls == []


@pytest.mark.asyncio
async def test_refresh_stores_a_complete_qualifying_podium_and_stops(jolpica):
    jolpica.responses["qualifying.json"] = _payload("qualifying", _podium("qualifying"))
    now = QA_Q + timedelta(hours=2)
    assert await wr.refresh_weekend_results(now) == [(2026, 22, wr.QUALIFYING)]
    assert jolpica.calls == ["https://api.jolpi.ca/ergast/f1/2026/22/qualifying.json?limit=30"]
    podium = wr.stored_podium(2026, 22, wr.QUALIFYING)
    assert [row["name"] for row in podium] == ["Norris", "Piastri", "Verstappen"]
    assert podium[0] == {
        "pos": 1,
        "code": "NOR",
        "name": "Norris",
        "team": "Team",
        "time": "1:21.000",
    }
    assert await wr.refresh_weekend_results(now + timedelta(hours=3)) == []
    assert len(jolpica.calls) == 1

    # The podium survives a restart and is not fetched again.
    wr._reset_for_tests()
    assert await wr.load_weekend_results_from_db() == 1
    assert wr.stored_podium(2026, 22, wr.QUALIFYING) == podium
    assert await wr.refresh_weekend_results(now + timedelta(hours=4)) == []
    assert len(jolpica.calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"MRData": {"RaceTable": {"Races": []}}},
        _payload("qualifying", _podium("qualifying")[:2]),
        _payload(
            "qualifying",
            [
                _podium("qualifying")[0],
                _podium("qualifying")[1],
                {**_podium("qualifying")[2], "position": "4"},
            ],
        ),
        _payload("qualifying", [{**row, "Q3": None} for row in _podium("qualifying")]),
        _payload("qualifying", _podium("qualifying"), round_num="21"),
        httpx.ConnectError("offline"),
        httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "u"), response=httpx.Response(404)
        ),
    ],
    ids=["empty", "two-rows", "missing-p3", "no-time", "other-round", "offline", "404"],
)
@pytest.mark.asyncio
async def test_unusable_answers_are_negative_cached(jolpica, answer):
    jolpica.responses["qualifying.json"] = answer
    now = QA_Q + timedelta(hours=2)
    assert await wr.refresh_weekend_results(now) == []
    assert wr.stored_podium(2026, 22, wr.QUALIFYING) is None
    assert await wr.refresh_weekend_results(now + timedelta(minutes=30)) == []
    assert len(jolpica.calls) == 1
    # Persisted, so a restart does not ask again inside the retry interval either.
    wr._reset_for_tests()
    await wr.load_weekend_results_from_db()
    assert await wr.refresh_weekend_results(now + timedelta(minutes=40)) == []
    assert len(jolpica.calls) == 1
    # One retry per interval until the cutoff.
    await wr.refresh_weekend_results(now + wr.RETRY_INTERVAL)
    assert len(jolpica.calls) == 2


@pytest.mark.asyncio
async def test_race_podium_and_persistence_failure(jolpica, monkeypatch):
    jolpica.responses["results.json"] = _payload("race", _podium("race"))
    failing = MagicMock(save_weekend_result=AsyncMock(side_effect=RuntimeError("disk")))
    monkeypatch.setattr(wr, "get_database", lambda: failing)
    now = QA_RACE + timedelta(hours=2, minutes=50)
    assert await wr.refresh_weekend_results(now) == [(2026, 22, wr.RACE)]
    assert wr.stored_podium(2026, 22, wr.RACE)[2]["time"] == "+3.000"


@pytest.mark.asyncio
async def test_refresh_is_bounded_and_never_overlaps(jolpica, monkeypatch):
    monkeypatch.setattr(wr, "_refresh", AsyncMock(side_effect=TimeoutError))
    assert await wr.refresh_weekend_results(QA_Q + timedelta(hours=2)) == []
    lock = wr.get_loop_lock(wr._refresh_locks)
    async with lock:
        assert await wr.refresh_weekend_results(QA_Q + timedelta(hours=2)) == []


@pytest.mark.asyncio
async def test_candidate_races_cover_the_cutoff_across_new_year(monkeypatch):
    seasons = []

    def races(self, season):
        seasons.append(season)
        return []

    monkeypatch.setattr(F1Service, "get_all_races_from_static", races)
    assert wr._candidate_races(datetime(2027, 1, 1, 5, 0, tzinfo=UTC)) == []
    assert seasons == [2026, 2027]


# --- Database --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_adds_the_weekend_results_table(tmp_path):
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE cache_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    database = Database(str(path))
    try:
        assert await database.load_weekend_results() == []
        await database.save_weekend_result(2026, 22, "qualifying", None, FRIDAY)
        await database.save_weekend_result(2026, 22, "qualifying", '[{"pos": 1}]', FRIDAY)
        await database.save_weekend_result(2026, 22, "qualifying", None, FRIDAY + MINUTE)
        rows = await database.load_weekend_results()
    finally:
        await database.close()
    assert rows == [
        {
            "season": 2026,
            "round": 22,
            "session": "qualifying",
            "podium": '[{"pos": 1}]',
            "checked_at": (FRIDAY + MINUTE).isoformat(),
        }
    ]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION == 8


@pytest.mark.asyncio
async def test_malformed_database_rows_are_skipped(monkeypatch):
    rows = [
        {"season": 2026, "round": 22, "session": "race", "podium": "[1, 2]", "checked_at": "x"},
        {"season": 2026, "round": 22, "session": "qualifying", "podium": "{", "checked_at": "x"},
        {
            "season": 2026,
            "round": 22,
            "session": "race",
            "podium": json.dumps(list(_stored("race"))),
            "checked_at": FRIDAY.isoformat(),
        },
        {"season": "x", "round": 1, "session": "race", "podium": None, "checked_at": "2026-01-01"},
    ]
    monkeypatch.setattr(
        wr, "get_database", lambda: MagicMock(load_weekend_results=AsyncMock(return_value=rows))
    )
    assert await wr.load_weekend_results_from_db() == 4
    assert wr.stored_podium(2026, 22, wr.RACE) == _stored("race")
    assert wr.stored_podium(2026, 22, wr.QUALIFYING) is None


def test_reset_db_cache_scope_includes_weekend_results():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "reset_db.sh").read_text()
    assert "CACHE_TABLES=(cache_meta generated_images weekend_results)" in script


# --- Routes ----------------------------------------------------------------------------------


@pytest.fixture
def client_app(frozen, monkeypatch):
    """Mount the image and preview routes with Qatar 2026 as the next race."""
    race = _qatar()
    service = SimpleNamespace(
        get_next_race_from_static=MagicMock(return_value=race),
        get_all_races_from_static=MagicMock(return_value=[race]),
    )
    bmp_artifacts._singleflight_tasks.clear()
    monkeypatch.setattr(images, "record_api_call", MagicMock())
    monkeypatch.setattr(images, "_get_pregenerated_calendar_path", MagicMock(return_value=None))
    monkeypatch.setattr(F1Service, "get_next_race_from_static", lambda self: _qatar())
    for module in (images, previews):
        monkeypatch.setattr(module, "enforce_rate_limit", MagicMock())
    app = FastAPI()
    app.include_router(images.router)
    app.include_router(previews.router)
    app.dependency_overrides[get_f1_service] = lambda: service
    yield app
    bmp_artifacts._singleflight_tasks.clear()


async def _get(app, url, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/calendar.bmp?weekend_mode=on",
        "/auto.bmp?weekend_mode=AUTO",
        "/preview/configure/calendar.png?weekend_mode=1",
    ],
)
async def test_invalid_weekend_mode_returns_422(client_app, url):
    response = await _get(client_app, url)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_off_and_outside_the_weekend_serve_the_default_image(client_app, frozen):
    frozen.now = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    default = await _get(client_app, "/calendar.bmp?weather=false")
    off = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=off")
    auto = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    assert default.content == off.content == auto.content
    assert default.headers["etag"] == off.headers["etag"] == auto.headers["etag"]
    assert off.headers["x-cache"] == auto.headers["x-cache"] == "HIT"
    assert off.headers["x-next-refresh-seconds"] == default.headers["x-next-refresh-seconds"]
    assert not get_bmp_variant_cache()
    # weekend_mode=auto still wakes the panel when the weekend view starts.
    window_start = QA_FP1 - WEEKEND_LEAD
    assert int(auto.headers["x-next-refresh-seconds"]) <= int(
        (window_start - frozen.now).total_seconds() + rh.SETTLE_SECONDS
    )


@pytest.mark.asyncio
async def test_weekend_variant_uses_the_variant_cache_and_session_hint(client_app, frozen):
    frozen.now = FRIDAY
    default = await _get(client_app, "/calendar.bmp?weather=false")
    auto = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    assert auto.status_code == 200 and auto.headers["x-cache"] == "MISS"
    assert auto.content != default.content
    assert len(get_bmp_variant_cache()) == 1
    assert all(":weekend=" not in key for key in get_bmp_cache())
    (key,) = get_bmp_variant_cache()
    assert ":weekend=1:FP2:next:none:" in key
    # "FP2: 2 h" changes at the next hour tick, before FP2 starts.
    assert int(auto.headers["x-next-refresh-seconds"]) <= 3600 + rh.SETTLE_SECONDS
    again = await _get(
        client_app,
        "/calendar.bmp?weather=false&weekend_mode=auto",
        {"If-None-Match": auto.headers["etag"]},
    )
    assert again.status_code == 304 and again.headers["x-cache"] == "HIT"
    # The next session boundary starts a new cache entry and a new image.
    frozen.now = QA_FP2 + timedelta(minutes=5)
    live = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    assert live.headers["etag"] != auto.headers["etag"]
    assert any(":weekend=1:FP2:live:" in key for key in get_bmp_variant_cache())


@pytest.mark.asyncio
async def test_weekend_variant_http_lifetime_stops_at_the_next_session_boundary(client_app, frozen):
    frozen.now = QA_FP2 - timedelta(minutes=10)
    auto = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    assert auto.headers["cache-control"] == "public, max-age=600"
    frozen.now = QA_FP2 - timedelta(minutes=4)
    hit = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    assert hit.headers["x-cache"] == "HIT"
    assert hit.headers["cache-control"] == "public, max-age=240"
    default = await _get(client_app, "/calendar.bmp?weather=false")
    assert default.headers["cache-control"] == "public, max-age=3600"


@pytest.mark.asyncio
async def test_new_podium_invalidates_the_cached_weekend_image(client_app, frozen):
    frozen.now = QA_Q + timedelta(hours=2)
    before = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    # Results may still arrive: wake at least hourly.
    assert int(before.headers["x-next-refresh-seconds"]) <= rh.HOURLY_REFRESH_SECONDS
    wr._store((2026, 22, wr.QUALIFYING), _stored("qualifying"), frozen.now)
    after = await _get(
        client_app,
        "/calendar.bmp?weather=false&weekend_mode=auto",
        {"If-None-Match": before.headers["etag"]},
    )
    assert after.status_code == 200 and after.headers["x-cache"] == "MISS"
    assert after.headers["etag"] != before.headers["etag"]
    # The default calendar keeps the bundled history.
    default = await _get(client_app, "/calendar.bmp?weather=false")
    race = _qatar()
    historical = F1Service.get_historical_from_static("losail")
    assert default.content == bmp_artifacts.render_calendar_bytes(
        "1bit", "en", race, historical, None, "off"
    )


@pytest.mark.asyncio
async def test_stored_podiums_alone_select_the_variant_after_the_weekend(client_app, frozen):
    frozen.now = QA_RACE + NEXT_RACE_GRACE + timedelta(hours=1)
    wr._store((2026, 22, wr.RACE), _stored("race"), frozen.now)
    default = await _get(client_app, "/calendar.bmp?weather=false&year=2026&round=22")
    auto = await _get(
        client_app, "/calendar.bmp?weather=false&year=2026&round=22&weekend_mode=auto"
    )
    assert auto.content != default.content
    assert any(":weekend=off:" in key for key in get_bmp_variant_cache())


@pytest.mark.asyncio
async def test_countdown_and_weekend_variants_combine(client_app, frozen):
    response = await _get(
        client_app, "/calendar.bmp?weather=false&weekend_mode=auto&countdown=days"
    )
    assert response.status_code == 200
    (key,) = get_bmp_variant_cache()
    assert ":countdown=days:" in key and ":weekend=" in key


@pytest.mark.asyncio
async def test_auto_screen_and_preview_forward_the_weekend_mode(client_app, frozen, monkeypatch):
    calendar = await _get(client_app, "/calendar.bmp?weather=false&weekend_mode=auto")
    auto = await _get(client_app, "/auto.bmp?weather=false&weekend_mode=auto")
    assert auto.headers["x-active-screen"] == "calendar"
    assert auto.content == calendar.content
    cached = AsyncMock(return_value=None)
    monkeypatch.setattr(previews, "_cached_preview_response", cached)
    preview = await _get(client_app, "/preview/configure/calendar.png?weekend_mode=auto")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    cached.assert_not_awaited()
    # The preview shares the calendar's weekend BMP entry (same canonical key).
    assert sum(":weekend=" in key for key in get_bmp_variant_cache()) == 1
    plain = await _get(client_app, "/preview/configure/calendar.png")
    assert plain.status_code == 200
    cached.assert_awaited_once()
    frozen.now = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
    outside = await _get(client_app, "/preview/configure/calendar.png?weekend_mode=auto")
    assert outside.status_code == 200
    assert cached.await_count == 1


@pytest.mark.asyncio
async def test_api_docs_list_the_weekend_mode():
    from app.routes.api import api_info

    endpoints = (await api_info())["endpoints"]
    for path in ("/calendar.bmp", "/auto.bmp", "/preview/configure/{screen_type}.png"):
        parameter = endpoints[path]["parameters"]["weekend_mode"]
        assert parameter["values"] == ["off", "auto"] and parameter["default"] == "off"
