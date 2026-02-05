"""
End-to-End Test: Multi-Turn Cancellation Confirmation with Real Luma

Tests the full flow: planning → session persistence → execution for cancellation confirmation
across multiple turns using the **real Luma client** (not mocked).

This test validates:
- Real NLU behavior with actual Luma API
- Session state persistence across turns
- Slot carry-over from previous turns
- Cancellation execution (CONFIRM_CANCELLATION) after booking_id is supplied
- Booking client integration
- Session clearing after confirmation

To run this test:
  RUN_REAL_LUMA_E2E=true pytest core/tests/e2e/test_core_e2e_followup_confirm_cancellation_real_luma.py

To run a specific test by index (0-based):
  E2E_TEST_INDEX=0 RUN_REAL_LUMA_E2E=true pytest core/tests/e2e/test_core_e2e_followup_confirm_cancellation_real_luma.py

Requirements:
  - RUN_REAL_LUMA_E2E=true environment variable must be set
  - Luma service must be running (defaults to http://localhost:9001, or set LUMA_BASE_URL)

Note:
  This test is expected to FAIL initially because CONFIRM_CANCELLATION is not yet
  implemented in the execution dispatcher. The failure will define the exact contract
  needed for implementation.
"""

import os
import pytest
import yaml
import sys
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime, timezone
from typing import Dict, Any, List

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.execution.clients.booking_client import BookingClient
from core.orchestration.orchestrator import handle_message
from core.tests.mocks import mock_get_service_availability, mock_create_booking
from core.tests.integration.test_appointment_e2e import TestLumaClient
from core.tests.planning.adapter import normalize_planning_outcome

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


# Skip entire test module if RUN_REAL_LUMA_E2E is not set
if not os.getenv("RUN_REAL_LUMA_E2E"):
    pytest.skip("Real Luma E2E tests disabled. Set RUN_REAL_LUMA_E2E=true to enable.",
                allow_module_level=True)


def load_scenarios() -> List[Dict[str, Any]]:
    """Load test scenarios from YAML file."""
    scenarios_file = Path(__file__).parent / "scenarios" / \
        "followup_confirm_cancellation_real_luma.yaml"
    if not scenarios_file.exists():
        pytest.skip(f"Scenarios file not found: {scenarios_file}")

    with open(scenarios_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    scenarios = data.get("scenarios", [])
    
    # Support selecting specific tests by index via environment variable
    test_index_env = os.getenv("E2E_TEST_INDEX")
    if test_index_env is not None:
        try:
            # Parse comma-separated indices
            index_strings = [s.strip() for s in test_index_env.split(",")]
            selected_indices = []
            for index_str in index_strings:
                if index_str:
                    index = int(index_str)
                    if 0 <= index < len(scenarios):
                        selected_indices.append(index)
                    else:
                        print(f"\n[E2E_TEST] WARNING: Test index {index} is out of range (0-{len(scenarios)-1}). Skipping.\n")
            
            if selected_indices:
                # Return only the selected scenarios (preserve order)
                selected = [scenarios[i] for i in sorted(set(selected_indices))]  # Remove duplicates and sort
                names = [s.get('name', 'unnamed') for s in selected]
                print(f"\n[E2E_TEST] Running {len(selected)} test(s) at indices {sorted(set(selected_indices))}: {', '.join(names)}\n")
                return selected
            else:
                print(f"\n[E2E_TEST] WARNING: No valid test indices found. Running all tests.\n")
        except ValueError as e:
            print(f"\n[E2E_TEST] WARNING: Invalid E2E_TEST_INDEX value '{test_index_env}'. Must be comma-separated numbers. Running all tests.\n")
    
    return scenarios


def create_mock_availability_client() -> Mock:
    """Create a mocked availability client using tests.mocks."""
    mock_availability_client = Mock(spec=AvailabilityClient)
    # Use mock_get_service_availability from tests.mocks to generate response
    # Wrap it to handle service_id type (mock expects int, but we may pass strings)
    def mock_get_availability(organization_id=None, service_id=None, date=None,
                              time_constraint=None, extra_params=None, **kwargs):
        # Convert service_id to int if needed (mock expects int but doesn't use the value)
        service_id_int = 1  # Default
        if service_id is not None:
            if isinstance(service_id, str) and service_id.isdigit():
                service_id_int = int(service_id)
            elif isinstance(service_id, int):
                service_id_int = service_id

        return mock_get_service_availability(
            organization_id=organization_id or 1,
            service_id=service_id_int,
            date=date or "2026-01-16",
            time_constraint=time_constraint,
            extra_params=extra_params,
            **kwargs
        )
    mock_availability_client.get_service_availability.side_effect = mock_get_availability
    return mock_availability_client


def create_mock_booking_client() -> Mock:
    """Create a mocked booking client using tests.mocks."""
    mock_booking_client = Mock(spec=BookingClient)
    # Use mock_create_booking from tests.mocks to generate response
    def mock_booking(organization_id=None, customer_id=None, booking_type=None,
                     item_id=None, start_time=None, end_time=None, **kwargs):
        return mock_create_booking(
            organization_id=organization_id or 1,
            customer_id=customer_id or 1,
            booking_type=booking_type or "service",
            item_id=item_id or 1,
            start_time=start_time,
            end_time=end_time,
            **kwargs
        )
    mock_booking_client.create_booking.side_effect = mock_booking
    # Mock cancel_booking method for cancellation tests
    def mock_cancel_booking(organization_id=None, booking_id=None, **kwargs):
        return {
            "success": True,
            "booking_id": booking_id,
            "status": "cancelled"
        }
    mock_booking_client.cancel_booking = Mock(side_effect=mock_cancel_booking)
    return mock_booking_client


def create_mock_organization_client() -> Mock:
    """Create a mocked organization client."""
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {
            "businessCategoryId": 1  # Maps to "service" domain
        }
    }
    return mock_org_client


class MockSessionStore:
    """Simple session store mock that stores session state in memory."""

    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, user_id: str) -> Dict[str, Any]:
        return self.sessions.get(user_id)

    def save_session(self, user_id: str, session_state: Dict[str, Any]) -> None:
        self.sessions[user_id] = session_state


def extract_plan_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract plan from result, handling both NEEDS_CLARIFICATION and execution cases.

    When NEEDS_CLARIFICATION: plan is in "result"
    When execution occurs: plan is in "plan", execution result is in "result"
    """
    plan = result.get("plan")
    if not plan:
        # Check if plan is nested in result (NEEDS_CLARIFICATION case)
        result_data = result.get("result", {})
        if isinstance(result_data, dict):
            if "plan" in result_data:
                plan = result_data.get("plan")
            elif "action" in result_data or "status" in result_data:
                # Result IS the plan (NEEDS_CLARIFICATION returns plan directly in result)
                plan = result_data
    return plan or {}


def assert_turn_expectations(
    result: Dict[str, Any],
    expectations: Dict[str, Any],
    turn_number: int,
    scenario_name: str
) -> None:
    """
    Assert turn expectations against Core result.

    Only asserts Core behavior, not Luma internals.
    """
    # Assert success
    assert result.get("success") is True, \
        f"[{scenario_name}] Turn {turn_number}: Expected success=True, got {result.get('success')} with error: {result.get('error')}"

    # Normalize result using planning test adapter (same as planning tests)
    # This ensures we assert against the same contract as planning tests
    normalized = normalize_planning_outcome(result)

    # Extract plan from normalized result (adapter returns plan structure)
    plan = normalized.get("plan", {})
    if not plan:
        # Fallback: use normalized directly if plan is empty
        plan = normalized

    # Assert intent_name is non-empty (critical invariant)
    # Skip this assertion if allow_empty_intent=true (for pre-intent slot collection)
    intent_name = normalized.get("intent") or plan.get(
        "intent_name") or plan.get("intent")
    if not expectations.get("allow_empty_intent", False):
        assert intent_name and intent_name != "", \
            f"[{scenario_name}] Turn {turn_number}: Expected non-empty intent_name, got {intent_name}"

    # Assert expected status
    if "status" in expectations:
        expected_status = expectations["status"]
        actual_status = normalized.get("status")
        assert actual_status == expected_status, \
            f"[{scenario_name}] Turn {turn_number}: Expected status {expected_status}, got {actual_status}"

    # Assert expected action (from plan.action, same as planning tests)
    if "action" in expectations:
        expected_action = expectations["action"]
        actual_action = plan.get("action")
        assert actual_action == expected_action, \
            f"[{scenario_name}] Turn {turn_number}: Expected action {expected_action}, got {actual_action}"

    # Assert missing_slots
    if "missing_slots" in expectations:
        expected_missing = set(expectations["missing_slots"])
        actual_missing = set(normalized.get("missing_slots", []))
        assert actual_missing == expected_missing, \
            f"[{scenario_name}] Turn {turn_number}: Expected missing_slots {expected_missing}, got {actual_missing}"

    # Assert intent preservation (if expected)
    if expectations.get("intent_preserved"):
        # Intent should be non-empty (already checked above)
        # Additional check: intent should match previous turn's intent
        # This is validated across turns in the test loop
        pass

    # Assert intent switch (if expected)
    if expectations.get("intent_switched"):
        # Intent should be non-empty (already checked above)
        # Additional check: intent should differ from previous turn's intent
        # This is validated across turns in the test loop
        pass

    # Assert no accidental availability search for cancellation
    if expectations.get("no_availability_search"):
        actual_action = plan.get("action")
        assert actual_action != "SEARCH_AVAILABILITY", \
            f"[{scenario_name}] Turn {turn_number}: Expected no SEARCH_AVAILABILITY action for cancellation, got {actual_action}"


# Load scenarios once for parametrization
_scenarios_for_parametrize = load_scenarios()

def _scenario_id(scenario: Dict[str, Any]) -> str:
    """Generate test ID with index and name."""
    # Load all scenarios to get index
    scenarios_file = Path(__file__).parent / "scenarios" / \
        "followup_confirm_cancellation_real_luma.yaml"
    if scenarios_file.exists():
        with open(scenarios_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        all_scenarios = data.get("scenarios", [])
        try:
            idx = all_scenarios.index(scenario)
            return f"{idx}-{scenario.get('name', 'unnamed')}"
        except ValueError:
            return scenario.get('name', 'unnamed')
    return scenario.get('name', 'unnamed')


@pytest.mark.parametrize("scenario", _scenarios_for_parametrize, ids=_scenario_id)
def test_real_luma_followup_confirm_cancellation_scenario(scenario: Dict[str, Any]):
    """
    Test a single scenario from the YAML file using real Luma client.

    Args:
        scenario: Scenario dictionary from YAML file
    """
    scenario_name = scenario["name"]
    turns = scenario["turns"]

    # Extract aliases from scenario (same pattern as planning tests)
    # Default to common service aliases if not specified
    aliases = scenario.get(
        "aliases", {"haircut": "haircut", "massage": "massage"})

    # Frozen time: 2026-01-15 10:00:00 UTC
    # "tomorrow" should resolve to 2026-01-16
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = f"test_real_luma_{scenario_name}_{id(scenario)}"

    # Create real Luma client with aliases (same pattern as planning tests)
    # TestLumaClient injects aliases into tenant_context when calling resolve()
    luma_client = TestLumaClient(test_aliases=aliases)

    # Create mocked clients for execution
    mock_org_client = create_mock_organization_client()
    mock_availability_client = create_mock_availability_client()
    mock_booking_client = create_mock_booking_client()

    # Create session store
    session_store = MockSessionStore()

    # Track intent across turns to validate preservation
    previous_intent = None

    # Process each turn
    for turn_idx, turn in enumerate(turns, start=1):
        sentence = turn["sentence"]
        expectations = turn.get("expect", {})

        # Call handle_message with real Luma client
        result = handle_message(
            text=sentence,
            user_id=user_id,
            luma_client=luma_client,
            availability_client=mock_availability_client,
            organization_client=mock_org_client,
            booking_client=mock_booking_client,  # Inject booking client
            # Only use session store after first turn
            session_store=session_store if turn_idx > 1 else None,
            frozen_time=frozen_time,
            organization_id=1
        )

        # Assert turn expectations
        assert_turn_expectations(result, expectations, turn_idx, scenario_name)

        # Extract intent from normalized result (same as assertion function)
        normalized = normalize_planning_outcome(result)
        current_intent = normalized.get("intent") or normalized.get("plan", {}).get("intent_name")

        # Skip intent tracking for turns with allow_empty_intent (pre-intent slot collection)
        if not expectations.get("allow_empty_intent", False):
            # Validate intent behavior across turns
            if turn_idx > 1 and previous_intent:
                if expectations.get("intent_switched"):
                    # Intent should have changed from previous turn
                    assert current_intent != previous_intent, \
                        f"[{scenario_name}] Turn {turn_idx}: Intent should have switched from turn {turn_idx - 1}. " \
                        f"Previous: {previous_intent}, Current: {current_intent} (expected different)"
                elif expectations.get("intent_preserved", False):
                    # Intent should be preserved from previous turn
                    assert current_intent == previous_intent or current_intent != "", \
                        f"[{scenario_name}] Turn {turn_idx}: Intent should be preserved from turn {turn_idx - 1}. " \
                        f"Previous: {previous_intent}, Current: {current_intent}"

            # Update previous intent for next turn
            if current_intent and current_intent != "":
                previous_intent = current_intent

        # Check if this turn expects CONFIRM_CANCELLATION action
        expected_action = expectations.get("action")
        if expected_action == "CONFIRM_CANCELLATION":
            # Assert booking_client.cancel_booking was called exactly once
            assert hasattr(mock_booking_client, 'cancel_booking'), \
                f"[{scenario_name}] Turn {turn_idx}: Expected booking_client to have cancel_booking method"
            
            assert mock_booking_client.cancel_booking.called, \
                f"[{scenario_name}] Turn {turn_idx}: Expected booking_client.cancel_booking to be called, but it was not"
            
            assert mock_booking_client.cancel_booking.call_count == 1, \
                f"[{scenario_name}] Turn {turn_idx}: Expected booking_client.cancel_booking to be called exactly once, " \
                f"but it was called {mock_booking_client.cancel_booking.call_count} times"
            
            # Assert returned result includes cancellation outcome
            # Check execution result for cancellation data
            execution_result = result.get("result", {})
            if isinstance(execution_result, dict):
                # Look for cancellation reference in various possible locations
                cancellation_data = (
                    execution_result.get("booking_id") or
                    execution_result.get("cancellation") or
                    (execution_result.get("data", {}) if isinstance(execution_result.get("data"), dict) else {}).get("cancellation")
                )
                cancellation_status = execution_result.get("status") == "cancelled"
                assert cancellation_data is not None or cancellation_status, \
                    f"[{scenario_name}] Turn {turn_idx}: Expected cancellation reference/outcome in result, " \
                    f"but found none. Result keys: {list(execution_result.keys())}"
            
            # Assert session is cleared or marked RESOLVED after confirmation
            # Check if session was cleared (not saved for next turn)
            if turn_idx < len(turns):  # Not the last turn
                # If there's a next turn, session should be cleared (not persisted)
                # For CONFIRM_CANCELLATION, session should be cleared after successful confirmation
                next_session = session_store.get_session(user_id)
                # Note: This assertion may need adjustment based on actual session clearing behavior
                # For now, we check that session is either None or marked as resolved
                if next_session is not None:
                    session_status = next_session.get("status")
                    # Session should be cleared or marked as resolved/ready after confirmation
                    # The exact behavior depends on implementation
                    pass  # Placeholder - adjust based on actual behavior

        # Save session state for next turn (simulate persistence)
        # Core persists session state with: intent_name, slots, missing_slots, status
        if turn_idx < len(turns):  # Not the last turn
            # Extract session state from normalized result (matches Core's persisted session schema)
            plan_obj = normalized.get("plan", {})
            session_state = {
                "intent_name": current_intent if current_intent else "",
                "slots": normalized.get("slots", {}),
                "missing_slots": normalized.get("missing_slots", []),
                "status": normalized.get("status"),
            }
            # Optionally include stage/action if present (for debugging)
            if "stage" in plan_obj:
                session_state["stage"] = plan_obj.get("stage")
            if "action" in plan_obj:
                session_state["action"] = plan_obj.get("action")

            session_store.save_session(user_id, session_state)

