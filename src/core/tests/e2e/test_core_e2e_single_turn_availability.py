"""
End-to-End Test: Availability Search

Tests the full flow: planning → execution for availability search.

Scenario: "book haircut tomorrow at 3pm"

This test:
- Mocks Luma response
- Calls canonical handle_message() which orchestrates planning and execution
- Asserts planning action == SEARCH_AVAILABILITY
- Asserts availability client was called
- Asserts returned slots are normalized
"""

from core.orchestration.orchestrator import handle_message
from core.orchestration.nlu import LumaClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.clients.organization_client import OrganizationClient
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


def test_core_e2e_single_turn_availability():
    """
    E2E test: "book haircut tomorrow at 3pm"

    Flow:
    1. Mock Luma response with CREATE_APPOINTMENT intent, service_id, date, time
    2. Mock availability client response
    3. Call canonical handle_message() which orchestrates planning and execution
    4. Assert planning action == SEARCH_AVAILABILITY
    5. Assert availability client was called with correct parameters
    6. Assert returned slots are normalized (starts_at, ends_at)
    """
    # Frozen time: 2026-01-15 10:00:00 UTC
    # "tomorrow" should resolve to 2026-01-16
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Mock Luma response for "book haircut tomorrow at 3pm"
    # This response must pass contract validation (assert_luma_contract)
    mock_luma_response = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [
                {
                    "text": "haircut",
                    "canonical": "beauty_and_wellness.haircut"
                }
            ],
            "datetime_range": {
                "start": "2026-01-16T15:00:00Z",
                "end": "2026-01-16T15:30:00Z"
            },
            "confirmation_state": "pending",
            "booking_state": "RESOLVED"
        },
        "facts": {
            "service_id": "haircut",
            "times": ["3pm"]
        },
        "slots": {
            "service_id": "haircut",
            "date": "2026-01-16",
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

    # Mock Luma client
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = mock_luma_response

    # Mock organization client
    # The org_domain_cache expects get_details() to return a response with
    # organization.businessCategoryId. For service domain, businessCategoryId should be 1.
    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {
            "businessCategoryId": 1  # Maps to "service" domain
        }
    }

    # Step 1: Mock availability client
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

    # Step 2: Call canonical handle_message() which orchestrates planning and execution
    # Note: frozen_time is reserved for future use, but we pass it for API compatibility
    # The Luma response already has the resolved date, so we don't need to mock datetime
    result = handle_message(
        text="book haircut tomorrow at 3pm",
        user_id="test_user_e2e_001",
        luma_client=mock_luma_client,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1
    )

    # Step 3: Assert result structure
    assert result is not None
    assert result.get("success") is True, \
        f"Expected success=True, got {result.get('success')} with error: {result.get('error')}"

    # Extract plan and execution result from response
    plan = result.get("plan")
    execution_result = result.get("result")

    # Step 4: Assert planning result
    assert plan is not None
    assert plan.get("action") == "SEARCH_AVAILABILITY", \
        f"Expected action SEARCH_AVAILABILITY, got {plan.get('action')}"
    assert plan.get("intent_name") == "CREATE_APPOINTMENT"
    assert plan.get("stage") is not None
    assert "slots" in plan
    assert plan["slots"].get("service_id") == "haircut"
    assert plan["slots"].get("date") == "2026-01-16"
    assert plan["slots"].get("time") == "3pm", \
        f"Expected raw time '3pm', got {plan['slots'].get('time')}"
    # Assert normalized time in time_constraint
    assert "time_constraint" in plan
    assert plan["time_constraint"].get("start") == "15:00", \
        f"Expected normalized time '15:00' in time_constraint, got {plan['time_constraint'].get('start')}"

    # Step 5: Assert availability client was called
    mock_availability_client.get_service_availability.assert_called_once()
    call_args = mock_availability_client.get_service_availability.call_args

    # Verify call parameters
    assert call_args.kwargs["organization_id"] == 1
    assert call_args.kwargs["service_id"] == "haircut"
    assert call_args.kwargs["date"] == "2026-01-16"
    assert "extra_params" in call_args.kwargs
    assert call_args.kwargs["extra_params"]["time_constraint"]["mode"] == "exact"

    # Step 6: Assert execution result structure
    assert execution_result is not None
    assert execution_result.get("type") == "availability"
    assert execution_result.get("status") == "success"
    assert "slots" in execution_result

    # Step 7: Assert returned slots are normalized
    slots = execution_result["slots"]
    assert isinstance(slots, list)
    assert len(slots) == 2

    # Check first slot
    slot1 = slots[0]
    assert "starts_at" in slot1
    assert "ends_at" in slot1
    assert slot1["starts_at"] == "2026-01-16T15:00:00Z"
    assert slot1["ends_at"] == "2026-01-16T15:30:00Z"

    # Check second slot
    slot2 = slots[1]
    assert "starts_at" in slot2
    assert "ends_at" in slot2
    assert slot2["starts_at"] == "2026-01-16T15:30:00Z"
    assert slot2["ends_at"] == "2026-01-16T16:00:00Z"

    # Verify no other fields in normalized slots (only starts_at and ends_at)
    assert len(slot1.keys(
    )) == 2, f"Expected only starts_at and ends_at, got {list(slot1.keys())}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
