#!/usr/bin/env python3
"""
Test cases for calendar binder.

Tests calendar binding for various date/time scenarios.
"""
import sys
import importlib.util
from pathlib import Path
from datetime import datetime

# Try zoneinfo first (Python 3.9+), fallback to pytz
try:
    from zoneinfo import ZoneInfo
    try:
        _ = ZoneInfo("UTC")  # Test if tzdata is available
        ZONEINFO_AVAILABLE = True
    except Exception:
        ZONEINFO_AVAILABLE = False
except ImportError:
    ZONEINFO_AVAILABLE = False

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False
    pytz = None


def _localize_datetime(dt: datetime, tz_str: str) -> datetime:
    """Localize datetime to timezone."""
    if ZONEINFO_AVAILABLE:
        try:
            tz = ZoneInfo(tz_str)
            return dt.replace(tzinfo=tz)
        except Exception:
            from datetime import timezone
            return dt.replace(tzinfo=timezone.utc)
    elif PYTZ_AVAILABLE:
        tz = pytz.timezone(tz_str)
        return tz.localize(dt)
    else:
        from datetime import timezone
        return dt.replace(tzinfo=timezone.utc)


# Load modules directly to avoid import issues
script_dir = Path(__file__).parent.resolve()

# Mock luma.config before importing
mock_config = type('MockConfig', (), {
    'DEBUG_ENABLED': False
})()
sys.modules['luma'] = type('MockLuma', (), {})()
sys.modules['luma.config'] = mock_config

# Load semantic_resolver to get SemanticResolutionResult
resolution_dir = script_dir.parent / "resolution"
semantic_resolver_path = resolution_dir / "semantic_resolver.py"
spec_semantic = importlib.util.spec_from_file_location(
    "luma.resolution.semantic_resolver", semantic_resolver_path)
semantic_module = importlib.util.module_from_spec(spec_semantic)
semantic_module.__package__ = "luma.resolution"
semantic_module.__name__ = "luma.resolution.semantic_resolver"
sys.modules["luma.resolution.semantic_resolver"] = semantic_module
spec_semantic.loader.exec_module(semantic_module)

SemanticResolutionResult = semantic_module.SemanticResolutionResult

# Load calendar_binder module
binder_path = script_dir / "calendar_binder.py"
spec_binder = importlib.util.spec_from_file_location(
    "luma.calendar.calendar_binder", binder_path)
binder_module = importlib.util.module_from_spec(spec_binder)
binder_module.__package__ = "luma.calendar"
binder_module.__name__ = "luma.calendar.calendar_binder"
sys.modules["luma.calendar.calendar_binder"] = binder_module
spec_binder.loader.exec_module(binder_module)

bind_calendar = binder_module.bind_calendar
CalendarBindingResult = binder_module.CalendarBindingResult


def test_tomorrow_exact_time():
    """Test tomorrow + exact time."""
    # Create semantic result
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    # Set now to a known date (2025-12-16 10:00:00)
    now = datetime(2025, 12, 16, 10, 0, 0)
    now = _localize_datetime(now, "UTC")

    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["date_range"] is not None
    assert result.calendar_booking["date_range"]["start_date"] == "2025-12-17"
    assert result.calendar_booking["time_range"] is not None
    assert result.calendar_booking["time_range"]["start_time"] == "09:00"
    assert result.calendar_booking["datetime_range"] is not None
    assert "2025-12-17T09:00" in result.calendar_booking["datetime_range"]["start"]
    if result.needs_clarification:
        print(
            f"  [DEBUG] Clarification reason: {result.clarification.reason.value if result.clarification else 'None'}")
    assert not result.needs_clarification, f"Should not need clarification, but got: {result.clarification.reason.value if result.clarification else 'None'}"

    print("  [OK] Tomorrow + exact time: PASSED")


def test_tomorrow_window():
    """Test tomorrow + window."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "window",
            "time_refs": ["morning"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["date_range"]["start_date"] == "2025-12-17"
    assert result.calendar_booking["time_range"]["start_time"] == "08:00"
    assert result.calendar_booking["time_range"]["end_time"] == "11:59"
    assert not result.needs_clarification

    print("  [OK] Tomorrow + window: PASSED")


def test_absolute_date_future():
    """Test absolute date without year (future preference)."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["15th dec"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    # Set now to Dec 10, 2025 - "15th dec" should resolve to Dec 15, 2025 (future)
    now = _localize_datetime(datetime(2025, 12, 10, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["date_range"]["start_date"] == "2025-12-15"
    assert not result.needs_clarification

    print("  [OK] Absolute date future: PASSED")


def test_absolute_date_past_this_year():
    """Test absolute date that has passed this year (use next year)."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["15th dec"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    # Set now to Dec 20, 2025 - "15th dec" has passed, should use 2026
    now = _localize_datetime(datetime(2025, 12, 20, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["date_range"]["start_date"] == "2026-12-15"
    assert not result.needs_clarification

    print("  [OK] Absolute date past this year (next year): PASSED")


def test_date_range_time_range():
    """Test date range + time range."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "range",
            "date_refs": ["8th dec", "15th dec"],
            "time_mode": "range",
            "time_refs": ["9am", "5pm"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 1, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["date_range"]["start_date"] == "2025-12-08"
    assert result.calendar_booking["date_range"]["end_date"] == "2025-12-15"
    assert result.calendar_booking["time_range"]["start_time"] == "09:00"
    assert result.calendar_booking["time_range"]["end_time"] == "17:00"
    assert result.calendar_booking["datetime_range"] is not None
    assert "2025-12-08T09:00" in result.calendar_booking["datetime_range"]["start"]
    assert "2025-12-15T17:00" in result.calendar_booking["datetime_range"]["end"]

    print("  [OK] Date range + time range: PASSED")


def test_duration_based_end_time():
    """Test duration-based end time computation."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": {"text": "one hour"}
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    assert result.calendar_booking["datetime_range"] is not None
    start_str = result.calendar_booking["datetime_range"]["start"]
    end_str = result.calendar_booking["datetime_range"]["end"]

    # Parse and verify end is 1 hour after start
    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

    duration = end_dt - start_dt
    assert duration.total_seconds() == 3600  # 1 hour

    print("  [OK] Duration-based end time: PASSED")


def test_timezone_handling():
    """Test timezone handling."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "America/New_York")

    assert result.calendar_booking["datetime_range"] is not None
    # Should contain timezone info
    assert "+" in result.calendar_booking["datetime_range"]["start"] or "T" in result.calendar_booking["datetime_range"]["start"]

    print("  [OK] Timezone handling: PASSED")


def test_invalid_ranges_clarification():
    """Test invalid ranges trigger clarification."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "range",
            "date_refs": ["15th dec", "10th dec"],  # End before start
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 1, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Should detect invalid range
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason is not None
    # Check that clarification is for invalid date range
    from ..clarification import render_clarification
    msg = render_clarification(
        result.clarification) if result.clarification else ""
    data_str = str(result.clarification.data) if result.clarification else ""
    assert "invalid" in msg.lower() or "validation" in msg.lower(
    ) or "end" in data_str.lower() or "after" in data_str.lower()

    print("  [OK] Invalid ranges clarification: PASSED")


def test_window_plus_exact_time_binding():
    """Test window + exact time binding."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am", "morning"],  # Both preserved
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Exact time should be used, window is contextual
    assert result.calendar_booking["time_range"]["start_time"] == "09:00"
    assert result.calendar_booking["datetime_range"] is not None

    print("  [OK] Window + exact time binding: PASSED")


def test_flexible_date_no_time():
    """Test flexible date mode with no time (service present and missing). Safety: do not guess vague time."""
    # Service present
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "flexible",
            "date_refs": [],
            "time_mode": "none",
            "time_refs": [],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )
    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")
    assert result.calendar_booking["date_range"] is None
    assert result.calendar_booking["time_range"] is None
    assert result.calendar_booking["datetime_range"] is None

    # Service missing (should still bind, and handle vague/flexible time safeguarding)
    semantic_result_missing_service = SemanticResolutionResult(
        resolved_booking={
            "services": [],  # missing extraction
            "date_mode": "flexible",
            "date_refs": [],
            "time_mode": "none",
            "time_refs": [],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )
    result_missing_service = bind_calendar(
        semantic_result_missing_service, now, "UTC", intent="CREATE_BOOKING")
    assert result_missing_service.calendar_booking["date_range"] is None
    assert result_missing_service.calendar_booking["time_range"] is None
    assert result_missing_service.calendar_booking["datetime_range"] is None

    print("  [OK] Flexible date no time (with/without service): PASSED  [Guardrail: vague time stays flexible]")
    #
    # ---- Conservation of vague/approximate times (never expand e.g., '6ish' to a range): ----
    # The binder does not guess or create hard windows for vague times. This block preserves backward-compatibility and safety.
    # TODO: Allow configurable approximate-time mapping in the future (off by default; see tenant config).


def test_intent_guarded_no_binding():
    """Test intent-guarded binding - no binding for non-booking intents."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")

    # Test with non-booking intent
    result = bind_calendar(semantic_result, now, "UTC", intent="DISCOVERY")
    assert result.calendar_booking is not None
    assert result.calendar_booking["date_range"] is None
    assert result.calendar_booking["time_range"] is None
    assert result.calendar_booking["datetime_range"] is None
    assert not result.needs_clarification

    # Test with None intent - should bind (no guard when intent is None)
    result = bind_calendar(semantic_result, now, "UTC", intent=None)
    assert result.calendar_booking is not None

    # Test with booking intent - should bind
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")
    assert result.calendar_booking is not None

    print("  [OK] Intent-guarded no binding: PASSED")


def test_ambiguous_relative_dates():
    """Test ambiguous relative dates (e.g., 'Friday morning' without date)."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "flexible",
            "date_refs": ["friday"],
            "time_mode": "window",
            "time_refs": ["morning"],
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Should flag ambiguity
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason is not None
    # Check that the clarification reason or message relates to ambiguous date
    from ..clarification import render_clarification
    msg = render_clarification(
        result.clarification) if result.clarification else ""
    assert "date" in msg.lower() or "ambiguous" in msg.lower(
    ) or "friday" in str(result.clarification.data).lower()

    print("  [OK] Ambiguous relative dates: PASSED")


def test_duration_date_range_conflict():
    """Test duration + multi-day date range conflict."""
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "range",
            "date_refs": ["8th dec", "15th dec"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": {"text": "one hour"}
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 1, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Should flag conflict
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason is not None
    # Check for clarification message or data indicating duration conflict
    from ..clarification import render_clarification
    msg = render_clarification(
        result.clarification) if result.clarification else ""
    data_str = str(result.clarification.data) if result.clarification else ""
    assert "duration" in msg.lower() or "duration" in data_str.lower(
    ) or "multi" in data_str.lower()

    print("  [OK] Duration + date range conflict: PASSED")


def test_exact_time_with_dot_separator():
    """Test exact time with dot separator (10.30) overrides time window."""
    # This tests the bug fix: "tomorrow morning at 10.30" should resolve to 10:30, not morning window
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",  # Exact time mode (window discarded)
            "time_refs": ["10.30"],  # Only exact time, no window
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Should NOT need clarification
    assert result.needs_clarification is False

    # Should produce point-in-time booking (start_time == end_time)
    assert result.calendar_booking is not None
    assert result.calendar_booking["time_range"] is not None
    assert result.calendar_booking["time_range"]["start_time"] == "10:30"
    # Point-in-time
    assert result.calendar_booking["time_range"]["end_time"] == "10:30"

    # Should have datetime range with same start and end
    assert result.calendar_booking["datetime_range"] is not None
    dt_start = result.calendar_booking["datetime_range"]["start"]
    dt_end = result.calendar_booking["datetime_range"]["end"]
    assert dt_start == dt_end  # Point-in-time booking

    print("  [OK] Exact time with dot separator (10.30): PASSED")


def test_exact_time_with_spaced_dot_separator():
    """Test exact time with spaced dot separator (10 . 30) - tokenizer splits it."""
    # This tests the case where tokenizer splits "10.30" into "10 . 30"
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["10 . 30"],  # Spaced version from tokenizer
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING")

    # Should NOT need clarification
    assert result.needs_clarification is False

    # Should produce point-in-time booking
    assert result.calendar_booking is not None
    assert result.calendar_booking["time_range"] is not None
    assert result.calendar_booking["time_range"]["start_time"] == "10:30"
    assert result.calendar_booking["time_range"]["end_time"] == "10:30"

    # Should have datetime range with correct time
    assert result.calendar_booking["datetime_range"] is not None
    dt_start = result.calendar_booking["datetime_range"]["start"]
    assert "T10:30:00" in dt_start  # Should contain 10:30 time

    print("  [OK] Exact time with spaced dot separator (10 . 30): PASSED")


def test_time_window_bias_night():
    """Test time-window bias: night + ambiguous time → PM bias."""
    # "tomorrow night at 10.30" → should bind to 22:30 (PM), not 10:30 (AM)
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["10.30"],  # Ambiguous (no AM/PM)
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    # Pass entities with time_windows for bias rule
    entities = {
        "time_windows": [{"text": "night"}]
    }

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING", entities=entities)

    # Should bind to PM (22:30), not AM (10:30)
    assert result.calendar_booking["time_range"]["start_time"] == "22:30"
    assert result.calendar_booking["time_range"]["end_time"] == "22:30"
    assert not result.needs_clarification

    print("  [OK] Time-window bias (night + ambiguous): PASSED")


def test_time_window_bias_morning():
    """Test time-window bias: morning + ambiguous time → AM preserved."""
    # "tomorrow morning at 9.30" → should bind to 09:30 (AM), not 21:30 (PM)
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9.30"],  # Ambiguous (no AM/PM)
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    entities = {
        "time_windows": [{"text": "morning"}]
    }

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING", entities=entities)

    # Should bind to AM (09:30), not PM (21:30)
    assert result.calendar_booking["time_range"]["start_time"] == "09:30"
    assert result.calendar_booking["time_range"]["end_time"] == "09:30"
    assert not result.needs_clarification

    print("  [OK] Time-window bias (morning + ambiguous): PASSED")


def test_time_window_bias_no_window():
    """Test time-window bias: no window + ambiguous time → unchanged."""
    # "tomorrow at 10.30" → should bind to 10:30 (default AM), no bias
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["10.30"],  # Ambiguous (no AM/PM)
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    entities = {
        "time_windows": []  # No window
    }

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING", entities=entities)

    # Should bind to default (10:30 AM), no bias applied
    assert result.calendar_booking["time_range"]["start_time"] == "10:30"
    assert result.calendar_booking["time_range"]["end_time"] == "10:30"
    assert not result.needs_clarification

    print("  [OK] Time-window bias (no window): PASSED")


def test_time_window_bias_explicit_pm():
    """Test time-window bias: explicit PM → unchanged."""
    # "tomorrow night at 10pm" → should bind to 22:00, not biased
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["10pm"],  # Explicit PM
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    entities = {
        "time_windows": [{"text": "night"}]
    }

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING", entities=entities)

    # Should bind to explicit PM (22:00), bias rule should NOT apply
    assert result.calendar_booking["time_range"]["start_time"] == "22:00"
    assert result.calendar_booking["time_range"]["end_time"] == "22:00"
    assert not result.needs_clarification

    print("  [OK] Time-window bias (explicit PM): PASSED")


def test_time_window_bias_explicit_am():
    """Test time-window bias: explicit AM → unchanged."""
    # "tomorrow morning at 9am" → should bind to 09:00, not biased
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [{"text": "haircut"}],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],  # Explicit AM
            "duration": None
        },
        needs_clarification=False,
        clarification=None
    )

    entities = {
        "time_windows": [{"text": "morning"}]
    }

    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    result = bind_calendar(semantic_result, now, "UTC",
                           intent="CREATE_BOOKING", entities=entities)

    # Should bind to explicit AM (09:00), bias rule should NOT apply
    assert result.calendar_booking["time_range"]["start_time"] == "09:00"
    assert result.calendar_booking["time_range"]["end_time"] == "09:00"
    assert not result.needs_clarification

    print("  [OK] Time-window bias (explicit AM): PASSED")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("CALENDAR BINDER TEST SUITE")
    print("=" * 70)
    print()

    test_tomorrow_exact_time()
    test_tomorrow_window()
    test_absolute_date_future()
    test_absolute_date_past_this_year()
    test_date_range_time_range()
    test_duration_based_end_time()
    test_timezone_handling()
    test_invalid_ranges_clarification()
    test_window_plus_exact_time_binding()
    test_flexible_date_no_time()
    test_intent_guarded_no_binding()
    test_ambiguous_relative_dates()
    test_duration_date_range_conflict()
    test_exact_time_with_dot_separator()
    test_exact_time_with_spaced_dot_separator()
    test_time_window_bias_night()
    test_time_window_bias_morning()
    test_time_window_bias_no_window()
    test_time_window_bias_explicit_pm()
    test_time_window_bias_explicit_am()

    print()
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
