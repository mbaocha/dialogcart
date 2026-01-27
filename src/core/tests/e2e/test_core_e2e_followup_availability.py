"""
End-to-End Test: Multi-Turn Availability Search

Tests the full flow: planning → session persistence → execution for availability search
across multiple turns.

Scenario: User provides time in a follow-up message

Conversation:
1. "book haircut tomorrow" → NEEDS_CLARIFICATION (missing time)
2. "3pm" → SEARCH_AVAILABILITY executed

This test validates:
- Session state persistence across turns
- UNKNOWN intent override using prior session intent
- Slot carry-over from previous turns
- Availability execution only after missing slots are supplied
"""

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
import pytest
import sys
from pathlib import Path
from unittest.mock import Mock
from datetime import datetime, timezone

# Add src to path BEFORE importing core modules
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Now import core modules after path is set up


def test_core_e2e_followup_availability_time_provided_later():
    """
    E2E test: Multi-turn availability search with time provided in follow-up

    Flow:
    1. Turn 1: "book haircut tomorrow"
       - Mock Luma: CREATE_APPOINTMENT, missing time
       - Assert: NEEDS_CLARIFICATION, no availability call
    2. Turn 2: "3pm"
       - Mock Luma: UNKNOWN intent, time="3pm"
       - Assert: Intent overridden to CREATE_APPOINTMENT
       - Assert: Slots merged (service_id + date from turn 1, time from turn 2)
       - Assert: SEARCH_AVAILABILITY executed
       - Assert: Availability client called with correct parameters
    """
    # Frozen time: 2026-01-15 10:00:00 UTC
    # "tomorrow" should resolve to 2026-01-16
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_e2e_followup_001"

    # Mock organization client (same for both turns)
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {
            "businessCategoryId": 1  # Maps to "service" domain
        }
    }

    # Mock availability client (only used in turn 2)
    mock_availability_client = Mock(spec=AvailabilityClient)
    mock_availability_response = {
        "slots": [
            {
                "start": "2026-01-16T15:00:00Z",
                "end": "2026-01-16T15:30:00Z",
                "staff_id": 5
            },
            {
                "start": "2026-01-16T15:30:00Z",
                "end": "2026-01-16T16:00:00Z",
                "staff_id": 5
            }
        ]
    }
    mock_availability_client.get_service_availability.return_value = mock_availability_response

    # ============================================================================
    # TURN 1: "book haircut tomorrow"
    # ============================================================================

    # Mock Luma response for turn 1: CREATE_APPOINTMENT with missing time
    mock_luma_response_turn1 = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": True,
        "booking": {
            "booking_type": "service",
            "services": [
                {
                    "text": "haircut",
                    "canonical": "beauty_and_wellness.haircut"
                }
            ],
            "datetime_range": {
                "start": "2026-01-16T00:00:00Z",
                "end": "2026-01-16T23:59:59Z"
            },
            "confirmation_state": "pending",
            "booking_state": "RESOLVED"
        },
        "facts": {
            "service_id": "haircut"
        },
        "slots": {
            "service_id": "haircut",
            "date": "2026-01-16"
        },
        "missing_slots": ["time"],
        "context": {}
    }

    mock_luma_client_turn1 = Mock(spec=LumaClient)
    mock_luma_client_turn1.resolve.return_value = mock_luma_response_turn1

    # Call handle_message for turn 1
    result_turn1 = handle_message(
        text="book haircut tomorrow",
        user_id=user_id,
        luma_client=mock_luma_client_turn1,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1
    )

    # Assert turn 1 result
    assert result_turn1 is not None
    assert result_turn1.get("success") is True, \
        f"Turn 1: Expected success=True, got {result_turn1.get('success')} with error: {result_turn1.get('error')}"

    # Extract plan from turn 1 (when no execution, result contains the plan)
    plan_turn1 = result_turn1.get("result", {})

    # Assert NEEDS_CLARIFICATION status
    assert plan_turn1.get("status") == "NEEDS_CLARIFICATION", \
        f"Turn 1: Expected status NEEDS_CLARIFICATION, got {plan_turn1.get('status')}"

    # Assert missing_slots contains "time"
    missing_slots_turn1 = plan_turn1.get("missing_slots", [])
    assert "time" in missing_slots_turn1, \
        f"Turn 1: Expected 'time' in missing_slots, got {missing_slots_turn1}"

    # Assert no availability call was made in turn 1
    mock_availability_client.get_service_availability.assert_not_called()

    # Extract session state from turn 1 for turn 2
    # The session state should contain intent, slots, and status
    session_state = {
        "intent": plan_turn1.get("intent_name", "CREATE_APPOINTMENT"),
        "slots": plan_turn1.get("slots", {}),
        "status": plan_turn1.get("status"),
        "stage": plan_turn1.get("stage"),
        "action": plan_turn1.get("action")
    }

    # ============================================================================
    # TURN 2: "3pm"
    # ============================================================================

    # Mock Luma response for turn 2: UNKNOWN intent with time
    mock_luma_response_turn2 = {
        "success": True,
        "intent": {
            "name": "UNKNOWN",
            "confidence": 0.5
        },
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [],
            "datetime_range": {
                "start": "2026-01-16T15:00:00Z",
                "end": "2026-01-16T15:30:00Z"
            },
            "confirmation_state": "pending",
            "booking_state": "RESOLVED"
        },
        "facts": {
            "times": ["3pm"]
        },
        "slots": {
            "time": "3pm"
        },
        "time_constraint": {
            "mode": "exact",
            "start": "15:00",
            "end": "15:00"
        },
        "missing_slots": [],
        "context": {}
    }

    mock_luma_client_turn2 = Mock(spec=LumaClient)
    mock_luma_client_turn2.resolve.return_value = mock_luma_response_turn2

    # Create a simple session store mock that returns session_state
    class MockSessionStore:
        def __init__(self, session_state):
            self.session_state = session_state

        def get_session(self, user_id):
            return self.session_state

    session_store = MockSessionStore(session_state)

    # Call handle_message for turn 2 with session_store
    result_turn2 = handle_message(
        text="3pm",
        user_id=user_id,
        luma_client=mock_luma_client_turn2,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1
    )

    # Assert turn 2 result
    assert result_turn2 is not None
    assert result_turn2.get("success") is True, \
        f"Turn 2: Expected success=True, got {result_turn2.get('success')} with error: {result_turn2.get('error')}"

    # Extract plan and execution result from turn 2
    plan_turn2 = result_turn2.get("plan")
    execution_result_turn2 = result_turn2.get("result")

    # Assert plan exists and has correct action
    assert plan_turn2 is not None, "Turn 2: Expected plan in result"
    assert plan_turn2.get("action") == "SEARCH_AVAILABILITY", \
        f"Turn 2: Expected action SEARCH_AVAILABILITY, got {plan_turn2.get('action')}"

    # Assert intent was overridden from UNKNOWN to CREATE_APPOINTMENT
    assert plan_turn2.get("intent_name") == "CREATE_APPOINTMENT", \
        f"Turn 2: Expected intent_name CREATE_APPOINTMENT (overridden from UNKNOWN), got {plan_turn2.get('intent_name')}"

    # Assert slots are merged correctly (service_id + date from turn 1, time from turn 2)
    slots_turn2 = plan_turn2.get("slots", {})
    assert slots_turn2.get("service_id") == "haircut", \
        f"Turn 2: Expected service_id 'haircut' (from turn 1), got {slots_turn2.get('service_id')}"
    assert slots_turn2.get("date") == "2026-01-16", \
        f"Turn 2: Expected date '2026-01-16' (from turn 1), got {slots_turn2.get('date')}"
    assert slots_turn2.get("time") == "3pm", \
        f"Turn 2: Expected raw time '3pm' (from turn 2), got {slots_turn2.get('time')}"

    # Assert time_constraint is present with normalized time
    assert "time_constraint" in plan_turn2, "Turn 2: Expected time_constraint in plan"
    assert plan_turn2["time_constraint"].get("start") == "15:00", \
        f"Turn 2: Expected normalized time '15:00' in time_constraint, got {plan_turn2['time_constraint'].get('start')}"

    # Assert availability client was called once
    mock_availability_client.get_service_availability.assert_called_once()
    call_args = mock_availability_client.get_service_availability.call_args

    # Verify call parameters
    assert call_args.kwargs["organization_id"] == 1
    assert call_args.kwargs["service_id"] == "haircut"
    assert call_args.kwargs["date"] == "2026-01-16"
    assert "extra_params" in call_args.kwargs
    assert call_args.kwargs["extra_params"]["time_constraint"]["mode"] == "exact"

    # Assert execution result structure
    assert execution_result_turn2 is not None, "Turn 2: Expected execution result"
    assert execution_result_turn2.get("type") == "availability", \
        f"Turn 2: Expected result.type 'availability', got {execution_result_turn2.get('type')}"
    assert execution_result_turn2.get("status") == "success", \
        f"Turn 2: Expected result.status 'success', got {execution_result_turn2.get('status')}"
    assert "slots" in execution_result_turn2, "Turn 2: Expected 'slots' in execution result"

    # Assert returned slots are normalized
    slots = execution_result_turn2["slots"]
    assert isinstance(slots, list), "Turn 2: Expected slots to be a list"
    assert len(slots) == 2, f"Turn 2: Expected 2 slots, got {len(slots)}"

    # Check first slot
    slot1 = slots[0]
    assert "starts_at" in slot1, "Turn 2: Expected 'starts_at' in slot1"
    assert "ends_at" in slot1, "Turn 2: Expected 'ends_at' in slot1"
    assert slot1["starts_at"] == "2026-01-16T15:00:00Z"
    assert slot1["ends_at"] == "2026-01-16T15:30:00Z"

    # Check second slot
    slot2 = slots[1]
    assert "starts_at" in slot2, "Turn 2: Expected 'starts_at' in slot2"
    assert "ends_at" in slot2, "Turn 2: Expected 'ends_at' in slot2"
    assert slot2["starts_at"] == "2026-01-16T15:30:00Z"
    assert slot2["ends_at"] == "2026-01-16T16:00:00Z"

    # Verify no other fields in normalized slots (only starts_at and ends_at)
    assert len(slot1.keys()) == 2, \
        f"Turn 2: Expected only starts_at and ends_at in slot1, got {list(slot1.keys())}"
    assert len(slot2.keys()) == 2, \
        f"Turn 2: Expected only starts_at and ends_at in slot2, got {list(slot2.keys())}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
