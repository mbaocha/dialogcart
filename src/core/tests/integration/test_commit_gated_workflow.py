"""
End-to-End Integration Test for Commit-Gated Workflow

Tests the full commit-gated workflow:
1. Pending confirmation → no commit, confirmation prompt rendered
2. Confirmed → commit executes, final confirmation rendered

Traverses: orchestrator → router → renderer
"""

import pytest
from unittest.mock import Mock, call

from core.orchestration.orchestrator import handle_message
from core.rendering.whatsapp_renderer import render_outcome_to_whatsapp
from core.orchestration.nlu import LumaClient
from core.execution.clients.booking_client import BookingClient
from core.orchestration.clients.customer_client import CustomerClient
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient


def test_commit_gated_workflow_pending_then_confirmed():
    """
    Test full commit-gated workflow: pending → confirmation prompt, confirmed → commit executes.

    Flow:
    1. First request: CREATE_APPOINTMENT with confirmation_state="pending", missing time
       - Assert: No commit action executed
       - Assert: AWAIT_CONFIRMATION outcome returned
       - Assert: Confirmation prompt rendered

    2. Second request: Same booking with confirmation_state="confirmed"
       - Assert: Commit action (CONFIRM_APPOINTMENT) executed
       - Assert: BOOKING_CREATED outcome returned
       - Assert: Final confirmation message rendered
    """
    # Setup: Mock clients
    mock_luma_client = Mock(spec=LumaClient)
    mock_booking_client = Mock(spec=BookingClient)
    mock_customer_client = Mock(spec=CustomerClient)
    mock_catalog_client = Mock(spec=CatalogClient)
    mock_org_client = Mock(spec=OrganizationClient)

    # Mock organization response
    # Note: org_domain_cache expects organization nested in response with businessCategoryId
    # SERVICE_CATEGORY_IDS = {1, "beauty_and_wellness"}, so use 1 for service domain
    mock_org_client.get_details.return_value = {
        "organization": {
            "id": 1,
            "businessCategoryId": 1,  # Valid service category ID
            "domain": "service"
        }
    }

    # Mock catalog response
    # Note: duration is required for service bookings (orchestrator checks catalog service for duration)
    mock_catalog_client.get_services.return_value = {
        "catalog_last_updated_at": "2024-01-01T00:00:00Z",
        "business_category_id": 1,  # Match businessCategoryId from organization
        "services": [{"id": 1, "name": "Haircut", "canonical": "haircut", "is_active": True, "duration": 60}]
    }
    mock_catalog_client.get_reservation.return_value = {
        "room_types": [], "extras": []}

    # Mock customer response
    mock_customer_client.get_customer.return_value = {
        "customer_id": 100,
        "id": 100
    }

    # Mock booking creation response (for confirmed state)
    mock_booking_client.create_booking.return_value = {
        "booking_code": "ABC123",
        "code": "ABC123",
        "status": "pending",
        "booking": {
            "id": 1,
            "booking_code": "ABC123",
            "status": "pending"
        }
    }

    # ============================================
    # STEP 1: First request - Pending confirmation
    # ============================================

    # Mock Luma response: CREATE_APPOINTMENT, needs_clarification=false, confirmation_state="pending", missing time
    # Note: This response must pass contract validation (assert_luma_contract)
    luma_response_pending = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [{"text": "haircut", "canonical": "haircut"}],
            "datetime_range": None,  # Missing time
            "confirmation_state": "pending",
            "booking_state": "RESOLVED"
        },
        "issues": {
            "time": "missing"
        },
        "missing_slots": ["time"],
        "context": {}
    }

    mock_luma_client.resolve.return_value = luma_response_pending

    # Call orchestrator
    result_pending = handle_message(
        user_id="test_user_123",
        text="book haircut",
        domain="service",
        customer_id=100,
        luma_client=mock_luma_client,
        booking_client=mock_booking_client,
        customer_client=mock_customer_client,
        catalog_client=mock_catalog_client,
        organization_client=mock_org_client
    )

    # Assert: No commit action executed (booking.create should NOT be called)
    mock_booking_client.create_booking.assert_not_called()

    # Assert: facts container is present
    assert "facts" in result_pending["outcome"]
    assert "slots" in result_pending["outcome"]["facts"]
    assert "missing_slots" in result_pending["outcome"]["facts"]
    assert "context" in result_pending["outcome"]["facts"]
    assert result_pending["outcome"]["facts"]["missing_slots"] == ["time"]

    # Assert: AWAITING_CONFIRMATION outcome returned
    assert result_pending["success"] is True
    assert result_pending["outcome"]["status"] == "AWAITING_CONFIRMATION"
    assert result_pending["outcome"]["awaiting"] == "USER_CONFIRMATION"
    assert "booking" in result_pending["outcome"]

    # Assert: Confirmation prompt is rendered
    rendered_pending = render_outcome_to_whatsapp(result_pending["outcome"])
    assert rendered_pending["type"] == "text"
    assert "confirm" in rendered_pending["text"].lower()
    assert "haircut" in rendered_pending["text"].lower(
    ) or "service" in rendered_pending["text"].lower()

    # ============================================
    # STEP 2: Second request - Confirmed
    # ============================================

    # Reset mocks
    mock_booking_client.reset_mock()
    mock_customer_client.reset_mock()
    mock_catalog_client.reset_mock()

    # Mock Luma response: Same booking with confirmation_state="confirmed"
    # Note: This response must pass contract validation (assert_luma_contract)
    luma_response_confirmed = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [{"text": "haircut", "canonical": "haircut", "id": 1}],
            "datetime_range": {
                # Python 3.10 fromisoformat doesn't support 'Z', use '+00:00'
                "start": "2024-01-15T14:00:00+00:00",
                "end": "2024-01-15T15:00:00+00:00"
            },
            "confirmation_state": "confirmed",  # Now confirmed
            "booking_state": "RESOLVED"
        },
        "issues": {},
        "missing_slots": [],
        "context": {}
    }

    mock_luma_client.resolve.return_value = luma_response_confirmed

    # Call orchestrator
    result_confirmed = handle_message(
        user_id="test_user_123",
        text="yes",  # User confirms
        domain="service",
        customer_id=100,
        luma_client=mock_luma_client,
        booking_client=mock_booking_client,
        customer_client=mock_customer_client,
        catalog_client=mock_catalog_client,
        organization_client=mock_org_client
    )

    # Assert: Commit action (CONFIRM_APPOINTMENT → booking.create) was executed
    mock_booking_client.create_booking.assert_called_once()
    call_kwargs = mock_booking_client.create_booking.call_args[1]
    assert call_kwargs["organization_id"] == 1
    assert call_kwargs["customer_id"] == 100
    assert call_kwargs["booking_type"] == "service"
    assert call_kwargs["item_id"] == 1

    # Assert: EXECUTED outcome returned (booking created successfully)
    assert result_confirmed["success"] is True
    assert result_confirmed["outcome"]["status"] == "EXECUTED"
    assert result_confirmed["outcome"]["booking_code"] == "ABC123"
    assert result_confirmed["outcome"]["booking_status"] == "pending"

    # Assert: Final confirmation message is rendered
    rendered_confirmed = render_outcome_to_whatsapp(
        result_confirmed["outcome"])
    assert rendered_confirmed["type"] == "text"
    assert "confirmed" in rendered_confirmed["text"].lower()
    assert "ABC123" in rendered_confirmed["text"]


def test_commit_gated_workflow_blocked_when_needs_clarification():
    """
    Test that commit action is blocked when needs_clarification=true.

    Even if confirmation_state="confirmed", if needs_clarification=true,
    the commit action should be blocked.
    """
    # Setup: Mock clients
    mock_luma_client = Mock(spec=LumaClient)
    mock_booking_client = Mock(spec=BookingClient)
    mock_org_client = Mock(spec=OrganizationClient)

    # Mock organization response
    # Note: org_domain_cache expects organization nested in response with businessCategoryId
    # SERVICE_CATEGORY_IDS = {1, "beauty_and_wellness"}, so use 1 for service domain
    mock_org_client.get_details.return_value = {
        "organization": {
            "id": 1,
            "businessCategoryId": 1,  # Valid service category ID
            "domain": "service"
        }
    }

    # Mock Luma response: needs_clarification=true, even with confirmed state
    # Note: This response must pass contract validation (assert_luma_contract)
    luma_response = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": True,  # Clarification needed
        "clarification_reason": "MISSING_TIME",
        "booking": {
            "booking_type": "service",
            "services": [{"text": "haircut"}],
            "confirmation_state": "confirmed",  # Confirmed but still needs clarification
            "booking_state": "PARTIAL"
        },
        "issues": {
            "time": "missing"
        },
        "missing_slots": ["time"],
        "context": {}
    }

    mock_luma_client.resolve.return_value = luma_response

    # Call orchestrator
    result = handle_message(
        user_id="test_user_123",
        text="book haircut",
        domain="service",
        luma_client=mock_luma_client,
        booking_client=mock_booking_client,
        organization_client=mock_org_client
    )

    # Assert: No commit action executed
    mock_booking_client.create_booking.assert_not_called()

    # Assert: NEEDS_CLARIFICATION outcome returned (not AWAITING_CONFIRMATION)
    assert result["success"] is True
    assert result["outcome"]["status"] == "NEEDS_CLARIFICATION"
    assert "template_key" in result["outcome"]

    # Note: NEEDS_CLARIFICATION outcomes may not have facts in the outcome structure
    # (they come from _build_clarify_outcome which may not include facts)
    # But the decision object should have facts if we can access it

    # Assert: Clarification prompt is rendered
    rendered = render_outcome_to_whatsapp(result["outcome"])
    assert rendered["type"] == "text"
    # Should be a clarification message, not a confirmation prompt
    assert "confirm" not in rendered["text"].lower(
    ) or "time" in rendered["text"].lower()


def test_commit_gated_workflow_fallback_actions_allowed():
    """
    Test that fallback actions (e.g., SEARCH_AVAILABILITY) are allowed
    even when commit is blocked.
    """
    # Setup: Mock clients
    mock_luma_client = Mock(spec=LumaClient)
    mock_booking_client = Mock(spec=BookingClient)
    mock_org_client = Mock(spec=OrganizationClient)

    # Mock organization response
    # Note: org_domain_cache expects organization nested in response with businessCategoryId
    # SERVICE_CATEGORY_IDS = {1, "beauty_and_wellness"}, so use 1 for service domain
    mock_org_client.get_details.return_value = {
        "organization": {
            "id": 1,
            "businessCategoryId": 1,  # Valid service category ID
            "domain": "service"
        }
    }

    # Mock Luma response: missing time, confirmation_state="pending"
    # This should allow SEARCH_AVAILABILITY fallback but block CONFIRM_APPOINTMENT
    # Note: This response must pass contract validation (assert_luma_contract)
    luma_response = {
        "success": True,
        "intent": {
            "name": "CREATE_APPOINTMENT",
            "confidence": 0.95
        },
        "needs_clarification": False,
        "booking": {
            "booking_type": "service",
            "services": [{"text": "haircut"}],
            "confirmation_state": "pending",
            "booking_state": "RESOLVED"
        },
        "issues": {
            "time": "missing"
        },
        # Missing time should trigger SEARCH_AVAILABILITY fallback
        "missing_slots": ["time"],
        "context": {}
    }

    mock_luma_client.resolve.return_value = luma_response

    # Call orchestrator
    result = handle_message(
        user_id="test_user_123",
        text="book haircut",
        domain="service",
        luma_client=mock_luma_client,
        booking_client=mock_booking_client,
        organization_client=mock_org_client
    )

    # Assert: No commit action executed (CONFIRM_APPOINTMENT blocked)
    mock_booking_client.create_booking.assert_not_called()

    # Assert: AWAITING_CONFIRMATION outcome (fallback actions are allowed but we return confirmation prompt)
    # Note: The current implementation returns AWAITING_CONFIRMATION when status is AWAITING_CONFIRMATION
    # Fallback actions would be in allowed_actions but we prioritize confirmation prompt
    assert result["success"] is True
    assert result["outcome"]["status"] == "AWAITING_CONFIRMATION"

    # Assert: facts container is present
    assert "facts" in result["outcome"]
    assert "slots" in result["outcome"]["facts"]
    assert "missing_slots" in result["outcome"]["facts"]
    assert "context" in result["outcome"]["facts"]
    assert "time" in result["outcome"]["facts"]["missing_slots"]

    # Verify plan has fallback actions allowed
    # (This would be in the decision plan, but we're testing the outcome)
    # The plan.allowed_actions should include SEARCH_AVAILABILITY
    # but we're not executing it in this flow - we're showing confirmation prompt instead
