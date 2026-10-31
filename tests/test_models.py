"""Tests for data models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models import F1Response, PerfMetricsPayload, Race, RaceSession, ScheduleEvent


def test_schedule_event_accepts_legacy_datetime_alias():
    event = ScheduleEvent(
        name="Race",
        datetime=datetime(2026, 3, 8, 15, 0),
        display_time="15:00",
    )

    assert event.event_datetime == datetime(2026, 3, 8, 15, 0)


def test_schedule_event_accepts_internal_field_name():
    event = ScheduleEvent(
        name="Qualifying",
        event_datetime=datetime(2026, 3, 7, 15, 0),
        display_time="15:00",
    )

    assert event.event_datetime == datetime(2026, 3, 7, 15, 0)


@pytest.mark.parametrize(
    "page_path", ["/", "/cs/configure/calendar", "/pt-BR/stats", "/a_b.c~d-e/"]
)
def test_perf_metrics_payload_accepts_site_relative_paths(page_path):
    assert PerfMetricsPayload(page_path=page_path).page_path == page_path


@pytest.mark.parametrize(
    "page_path",
    [
        "",
        "stats",
        "//evil.example/",
        "/stats?x=1",
        "/stats#top",
        "/a b",
        "https://x.test/",
        "/\u00e9",
    ],
)
def test_perf_metrics_payload_rejects_non_site_paths(page_path):
    with pytest.raises(ValidationError):
        PerfMetricsPayload(page_path=page_path)


def test_race_accepts_sessions_without_times(sepang_2017_payload):
    race = Race(**sepang_2017_payload)
    assert race.FirstPractice.time is None
    assert RaceSession(date="2017-09-29", time=None).time is None
    assert RaceSession(date="2017-09-29", time="03:00:00Z").time == "03:00:00Z"
    response = F1Response(MRData={"RaceTable": {"Races": [sepang_2017_payload]}})
    assert response.race == race
