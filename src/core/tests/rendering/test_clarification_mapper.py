"""
Unit tests for clarification reason mapper.

Tests the mapping logic from decision/plan objects to clarification reasons.
"""

import pytest

from core.rendering.mapper.clarification_mapper import derive_clarification_reason


def test_derive_clarification_reason_returns_none_when_status_not_needs_clarification():
    """Test that function returns None when status is not NEEDS_CLARIFICATION."""
    decision = {"status": "READY", "missing_slots": ["time"]}
    result = derive_clarification_reason(decision)
    assert result is None


def test_derive_clarification_reason_returns_none_for_awaiting_confirmation():
    """Test that function returns None for AWAITING_CONFIRMATION status."""
    decision = {"status": "AWAITING_CONFIRMATION", "missing_slots": []}
    result = derive_clarification_reason(decision)
    assert result is None


def test_derive_clarification_reason_maps_time_to_missing_time():
    """Test that missing_slots=['time'] maps to MISSING_TIME."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["time"]}
    result = derive_clarification_reason(decision)
    assert result == "MISSING_TIME"


def test_derive_clarification_reason_maps_date_to_missing_date():
    """Test that missing_slots=['date'] maps to MISSING_DATE."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["date"]}
    result = derive_clarification_reason(decision)
    assert result == "MISSING_DATE"


def test_derive_clarification_reason_maps_empty_list_to_needs_clarification():
    """Test that missing_slots=[] maps to NEEDS_CLARIFICATION."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": []}
    result = derive_clarification_reason(decision)
    assert result == "NEEDS_CLARIFICATION"


def test_derive_clarification_reason_handles_missing_missing_slots():
    """Test that function handles missing missing_slots field."""
    decision = {"status": "NEEDS_CLARIFICATION"}
    result = derive_clarification_reason(decision)
    assert result == "NEEDS_CLARIFICATION"


def test_derive_clarification_reason_handles_non_list_missing_slots():
    """Test that function handles non-list missing_slots."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": "not a list"}
    result = derive_clarification_reason(decision)
    assert result == "NEEDS_CLARIFICATION"


def test_derive_clarification_reason_handles_multiple_missing_slots():
    """Test that function uses first missing slot for multiple missing slots."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["time", "date"]}
    result = derive_clarification_reason(decision)
    assert result == "MISSING_TIME"  # Uses first slot


def test_derive_clarification_reason_handles_other_missing_slots():
    """Test that function generates MISSING_<SLOT> for single missing slot."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["service"]}
    result = derive_clarification_reason(decision)
    assert result == "MISSING_SERVICE"


def test_derive_clarification_reason_smart_mapping_single_slot():
    """Test that function generates MISSING_<SLOT_NAME_UPPER> for any single missing slot."""
    # Test with various slot names
    test_cases = [
        ("location", "MISSING_LOCATION"),
        ("service_id", "MISSING_SERVICE_ID"),
        ("customer_name", "MISSING_CUSTOMER_NAME"),
        ("phone", "MISSING_PHONE"),
    ]

    for slot_name, expected_reason in test_cases:
        decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": [slot_name]}
        result = derive_clarification_reason(decision)
        assert (
            result == expected_reason
        ), f"Expected {expected_reason} for slot '{slot_name}', got {result}"


def test_derive_clarification_reason_uses_first_slot_for_multiple():
    """Test that function uses first missing slot when multiple slots are missing."""
    decision = {"status": "NEEDS_CLARIFICATION", "missing_slots": ["date", "time"]}
    result = derive_clarification_reason(decision)
    assert result == "MISSING_DATE"  # Uses first slot
