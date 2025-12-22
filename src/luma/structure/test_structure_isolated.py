#!/usr/bin/env python3
"""
Isolated test cases for structural interpretation layer.
Tests without requiring full luma package dependencies.
"""

import sys
import importlib.util
from pathlib import Path

# Load modules directly
script_dir = Path(__file__).parent.resolve()

# Mock luma.config before importing
mock_config = type('MockConfig', (), {
    'DEBUG_ENABLED': False
})()
sys.modules['luma'] = type('MockLuma', (), {})()
sys.modules['luma.config'] = mock_config

# Load structure modules directly
types_path = script_dir / "structure_types.py"
spec_types = importlib.util.spec_from_file_location("luma.structure.structure_types", types_path)
types_module = importlib.util.module_from_spec(spec_types)
types_module.__package__ = "luma.structure"
types_module.__name__ = "luma.structure.structure_types"
sys.modules["luma.structure.structure_types"] = types_module
spec_types.loader.exec_module(types_module)

rules_path = script_dir / "rules.py"
spec_rules = importlib.util.spec_from_file_location("luma.structure.rules", rules_path)
rules_module = importlib.util.module_from_spec(spec_rules)
rules_module.__package__ = "luma.structure"
rules_module.__name__ = "luma.structure.rules"
sys.modules["luma.structure.rules"] = rules_module
spec_rules.loader.exec_module(rules_module)

interpreter_path = script_dir / "interpreter.py"
spec_interpreter = importlib.util.spec_from_file_location("luma.structure.interpreter", interpreter_path)
interpreter_module = importlib.util.module_from_spec(spec_interpreter)
interpreter_module.__package__ = "luma.structure"
interpreter_module.__name__ = "luma.structure.interpreter"
sys.modules["luma.structure.interpreter"] = interpreter_module
spec_interpreter.loader.exec_module(interpreter_module)

StructureResult = types_module.StructureResult
interpret_structure = interpreter_module.interpret_structure


def test_shared_vs_separate_services():
    """Test service scope determination."""
    
    # Shared services: joined by "and"
    psentence1 = "book servicefamilytoken and servicefamilytoken datetoken"
    entities1 = {
        "business_categories": [
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
    print("  [OK] Shared services detected")
    
    # Separate services: verb between them
    psentence2 = "book servicefamilytoken then book servicefamilytoken"
    entities2 = {
        "business_categories": [
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
    print("  [OK] Separate services detected")
    
    print("PASSED: Shared vs separate services\n")


def test_exact_vs_range_time():
    """Test time type determination."""
    
    # Exact time
    psentence1 = "book servicefamilytoken at timetoken"
    entities1 = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.time_type == "exact", f"Expected 'exact', got '{result1.time_type}'"
    print("  [OK] Exact time detected")
    
    # Time range
    psentence2 = "book servicefamilytoken between timetoken and timetoken"
    entities2 = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.time_type == "range", f"Expected 'range', got '{result2.time_type}'"
    print("  [OK] Time range detected")
    
    # Time window
    psentence3 = "book servicefamilytoken timewindowtoken"
    entities3 = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "times": [],
        "time_windows": [{"text": "morning"}],
        "durations": []
    }
    result3 = interpret_structure(psentence3, entities3)
    assert result3.time_type == "window", f"Expected 'window', got '{result3.time_type}'"
    print("  [OK] Time window detected")
    
    print("PASSED: Exact vs range time\n")


def test_shared_vs_per_service_time():
    """Test time scope determination."""
    
    # Shared time: comes after all services
    psentence1 = "book servicefamilytoken and servicefamilytoken at timetoken"
    entities1 = {
        "business_categories": [
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
    print("  [OK] Shared time scope detected")
    
    # Per-service time: interleaved with services
    psentence2 = "book servicefamilytoken at timetoken and servicefamilytoken"
    entities2 = {
        "business_categories": [
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
    print("  [OK] Per-service time scope detected")
    
    print("PASSED: Shared vs per-service time\n")


def test_duration_detection():
    """Test duration detection."""
    
    psentence = "book servicefamilytoken for durationtoken"
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "times": [],
        "time_windows": [],
        "durations": [{"text": "one hour"}]
    }
    result = interpret_structure(psentence, entities)
    assert result.has_duration is True, "Should detect duration"
    print("  [OK] Duration detected")
    
    print("PASSED: Duration detection\n")


def test_clarification_needed():
    """Test clarification detection."""
    
    # Multiple dates without range marker
    psentence1 = "book servicefamilytoken datetoken datetoken"
    entities1 = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}, {"text": "next week"}],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result1 = interpret_structure(psentence1, entities1)
    assert result1.needs_clarification is True, "Should need clarification for multiple dates"
    print("  [OK] Multiple dates clarification detected")
    
    # Multiple times without range marker
    psentence2 = "book servicefamilytoken timetoken timetoken"
    entities2 = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    result2 = interpret_structure(psentence2, entities2)
    assert result2.needs_clarification is True, "Should need clarification for multiple times"
    print("  [OK] Multiple times clarification detected")
    
    print("PASSED: Clarification detection\n")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("STRUCTURAL INTERPRETATION TEST SUITE")
    print("=" * 70)
    print()
    
    test_shared_vs_separate_services()
    test_exact_vs_range_time()
    test_shared_vs_per_service_time()
    test_duration_detection()
    test_clarification_needed()
    
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()

