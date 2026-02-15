"""
End-to-End Sentinel Test: Cross-Intent Messy Real-World Flow

Cross-intent E2E sentinel test to guard YAML-driven behavior.

Purpose:
- Validate intent durability, switching, and session boundaries under a realistic multi-intent conversation.
- Act as a regression sentinel (not logic verification).

Test scenario:
- User starts CREATE_APPOINTMENT
- Completes slot filling
- Switches to CANCEL_BOOKING mid-flow
- Rejects once
- Confirms cancellation

Constraints:
- Do NOT assert internal steps or missing_slots.
- Do NOT assert execution counts.
- Only assert user-visible behavior (status, action, intent continuity).

Goal:
- Catch regressions where procedural logic, slot leakage, or incorrect session resets reappear.

To run this test:
  RUN_REAL_LUMA_E2E=true pytest core/tests/e2e/test_core_e2e_cross_intent_messy_real_world_flow.py

Requirements:
  - RUN_REAL_LUMA_E2E=true environment variable must be set
  - Luma service must be running (defaults to http://localhost:9001, or set LUMA_BASE_URL)
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest
import yaml

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.execution.clients.booking_client import BookingClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
from core.tests.mocks import (
    mock_cancel_booking,
    mock_create_booking,
    mock_get_service_availability,
)
from core.tests.planning.adapter import normalize_planning_outcome

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


# Skip entire test module if RUN_REAL_LUMA_E2E is not set
if not os.getenv("RUN_REAL_LUMA_E2E"):
    pytest.skip(
        "Real Luma E2E tests disabled. Set RUN_REAL_LUMA_E2E=true to enable.",
        allow_module_level=True,
    )


class TestLumaClient(LumaClient):
    """Custom LumaClient that injects tenant_context from test aliases."""

    def __init__(self, test_aliases: Optional[Dict[str, str]] = None):
        """Initialize with test aliases to inject."""
        super().__init__()
        self.test_aliases = test_aliases or {}
        self.last_response: Optional[Dict[str, Any]] = None

    def resolve(
        self,
        user_id: str,
        text: str,
        domain: str = "service",
        timezone: str = "UTC",
        tenant_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Override resolve to inject test aliases into tenant_context.

        Test aliases are merged into tenant_context, preserving other fields like booking_mode.
        """
        # Merge test aliases into tenant_context
        merged_tenant_context = tenant_context.copy() if tenant_context else {}
        if self.test_aliases:
            merged_tenant_context["aliases"] = self.test_aliases

        # Call parent resolve with merged tenant_context
        response = super().resolve(
            user_id=user_id,
            text=text,
            domain=domain,
            timezone=timezone,
            tenant_context=merged_tenant_context,
        )

        # Store last response for debugging
        self.last_response = response

        return response


def load_scenarios() -> List[Dict[str, Any]]:
    """Load test scenarios from YAML file."""
    scenarios_file = (
        Path(__file__).parent / "scenarios" / "cross_intent_messy_real_world_flow.yaml"
    )
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
                        print(
                            f"\n[E2E_TEST] WARNING: Test index {index} is out of range (0-{len(scenarios)-1}). Skipping.\n"
                        )

            if selected_indices:
                # Return only the selected scenarios (preserve order)
                # Remove duplicates and sort
                selected = [scenarios[i] for i in sorted(set(selected_indices))]
                names = [s.get("name", "unnamed") for s in selected]
                print(
                    f"\n[E2E_TEST] Running {len(selected)} test(s) at indices {sorted(set(selected_indices))}: {', '.join(names)}\n"
                )
                return selected
            else:
                print(
                    f"\n[E2E_TEST] WARNING: No valid test indices found. Running all tests.\n"
                )
        except ValueError as e:
            print(
                f"\n[E2E_TEST] WARNING: Invalid E2E_TEST_INDEX value '{test_index_env}'. Must be comma-separated numbers. Running all tests.\n"
            )

    return scenarios


def create_mock_availability_client(frozen_time: Optional[datetime] = None) -> Mock:
    """
    Create a mocked availability client using tests.mocks.

    Args:
        frozen_time: Optional frozen time for resolving relative dates like "tomorrow".
                    If provided, "tomorrow" will resolve to frozen_time + 1 day.
    """
    mock_availability_client = Mock(spec=AvailabilityClient)

    # Use mock_get_service_availability from tests.mocks to generate response
    # Wrap it to handle service_id type (mock expects int, but we may pass strings)
    def mock_get_availability(
        organization_id=None,
        service_id=None,
        date=None,
        time_constraint=None,
        extra_params=None,
        **kwargs,
    ):
        # Convert service_id to int if needed (mock expects int but doesn't use the value)
        service_id_int = 1  # Default
        if service_id is not None:
            if isinstance(service_id, str) and service_id.isdigit():
                service_id_int = int(service_id)
            elif isinstance(service_id, int):
                service_id_int = service_id

        # Resolve relative dates like "tomorrow" to actual dates
        from datetime import timedelta

        resolved_date = date
        if date and isinstance(date, str):
            date_lower = date.lower().strip()
            if date_lower == "tomorrow":
                # Resolve "tomorrow" to frozen_time + 1 day (or default if no frozen_time)
                if frozen_time:
                    resolved_date = (frozen_time + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    resolved_date = "2026-01-16"  # Default fallback
            elif date_lower in ("today", "now"):
                # Resolve "today" to frozen_time date (or default if no frozen_time)
                if frozen_time:
                    resolved_date = frozen_time.strftime("%Y-%m-%d")
                else:
                    resolved_date = "2026-01-15"  # Default fallback
            # If date is already in YYYY-MM-DD format, use it as-is
            elif (
                len(date_lower) == 10 and date_lower[4] == "-" and date_lower[7] == "-"
            ):
                # Looks like YYYY-MM-DD format, use as-is
                resolved_date = date
            # Otherwise, try to use frozen_time + 1 day or default
            else:
                if frozen_time:
                    resolved_date = (frozen_time + timedelta(days=1)).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    resolved_date = "2026-01-16"

        # Default to frozen_time + 1 day if no date provided and frozen_time is available
        if not resolved_date:
            if frozen_time:
                resolved_date = (frozen_time + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                resolved_date = "2026-01-16"

        return mock_get_service_availability(
            organization_id=organization_id or 1,
            service_id=service_id_int,
            date=resolved_date,
            time_constraint=time_constraint,
            extra_params=extra_params,
            **kwargs,
        )

    mock_availability_client.get_service_availability.side_effect = (
        mock_get_availability
    )
    return mock_availability_client


def create_mock_booking_client() -> Mock:
    """Create a mocked booking client using tests.mocks."""
    mock_booking_client = Mock(spec=BookingClient)

    # Track created bookings for cancellation
    created_bookings = {}
    # Track most recently created booking for context inference
    most_recent_booking = None

    def mock_booking(
        organization_id=None,
        customer_id=None,
        booking_type=None,
        item_id=None,
        start_time=None,
        end_time=None,
        **kwargs,
    ):
        booking_result = mock_create_booking(
            organization_id=organization_id or 1,
            customer_id=customer_id or 1,
            booking_type=booking_type or "service",
            item_id=item_id or 1,
            start_time=start_time,
            end_time=end_time,
            **kwargs,
        )
        # Store booking for later retrieval/cancellation
        if isinstance(booking_result, dict):
            booking_id = booking_result.get("booking_id") or booking_result.get("id")
            if booking_id:
                booking_id_str = str(booking_id)
                created_bookings[booking_id_str] = booking_result
                most_recent_booking = booking_result
        return booking_result

    def mock_get_booking_func(booking_id: str, **kwargs):
        # Return stored booking if available
        if booking_id in created_bookings:
            return created_bookings[booking_id]
        # If booking_id is missing or invalid, return most recent booking (test workaround)
        # This simulates the system inferring booking_id from context
        if most_recent_booking:
            return most_recent_booking
        # Fallback: return a simple mock booking structure
        return {
            "id": booking_id,
            "booking_id": booking_id,
            "status": "confirmed",
            "service_id": 1,
            "item_id": 1,
        }

    def mock_cancel_booking_func(booking_id: str, **kwargs):
        # Cancel stored booking if available
        if booking_id in created_bookings:
            booking = created_bookings[booking_id]
            booking["status"] = "cancelled"
        return mock_cancel_booking(booking_id=booking_id, **kwargs)

    # Use nonlocal to update most_recent_booking
    def get_booking_wrapper(booking_id: str, **kwargs):
        nonlocal most_recent_booking
        if booking_id in created_bookings:
            return created_bookings[booking_id]
        if most_recent_booking:
            return most_recent_booking
        return {
            "id": booking_id,
            "booking_id": booking_id,
            "status": "confirmed",
            "service_id": 1,
            "item_id": 1,
        }

    def get_most_recent_booking_wrapper(
        organization_id=None, customer_id=None, **kwargs
    ):
        """Return the most recently created booking (test helper for FETCH_BOOKING)."""
        nonlocal most_recent_booking
        # Return most recent booking if available, even if organization_id/customer_id are None
        # This simulates the system inferring booking from context
        return most_recent_booking

    def create_booking_wrapper(*args, **kwargs):
        nonlocal most_recent_booking
        result = mock_booking(*args, **kwargs)
        if isinstance(result, dict):
            # Extract booking_id from nested structure: {"booking": {"id": 1, ...}}
            booking_obj = (
                result.get("booking")
                if isinstance(result.get("booking"), dict)
                else result
            )
            booking_id = booking_obj.get("booking_id") or booking_obj.get("id")
            if booking_id:
                booking_id_str = str(booking_id)
                created_bookings[booking_id_str] = result
                most_recent_booking = result
        return result

    mock_booking_client.create_booking.side_effect = create_booking_wrapper
    mock_booking_client.get_booking.side_effect = get_booking_wrapper
    mock_booking_client.get_most_recent_booking = get_most_recent_booking_wrapper
    mock_booking_client.cancel_booking.side_effect = mock_cancel_booking_func
    return mock_booking_client


def create_mock_organization_client() -> Mock:
    """Create a mocked organization client."""
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}  # Maps to "service" domain
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
    scenario_name: str,
) -> None:
    """
    Assert turn expectations against Core result.

    SENTINEL TEST CONSTRAINTS:
    - Only assert user-visible behavior (status, action, intent continuity)
    - Do NOT assert internal steps or missing_slots
    - Do NOT assert execution counts
    """
    # Assert success
    assert (
        result.get("success") is True
    ), f"[{scenario_name}] Turn {turn_number}: Expected success=True, got {result.get('success')} with error: {result.get('error')}"

    # Normalize result using planning test adapter (same as planning tests)
    # This ensures we assert against the same contract as planning tests
    normalized = normalize_planning_outcome(result)

    # Extract plan from normalized result (adapter returns plan structure)
    plan = normalized.get("plan", {})
    if not plan:
        # Fallback: use normalized directly if plan is empty
        plan = normalized

    # Assert expected intent (if specified)
    if "intent" in expectations:
        expected_intent = expectations["intent"]
        actual_intent = (
            normalized.get("intent") or plan.get("intent_name") or plan.get("intent")
        )
        assert (
            actual_intent == expected_intent
        ), f"[{scenario_name}] Turn {turn_number}: Expected intent {expected_intent}, got {actual_intent}"

    # Assert expected status
    if "status" in expectations:
        expected_status = expectations["status"]
        actual_status = normalized.get("status")
        assert (
            actual_status == expected_status
        ), f"[{scenario_name}] Turn {turn_number}: Expected status {expected_status}, got {actual_status}"

    # Assert expected action (from plan.action, same as planning tests)
    if "action" in expectations:
        expected_action = expectations["action"]
        actual_action = plan.get("action")
        assert (
            actual_action == expected_action
        ), f"[{scenario_name}] Turn {turn_number}: Expected action {expected_action}, got {actual_action}"

    # NOTE: We do NOT assert missing_slots, execution counts, or internal details
    # This is a sentinel test focused on user-visible behavior only


# Load scenarios once for parametrization
_scenarios_for_parametrize = load_scenarios()


def _scenario_id(scenario: Dict[str, Any]) -> str:
    """Generate test ID with index and name."""
    # Load all scenarios to get index
    scenarios_file = (
        Path(__file__).parent / "scenarios" / "cross_intent_messy_real_world_flow.yaml"
    )
    if scenarios_file.exists():
        with open(scenarios_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        all_scenarios = data.get("scenarios", [])
        try:
            idx = all_scenarios.index(scenario)
            return f"{idx}-{scenario.get('name', 'unnamed')}"
        except ValueError:
            return scenario.get("name", "unnamed")
    return scenario.get("name", "unnamed")


@pytest.mark.parametrize("scenario", _scenarios_for_parametrize, ids=_scenario_id)
def test_cross_intent_messy_real_world_flow(scenario: Dict[str, Any]):
    """
    Test a single scenario from the YAML file using real Luma client.

    This is a sentinel test that guards against regressions in YAML-driven behavior.
    It validates intent durability, switching, and session boundaries without
    asserting internal implementation details.

    Args:
        scenario: Scenario dictionary from YAML file
    """
    scenario_name = scenario["name"]
    turns = scenario["turns"]

    # Extract aliases from scenario (same pattern as planning tests)
    # Default to common service aliases if not specified
    aliases = scenario.get("aliases", {"haircut": "haircut", "massage": "massage"})

    # Frozen time: 2026-01-15 10:00:00 UTC
    # "tomorrow" should resolve to 2026-01-16
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = f"test_cross_intent_{scenario_name}_{id(scenario)}"

    # Create real Luma client with aliases (same pattern as planning tests)
    # TestLumaClient injects aliases into tenant_context when calling resolve()
    luma_client = TestLumaClient(test_aliases=aliases)

    # Create mocked clients for execution
    mock_org_client = create_mock_organization_client()
    # Pass frozen_time to availability mock so it can resolve relative dates like "tomorrow"
    mock_availability_client = create_mock_availability_client(frozen_time=frozen_time)
    mock_booking_client = create_mock_booking_client()

    # Create session store
    session_store = MockSessionStore()

    # Track intent across turns to validate preservation/switching
    previous_intent = None
    # Track booking_id from created bookings for cross-intent flows
    created_booking_id = None

    # Process each turn
    for turn_idx, turn in enumerate(turns, start=1):
        sentence = turn["sentence"]
        expectations = turn.get("expect", {})

        # For CANCEL_BOOKING/MODIFY_BOOKING turns after a booking was created,
        # inject booking_id into the sentence context if needed
        # This simulates the system inferring booking_id from context
        # Note: This is a test workaround - in production, booking_id would be inferred differently
        if created_booking_id and turn_idx > 2:
            # Check if this turn expects FETCH_BOOKING or CONFIRM_CANCELLATION
            expected_action = expectations.get("action")
            if expected_action in ("FETCH_BOOKING", "CONFIRM_CANCELLATION"):
                # The booking_id will be injected via session state if needed
                # For now, we rely on the mock booking client to handle it
                pass

        # Call handle_message with real Luma client
        result = handle_message(
            text=sentence,
            user_id=user_id,
            luma_client=luma_client,
            availability_client=mock_availability_client,
            organization_client=mock_org_client,
            booking_client=mock_booking_client,
            # Only use session store after first turn
            session_store=session_store if turn_idx > 1 else None,
            frozen_time=frozen_time,
            organization_id=1,
        )

        # Extract booking_id from execution result for cross-intent flows
        # This handles both CONFIRM_APPOINTMENT (creates booking) and FETCH_BOOKING (fetches booking)
        execution_result = result.get("result", {})
        if isinstance(execution_result, dict):
            # Check for booking_id in execution result (from CONFIRM_APPOINTMENT or FETCH_BOOKING)
            booking_id = (
                execution_result.get("booking_id")
                or (
                    execution_result.get("booking", {}).get("id")
                    if isinstance(execution_result.get("booking"), dict)
                    else None
                )
                or (
                    execution_result.get("booking", {}).get("booking_id")
                    if isinstance(execution_result.get("booking"), dict)
                    else None
                )
                or execution_result.get("booking_code")
            )
            if booking_id:
                created_booking_id = booking_id

        # Assert turn expectations (only user-visible behavior)
        assert_turn_expectations(result, expectations, turn_idx, scenario_name)

        # Extract intent from normalized result (same as assertion function)
        normalized = normalize_planning_outcome(result)
        current_intent = normalized.get("intent") or normalized.get("plan", {}).get(
            "intent_name"
        )

        # Validate intent behavior across turns
        if turn_idx > 1 and previous_intent:
            if expectations.get("intent_switched"):
                # Intent should have changed from previous turn
                assert current_intent != previous_intent, (
                    f"[{scenario_name}] Turn {turn_idx}: Intent should have switched from turn {turn_idx - 1}. "
                    f"Previous: {previous_intent}, Current: {current_intent} (expected different)"
                )
            elif expectations.get("intent_preserved", False):
                # Intent should be preserved from previous turn
                assert current_intent == previous_intent, (
                    f"[{scenario_name}] Turn {turn_idx}: Intent should be preserved from turn {turn_idx - 1}. "
                    f"Previous: {previous_intent}, Current: {current_intent}"
                )

        # Update previous intent for next turn
        if current_intent and current_intent != "":
            previous_intent = current_intent

        # Save session state for next turn (simulate persistence)
        # Core persists session state with: intent_name, slots, missing_slots, status, availability_fingerprint
        if turn_idx < len(turns):  # Not the last turn
            # Extract session state from normalized result (matches Core's persisted session schema)
            plan_obj = normalized.get("plan", {})
            # Get slots from normalized result (includes booking_id from FETCH_BOOKING execution)
            slots = normalized.get("slots", {}).copy()
            # Also check plan slots (execution results update plan slots directly)
            plan_slots = plan_obj.get("slots", {})
            if isinstance(plan_slots, dict):
                slots.update(plan_slots)
            # Extract booking_id from execution result if present (for FETCH_BOOKING)
            if isinstance(execution_result, dict):
                exec_booking_id = execution_result.get("booking_id")
                if exec_booking_id:
                    slots["booking_id"] = exec_booking_id
            session_state = {
                "intent_name": current_intent if current_intent else "",
                "slots": slots,
                "missing_slots": normalized.get("missing_slots", []),
                "status": normalized.get("status"),
            }
            # Optionally include stage/action if present (for debugging)
            if "stage" in plan_obj:
                session_state["stage"] = plan_obj.get("stage")
            if "action" in plan_obj:
                session_state["action"] = plan_obj.get("action")

            # CRITICAL: Preserve availability_fingerprint from execution result
            # The orchestrator attaches it to execution_result when SEARCH_AVAILABILITY succeeds
            execution_result = result.get("result", {})
            if isinstance(execution_result, dict):
                # Check if execution_result has availability_fingerprint (from SEARCH_AVAILABILITY)
                if execution_result.get("availability_fingerprint"):
                    session_state["availability_fingerprint"] = execution_result.get(
                        "availability_fingerprint"
                    )
                # Also check plan for fingerprint (attached by orchestrator for persistence)
                elif plan_obj.get("availability_fingerprint"):
                    session_state["availability_fingerprint"] = plan_obj.get(
                        "availability_fingerprint"
                    )

                # CRITICAL: Preserve resolved_datetime_range from execution result
                if execution_result.get("resolved_datetime_range"):
                    session_state["resolved_datetime_range"] = execution_result.get(
                        "resolved_datetime_range"
                    )
                # Also check plan for resolved_datetime_range (attached by orchestrator for persistence)
                elif plan_obj.get("resolved_datetime_range"):
                    session_state["resolved_datetime_range"] = plan_obj.get(
                        "resolved_datetime_range"
                    )

            # Also preserve from previous session if present (cross-turn persistence)
            previous_session = session_store.get_session(user_id)
            if previous_session:
                if previous_session.get("availability_fingerprint"):
                    # Only preserve if not already set from execution result
                    if "availability_fingerprint" not in session_state:
                        session_state["availability_fingerprint"] = (
                            previous_session.get("availability_fingerprint")
                        )
                if previous_session.get("resolved_datetime_range"):
                    # Only preserve if not already set from execution result
                    if "resolved_datetime_range" not in session_state:
                        session_state["resolved_datetime_range"] = previous_session.get(
                            "resolved_datetime_range"
                        )

            session_store.save_session(user_id, session_state)
