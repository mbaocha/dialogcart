"""
Isolated unit tests for awaiting_slot prioritization in planning.

These tests verify that awaiting_slot correctly prioritizes missing_slots
without relying on the scenario engine or full handle_message flows.
"""

from typing import Any, Dict, List, Optional

from core.planning.turn_state import finalize_turn_state


def _prioritize_awaiting_slot(
    missing_slots: List[str], session_state: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Helper that mirrors turn_state._prioritize_awaiting_slot for isolated tests.
    """
    if session_state and isinstance(session_state, dict):
        awaiting_slot = session_state.get("awaiting_slot")
        if awaiting_slot is not None and awaiting_slot in missing_slots:
            missing_slots = [s for s in missing_slots if s != awaiting_slot]
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
    intent_name = "CREATE_APPOINTMENT"
    collected_slots = {
        "service_id": "haircut"
        # Missing: date, time
    }
    session_state = {
        "awaiting_slot": "time",
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
    }

    turn_state = finalize_turn_state(
        intent_name=intent_name,
        merged_session_slots=collected_slots,
        planning_context={"awaiting_slot": session_state.get("awaiting_slot")},
    )
    result = turn_state.get("missing_slots") or []

    assert result[0] == "time", f"Expected 'time' at index 0, got {result[0]}"
    assert set(result) == {
        "date",
        "time",
    }, f"Expected set {{'date', 'time'}}, got {set(result)}"
    assert len(result) == 2, f"Expected length 2, got {len(result)}"
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
    intent_name = "CREATE_APPOINTMENT"
    collected_slots = {
        "service_id": "haircut"
        # Missing: date, time
    }

    turn_state = finalize_turn_state(
        intent_name=intent_name,
        merged_session_slots=collected_slots,
        planning_context={},
    )
    result = turn_state.get("missing_slots") or []

    assert result == sorted(result), f"Expected sorted list, got {result}"
    assert result[0] == "date", f"Expected 'date' at index 0, got {result[0]}"
    assert result[1] == "time", f"Expected 'time' at index 1, got {result[1]}"
    assert set(result) == {
        "date",
        "time",
    }, f"Expected set {{'date', 'time'}}, got {set(result)}"


def test_awaiting_slot_not_in_missing_slots():
    """
    Test that awaiting_slot has no effect when it's not in missing_slots.

    Scenario:
    - missing_slots = ["date"]
    - awaiting_slot = "time" (not in missing_slots)

    Expected:
    - No change to missing_slots
    """
    missing_slots = ["date"]
    session_state = {"awaiting_slot": "time", "intent_name": "CREATE_APPOINTMENT"}

    result = _prioritize_awaiting_slot(missing_slots, session_state)

    assert (
        result == missing_slots
    ), f"Expected no change, got {result} != {missing_slots}"
    assert result == ["date"], f"Expected ['date'], got {result}"


def test_awaiting_slot_with_multiple_missing_slots():
    """
    Test that awaiting_slot prioritization works with multiple missing slots.

    Scenario:
    - missing_slots = ["service_id", "date", "time"] (policy order)
    - awaiting_slot = "time"

    Expected:
    - result[0] == "time"
    - Remaining slots preserve order: ["service_id", "date"]
    """
    missing_slots = ["service_id", "date", "time"]
    session_state = {"awaiting_slot": "time", "intent_name": "CREATE_APPOINTMENT"}

    result = _prioritize_awaiting_slot(missing_slots, session_state)

    assert result[0] == "time", f"Expected 'time' at index 0, got {result[0]}"
    assert set(result) == {
        "date",
        "service_id",
        "time",
    }, f"Expected all slots, got {set(result)}"
    assert result[1:] == [
        "service_id",
        "date",
    ], f"Expected ['service_id', 'date'], got {result[1:]}"


def test_awaiting_slot_with_none_session_state():
    """
    Test that behavior is unchanged when session_state is None.

    Scenario:
    - missing_slots = ["date", "time"]
    - session_state = None

    Expected:
    - No change to missing_slots
    """
    missing_slots = ["date", "time"]

    result = _prioritize_awaiting_slot(missing_slots, None)

    assert (
        result == missing_slots
    ), f"Expected no change, got {result} != {missing_slots}"
    assert result == sorted(result), f"Expected sorted, got {result}"


def test_build_decision_plan_exposes_prioritized_missing_slots():
    """build_decision_plan must return awaiting_slot-prioritized missing_slots on the plan."""
    from core.tests.harness.planning_compat import build_decision_plan

    session_state = {
        "awaiting_slot": "service_id",
        "intent_name": "CREATE_RESERVATION",
        "status": "NEEDS_CLARIFICATION",
    }
    luma_response = {
        "intent": {"name": "CREATE_RESERVATION"},
        "missing_slots": ["date_range", "service_id"],
        "needs_clarification": True,
        "slots": {"service_id": None, "booking_id": None},
    }

    plan = build_decision_plan(
        "CREATE_RESERVATION",
        luma_response,
        domain="reservation",
        session_state=session_state,
    )

    assert plan.get("missing_slots") == [
        "service_id",
        "date_range",
    ], f"Expected prioritized missing_slots on plan, got {plan.get('missing_slots')}"
