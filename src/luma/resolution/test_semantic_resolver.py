#!/usr/bin/env python3
"""
Test cases for semantic resolver.

Tests semantic resolution for various time/date scenarios.
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

# Load semantic_resolver module
resolver_path = script_dir / "semantic_resolver.py"
spec_resolver = importlib.util.spec_from_file_location("luma.resolution.semantic_resolver", resolver_path)
resolver_module = importlib.util.module_from_spec(spec_resolver)
resolver_module.__package__ = "luma.resolution"
resolver_module.__name__ = "luma.resolution.semantic_resolver"
sys.modules["luma.resolution.semantic_resolver"] = resolver_module
spec_resolver.loader.exec_module(resolver_module)

resolve_semantics = resolver_module.resolve_semantics
SemanticResolutionResult = resolver_module.SemanticResolutionResult


def test_exact_time():
    """Test exact time resolution."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "9am",
            "duration": None
        },
        "structure": {
            "time_type": "exact",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "9am"}],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["time_mode"] == "exact"
    assert result.resolved_booking["time_refs"] == ["9am"]
    assert result.resolved_booking["date_mode"] == "single_day"
    assert not result.needs_clarification
    
    print("  [OK] Exact time resolution: PASSED")


def test_time_window():
    """Test time window resolution."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "morning",
            "duration": None
        },
        "structure": {
            "time_type": "window",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [{"text": "morning"}],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["time_mode"] == "window"
    assert result.resolved_booking["time_refs"] == ["morning"]
    assert not result.needs_clarification
    
    print("  [OK] Time window resolution: PASSED")


def test_time_range():
    """Test time range resolution."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "9am to 5pm",
            "duration": None
        },
        "structure": {
            "time_type": "range",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["time_mode"] == "range"
    assert len(result.resolved_booking["time_refs"]) == 2
    assert "9am" in result.resolved_booking["time_refs"]
    assert "5pm" in result.resolved_booking["time_refs"]
    assert not result.needs_clarification
    
    print("  [OK] Time range resolution: PASSED")


def test_window_plus_exact_time():
    """Test window + exact time - exact time wins, window is discarded."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "9am",
            "duration": None
        },
        "structure": {
            "time_type": "exact",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "9am"}],
        "time_windows": [{"text": "morning"}],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    # Exact time takes precedence, window is discarded
    assert result.resolved_booking["time_mode"] == "exact"
    assert result.resolved_booking["time_refs"] == ["9am"]  # Only exact time, no window
    assert "morning" not in result.resolved_booking["time_refs"]
    assert not result.needs_clarification  # Should not need clarification
    
    print("  [OK] Window + exact time (exact wins): PASSED")


def test_exact_time_with_dot_separator():
    """Test exact time with dot separator (e.g., '10.30')."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "10.30",
            "duration": None
        },
        "structure": {
            "time_type": "exact",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "10.30"}],
        "time_windows": [{"text": "morning"}],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    # Exact time (10.30) should win over morning window
    assert result.resolved_booking["time_mode"] == "exact"
    assert result.resolved_booking["time_refs"] == ["10.30"]  # Only exact time
    assert "morning" not in result.resolved_booking["time_refs"]
    assert not result.needs_clarification
    
    print("  [OK] Exact time with dot separator (10.30): PASSED")


def test_absolute_date_precedence():
    """Test absolute date takes precedence over relative."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "15th dec",
            "time_ref": "9am",
            "duration": None
        },
        "structure": {
            "time_type": "exact",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [{"text": "15th dec"}],
        "times": [{"text": "9am"}],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["date_mode"] == "single_day"
    assert result.resolved_booking["date_refs"] == ["15th dec"]
    assert not result.needs_clarification
    
    print("  [OK] Absolute date precedence: PASSED")


def test_conflicting_dates():
    """Test conflicting dates detection."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": None,
            "duration": None
        },
        "structure": {
            "time_type": "none",
            "needs_clarification": True  # Structure flags ambiguity
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}, {"text": "next week"}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason is not None
    
    print("  [OK] Conflicting dates detection: PASSED")


def test_multiple_times_without_range():
    """Test multiple times without range marker."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "9am",
            "duration": None
        },
        "structure": {
            "time_type": "exact",  # Not "range"
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "9am"}, {"text": "5pm"}],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason.value == "AMBIGUOUS_TIME_NO_WINDOW"
    
    print("  [OK] Multiple times without range: PASSED")


def test_date_range():
    """Test date range resolution."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "8th dec to 15th dec",
            "time_ref": None,
            "duration": None
        },
        "structure": {
            "time_type": "none",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "dates_absolute": [{"text": "8th dec"}, {"text": "15th dec"}],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["date_mode"] == "range"
    assert len(result.resolved_booking["date_refs"]) == 2
    assert not result.needs_clarification
    
    print("  [OK] Date range resolution: PASSED")


def test_duration_preservation():
    """Test duration is preserved."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": "tomorrow",
            "time_ref": "9am",
            "duration": {"text": "one hour"}
        },
        "structure": {
            "time_type": "exact",
            "has_duration": True,
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "tomorrow"}],
        "dates_absolute": [],
        "times": [{"text": "9am"}],
        "time_windows": [],
        "durations": [{"text": "one hour"}]
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["duration"] is not None
    assert result.resolved_booking["duration"]["text"] == "one hour"
    
    print("  [OK] Duration preservation: PASSED")


def test_no_time_no_date():
    """Test flexible mode when no time/date constraints."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {
            "services": [{"text": "haircut"}],
            "date_ref": None,
            "time_ref": None,
            "duration": None
        },
        "structure": {
            "time_type": "none",
            "needs_clarification": False
        }
    }
    
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "dates_absolute": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    
    result = resolve_semantics(intent_result, entities)
    
    assert result.resolved_booking["time_mode"] == "none"
    assert result.resolved_booking["date_mode"] == "flexible"
    assert not result.needs_clarification
    
    print("  [OK] No time/no date (flexible): PASSED")


# ============================================================================
# NEW DATE RESOLUTION TESTS - Hardened Date Interpretation
# ============================================================================

def test_simple_relative_days():
    """Test simple relative days: today, tomorrow, day after tomorrow, tonight."""
    test_cases = [
        ("today", "single_day"),
        ("tomorrow", "single_day"),
        ("day after tomorrow", "single_day"),
        ("tonight", "single_day"),
    ]
    
    for date_text, expected_mode in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == expected_mode
        assert not result.needs_clarification, f"Should not need clarification for '{date_text}'"
    
    print("  [OK] Simple relative days: PASSED")


def test_week_based():
    """Test week-based: this week, next week → range."""
    test_cases = ["this week", "next week"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "range"
        assert not result.needs_clarification
    
    print("  [OK] Week-based (range): PASSED")


def test_weekend_references():
    """Test weekend references: this weekend, next weekend → range."""
    test_cases = ["this weekend", "next weekend"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "range"
        assert not result.needs_clarification
    
    print("  [OK] Weekend references (range): PASSED")


def test_specific_weekdays():
    """Test specific weekdays: this Monday, next Monday, coming Friday → single_day."""
    test_cases = ["this Monday", "next Monday", "coming Friday", "this Friday"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "single_day"
        assert not result.needs_clarification
    
    print("  [OK] Specific weekdays (single_day): PASSED")


def test_month_relative():
    """Test month-relative: this month, next month → range."""
    test_cases = ["this month", "next month"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "range"
        assert not result.needs_clarification
    
    print("  [OK] Month-relative (range): PASSED")


def test_calendar_dates():
    """Test calendar dates: 15th Dec, Dec 15, 12 July → single_day."""
    test_cases = ["15th Dec", "Dec 15", "12 July", "15th December"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [],
            "dates_absolute": [{"text": date_text}],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "single_day"
        # Note: locale-ambiguous dates like 07/12 will need clarification (tested separately)
    
    print("  [OK] Calendar dates (single_day): PASSED")


def test_locale_ambiguous_date():
    """Test locale-ambiguous dates: 07/12 → needs_clarification."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {"services": [{"text": "haircut"}]},
        "structure": {"needs_clarification": False}
    }
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [],
        "dates_absolute": [{"text": "07/12"}],  # Could be July 12 or Dec 7
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result = resolve_semantics(intent_result, entities)
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason.value == "LOCALE_AMBIGUOUS_DATE"
    
    print("  [OK] Locale-ambiguous date (clarification): PASSED")


def test_plural_weekday():
    """Test plural weekdays: next Mondays → needs_clarification."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {"services": [{"text": "haircut"}]},
        "structure": {"needs_clarification": False}
    }
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "next Mondays"}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result = resolve_semantics(intent_result, entities)
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason.value == "AMBIGUOUS_PLURAL_WEEKDAY"
    
    print("  [OK] Plural weekday (clarification): PASSED")


def test_vague_date_reference():
    """Test vague date references: sometime soon → needs_clarification."""
    test_cases = ["sometime soon", "later", "whenever you're free"]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.needs_clarification is True
        assert result.clarification is not None
        assert result.clarification.reason.value == "VAGUE_DATE_REFERENCE"
    
    print("  [OK] Vague date reference (clarification): PASSED")


def test_context_dependent_date():
    """Test context-dependent dates: Thursday just gone → needs_clarification."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {"services": [{"text": "haircut"}]},
        "structure": {"needs_clarification": False}
    }
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "Thursday just gone"}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result = resolve_semantics(intent_result, entities)
    assert result.needs_clarification is True
    assert result.clarification is not None
    assert result.clarification.reason.value == "CONTEXT_DEPENDENT_DATE"
    
    print("  [OK] Context-dependent date (clarification): PASSED")


def test_fine_grained_modifiers():
    """Test fine-grained modifiers: early next week → range (never single_day)."""
    test_cases = [
        "early next week",
        "mid next week",
        "end of next week",
        "early next month",
        "mid next month",
        "end of next month"
    ]
    
    for date_text in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == "range", f"'{date_text}' should resolve to range, not single_day"
        # Should not need clarification (range is acceptable)
    
    print("  [OK] Fine-grained modifiers (range only): PASSED")


def test_simple_date_ranges():
    """Test simple date ranges: between Monday and Wednesday → range."""
    intent_result = {
        "intent": "CREATE_BOOKING",
        "booking": {"services": [{"text": "haircut"}]},
        "structure": {"date_type": "range", "needs_clarification": False}
    }
    entities = {
        "business_categories": [{"text": "haircut"}],
        "dates": [{"text": "Monday"}, {"text": "Wednesday"}],
        "dates_absolute": [],
        "times": [],
        "time_windows": [],
        "durations": []
    }
    result = resolve_semantics(intent_result, entities)
    assert result.resolved_booking["date_mode"] == "range"
    assert len(result.resolved_booking["date_refs"]) == 2
    assert not result.needs_clarification
    
    print("  [OK] Simple date ranges: PASSED")


def test_misspellings():
    """Test misspellings: tomorow, tomrw, nxt week → normalized and resolved."""
    test_cases = [
        ("tomorow", "single_day"),
        ("tomrw", "single_day"),
        ("nxt week", "range"),
    ]
    
    for date_text, expected_mode in test_cases:
        intent_result = {
            "intent": "CREATE_BOOKING",
            "booking": {"services": [{"text": "haircut"}]},
            "structure": {"needs_clarification": False}
        }
        entities = {
            "business_categories": [{"text": "haircut"}],
            "dates": [{"text": date_text}],
            "dates_absolute": [],
            "times": [],
            "time_windows": [],
            "durations": []
        }
        result = resolve_semantics(intent_result, entities)
        assert result.resolved_booking["date_mode"] == expected_mode
        assert not result.needs_clarification
    
    print("  [OK] Misspellings (normalized): PASSED")


def main():
    """Run all test cases."""
    print("=" * 70)
    print("SEMANTIC RESOLVER TEST SUITE")
    print("=" * 70)
    print()
    
    test_exact_time()
    test_time_window()
    test_time_range()
    test_window_plus_exact_time()
    test_exact_time_with_dot_separator()
    test_absolute_date_precedence()
    test_conflicting_dates()
    test_multiple_times_without_range()
    test_date_range()
    test_duration_preservation()
    test_no_time_no_date()
    
    # New hardened date interpretation tests
    print()
    print("=" * 70)
    print("HARDENED DATE INTERPRETATION TESTS")
    print("=" * 70)
    print()
    
    test_simple_relative_days()
    test_week_based()
    test_weekend_references()
    test_specific_weekdays()
    test_month_relative()
    test_calendar_dates()
    test_locale_ambiguous_date()
    test_plural_weekday()
    test_vague_date_reference()
    test_context_dependent_date()
    test_fine_grained_modifiers()
    test_simple_date_ranges()
    test_misspellings()
    
    print()
    print("=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()

