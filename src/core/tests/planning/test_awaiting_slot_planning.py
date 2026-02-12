"""
Isolated unit tests for awaiting_slot prioritization in planning.

These tests verify that awaiting_slot correctly prioritizes missing_slots
without relying on the scenario engine or full integration tests.
"""

import pytest
from typing import Dict, Any, Optional, List
from core.planning.orchestration.missing_slots import compute_missing_slots


def _prioritize_awaiting_slot(
    missing_slots: List[str],
    session_state: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Helper function that implements the awaiting_slot prioritization logic.
    
    This mirrors the logic in build_decision_plan to allow isolated testing.
    
    Args:
        missing_slots: List of missing slot names
        session_state: Optional session state containing awaiting_slot
        
    Returns:
        List with awaiting_slot at index 0 if present, otherwise unchanged
    """
    if session_state and isinstance(session_state, dict):
        awaiting_slot = session_state.get("awaiting_slot")
        # If awaiting_slot exists AND is in missing_slots, move it to index 0
        if awaiting_slot is not None and awaiting_slot in missing_slots:
            # Remove awaiting_slot from its current position
            missing_slots = [s for s in missing_slots if s != awaiting_slot]
            # Insert at index 0, preserving the rest of the order
            missing_slots.insert(0, awaiting_slot)
    
    return missing_slots


def test_missing_slots_prioritized_when_awaiting_slot_present():
    """
    Test that missing_slots are prioritized when awaiting_slot is present.
    
    Scenario:
    - Intent requires ["date", "time"]
    - Both slots are missing
    - awaiting_slot="time"
    
    Expected:
    - result[0] == "time"
    - set(result) == {"date", "time"}
    """
    # Setup: intent that requires date and time
    intent_name = "CREATE_APPOINTMENT"
    
    # Setup: collected_slots missing both date and time
    collected_slots = {
        "service_id": "haircut"
        # Missing: date, time
    }
    
    # Setup: session_state with awaiting_slot="time"
    session_state = {
        "awaiting_slot": "time",
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION"
    }
    
    # Execute: compute missing slots
    missing_slots = compute_missing_slots(
        intent_name=intent_name,
        collected_slots=collected_slots,
        session_state=session_state
    )
    
    # Apply prioritization (mirrors logic in build_decision_plan)
    result = _prioritize_awaiting_slot(missing_slots, session_state)
    
    # Assert: awaiting_slot is at index 0
    assert result[0] == "time", f"Expected 'time' at index 0, got {result[0]}"
    
    # Assert: all slots are present (set equality)
    assert set(result) == {"date", "time"}, f"Expected set {{'date', 'time'}}, got {set(result)}"
    
    # Assert: result has correct length
    assert len(result) == 2, f"Expected length 2, got {len(result)}"
    
    # Assert: remaining slots are in original order (date should be at index 1)
    assert result[1] == "date", f"Expected 'date' at index 1, got {result[1]}"


def test_no_behavior_change_when_awaiting_slot_absent():
    """
    Test that behavior remains unchanged when awaiting_slot is absent.
    
    Scenario:
    - Intent requires ["date", "time"]
    - Both slots are missing
    - No awaiting_slot in session_state
    
    Expected:
    - result == sorted(result) (alphabetical order)
    - Ordering remains alphabetical
    """
    # Setup: intent that requires date and time
    intent_name = "CREATE_APPOINTMENT"
    
    # Setup: collected_slots missing both date and time
    collected_slots = {
        "service_id": "haircut"
        # Missing: date, time
    }
    
    # Setup: session_state without awaiting_slot
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION"
        # No awaiting_slot field
    }
    
    # Execute: compute missing slots
    result = compute_missing_slots(
        intent_name=intent_name,
        collected_slots=collected_slots,
        session_state=session_state
    )
    
    # Apply prioritization (should have no effect since awaiting_slot is absent)
    result = _prioritize_awaiting_slot(result, session_state)
    
    # Assert: result is sorted (alphabetical order preserved)
    assert result == sorted(result), f"Expected sorted list, got {result}"
    
    # Assert: alphabetical order (date comes before time)
    assert result[0] == "date", f"Expected 'date' at index 0, got {result[0]}"
    assert result[1] == "time", f"Expected 'time' at index 1, got {result[1]}"
    
    # Assert: all required slots are present
    assert set(result) == {"date", "time"}, f"Expected set {{'date', 'time'}}, got {set(result)}"


def test_awaiting_slot_not_in_missing_slots():
    """
    Test that awaiting_slot has no effect when it's not in missing_slots.
    
    Scenario:
    - missing_slots = ["date"]
    - awaiting_slot = "time" (not in missing_slots)
    
    Expected:
    - No change to missing_slots
    """
    # Setup: missing_slots with only date
    missing_slots = ["date"]
    
    # Setup: session_state with awaiting_slot="time" (not in missing_slots)
    session_state = {
        "awaiting_slot": "time",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    # Execute: prioritize awaiting_slot
    result = _prioritize_awaiting_slot(missing_slots, session_state)
    
    # Assert: no change (awaiting_slot not in missing_slots)
    assert result == missing_slots, f"Expected no change, got {result} != {missing_slots}"
    assert result == ["date"], f"Expected ['date'], got {result}"


def test_awaiting_slot_with_multiple_missing_slots():
    """
    Test that awaiting_slot prioritization works with multiple missing slots.
    
    Scenario:
    - missing_slots = ["date", "service_id", "time"]
    - awaiting_slot = "time"
    
    Expected:
    - result[0] == "time"
    - Remaining slots preserve order: ["date", "service_id"]
    """
    # Setup: missing_slots with multiple slots
    missing_slots = ["date", "service_id", "time"]
    
    # Setup: session_state with awaiting_slot="time"
    session_state = {
        "awaiting_slot": "time",
        "intent_name": "CREATE_APPOINTMENT"
    }
    
    # Execute: prioritize awaiting_slot
    result = _prioritize_awaiting_slot(missing_slots, session_state)
    
    # Assert: awaiting_slot is at index 0
    assert result[0] == "time", f"Expected 'time' at index 0, got {result[0]}"
    
    # Assert: all slots are present
    assert set(result) == {"date", "service_id", "time"}, f"Expected all slots, got {set(result)}"
    
    # Assert: remaining slots preserve order
    assert result[1:] == ["date", "service_id"], f"Expected ['date', 'service_id'], got {result[1:]}"


def test_awaiting_slot_with_none_session_state():
    """
    Test that behavior is unchanged when session_state is None.
    
    Scenario:
    - missing_slots = ["date", "time"]
    - session_state = None
    
    Expected:
    - No change to missing_slots
    """
    # Setup: missing_slots
    missing_slots = ["date", "time"]
    
    # Execute: prioritize with None session_state
    result = _prioritize_awaiting_slot(missing_slots, None)
    
    # Assert: no change
    assert result == missing_slots, f"Expected no change, got {result} != {missing_slots}"
    assert result == sorted(result), f"Expected sorted, got {result}"

