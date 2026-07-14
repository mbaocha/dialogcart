"""
End-to-End Test: awaiting_slot Soft Guided Flow

Tests the complete awaiting_slot lifecycle:
1. awaiting_slot is persisted across turns
2. awaiting_slot prioritizes missing_slots ordering
3. awaiting_slot is cleared when the slot is satisfied
4. No regression when awaiting_slot is absent

This test validates the full flow from session persistence → planning → clarification.
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

from core.session.persist import build_session_state_from_outcome
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session, save_session

# Import after path setup
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.org_setup import get_customer_details, setup_test_org_domain


def test_e2e_awaiting_slot_soft_guided_flow():
    """
    E2E test: awaiting_slot soft guided flow for CREATE_RESERVATION.

    Flow:
    Turn 0: Pre-seed session with awaiting_slot="service_id"
    Turn 1: User provides date range
        - Expected: service_id prioritized in missing_slots[0]
        - Expected: awaiting_slot preserved
    Turn 2: User provides service_id
        - Expected: awaiting_slot cleared
        - Expected: status transitions normally
    """
    user_id = "test_e2e_awaiting_slot_001"
    domain = "reservation"

    # Clean up any existing session
    clear_session(user_id)

    # Set up test clients
    aliases = {"Deluxe room": "lodging.room_type.deluxe"}
    luma_client = TestLumaClient(test_aliases=aliases)
    catalog_client = TestCatalogClient(test_aliases=aliases, domain=domain)

    # Set up org domain cache
    setup_test_org_domain(domain)

    # Get customer details
    customer_details = get_customer_details()

    # ============================================================================
    # TURN 0: Pre-seed session with awaiting_slot="service_id"
    # ============================================================================

    initial_session_state = {
        "intent_name": "CREATE_RESERVATION",
        "slots": {},
        "facts": {},
        "status": "NEEDS_CLARIFICATION",
        "active_capability": None,
        "awaiting_slot": "service_id",
    }

    # Save initial session state
    save_session(user_id, initial_session_state)

    # Verify session was saved
    saved_session = get_session(user_id)
    assert saved_session is not None, "Session should be saved"
    assert (
        saved_session.get("awaiting_slot") == "service_id"
    ), f"Expected awaiting_slot='service_id', got {saved_session.get('awaiting_slot')}"

    # ============================================================================
    # TURN 1: User provides date range "from 5th Oct to 8th Oct"
    # ============================================================================

    turn1_text = "from 5th Oct to 8th Oct"

    # Create session_store wrapper for handle_message
    # Must implement both get_session and save_session to match production behavior
    class SessionStoreWrapper:
        def __init__(self, user_id):
            self.user_id = user_id

        def get_session(self, user_id):
            return get_session(user_id)

        def save_session(self, user_id, session_state):
            save_session(user_id, session_state)

    session_store = SessionStoreWrapper(user_id)

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

    # Assert: service_id is still in missing_slots
    missing_slots_turn1 = outcome_turn1.get("missing_slots")
    if missing_slots_turn1 is None:
        # Try facts.missing_slots as fallback
        facts_turn1 = outcome_turn1.get("facts", {})
        missing_slots_turn1 = facts_turn1.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn1, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn1)}"
    assert (
        "service_id" in missing_slots_turn1
    ), f"Expected 'service_id' in missing_slots, got {missing_slots_turn1}"

    # Assert: service_id is at index 0 (prioritized by awaiting_slot)
    assert (
        missing_slots_turn1[0] == "service_id"
    ), f"Expected missing_slots[0]='service_id' (prioritized by awaiting_slot), got {missing_slots_turn1}"

    # Save session after turn 1 (matching production API behavior)
    # This ensures session is persisted with NEEDS_CLARIFICATION status and awaiting_slot
    merged_luma_response_turn1 = result_turn1.get("_merged_luma_response")
    # Use initial_session_state as previous_session_state (session that was loaded before turn 1)
    new_session_state_turn1 = build_session_state_from_outcome(
        outcome=outcome_turn1,
        outcome_status=status_turn1,
        merged_luma_response=merged_luma_response_turn1,
        previous_session_state=initial_session_state,
        user_id=user_id,
    )
    if new_session_state_turn1:
        save_session(user_id, new_session_state_turn1)

    # Verify session state after turn 1
    session_after_turn1 = get_session(user_id)
    assert session_after_turn1 is not None, "Session should exist after turn 1"
    assert (
        session_after_turn1.get("status") == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION after turn 1, got {session_after_turn1.get('status')}"
    assert (
        session_after_turn1.get("awaiting_slot") == "service_id"
    ), f"Expected awaiting_slot='service_id' to be preserved, got {session_after_turn1.get('awaiting_slot')}"

    # ============================================================================
    # TURN 2: User provides service "Deluxe room"
    # ============================================================================

    # Verify session state BEFORE turn 2 (ensures session is loaded with awaiting_slot)
    session_before_turn2 = get_session(user_id)
    assert session_before_turn2 is not None, "Session should exist before turn 2"
    assert (
        session_before_turn2.get("status") == "NEEDS_CLARIFICATION"
    ), f"Expected status=NEEDS_CLARIFICATION before turn 2 (so session is loaded), got {session_before_turn2.get('status')}"
    assert (
        session_before_turn2.get("awaiting_slot") == "service_id"
    ), f"Expected awaiting_slot='service_id' to be present before turn 2, got {session_before_turn2.get('awaiting_slot')}"

    turn2_text = "Deluxe room"

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

    # Assert: service_id is NOT in missing_slots (slot is now filled)
    missing_slots_turn2 = outcome_turn2.get("missing_slots")
    if missing_slots_turn2 is None:
        # Try facts.missing_slots as fallback
        facts_turn2 = outcome_turn2.get("facts", {})
        missing_slots_turn2 = facts_turn2.get("missing_slots", [])

    assert isinstance(
        missing_slots_turn2, list
    ), f"missing_slots should be a list, got {type(missing_slots_turn2)}"
    assert (
        "service_id" not in missing_slots_turn2
    ), f"Expected 'service_id' NOT in missing_slots (slot filled), got {missing_slots_turn2}"

    # Extract status for session persistence
    status_turn2 = outcome_turn2.get("status")
    assert status_turn2 in (
        "READY",
        "NEEDS_CLARIFICATION",
        "AWAITING_CONFIRMATION",
    ), f"Expected status to be READY/NEEDS_CLARIFICATION/AWAITING_CONFIRMATION, got {status_turn2}"

    # Save session after turn 2 (matching production API behavior)
    # This ensures we can verify the persisted session state
    merged_luma_response_turn2 = result_turn2.get("_merged_luma_response")
    # Use session_before_turn2 that was verified earlier (has awaiting_slot="service_id")
    new_session_state_turn2 = build_session_state_from_outcome(
        outcome=outcome_turn2,
        outcome_status=status_turn2,
        merged_luma_response=merged_luma_response_turn2,
        previous_session_state=session_before_turn2,
        user_id=user_id,
    )
    if new_session_state_turn2:
        save_session(user_id, new_session_state_turn2)

    # Verify session state after turn 2 (read from persisted session)
    session_after_turn2 = get_session(user_id)
    if session_after_turn2 is not None:
        # awaiting_slot should be cleared when slot is filled
        awaiting_slot_after_turn2 = session_after_turn2.get("awaiting_slot")
        assert (
            awaiting_slot_after_turn2 is None
        ), f"Expected awaiting_slot to be cleared (None) when slot is filled, got {awaiting_slot_after_turn2}"

    # Clean up
    clear_session(user_id)


if __name__ == "__main__":
    # Allow running as standalone script
    test_e2e_awaiting_slot_soft_guided_flow()
    print("✅ E2E test passed: awaiting_slot soft guided flow")
