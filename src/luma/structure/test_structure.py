#!/usr/bin/env python3
"""
Test cases for structural interpretation layer.
"""

import sys
from pathlib import Path

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # structure/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import sys
from pathlib import Path

# Add src directory to path for imports
script_dir = Path(__file__).parent.resolve()  # structure/
luma_dir = script_dir.parent  # luma/
src_dir = luma_dir.parent  # src/

src_path = str(src_dir)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from luma.structure import interpret_structure, StructureResult


def test_shared_vs_separate_services():
    """Test service scope determination."""
    
    # Shared services: joined by "and"
    psentence1 = "book servicefamilytoken and servicefamilytoken datetoken"
    entities1 = {
        "service_families": [
            {"text": "haircut", "start": 1, "end": 2},
            {"text": "beard trim", "start": 3, "end": 4}
        ],
        "dates": [{"text": "tomorrow", "start": 4, "end": 5}],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.service_scope == "shared", f"Expected 'shared', got '{result1.service_scope}'"
    
    # Separate services: verb between them
    psentence2 = "book servicefamilytoken then book servicefamilytoken"
    entities2 = {
        "service_families": [
            {"text": "haircut", "start": 1, "end": 2},
            {"text": "beard trim", "start": 4, "end": 5}
        ],
        "dates": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.service_scope == "separate", f"Expected 'separate', got '{result2.service_scope}'"
    assert result2.booking_count > 1, "Should detect multiple bookings"
    
    print("✅ Shared vs separate services: PASSED")


def test_exact_vs_range_time():
    """Test time type determination."""
    
    # Exact time
    psentence1 = "book servicefamilytoken at timetoken"
    entities1 = {
        "service_families": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.time_type == "exact", f"Expected 'exact', got '{result1.time_type}'"
    
    # Time range
    psentence2 = "book servicefamilytoken between timetoken and timetoken"
    entities2 = {
        "service_families": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.time_type == "range", f"Expected 'range', got '{result2.time_type}'"
    
    # Time window
    psentence3 = "book servicefamilytoken timewindowtoken"
    entities3 = {
        "service_families": [{"text": "haircut"}],
        "dates": [],
        "times": [],
        "time_windows": [{"text": "morning"}],
        "durations": []
    }
    result3 = interpret_structure(psentence3, entities3)
    assert result3.time_type == "window", f"Expected 'window', got '{result3.time_type}'"
    
    print("✅ Exact vs range time: PASSED")


def test_shared_vs_per_service_time():
    """Test time scope determination."""
    
    # Shared time: comes after all services
    psentence1 = "book servicefamilytoken and servicefamilytoken at timetoken"
    entities1 = {
        "service_families": [
            {"text": "haircut", "start": 1, "end": 2},
            {"text": "beard trim", "start": 3, "end": 4}
        ],
        "dates": [],
        "times": [{"text": "9am", "start": 5, "end": 6}],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.time_scope == "shared", f"Expected 'shared', got '{result1.time_scope}'"
    
    # Per-service time: interleaved with services
    psentence2 = "book servicefamilytoken at timetoken and servicefamilytoken"
    entities2 = {
        "service_families": [
            {"text": "haircut", "start": 1, "end": 2},
            {"text": "beard trim", "start": 5, "end": 6}
        ],
        "dates": [],
        "times": [{"text": "9am", "start": 3, "end": 4}],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.time_scope == "per_service", f"Expected 'per_service', got '{result2.time_scope}'"
    
    print("✅ Shared vs per-service time: PASSED")


def test_duration_detection():
    """Test duration detection."""
    
    psentence = "book servicefamilytoken for durationtoken"
    entities = {
        "service_families": [{"text": "haircut"}],
        "dates": [],
        "times": [],
        "time_windows": [],
        "durations": [{"text": "one hour"}]
    }
    result = interpret_structure(psentence, entities)
    assert result.has_duration is True, "Should detect duration"
    
    print("✅ Duration detection: PASSED")


def test_clarification_needed():
    """Test clarification detection."""
    
    # Multiple dates without range marker
    psentence1 = "book servicefamilytoken datetoken datetoken"
    entities1 = {
        "service_families": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}, {"text": "next week"}],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.needs_clarification is True, "Should need clarification for multiple dates"
    
    # Multiple times without range marker
    psentence2 = "book servicefamilytoken timetoken timetoken"
    entities2 = {
        "service_families": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.needs_clarification is True, "Should need clarification for multiple times"
    
    print("✅ Clarification detection: PASSED")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("STRUCTURAL INTERPRETATION TEST SUITE")
    print("=" * 70)
    
    test_shared_vs_separate_services()
    test_exact_vs_range_time()
    test_shared_vs_per_service_time()
    test_duration_detection()
    test_clarification_needed()
    
    print("\n" + "=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()

