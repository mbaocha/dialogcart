"""
Unit tests for temporal slot normalization.

Tests that time expressions like "noon" are properly normalized to slots["time"]
before plan computation.
"""

import pytest
from core.orchestration.nlu.luma_response_processor import process_luma_response


def test_noon_normalization():
    """
    Test that "noon" is normalized to slots["time"] = "12:00" when time_constraint exists.
    
    Input: "noon" => slots["time"] == "12:00"
    """
    # Mock Luma response with time_constraint for "noon"
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut"
        },
        "context": {
            "time_constraint": {
                "start": "12:00",
                "mode": "exact"
            },
            "time_mode": "exact"
        },
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}]
        }
    }
    
    # Process response
    decision = process_luma_response(luma_response, "service", "test_user")
    
    # Verify time was normalized to slots
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    
    assert "time" in slots, f"Expected time in slots, got: {list(slots.keys())}"
    assert slots["time"] == "12:00", f"Expected time='12:00', got: {slots.get('time')}"


def test_noon_normalization_string():
    """
    Test that "noon" is normalized when time_constraint is a string.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut"
        },
        "context": {
            "time_constraint": "12:00"
        },
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}]
        }
    }
    
    decision = process_luma_response(luma_response, "service", "test_user")
    
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    
    assert "time" in slots, f"Expected time in slots, got: {list(slots.keys())}"
    assert slots["time"] == "12:00", f"Expected time='12:00', got: {slots.get('time')}"


def test_morning_normalization():
    """
    Test that "morning" is normalized when time_constraint exists.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut"
        },
        "context": {
            "time_constraint": {
                "start": "09:00",
                "mode": "window"
            },
            "time_mode": "window"
        },
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}]
        }
    }
    
    decision = process_luma_response(luma_response, "service", "test_user")
    
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    
    assert "time" in slots, f"Expected time in slots, got: {list(slots.keys())}"
    assert slots["time"] == "09:00", f"Expected time='09:00', got: {slots.get('time')}"


def test_time_already_in_slots():
    """
    Test that existing time in slots is not overwritten.
    """
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut",
            "time": "14:00"  # Already in slots
        },
        "context": {
            "time_constraint": {
                "start": "12:00",
                "mode": "exact"
            }
        },
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}]
        }
    }
    
    decision = process_luma_response(luma_response, "service", "test_user")
    
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    
    # Should preserve existing time, not overwrite with time_constraint
    assert slots["time"] == "14:00", f"Expected existing time='14:00' to be preserved, got: {slots.get('time')}"


def test_time_normalized_not_in_missing_slots():
    """
    Test that when time is present in slots (even if originally missing from raw_missing_slots),
    missing_slots must not include "time".
    
    This validates that missing_slots is recomputed from effective_collected_slots,
    so that normalized time (e.g., from context.time_constraint like "noon", "3pm")
    is not listed as missing.
    """
    # Mock Luma response where time_constraint exists but was originally "missing"
    # missing_slots originally might include "time", but after normalization it should not
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {
            "service_id": "haircut",
            "date": "2025-12-20"
            # time is NOT in slots initially - will be normalized from context
        },
        "context": {
            "time_constraint": {
                "start": "15:00",
                "mode": "exact"
            },
            "time_mode": "exact"
        },
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}]
        }
    }
    
    # Process response
    decision = process_luma_response(luma_response, "service", "test_user")
    
    # Verify time was normalized to slots
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})
    missing_slots = facts.get("missing_slots", [])
    
    # Time should be present in slots after normalization
    assert "time" in slots, f"Expected time in slots after normalization, got: {list(slots.keys())}"
    assert slots["time"] == "15:00", f"Expected time='15:00', got: {slots.get('time')}"
    
    # CRITICAL: missing_slots must NOT include "time" because it's present in effective_collected_slots
    assert "time" not in missing_slots, (
        f"Expected 'time' NOT in missing_slots (since it's present in slots), "
        f"but got missing_slots={missing_slots}, slots.keys()={list(slots.keys())}"
    )
    
    # Verify missing_slots only includes slots that are actually missing
    # For CREATE_APPOINTMENT, required slots are: service_id, date, time
    # After normalization, all three should be present, so missing_slots should be empty
    assert missing_slots == [], (
        f"Expected empty missing_slots (all required slots present), "
        f"got missing_slots={missing_slots}, slots={slots}"
    )
