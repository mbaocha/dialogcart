"""CalendarBinder Temporal path — ISO only (NL handled by TemporalResolver)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from nlu.calendar.calendar_binder import _bind_dates_from_temporal


def test_iso_single_day():
    now = datetime(2026, 7, 7, 10, 0, tzinfo=ZoneInfo("UTC"))
    result = _bind_dates_from_temporal(
        {
            "start_date": "2026-07-22",
            "start_date_expression": "22nd july",
            "end_date": None,
        },
        now,
        ZoneInfo("UTC"),
    )
    assert result == {"start_date": "2026-07-22", "end_date": "2026-07-22"}


def test_expression_without_iso_ignored():
    now = datetime(2026, 7, 7, 10, 0, tzinfo=ZoneInfo("UTC"))
    result = _bind_dates_from_temporal(
        {
            "start_date": None,
            "start_date_expression": "wednesday",
            "end_date": None,
        },
        now,
        ZoneInfo("UTC"),
    )
    assert result is None


def test_iso_range():
    now = datetime(2026, 7, 7, 10, 0, tzinfo=ZoneInfo("UTC"))
    result = _bind_dates_from_temporal(
        {
            "start_date": "2026-07-09",
            "end_date": "2026-07-11",
        },
        now,
        ZoneInfo("UTC"),
    )
    assert result == {"start_date": "2026-07-09", "end_date": "2026-07-11"}
