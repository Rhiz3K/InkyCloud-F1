"""Regression tests for the follow-up tier of the 2026-11-22 full review (issue #185)."""

from datetime import UTC, datetime

import pytest

from app.services import teams_extra as extras
from app.services.f1_service import F1Service
from app.services.teams_extra import TeamsExtra, build_teams_extra_context

# --- B6: a date-only race keeps its calendar date in the Teams panel ---------------------------


def _season_with_first_race(monkeypatch, *, time: str | None, race_date: str = "2026-03-14"):
    races = [race.model_copy(deep=True) for race in F1Service.get_season_from_static(2026)]
    races[0].date = race_date
    races[0].time = time
    monkeypatch.setattr(
        extras.F1Service,
        "get_season_from_static",
        staticmethod(lambda year: races if year == 2026 else []),
    )
    return races


@pytest.mark.parametrize("timezone_name", ["America/New_York", "Europe/Prague", "Asia/Tokyo"])
def test_date_only_race_keeps_its_date_and_has_no_invented_hour(monkeypatch, timezone_name):
    from app.services.renderer_teams_extras import _date_time

    _season_with_first_race(monkeypatch, time=None)
    now = datetime(2026, 3, 10, 12, tzinfo=UTC)

    ctx = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=now, timezone_name=timezone_name
    )

    assert ctx is not None and ctx.next_round == 1
    assert ctx.start_local.date().isoformat() == "2026-03-14"
    assert ctx.start_time_known is False
    assert ctx.rounds[0].date.isoformat() == "2026-03-14"
    assert _date_time(ctx, {}).endswith("14.03. · –")


def test_date_only_race_stays_upcoming_until_its_local_day_ends(monkeypatch):
    _season_with_first_race(monkeypatch, time=None)
    # 00:01Z on race day is still the previous evening in New York.
    early = datetime(2026, 3, 14, 0, 1, tzinfo=UTC)
    late = datetime(2026, 3, 15, 5, 0, tzinfo=UTC)  # 01:00 the next day in New York

    during = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=early, timezone_name="America/New_York"
    )
    after = build_teams_extra_context(
        TeamsExtra.COUNTDOWN, 2026, now=late, timezone_name="America/New_York"
    )

    assert during is not None and during.next_round == 1 and during.days_left == 1
    assert after is not None and after.next_round == 2
    assert after.rounds[0].done is True


def test_explicit_offset_is_not_overwritten_with_utc(monkeypatch):
    _season_with_first_race(monkeypatch, time="14:00:00+02:00")
    now = datetime(2026, 3, 10, tzinfo=UTC)

    ctx = build_teams_extra_context(TeamsExtra.COUNTDOWN, 2026, now=now, timezone_name="UTC")

    assert ctx is not None and ctx.start_time_known is True
    assert ctx.start_local == datetime(2026, 3, 14, 12, tzinfo=UTC)
