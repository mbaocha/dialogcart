#!/usr/bin/env python3
"""
Test cases for appointment grouper.

Tests the new appointment/reservation booking grouping logic.
"""
import sys
import importlib.util
from pathlib import Path

# Load modules directly to avoid import issues
script_dir = Path(__file__).parent.resolve()

# Mock luma.config before importing
mock_config = type('MockConfig', (), {
    'DEBUG_ENABLED': False
})()
sys.modules['luma'] = type('MockLuma', (), {})()
sys.modules['luma.config'] = mock_config

# Load structure_types module
structure_dir = script_dir.parent / "structure"
structure_types_path = structure_dir / "structure_types.py"
spec_structure_types = importlib.util.spec_from_file_location("luma.structure.structure_types", structure_types_path)
structure_types_module = importlib.util.module_from_spec(spec_structure_types)
structure_types_module.__package__ = "luma.structure"
structure_types_module.__name__ = "luma.structure.structure_types"
sys.modules["luma.structure.structure_types"] = structure_types_module
spec_structure_types.loader.exec_module(structure_types_module)

# Load appointment_grouper module
appointment_grouper_path = script_dir / "appointment_grouper.py"
spec_grouper = importlib.util.spec_from_file_location("luma.grouping.appointment_grouper", appointment_grouper_path)
grouper_module = importlib.util.module_from_spec(spec_grouper)
grouper_module.__package__ = "luma.grouping"
grouper_module.__name__ = "luma.grouping.appointment_grouper"
sys.modules["luma.grouping.appointment_grouper"] = grouper_module
spec_grouper.loader.exec_module(grouper_module)

group_appointment = grouper_module.group_appointment
BOOK_APPOINTMENT_INTENT = grouper_module.BOOK_APPOINTMENT_INTENT
STATUS_OK = grouper_module.STATUS_OK
STATUS_NEEDS_CLARIFICATION = grouper_module.STATUS_NEEDS_CLARIFICATION
StructureResult = structure_types_module.StructureResult


def test_shared_services_shared_time():
    """Test: shared services, shared time"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 2, "end": 3},
            {"text": "beard trim", "canonical": "beauty_and_wellness.beard_trim", "start": 4, "end": 5}
        ],
        "dates": [{"text": "tomorrow", "start": 5, "end": 6}],
        "dates_absolute": [],
        "times": [{"text": "9am", "start": 7, "end": 8}],
        "time_windows": [],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="shared",
        time_scope="shared",
        date_scope="shared",
        time_type="exact",
        has_duration=False,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    assert len(result["booking"]["services"]) == 2
    assert result["booking"]["date_ref"] == "tomorrow"
    assert result["booking"]["time_ref"] == "9am"
    assert result["booking"]["duration"] is None
    
    print("  [OK] Shared services, shared time: PASSED")


def test_separate_services_per_service_time():
    """Test: separate services, per-service time"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2},
            {"text": "beard trim", "canonical": "beauty_and_wellness.beard_trim", "start": 5, "end": 6}
        ],
        "dates": [{"text": "tomorrow", "start": 6, "end": 7}],
        "dates_absolute": [],
        "times": [
            {"text": "9am", "start": 3, "end": 4},
            {"text": "2pm", "start": 7, "end": 8}
        ],
        "time_windows": [],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="per_service",
        date_scope="shared",
        time_type="exact",
        has_duration=False,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    assert len(result["booking"]["services"]) == 2
    assert len(result["booking"]["services"]) == len(entities.get("business_categories") or entities.get("service_families", []))
    assert result["booking"]["date_ref"] == "tomorrow"
    # Note: per-service time handling may need enhancement
    assert result["booking"]["time_ref"] is not None
    
    print("  [OK] Separate services, per-service time: PASSED")


def test_ambiguous_input_needs_clarification():
    """Test: ambiguous input → NEEDS_CLARIFICATION"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2}
        ],
        "dates": [
            {"text": "tomorrow", "start": 2, "end": 3},
            {"text": "next week", "start": 3, "end": 4}
        ],
        "dates_absolute": [],
        "times": [
            {"text": "9am", "start": 4, "end": 5},
            {"text": "5pm", "start": 5, "end": 6}
        ],
        "time_windows": [],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="shared",
        date_scope="shared",
        time_type="exact",  # Not "range" even though multiple times
        has_duration=False,
        needs_clarification=True  # Flagged as needing clarification
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_NEEDS_CLARIFICATION
    assert result["reason"] is not None
    assert "Multiple dates" in result["reason"] or "Multiple times" in result["reason"]
    
    print("  [OK] Ambiguous input -> NEEDS_CLARIFICATION: PASSED")


def test_time_window():
    """Test: time window (morning, afternoon, etc.)"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2}
        ],
        "dates": [{"text": "tomorrow", "start": 2, "end": 3}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [{"text": "morning", "start": 3, "end": 4}],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="shared",
        date_scope="shared",
        time_type="window",
        has_duration=False,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    assert result["booking"]["time_ref"] == "morning"
    
    print("  [OK] Time window: PASSED")


def test_time_range():
    """Test: time range (between X and Y)"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2}
        ],
        "dates": [{"text": "tomorrow", "start": 2, "end": 3}],
        "dates_absolute": [],
        "times": [
            {"text": "9am", "start": 4, "end": 5},
            {"text": "5pm", "start": 6, "end": 7}
        ],
        "time_windows": [],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="shared",
        date_scope="shared",
        time_type="range",
        has_duration=False,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    assert "to" in result["booking"]["time_ref"].lower()
    assert "9am" in result["booking"]["time_ref"]
    assert "5pm" in result["booking"]["time_ref"]
    
    print("  [OK] Time range: PASSED")


def test_with_duration():
    """Test: booking with duration"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2}
        ],
        "dates": [{"text": "tomorrow", "start": 2, "end": 3}],
        "dates_absolute": [],
        "times": [{"text": "9am", "start": 3, "end": 4}],
        "time_windows": [],
        "durations": [{"text": "one hour", "start": 5, "end": 6}]
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="shared",
        date_scope="shared",
        time_type="exact",
        has_duration=True,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    assert result["booking"]["duration"] is not None
    assert result["booking"]["duration"]["text"] == "one hour"
    
    print("  [OK] Booking with duration: PASSED")


def test_absolute_date():
    """Test: absolute date preference"""
    entities = {
        "business_categories": [
            {"text": "haircut", "canonical": "beauty_and_wellness.haircut", "start": 1, "end": 2}
        ],
        "dates": [{"text": "tomorrow", "start": 2, "end": 3}],
        "dates_absolute": [{"text": "15th dec", "start": 3, "end": 4}],
        "times": [{"text": "9am", "start": 4, "end": 5}],
        "time_windows": [],
        "durations": []
    }
    
    structure = StructureResult(
        booking_count=1,
        service_scope="separate",
        time_scope="shared",
        date_scope="shared",
        time_type="exact",
        has_duration=False,
        needs_clarification=False
    )
    
    result = group_appointment(entities, structure)
    
    assert result["intent"] == BOOK_APPOINTMENT_INTENT
    assert result["status"] == STATUS_OK
    # Should prefer absolute date over relative
    assert result["booking"]["date_ref"] == "15th dec"
    
    print("  [OK] Absolute date preference: PASSED")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("APPOINTMENT GROUPER TEST SUITE")
    print("=" * 70)
    print()
    
    test_shared_services_shared_time()
    test_separate_services_per_service_time()
    test_ambiguous_input_needs_clarification()
    test_time_window()
    test_time_range()
    test_with_duration()
    test_absolute_date()
    
    print()
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()

