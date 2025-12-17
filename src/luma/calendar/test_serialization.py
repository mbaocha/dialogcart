#!/usr/bin/env python3
"""
Test serialization of CalendarBindingResult.

Verifies that CalendarBindingResult can be cleanly serialized to JSON
without leaking Python-specific objects.
"""
import sys
import importlib.util
import json
from pathlib import Path
from datetime import datetime

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
spec_semantic = importlib.util.spec_from_file_location("luma.resolution.semantic_resolver", semantic_resolver_path)
semantic_module = importlib.util.module_from_spec(spec_semantic)
semantic_module.__package__ = "luma.resolution"
semantic_module.__name__ = "luma.resolution.semantic_resolver"
sys.modules["luma.resolution.semantic_resolver"] = semantic_module
spec_semantic.loader.exec_module(semantic_module)

SemanticResolutionResult = semantic_module.SemanticResolutionResult

# Load calendar_binder module
binder_path = script_dir / "calendar_binder.py"
spec_binder = importlib.util.spec_from_file_location("luma.calendar.calendar_binder", binder_path)
binder_module = importlib.util.module_from_spec(spec_binder)
binder_module.__package__ = "luma.calendar"
binder_module.__name__ = "luma.calendar.calendar_binder"
sys.modules["luma.calendar.calendar_binder"] = binder_module
spec_binder.loader.exec_module(binder_module)

bind_calendar = binder_module.bind_calendar
CalendarBindingResult = binder_module.CalendarBindingResult

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


def test_json_serialization():
    """Test that CalendarBindingResult serializes to clean JSON."""
    # Create semantic result
    semantic_result = SemanticResolutionResult(
        resolved_booking={
            "services": [
                {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 3, "end": 4}
            ],
            "date_mode": "single_day",
            "date_refs": ["tomorrow"],
            "time_mode": "exact",
            "time_refs": ["9am"],
            "duration": None
        },
        needs_clarification=False,
        reason=None
    )
    
    # Set now to a known date
    now = _localize_datetime(datetime(2025, 12, 16, 10, 0, 0), "UTC")
    
    # Bind calendar
    result = bind_calendar(semantic_result, now, "UTC", intent="CREATE_BOOKING")
    
    # Serialize to dict
    serialized = result.to_dict()
    
    # Verify structure
    assert "calendar_booking" in serialized
    assert "needs_clarification" in serialized
    assert "reason" in serialized
    
    # Verify calendar_booking structure
    booking = serialized["calendar_booking"]
    assert "services" in booking
    assert "date_range" in booking
    assert "time_range" in booking
    assert "datetime_range" in booking
    assert "duration" in booking
    
    # Verify services are normalized (minimal shape)
    services = booking["services"]
    assert isinstance(services, list)
    assert len(services) > 0
    service = services[0]
    assert isinstance(service, dict)
    assert "text" in service
    assert "canonical" in service
    # Should NOT have start/end in serialized output (minimal shape)
    assert "start" not in service
    assert "end" not in service
    
    # Verify datetime_range contains ISO-8601 strings
    datetime_range = booking["datetime_range"]
    assert datetime_range is not None
    assert isinstance(datetime_range["start"], str)
    assert isinstance(datetime_range["end"], str)
    assert "T" in datetime_range["start"]  # ISO-8601 format
    assert "T" in datetime_range["end"]
    
    # Verify date_range contains strings
    date_range = booking["date_range"]
    assert date_range is not None
    assert isinstance(date_range["start_date"], str)
    assert isinstance(date_range["end_date"], str)
    
    # Verify time_range contains strings
    time_range = booking["time_range"]
    assert time_range is not None
    assert isinstance(time_range["start_time"], str)
    assert isinstance(time_range["end_time"], str)
    
    # Test JSON serialization (should not raise)
    try:
        json_str = json.dumps(serialized)
        assert len(json_str) > 0
        
        # Verify can be parsed back
        parsed = json.loads(json_str)
        assert parsed["needs_clarification"] == False
        assert parsed["reason"] is None
        assert isinstance(parsed["calendar_booking"]["services"], list)
    except (TypeError, ValueError) as e:
        raise AssertionError(f"JSON serialization failed: {e}")
    
    # Verify no Python objects leaked
    def check_no_python_objects(obj, path=""):
        """Recursively check for Python-specific objects."""
        if isinstance(obj, datetime):
            raise AssertionError(f"Found datetime object at {path}")
        if isinstance(obj, type):
            raise AssertionError(f"Found type object at {path}")
        if hasattr(obj, '__dict__') and not isinstance(obj, dict):
            raise AssertionError(f"Found non-dict object with __dict__ at {path}: {type(obj)}")
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_no_python_objects(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                check_no_python_objects(item, f"{path}[{i}]")
    
    check_no_python_objects(serialized)
    
    print("  [OK] JSON serialization: PASSED")


def test_none_values():
    """Test that None values are handled correctly."""
    # Create result with None values
    result = CalendarBindingResult(
        calendar_booking={
            "services": [],
            "date_range": None,
            "time_range": None,
            "datetime_range": None,
            "duration": None
        },
        needs_clarification=False,
        reason=None
    )
    
    serialized = result.to_dict()
    
    # Verify None values are preserved
    assert serialized["reason"] is None
    assert serialized["calendar_booking"]["date_range"] is None
    assert serialized["calendar_booking"]["duration"] is None
    
    # Should serialize to JSON with null
    json_str = json.dumps(serialized)
    assert "null" in json_str
    
    print("  [OK] None values handling: PASSED")


def test_with_reason():
    """Test serialization with clarification reason."""
    result = CalendarBindingResult(
        calendar_booking={
            "services": [{"text": "haircut"}],
            "date_range": None,
            "time_range": None,
            "datetime_range": None,
            "duration": None
        },
        needs_clarification=True,
        reason="Multiple dates without clear range"
    )
    
    serialized = result.to_dict()
    
    assert serialized["needs_clarification"] == True
    assert serialized["reason"] == "Multiple dates without clear range"
    
    # Should serialize to JSON
    json_str = json.dumps(serialized)
    assert "Multiple dates" in json_str
    
    print("  [OK] Reason serialization: PASSED")


def main():
    """Run all serialization tests."""
    print("=" * 70)
    print("CALENDAR BINDING RESULT SERIALIZATION TEST SUITE")
    print("=" * 70)
    print()
    
    test_json_serialization()
    test_none_values()
    test_with_reason()
    
    print()
    print("=" * 70)
    print("ALL SERIALIZATION TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()

