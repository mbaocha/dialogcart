"""
End-to-End Test: Adaptive Clarification Rendering

Tests that clarification templates adapt based on slot_attempts:
1. First clarification → base template
2. Second clarification (same missing slot) → variant template (ATTEMPT_2)
3. Status remains NEEDS_CLARIFICATION (planner unchanged)
4. Session state unchanged except slot_attempts
5. Rendering layer is truly downstream (no planner dependency)

This test validates the full flow from clarification → adaptive rendering → retry.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

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
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.org_setup import get_customer_details, setup_test_org_domain


def test_adaptive_clarification_variant_after_retry():
    """
    E2E test: Adaptive clarification rendering based on slot_attempts.

    Flow:
    Turn 1: User says "book haircut"
        - Expected: status = NEEDS_CLARIFICATION
        - Expected: missing_slots contains ["date", "time"]
        - Expected: slot_attempts["date"] == 1
        - Expected: text uses base template (MISSING_DATE)
        - Expected: text does NOT contain "still" (variant indicator)

    Turn 2: User says "still thinking" (irrelevant response, doesn't fill date)
        - Expected: status = NEEDS_CLARIFICATION (planner unchanged)
        - Expected: missing_slots still contains ["date", "time"]
        - Expected: slot_attempts["date"] == 2 (incremented)
        - Expected: text uses variant template (MISSING_DATE_ATTEMPT_2)
        - Expected: text contains "still" (variant indicator)
        - Expected: text is different from turn 1
    """
    user_id = "test_adaptive_clarification_001"
    domain = "service"

    # Clean up any existing session
    clear_session(user_id)

    # Set up test clients
    aliases = {"haircut": "beauty_and_wellness.haircut"}
    luma_client = TestLumaClient(test_aliases=aliases)
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache
    setup_test_org_domain(domain)

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
    result1 = handle_message(
        text=turn1_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 1 succeeded
    assert result1 is not None, "Turn 1 result should not be None"
    assert result1.get("success"), f"Turn 1 should succeed, got: {result1.get('error')}"

    # Extract outcome
    outcome1 = result1.get("outcome", {})
    assert outcome1 is not None, "Turn 1 outcome should not be None"

    # Assert: status is NEEDS_CLARIFICATION
    status1 = outcome1.get("status")
    assert (
        status1 == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION, got {status1}"

    # Assert: missing_slots contains date and time
    missing_slots1 = outcome1.get("missing_slots")
    if missing_slots1 is None:
        facts1 = outcome1.get("facts", {})
        missing_slots1 = facts1.get("missing_slots", [])

    assert isinstance(
        missing_slots1, list
    ), f"missing_slots should be a list, got {type(missing_slots1)}"
    assert (
        "date" in missing_slots1
    ), f"Expected 'date' in missing_slots, got {missing_slots1}"
    assert (
        "time" in missing_slots1
    ), f"Expected 'time' in missing_slots, got {missing_slots1}"

    # Assert: date is first missing slot (will be tracked)
    assert (
        missing_slots1[0] == "date"
    ), f"Expected missing_slots[0]='date' (first slot tracked), got {missing_slots1}"

    # Save session after turn 1
    merged_luma_response1 = result1.get("_merged_luma_response")
    previous_session1 = get_session(user_id)
    new_session_state1 = build_session_state_from_outcome(
        outcome=outcome1,
        outcome_status=status1,
        merged_luma_response=merged_luma_response1,
        previous_session_state=previous_session1,
        user_id=user_id,
    )
    if new_session_state1:
        save_session(user_id, new_session_state1)

    # Verify slot_attempts in session (source of truth)
    session_after_turn1 = get_session(user_id)
    assert session_after_turn1 is not None, "Session should exist after turn 1"
    slot_attempts_session1 = session_after_turn1.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_session1, dict
    ), f"slot_attempts should be a dict, got {type(slot_attempts_session1)}"
    assert (
        slot_attempts_session1.get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 in session, got {slot_attempts_session1.get('date')}"

    # Verify slot_attempts in decision.facts (for rendering access)
    decision1 = result1.get("_decision", {})
    facts1_decision = (
        decision1.get("facts", {}) if decision1 else outcome1.get("facts", {})
    )
    slot_attempts_decision1 = facts1_decision.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_decision1, dict
    ), f"slot_attempts should be a dict in decision.facts, got {type(slot_attempts_decision1)}"
    assert (
        slot_attempts_decision1.get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 in decision.facts, got {slot_attempts_decision1.get('date')}"

    # Assert: text is present and uses base template
    assert (
        "text" in result1
    ), f"Expected clarification text to be present, but 'text' not in result. Keys: {list(result1.keys())}"
    assert result1[
        "text"
    ], f"Expected clarification text to be non-empty, got: {result1.get('text')}"

    text1 = result1["text"].lower()

    # Should use base template (MISSING_DATE)
    assert (
        "date" in text1 or "day" in text1
    ), f"Expected text to mention 'date' or 'day', got: {result1['text']}"

    # Should NOT contain "still" (variant indicator)
    assert (
        "still" not in text1
    ), f"Expected base template (no 'still'), got: {result1['text']}"

    # ============================================================================
    # TURN 2: User says "still thinking" (irrelevant response, doesn't fill date)
    # ============================================================================

    # Verify session state BEFORE turn 2
    session_before_turn2 = get_session(user_id)
    assert session_before_turn2 is not None, "Session should exist before turn 2"
    assert (
        session_before_turn2.get("slot_attempts", {}).get("date") == 1
    ), f"Expected slot_attempts['date'] == 1 before turn 2, got {session_before_turn2.get('slot_attempts', {})}"

    turn2_text = "still thinking"

    # Execute turn 2
    result2 = handle_message(
        text=turn2_text,
        user_id=user_id,
        luma_client=luma_client,
        organization_client=None,
        session_store=session_store,
    )

    # Assert turn 2 succeeded
    assert result2 is not None, "Turn 2 result should not be None"
    assert result2.get("success"), f"Turn 2 should succeed, got: {result2.get('error')}"

    # Extract outcome
    outcome2 = result2.get("outcome", {})
    assert outcome2 is not None, "Turn 2 outcome should not be None"

    # 🛡 Safety Assertion: Status remains NEEDS_CLARIFICATION (planner unchanged)
    status2 = outcome2.get("status")
    assert (
        status2 == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION (planner unchanged), got {status2}"

    # 🛡 Safety Assertion: Verify decision plan status (rendering did not alter planner)
    decision2 = result2.get("_decision", {})
    if decision2:
        plan2 = decision2.get("plan", {})
        plan_status2 = plan2.get("status")
        assert (
            plan_status2 == "NEEDS_CLARIFICATION"
        ), f"Expected decision.plan.status=NEEDS_CLARIFICATION (rendering did not alter planner), got {plan_status2}"

    # Assert: missing_slots still contains date and time
    missing_slots2 = outcome2.get("missing_slots")
    if missing_slots2 is None:
        facts2 = outcome2.get("facts", {})
        missing_slots2 = facts2.get("missing_slots", [])

    assert isinstance(
        missing_slots2, list
    ), f"missing_slots should be a list, got {type(missing_slots2)}"
    assert (
        "date" in missing_slots2
    ), f"Expected 'date' still in missing_slots, got {missing_slots2}"

    # Save session after turn 2
    merged_luma_response2 = result2.get("_merged_luma_response")
    previous_session2 = get_session(user_id)
    new_session_state2 = build_session_state_from_outcome(
        outcome=outcome2,
        outcome_status=status2,
        merged_luma_response=merged_luma_response2,
        previous_session_state=previous_session2,
        user_id=user_id,
    )
    if new_session_state2:
        save_session(user_id, new_session_state2)

    # 🔍 Stronger Validation: Verify slot_attempts incremented in session
    session_after_turn2 = get_session(user_id)
    assert session_after_turn2 is not None, "Session should exist after turn 2"
    slot_attempts_session2 = session_after_turn2.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_session2, dict
    ), f"slot_attempts should be a dict, got {type(slot_attempts_session2)}"
    assert (
        slot_attempts_session2.get("date") == 2
    ), f"Expected slot_attempts['date'] == 2 in session, got {slot_attempts_session2.get('date')}"

    # Verify slot_attempts in decision.facts (for rendering access)
    facts2_decision = (
        decision2.get("facts", {}) if decision2 else outcome2.get("facts", {})
    )
    slot_attempts_decision2 = facts2_decision.get("slot_attempts", {})
    assert isinstance(
        slot_attempts_decision2, dict
    ), f"slot_attempts should be a dict in decision.facts, got {type(slot_attempts_decision2)}"
    assert (
        slot_attempts_decision2.get("date") == 2
    ), f"Expected slot_attempts['date'] == 2 in decision.facts, got {slot_attempts_decision2.get('date')}"

    # Assert: text is present and uses variant template
    assert (
        "text" in result2
    ), f"Expected clarification text to be present, but 'text' not in result. Keys: {list(result2.keys())}"
    assert result2[
        "text"
    ], f"Expected clarification text to be non-empty, got: {result2.get('text')}"

    text2 = result2["text"].lower()

    # Should use variant template (MISSING_DATE_ATTEMPT_2)
    assert (
        "date" in text2 or "day" in text2
    ), f"Expected text to mention 'date' or 'day', got: {result2['text']}"

    # Should contain "still" (variant indicator)
    assert (
        "still" in text2
    ), f"Expected variant template (contains 'still'), got: {result2['text']}"

    # Should be different from turn 1
    assert (
        text2 != text1
    ), f"Expected different text on retry, but got same text: {result2['text']}"

    # Clean up
    clear_session(user_id)
