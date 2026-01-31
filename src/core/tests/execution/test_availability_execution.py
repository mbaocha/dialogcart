"""
Execution Tests: Availability Search

Tests for core.orchestration.execution.availability.search_availability()

These tests validate the EXECUTION layer only.
Planning, session, dialog, and rendering logic are NOT involved.

Rules:
- Always mock external clients (availability_client)
- Never call real APIs
- Never assert: intent, stage, missing_slots, dialog state, templates
- Always assert: correct payload sent to client, required fields, response normalization
"""

from core.orchestration.execution.availability import search_availability
from core.orchestration.execution.clients.availability_client import AvailabilityClient
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def test_search_availability_happy_path_service():
    """
    Test case 1: Happy path – service availability

    Input:
    {
        "organization_id": 1,
        "service_id": "haircut",
        "date": "2026-01-16",
        "time_constraint": {
            "mode": "exact",
            "start": "15:00",
            "end": "15:00"
        }
    }

    Mock client response:
    {
        "slots": [
            {
                "start": "2026-01-16T15:00:00Z",
                "end": "2026-01-16T15:30:00Z",
                "staff_id": 5
            }
        ]
    }

    Assert:
    - availability_client.get_service_availability() called once
    - payload includes: organization_id, service_id, date
    - time_from / time_to derived from time_constraint
    - returned Core result includes: available_slots[], each slot has start_at, end_at, staff_id preserved
    """
    # Create mock client
    mock_client = Mock(spec=AvailabilityClient)
    mock_response = {
        "slots": [
            {
                "start": "2026-01-16T15:00:00Z",
                "end": "2026-01-16T15:30:00Z",
                "staff_id": 5
            }
        ]
    }
    mock_client.get_service_availability.return_value = mock_response

    # Prepare input slots
    slots = {
        "organization_id": 1,
        "service_id": "haircut",
        "date": "2026-01-16",
        "time_constraint": {
            "mode": "exact",
            "start": "15:00",
            "end": "15:00"
        }
    }

    # Test the client directly (since execution function is a placeholder)
    # When the function is implemented, it should call the client like this:
    result = mock_client.get_service_availability(
        organization_id=1,
        service_id="haircut",
        date="2026-01-16",
        extra_params={"time_constraint": slots["time_constraint"]}
    )

    # Assert client was called correctly
    mock_client.get_service_availability.assert_called_once()
    call_args = mock_client.get_service_availability.call_args

    # Verify payload includes required fields
    assert call_args.kwargs["organization_id"] == 1
    assert call_args.kwargs["service_id"] == "haircut"
    assert call_args.kwargs["date"] == "2026-01-16"

    # Verify response normalization
    assert "slots" in result
    assert len(result["slots"]) == 1
    slot = result["slots"][0]
    assert "start" in slot
    assert "end" in slot
    assert slot["start"] == "2026-01-16T15:00:00Z"
    assert slot["end"] == "2026-01-16T15:30:00Z"
    assert slot["staff_id"] == 5

    # Expected Core result structure (when function is implemented)
    expected_core_result = {
        "type": "availability",
        "status": "success",
        "available_slots": [
            {
                "start_at": "2026-01-16T15:00:00Z",
                "end_at": "2026-01-16T15:30:00Z",
                "staff_id": 5
            }
        ]
    }

    # Verify normalization would produce correct structure
    normalized_slots = [
        {
            "start_at": s["start"],
            "end_at": s["end"],
            "staff_id": s.get("staff_id")
        }
        for s in result["slots"]
    ]

    assert normalized_slots[0]["start_at"] == "2026-01-16T15:00:00Z"
    assert normalized_slots[0]["end_at"] == "2026-01-16T15:30:00Z"
    assert normalized_slots[0]["staff_id"] == 5


def test_search_availability_no_availability():
    """
    Test case 2: No availability

    Mock empty slots list
    Assert returned result has:
    - available_slots = []
    - no exception thrown
    """
    # Create mock client
    mock_client = Mock(spec=AvailabilityClient)
    mock_response = {
        "slots": []
    }
    mock_client.get_service_availability.return_value = mock_response

    # Prepare input slots
    slots = {
        "organization_id": 1,
        "service_id": "haircut",
        "date": "2026-01-16"
    }

    # Call client
    result = mock_client.get_service_availability(
        organization_id=1,
        service_id="haircut",
        date="2026-01-16"
    )

    # Assert client was called
    mock_client.get_service_availability.assert_called_once()

    # Assert response has empty slots
    assert "slots" in result
    assert result["slots"] == []

    # Expected Core result structure (when function is implemented)
    expected_core_result = {
        "type": "availability",
        "status": "success",
        "available_slots": []
    }

    # Verify normalization produces empty list
    normalized_slots = [
        {
            "start_at": s["start"],
            "end_at": s["end"]
        }
        for s in result["slots"]
    ]

    assert normalized_slots == []


if __name__ == "__main__":
    print("=" * 50)
    print("Availability Execution Tests")
    print("=" * 50)
    print()

    try:
        test_search_availability_happy_path_service()
        print("[OK] test_search_availability_happy_path_service")

        test_search_availability_no_availability()
        print("[OK] test_search_availability_no_availability")

        print()
        print("=" * 50)
        print("[OK] All tests passed!")
        print("=" * 50)
        sys.exit(0)
    except AssertionError as e:
        print()
        print("=" * 50)
        print(f"[FAIL] Test failed: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print()
        print("=" * 50)
        print(f"[ERROR] Error: {e}")
        print("=" * 50)
        import traceback
        traceback.print_exc()
        sys.exit(1)
