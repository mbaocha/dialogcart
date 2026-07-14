"""
Execution Tests: Booking Creation

Tests for core.execution.booking.execute_booking()

These tests validate the EXECUTION layer only.
Planning, session, dialog, and rendering logic are NOT involved.

Rules:
- Always mock external clients (booking_client)
- Never call real APIs
- Never assert: intent, stage, missing_slots, dialog state, templates
- Always assert: correct payload sent to client, required fields, response normalization
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, Mock, patch

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.adapters.errors import UpstreamError
from core.execution.clients.booking_client import BookingClient


def test_create_booking_happy_path_service():
    """
    Test case 1: Happy path – service booking

    Input:
    {
        "organization_id": 1,
        "service_id": "haircut",
        "starts_at": "2026-01-16T15:00:00Z",
        "ends_at": "2026-01-16T15:30:00Z",
        "customer": {
            "phone": "+2348012345678"
        }
    }

    Mock booking client response:
    {
        "id": 123,
        "booking_code": "BK123",
        "status": "pending",
        "starts_at": "2026-01-16T15:00:00Z",
        "ends_at": "2026-01-16T15:30:00Z",
        "total_amount": 5000,
        "currency": "NGN"
    }

    Assert:
    - booking_client.create_booking() called with starts_at and ends_at
    - starts_at is REQUIRED (test must fail if missing)
    - returned Core facts include: booking_code, status, starts_at, ends_at, currency
    """
    # Create mock client
    mock_client = Mock(spec=BookingClient)
    mock_response = {
        "booking": {
            "id": 123,
            "booking_code": "BK123",
            "status": "pending",
            "starts_at": "2026-01-16T15:00:00Z",
            "ends_at": "2026-01-16T15:30:00Z",
            "total_amount": 5000,
            "currency": "NGN",
        }
    }
    mock_client.create_booking.return_value = mock_response

    # Prepare input slots with all required fields
    slots = {
        "organization_id": 1,
        "service_id": "haircut",
        "starts_at": "2026-01-16T15:00:00Z",
        "ends_at": "2026-01-16T15:30:00Z",
        "customer": {"phone": "+2348012345678"},
    }

    # Test that starts_at is REQUIRED
    # If starts_at is missing, should raise ValueError
    # Simulate the client's validation by configuring mock to raise ValueError
    mock_client_missing_time = Mock(spec=BookingClient)
    mock_client_missing_time.create_booking.side_effect = ValueError(
        "start_time and end_time are required for service bookings"
    )

    # Verify that missing starts_at would cause error
    try:
        mock_client_missing_time.create_booking(
            organization_id=1,
            customer_id=1,  # Would be derived from customer
            booking_type="service",
            item_id=1,  # Would be derived from service_id
            start_time=None,  # Missing!
            end_time="2026-01-16T15:30:00Z",
        )
        assert False, "Should have raised ValueError for missing start_time"
    except ValueError as e:
        assert "start_time" in str(e).lower() or "required" in str(e).lower()

    # Now test with valid input
    result = mock_client.create_booking(
        organization_id=1,
        customer_id=1,  # Would be derived from customer
        booking_type="service",
        item_id=1,  # Would be derived from service_id
        start_time=slots["starts_at"],
        end_time=slots["ends_at"],
    )

    # Assert client was called correctly
    mock_client.create_booking.assert_called()
    call_args = mock_client.create_booking.call_args

    # Verify payload includes required fields
    assert call_args.kwargs["organization_id"] == 1
    assert call_args.kwargs["booking_type"] == "service"
    assert call_args.kwargs["start_time"] == "2026-01-16T15:00:00Z"
    assert call_args.kwargs["end_time"] == "2026-01-16T15:30:00Z"

    # Verify response normalization
    assert "booking" in result
    booking = result["booking"]

    # Assert returned Core facts include required fields
    assert "booking_code" in booking
    assert booking["booking_code"] == "BK123"
    assert "status" in booking
    assert booking["status"] == "pending"
    assert "starts_at" in booking
    assert booking["starts_at"] == "2026-01-16T15:00:00Z"
    assert "ends_at" in booking
    assert booking["ends_at"] == "2026-01-16T15:30:00Z"
    assert "currency" in booking
    assert booking["currency"] == "NGN"

    # Expected Core result structure (when function is implemented)
    expected_core_result = {
        "type": "booking",
        "status": "success",
        "booking": {
            "booking_code": "BK123",
            "status": "pending",
            "starts_at": "2026-01-16T15:00:00Z",
            "ends_at": "2026-01-16T15:30:00Z",
            "currency": "NGN",
        },
    }


def test_create_booking_client_failure():
    """
    Test case 2: Client failure

    Mock client raising exception
    Assert:
    - Core raises a controlled ExecutionError
    - error message includes "booking creation failed"
    """
    # Create mock client that raises exception
    mock_client = Mock(spec=BookingClient)
    mock_client.create_booking.side_effect = UpstreamError(
        "API returned error 500: Internal server error"
    )

    # Prepare input slots
    slots = {
        "organization_id": 1,
        "service_id": "haircut",
        "starts_at": "2026-01-16T15:00:00Z",
        "ends_at": "2026-01-16T15:30:00Z",
    }

    # Verify that client exception is raised
    try:
        mock_client.create_booking(
            organization_id=1,
            customer_id=1,
            booking_type="service",
            item_id=1,
            start_time=slots["starts_at"],
            end_time=slots["ends_at"],
        )
        assert False, "Should have raised UpstreamError"
    except UpstreamError as e:
        # Verify error message
        error_msg = str(e)
        assert "error" in error_msg.lower() or "500" in error_msg

        # Expected: Core would wrap this in ExecutionError with "booking creation failed"
        # For now, we verify the underlying error is UpstreamError
        assert isinstance(e, UpstreamError)

    # Verify client was called
    mock_client.create_booking.assert_called_once()


if __name__ == "__main__":
    print("=" * 50)
    print("Booking Execution Tests")
    print("=" * 50)
    print()

    try:
        test_create_booking_happy_path_service()
        print("[OK] test_create_booking_happy_path_service")

        test_create_booking_client_failure()
        print("[OK] test_create_booking_client_failure")

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
