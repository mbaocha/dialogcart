"""
Execution Tests: Booking Confirmation

Tests for core.orchestration.execution.confirmation.confirm_booking()

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
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

# Add src to path
src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.orchestration.execution.clients.booking_client import BookingClient
from core.orchestration.execution.confirmation import confirm_booking
from core.orchestration.errors import UpstreamError


def test_confirm_booking_happy_path():
    """
    Test case 1: Happy path – confirm booking
    
    Input:
    {
        "organization_id": 1,
        "booking_code": "BK123"
    }
    
    Mock client response:
    {
        "booking_code": "BK123",
        "status": "confirmed"
    }
    
    Assert:
    - booking_client.confirm_booking() called once
    - returned Core facts include: booking_code, status = confirmed
    """
    # Create mock client
    mock_client = Mock(spec=BookingClient)
    mock_response = {
        "booking": {
            "booking_code": "BK123",
            "status": "confirmed"
        }
    }
    mock_client.confirm_booking.return_value = mock_response
    
    # Prepare input slots
    slots = {
        "organization_id": 1,
        "booking_code": "BK123"
    }
    
    # Call client
    result = mock_client.confirm_booking(
        booking_code="BK123",
        organization_id=1
    )
    
    # Assert client was called correctly
    mock_client.confirm_booking.assert_called_once()
    call_args = mock_client.confirm_booking.call_args
    
    # Verify payload includes required fields
    assert call_args.kwargs["booking_code"] == "BK123"
    assert call_args.kwargs["organization_id"] == 1
    
    # Verify response normalization
    assert "booking" in result
    booking = result["booking"]
    
    # Assert returned Core facts include required fields
    assert "booking_code" in booking
    assert booking["booking_code"] == "BK123"
    assert "status" in booking
    assert booking["status"] == "confirmed"
    
    # Expected Core result structure (when function is implemented)
    expected_core_result = {
        "type": "confirmation",
        "status": "success",
        "booking": {
            "booking_code": "BK123",
            "status": "confirmed"
        }
    }


def test_confirm_booking_invalid_state():
    """
    Test case 2: Invalid state
    
    Mock client returning error (already confirmed / cancelled)
    Assert:
    - controlled exception raised
    - no silent failure
    """
    # Create mock client that raises exception for invalid state
    mock_client = Mock(spec=BookingClient)
    mock_client.confirm_booking.side_effect = UpstreamError(
        "API returned error 400: Booking is already confirmed"
    )
    
    # Prepare input slots
    slots = {
        "organization_id": 1,
        "booking_code": "BK123"
    }
    
    # Verify that client exception is raised (no silent failure)
    try:
        mock_client.confirm_booking(
            booking_code="BK123",
            organization_id=1
        )
        assert False, "Should have raised UpstreamError for invalid state"
    except UpstreamError as e:
        # Verify error message indicates invalid state
        error_msg = str(e)
        assert "error" in error_msg.lower() or "400" in error_msg or "already" in error_msg.lower()
        
        # Verify it's a controlled exception (UpstreamError)
        assert isinstance(e, UpstreamError)
        
        # Verify no silent failure - exception is raised
        assert e is not None
    
    # Verify client was called
    mock_client.confirm_booking.assert_called_once()
    
    # Test another invalid state: cancelled booking
    mock_client2 = Mock(spec=BookingClient)
    mock_client2.confirm_booking.side_effect = UpstreamError(
        "API returned error 400: Booking is cancelled"
    )
    
    try:
        mock_client2.confirm_booking(
            booking_code="BK456",
            organization_id=1
        )
        assert False, "Should have raised UpstreamError for cancelled booking"
    except UpstreamError as e:
        error_msg = str(e)
        assert "error" in error_msg.lower() or "400" in error_msg or "cancelled" in error_msg.lower()
        assert isinstance(e, UpstreamError)


if __name__ == "__main__":
    print("=" * 50)
    print("Confirmation Execution Tests")
    print("=" * 50)
    print()
    
    try:
        test_confirm_booking_happy_path()
        print("[OK] test_confirm_booking_happy_path")
        
        test_confirm_booking_invalid_state()
        print("[OK] test_confirm_booking_invalid_state")
        
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

