"""
Tests for Orchestrator Flow

Tests resolved flow, partial flow, and contract violations.
"""

import pytest
from unittest.mock import Mock, patch

from core.orchestration.orchestrator import handle_message
from core.errors.exceptions import ContractViolation, UpstreamError
from core.clients.luma_client import LumaClient
from core.clients.booking_client import BookingClient


def test_resolved_flow_calls_booking_client():
    """Test that resolved booking flow calls booking client."""
    luma_response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}],
            "datetime_range": {"start": "2024-01-01T10:00:00Z", "end": "2024-01-01T11:00:00Z"},
            "booking_state": "RESOLVED"
        }
    }
    
    api_response = {"booking_id": "123", "status": "confirmed"}
    
    mock_luma_client = Mock(spec=LumaClient)
    mock_luma_client.resolve.return_value = luma_response
    
    mock_booking_client = Mock(spec=BookingClient)
    mock_booking_client.create.return_value = api_response
    
    result = handle_message(
        user_id="user123",
        text="book haircut tomorrow at 2pm",
        luma_client=mock_luma_client,
        booking_client=mock_booking_client
    )
    
    assert result["success"] is True
    assert result["outcome"]["type"] == "EXECUTED"
    assert result["outcome"]["action"] == "booking.create"
    assert result["outcome"]["result"] == api_response
    
    mock_booking_client.create.assert_called_once_with("user123", luma_response["booking"])


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
    mock_luma_client.resolve.side_effect = UpstreamError("Luma service unavailable")
    
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

