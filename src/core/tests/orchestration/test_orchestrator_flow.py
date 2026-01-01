"""
Tests for Orchestrator Flow

Tests resolved flow, partial flow, and contract violations.
"""

import pytest
from unittest.mock import Mock, patch

from core.orchestration.orchestrator import handle_message
from core.orchestration.errors import ContractViolation, UpstreamError
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.clients.booking_client import BookingClient
from core.orchestration.clients.customer_client import CustomerClient
from core.orchestration.clients.catalog_client import CatalogClient


def test_resolved_flow_calls_booking_client():
    """Test that resolved booking flow calls booking client."""
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [{"text": "haircut", "canonical": "haircut", "id": 1}],
            "datetime_range": {"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T11:00:00Z"},
            "booking_state": "RESOLVED"
        }
    }

    # Mock responses
    services_response = {
        "catalog_last_updated_at": "2024-01-01T00:00:00Z",
        "business_category_id": 10,
        "services": [{"id": 1, "name": "Haircut", "canonical": "haircut"}]
    }
    reservation_response = {"room_types": [], "extras": []}
    customer_response = {"customer_id": 100, "id": 100}
    booking_response = {"booking_code": "ABC123",
                        "code": "ABC123", "status": "pending"}

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = luma_response

    mock_catalog_client = Mock(spec=CatalogClient)
    mock_catalog_client.get_services.return_value = services_response
    mock_catalog_client.get_reservation.return_value = reservation_response

    mock_customer_client = Mock(spec=CustomerClient)
    mock_customer_client.get_customer.return_value = customer_response

    mock_booking_client = Mock(spec=BookingClient)
    mock_booking_client.create_booking.return_value = booking_response

    result = handle_message(
        user_id="user123",
        text="book haircut tomorrow at 2pm",
        customer_id=100,
        luma_client=mock_luma_client,
        customer_client=mock_customer_client,
        booking_client=mock_booking_client,
        catalog_client=mock_catalog_client
    )

    assert result["success"] is True
    assert result["outcome"]["type"] == "BOOKING_CREATED"
    assert result["outcome"]["booking_code"] == "ABC123"
    assert result["outcome"]["status"] == "pending"

    # Verify catalog client was called
    mock_catalog_client.get_services.assert_called_once_with(1)
    mock_catalog_client.get_reservation.assert_called_once_with(1)

    # Verify booking client was called with correct parameters
    mock_booking_client.create_booking.assert_called_once()
    call_kwargs = mock_booking_client.create_booking.call_args[1]
    assert call_kwargs["organization_id"] == 1
    assert call_kwargs["customer_id"] == 100
    assert call_kwargs["booking_type"] == "service"
    assert call_kwargs["item_id"] == 1


def test_partial_flow_returns_template_key():
    """Test that partial booking (clarification) returns template_key."""
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": True,
        "clarification": {
            "reason": "MISSING_TIME",
            "data": {}
        },
        "booking": {
            "services": [{"text": "haircut"}],
            "datetime_range": None,
            "booking_state": "PARTIAL"
        }
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = luma_response

    result = handle_message(
        user_id="user123",
        text="book haircut",
        domain="hotel",
        luma_client=mock_luma_client
    )

    assert result["success"] is True
    assert result["outcome"]["type"] == "CLARIFY"
    assert result["outcome"]["template_key"] == "hotel.ask_time"
    assert "booking" in result["outcome"]


def test_contract_violation_raises_and_handled():
    """Test that contract violation is caught and handled gracefully."""
    # Missing datetime_range.start for RESOLVED booking
    invalid_luma_response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}],
            "datetime_range": {},  # Missing "start"
            "booking_state": "RESOLVED"
        }
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = invalid_luma_response

    result = handle_message(
        user_id="user123",
        text="book haircut tomorrow at 2pm",
        luma_client=mock_luma_client
    )

    assert result["success"] is False
    assert result["error"] == "contract_violation"
    assert "datetime_range.start" in result["message"]


def test_luma_error_handled():
    """Test that Luma upstream errors are handled gracefully."""
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.side_effect = UpstreamError(
        "Luma service unavailable")

    result = handle_message(
        user_id="user123",
        text="book haircut",
        luma_client=mock_luma_client
    )

    assert result["success"] is False
    assert result["error"] == "upstream_error"
    assert "Luma service unavailable" in result["message"]


def test_success_false_returns_error():
    """Test that success=false from Luma returns error response."""
    luma_response = {
        "success": False,
        "error": "Invalid input"
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = luma_response

    result = handle_message(
        user_id="user123",
        text="invalid",
        luma_client=mock_luma_client
    )

    assert result["success"] is False
    assert result["error"] == "luma_error"
    assert result["message"] == "Invalid input"


def test_unsupported_intent_returns_error():
    """Test that unsupported intent returns error."""
    luma_response = {
        "success": True,
        "intent": {"name": "UNSUPPORTED_INTENT"},
        "needs_clarification": False,
        "booking": {
            "services": [],
            "datetime_range": {"start": "2024-01-01T10:00:00Z"},
            "booking_state": "RESOLVED"
        }
    }

    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = luma_response

    result = handle_message(
        user_id="user123",
        text="unsupported action",
        luma_client=mock_luma_client
    )

    assert result["success"] is False
    assert result["error"] == "unsupported_intent"
    assert "UNSUPPORTED_INTENT" in result["message"]
