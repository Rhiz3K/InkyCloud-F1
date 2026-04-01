"""Tests for data models."""

from datetime import datetime

from app.models import ScheduleEvent


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
