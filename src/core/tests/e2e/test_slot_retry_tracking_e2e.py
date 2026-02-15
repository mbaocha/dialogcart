"""
End-to-End Test: Slot Retry Tracking

Tests the complete slot_attempts lifecycle:
1. slot_attempts is created when clarification happens
2. Counter increments on repeated clarification of same slot
3. Only first missing slot increments
4. Counter resets when slot is filled
5. Feature does not break existing flow

This test validates the full flow from clarification → retry tracking → reset.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Set execution mode to test for deterministic tests
os.environ["CORE_EXECUTION_MODE"] = "test"

# Add src/ to Python path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Load environment variables
try:
    from dotenv import load_dotenv

    project_root = Path(__file__).parent.parent.parent.parent
    core_env_file = Path(__file__).parent.parent.parent / ".env"
    env_file = project_root / ".env"
    env_local_file = project_root / ".env.local"

    if env_file.exists():
        load_dotenv(env_file, override=False)
    if core_env_file.exists():
        load_dotenv(core_env_file, override=True)
    if env_local_file.exists():
        load_dotenv(env_local_file, override=True)
except ImportError:
    pass
except Exception:
    pass

from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session, save_session

# Import after path setup
from core.tests.integration.test_appointment_e2e import (
    TestCatalogClient,
    TestLumaClient,
    _setup_test_org_domain,
    get_customer_details,
)


def test_e2e_slot_retry_tracking_increment_and_reset():
    """
    E2E test: slot_attempts increment and reset for CREATE_APPOINTMENT.

    Flow:
    Turn 1: User says "book haircut"
        - Expected: status = NEEDS_CLARIFICATION
        - Expected: missing_slots contains ["date", "time"]
        - Expected: slot_attempts["date"] == 1 (first missing slot)
        - Expected: decision.facts contains slot_attempts

    Turn 2: User says "still thinking" (no new info)
        - Expected: status = NEEDS_CLARIFICATION
        - Expected: missing_slots still contains ["date", "time"]
        - Expected: slot_attempts["date"] == 2 (incremented)
        - Expected: slot_attempts does NOT contain "time" (only first slot increments)

    Turn 3: User says "tomorrow" (fills date)
        - Expected: status = NEEDS_CLARIFICATION
        - Expected: missing_slots contains ["time"]
        - Expected: slot_attempts["date"] removed (slot filled)
        - Expected: slot_attempts["time"] == 1 (new first missing slot)

    Turn 4: User says "2pm" (fills time)
        - Expected: status = READY
        - Expected: slot_attempts empty or key removed entirely
    """
    user_id = "test_e2e_slot_retry_tracking_001"
    domain = "service"

    # Clean up any existing session
    clear_session(user_id)

    # Set up test clients
    aliases = {"haircut": "beauty_and_wellness.haircut"}
    luma_client = TestLumaClient(test_aliases=aliases)
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache
    _setup_test_org_domain(domain)

    # Get customer details
    customer_details = get_customer_details()

    # Create session_store wrapper for handle_message
    class SessionStoreWrapper:
        def __init__(self, user_id):
            self.user_id = user_id

        def get_session(self, user_id):
            return get_session(user_id)

        def save_session(self, user_id, session_state):
            save_session(user_id, session_state)

    session_store = SessionStoreWrapper(user_id)

    # ============================================================================
    # TURN 1: User says "book haircut"
    # ============================================================================

    turn1_text = "book haircut"

    # Execute turn 1
    result_turn1 = handle_message(
        text=turn1_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 1 succeeded
    assert result_turn1 is not None, "Turn 1 result should not be None"
    assert result_turn1.get(
        "success"
    ), f"Turn 1 should succeed, got: {result_turn1.get('error')}"

    # Extract outcome
    outcome_turn1 = result_turn1.get("outcome", {})
    assert outcome_turn1 is not None, "Turn 1 outcome should not be None"

    # Assert: status is NEEDS_CLARIFICATION
    status_turn1 = outcome_turn1.get("status")
    assert (
        status_turn1 == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION, got {status_turn1}"

    # Assert: missing_slots contains date and time
    missing_slots_turn1 = outcome_turn1.get("missing_slots")
    if missing_slots_turn1 is None:
        # Try facts.missing_slots as fallback
        facts_turn1 = outcome_turn1.get("facts", {})
        missing_slots_turn1 = facts_turn1.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn1, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn1)}"
    assert (
        "date" in missing_slots_turn1
    ), f"Expected 'date' in missing_slots, got {missing_slots_turn1}"
    assert (
        "time" in missing_slots_turn1
    ), f"Expected 'time' in missing_slots, got {missing_slots_turn1}"

    # Assert: date is first missing slot (will be tracked)
    assert (
        missing_slots_turn1[0] == "date"
    ), f"Expected missing_slots[0]='date' (first slot tracked), got {missing_slots_turn1}"

    # Assert: slot_attempts is present in persisted session (source of truth)
    # Note: We check persisted session first, then verify decision.facts matches
    # Save session after turn 1
    merged_luma_response_turn1 = result_turn1.get("_merged_luma_response")
    previous_session_turn1 = get_session(user_id)  # May be None on first turn
    new_session_state_turn1 = build_session_state_from_outcome(
        outcome=outcome_turn1,
        outcome_status=status_turn1,
        merged_luma_response=merged_luma_response_turn1,
        previous_session_state=previous_session_turn1,
        user_id=user_id,
    )
    if new_session_state_turn1:
        save_session(user_id, new_session_state_turn1)

    # Verify session state after turn 1 (source of truth)
    session_after_turn1 = get_session(user_id)
    assert session_after_turn1 is not None, "Session should exist after turn 1"
    assert (
        session_after_turn1.get("status") == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION after turn 1, got {session_after_turn1.get('status')}"
    assert (
        "slot_attempts" in session_after_turn1
    ), f"Expected 'slot_attempts' in session after clarification, got keys: {list(session_after_turn1.keys())}"
    slot_attempts_session_turn1 = session_after_turn1.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_session_turn1, dict
    ), f"slot_attempts should be a dict, got {type(slot_attempts_session_turn1)}"
    assert (
        slot_attempts_session_turn1.get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 in session, got {slot_attempts_session_turn1.get('date')}"
    assert (
        "time" not in slot_attempts_session_turn1
    ), f"Expected 'time' NOT in slot_attempts (only first slot tracked), got {list(slot_attempts_session_turn1.keys())}"

    # Also verify decision.facts contains slot_attempts (for API access)
    decision_turn1 = result_turn1.get("_decision", {})
    facts_turn1 = (
        decision_turn1.get("facts", {})
        if decision_turn1
        else outcome_turn1.get("facts", {})
    )
    slot_attempts_decision_turn1 = facts_turn1.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_decision_turn1, dict
    ), f"slot_attempts should be a dict in decision.facts, got {type(slot_attempts_decision_turn1)}"
    assert (
        slot_attempts_decision_turn1.get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 in decision.facts, got {slot_attempts_decision_turn1.get('date')}"

    # ============================================================================
    # TURN 2: User says "still thinking" (no new slot info)
    # ============================================================================

    # Verify session state BEFORE turn 2
    session_before_turn2 = get_session(user_id)
    assert session_before_turn2 is not None, "Session should exist before turn 2"
    assert (
        session_before_turn2.get("slot_attempts", {}).get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 before turn 2, got {session_before_turn2.get('slot_attempts', {})}"

    turn2_text = "still thinking"

    # Execute turn 2
    result_turn2 = handle_message(
        text=turn2_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 2 succeeded
    assert result_turn2 is not None, "Turn 2 result should not be None"
    assert result_turn2.get(
        "success"
    ), f"Turn 2 should succeed, got: {result_turn2.get('error')}"

    # Extract outcome
    outcome_turn2 = result_turn2.get("outcome", {})
    assert outcome_turn2 is not None, "Turn 2 outcome should not be None"

    # Assert: status is still NEEDS_CLARIFICATION
    status_turn2 = outcome_turn2.get("status")
    assert (
        status_turn2 == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION, got {status_turn2}"

    # Assert: missing_slots still contains date and time
    missing_slots_turn2 = outcome_turn2.get("missing_slots")
    if missing_slots_turn2 is None:
        facts_turn2 = outcome_turn2.get("facts", {})
        missing_slots_turn2 = facts_turn2.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn2, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn2)}"
    assert (
        "date" in missing_slots_turn2
    ), f"Expected 'date' still in missing_slots, got {missing_slots_turn2}"
    assert (
        "time" in missing_slots_turn2
    ), f"Expected 'time' still in missing_slots, got {missing_slots_turn2}"

    # Save session after turn 2
    merged_luma_response_turn2 = result_turn2.get("_merged_luma_response")
    new_session_state_turn2 = build_session_state_from_outcome(
        outcome=outcome_turn2,
        outcome_status=status_turn2,
        merged_luma_response=merged_luma_response_turn2,
        previous_session_state=session_before_turn2,
        user_id=user_id,
    )
    if new_session_state_turn2:
        save_session(user_id, new_session_state_turn2)

    # Verify session state after turn 2 (source of truth)
    session_after_turn2 = get_session(user_id)
    assert session_after_turn2 is not None, "Session should exist after turn 2"
    slot_attempts_session_turn2 = session_after_turn2.get("slot_attempts", {})
    assert (
        slot_attempts_session_turn2.get("date") == 2
    ), f"Expected slot_attempts['date'] == 2 in session, got {slot_attempts_session_turn2.get('date')}"
    assert (
        "time" not in slot_attempts_session_turn2
    ), f"Expected 'time' NOT in slot_attempts (only first slot tracked), got {list(slot_attempts_session_turn2.keys())}"

    # Also verify decision.facts contains incremented slot_attempts
    decision_turn2 = result_turn2.get("_decision", {})
    facts_turn2 = (
        decision_turn2.get("facts", {})
        if decision_turn2
        else outcome_turn2.get("facts", {})
    )
    slot_attempts_decision_turn2 = facts_turn2.get("slot_attempts", {})
    assert (
        slot_attempts_decision_turn2.get("date") == 2
    ), f"Expected slot_attempts['date'] == 2 in decision.facts, got {slot_attempts_decision_turn2.get('date')}"

    # ============================================================================
    # TURN 3: User says "tomorrow" (fills date slot)
    # ============================================================================

    # Verify session state BEFORE turn 3
    session_before_turn3 = get_session(user_id)
    assert session_before_turn3 is not None, "Session should exist before turn 3"

    turn3_text = "tomorrow"

    # Execute turn 3
    result_turn3 = handle_message(
        text=turn3_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 3 succeeded
    assert result_turn3 is not None, "Turn 3 result should not be None"
    assert result_turn3.get(
        "success"
    ), f"Turn 3 should succeed, got: {result_turn3.get('error')}"

    # Extract outcome
    outcome_turn3 = result_turn3.get("outcome", {})
    assert outcome_turn3 is not None, "Turn 3 outcome should not be None"

    # Assert: status is NEEDS_CLARIFICATION (time still missing)
    status_turn3 = outcome_turn3.get("status")
    assert (
        status_turn3 == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION (time still missing), got {status_turn3}"

    # Assert: missing_slots contains only time (date is filled)
    missing_slots_turn3 = outcome_turn3.get("missing_slots")
    if missing_slots_turn3 is None:
        facts_turn3 = outcome_turn3.get("facts", {})
        missing_slots_turn3 = facts_turn3.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn3, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn3)}"
    assert (
        "date" not in missing_slots_turn3
    ), f"Expected 'date' NOT in missing_slots (slot filled), got {missing_slots_turn3}"
    assert (
        "time" in missing_slots_turn3
    ), f"Expected 'time' in missing_slots, got {missing_slots_turn3}"

    # Save session after turn 3
    merged_luma_response_turn3 = result_turn3.get("_merged_luma_response")
    new_session_state_turn3 = build_session_state_from_outcome(
        outcome=outcome_turn3,
        outcome_status=status_turn3,
        merged_luma_response=merged_luma_response_turn3,
        previous_session_state=session_before_turn3,
        user_id=user_id,
    )
    if new_session_state_turn3:
        save_session(user_id, new_session_state_turn3)

    # Verify session state after turn 3 (source of truth)
    session_after_turn3 = get_session(user_id)
    assert session_after_turn3 is not None, "Session should exist after turn 3"
    slot_attempts_session_turn3 = session_after_turn3.get("slot_attempts", {})
    assert (
        "date" not in slot_attempts_session_turn3
    ), f"Expected 'date' NOT in slot_attempts (slot filled, counter reset), got {list(slot_attempts_session_turn3.keys())}"
    assert (
        slot_attempts_session_turn3.get("time") == 1
    ), f"Expected slot_attempts['time'] == 1 in session, got {slot_attempts_session_turn3.get('time')}"

    # Also verify decision.facts contains slot_attempts with time tracked
    decision_turn3 = result_turn3.get("_decision", {})
    facts_turn3 = (
        decision_turn3.get("facts", {})
        if decision_turn3
        else outcome_turn3.get("facts", {})
    )
    slot_attempts_decision_turn3 = facts_turn3.get("slot_attempts", {})
    assert (
        "date" not in slot_attempts_decision_turn3
    ), f"Expected 'date' NOT in slot_attempts in decision.facts, got {list(slot_attempts_decision_turn3.keys())}"
    assert (
        slot_attempts_decision_turn3.get("time") == 1
    ), f"Expected slot_attempts['time'] == 1 in decision.facts, got {slot_attempts_decision_turn3.get('time')}"

    # ============================================================================
    # TURN 4: User says "2pm" (fills time slot)
    # ============================================================================

    # Verify session state BEFORE turn 4
    session_before_turn4 = get_session(user_id)
    assert session_before_turn4 is not None, "Session should exist before turn 4"

    turn4_text = "2pm"

    # Execute turn 4
    result_turn4 = handle_message(
        text=turn4_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 4 succeeded
    assert result_turn4 is not None, "Turn 4 result should not be None"
    assert result_turn4.get(
        "success"
    ), f"Turn 4 should succeed, got: {result_turn4.get('error')}"

    # Extract outcome
    outcome_turn4 = result_turn4.get("outcome", {})
    assert outcome_turn4 is not None, "Turn 4 outcome should not be None"

    # Assert: status is READY (all slots filled)
    status_turn4 = outcome_turn4.get("status")
    assert status_turn4 in (
        "READY",
        "AWAITING_CONFIRMATION",
    ), f"Expected status=READY or AWAITING_CONFIRMATION, got {status_turn4}"

    # Assert: missing_slots is empty
    missing_slots_turn4 = outcome_turn4.get("missing_slots")
    if missing_slots_turn4 is None:
        facts_turn4 = outcome_turn4.get("facts", {})
        missing_slots_turn4 = facts_turn4.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn4, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn4)}"
    assert (
        len(missing_slots_turn4) == 0
    ), f"Expected missing_slots to be empty (all slots filled), got {missing_slots_turn4}"

    # Save session after turn 4
    merged_luma_response_turn4 = result_turn4.get("_merged_luma_response")
    new_session_state_turn4 = build_session_state_from_outcome(
        outcome=outcome_turn4,
        outcome_status=status_turn4,
        merged_luma_response=merged_luma_response_turn4,
        previous_session_state=session_before_turn4,
        user_id=user_id,
    )
    if new_session_state_turn4:
        save_session(user_id, new_session_state_turn4)

    # Verify session state after turn 4
    session_after_turn4 = get_session(user_id)
    if session_after_turn4 is not None:
        slot_attempts_session_turn4 = session_after_turn4.get("slot_attempts", {})
        # slot_attempts should be empty or key removed when all slots are filled
        if slot_attempts_session_turn4:
            # If key exists, it should be empty dict
            assert (
                len(slot_attempts_session_turn4) == 0
            ), f"Expected slot_attempts to be empty when all slots filled, got {slot_attempts_session_turn4}"
        # Key may be removed entirely, which is also valid

    # ============================================================================
    # ADDITIONAL ASSERTIONS: Verify existing fields unchanged
    # ============================================================================

    # Verify intent_name is preserved throughout
    assert (
        outcome_turn1.get("intent_name") == "CREATE_APPOINTMENT"
        or outcome_turn1.get("intent") == "CREATE_APPOINTMENT"
    ), "Intent should be CREATE_APPOINTMENT"
    assert (
        outcome_turn4.get("intent_name") == "CREATE_APPOINTMENT"
        or outcome_turn4.get("intent") == "CREATE_APPOINTMENT"
    ), "Intent should be preserved as CREATE_APPOINTMENT"

    # Verify slots are collected correctly
    slots_turn4 = outcome_turn4.get("slots", {})
    if not slots_turn4:
        facts_turn4 = outcome_turn4.get("facts", {})
        slots_turn4 = facts_turn4.get("slots", {})
    assert (
        "service_id" in slots_turn4 or "service" in slots_turn4
    ), "Service slot should be collected"
    assert "date" in slots_turn4, "Date slot should be collected"
    assert "time" in slots_turn4, "Time slot should be collected"

    # Verify awaiting_slot logic not broken (should work as before)
    # awaiting_slot is optional and may or may not be present
    # Just verify it doesn't cause crashes

    # Clean up
    clear_session(user_id)


if __name__ == "__main__":
    # Allow running as standalone script
    test_e2e_slot_retry_tracking_increment_and_reset()
    print("✅ E2E test passed: slot retry tracking increment and reset")
