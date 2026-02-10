"""
Unit Tests: Capability Renderer Shadow Mode

Tests that shadow renderer produces equivalent text to adapter text at API boundary.
This validates equivalence before switching source of truth.

NOTE: This test is DISABLED in the current baseline architecture.
It tests behavior (shadow renderer, post-PLAN_FINAL rendering hook) that does not
exist in the current baseline. This test belongs to a future rendering-refactor
architecture and is intentionally disabled until that refactor is implemented.

To re-enable: Remove the @pytest.mark.skip decorator once shadow rendering is
fully implemented in the baseline architecture.
"""

import pytest
from unittest.mock import Mock, patch
from core.orchestration.orchestrator import handle_message
from core.orchestration.nlu import LumaClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.session import clear_session, get_session
from capabilities.adapters.payment import PaymentAdapter
from capabilities.clients.payment import MockPaymentClient, reset_payment_store
from capabilities.registry import register_adapter, clear_registry
import os

# Set execution mode to test
os.environ["CORE_EXECUTION_MODE"] = "test"


@pytest.mark.skip(reason="Test belongs to future rendering-refactor architecture. "
                         "Shadow renderer and post-PLAN_FINAL rendering hook do not exist in current baseline.")
def test_shadow_renderer_equivalence_payment():
    """
    Test that shadow renderer text matches adapter text at API boundary.
    
    This test validates:
    1. Normalized API text == adapter-derived text (current behavior)
    2. shadow_renderer_text == normalized API text (exact match for equivalence)
    
    This locks equivalence at API boundary before switching source of truth.
    """
    user_id = "test-shadow-renderer"
    text = "book a room"
    
    # Cleanup
    clear_session(user_id)
    clear_registry()
    reset_payment_store()
    
    # Register payment adapter
    payment_client = MockPaymentClient()
    register_adapter(PaymentAdapter(payment_client=payment_client))
    
    try:
        # Mock Luma client (response with all required slots)
        mock_luma_response = {
            "success": True,
            "intent": {
                "name": "CREATE_RESERVATION",
                "confidence": 0.95
            },
            "needs_clarification": False,
            "booking": {
                "booking_type": "reservation",
                "services": [
                    {
                        "text": "room",
                        "canonical": "hospitality.room"
                    }
                ],
                "datetime_range": {
                    "start": "2026-01-20T14:00:00Z",
                    "end": "2026-01-22T11:00:00Z"
                },
                "booking_state": "RESOLVED",
                "booking_id": 12345,
                "booking_code": "booking_12345",
                "total_amount": 200.0,
                "currency": "USD"
            },
            "slots": {
                "service_id": "room",
                "date_range": "2026-01-20 to 2026-01-22",
                "booking_id": 12345,
                "booking_code": "booking_12345",
                "total_amount": 200.0,
                "currency": "USD"
            },
            "missing_slots": [],
            "context": {},
            "facts": {}
        }
        
        mock_luma_client = Mock(spec=LumaClient)
        mock_luma_client.resolve.return_value = mock_luma_response
        
        # Mock organization client with payment_required = True
        mock_org_client = Mock(spec=OrganizationClient)
        mock_org_client.get_details.return_value = {
            "organization": {
                "payment_required": True,
                "businessCategoryId": 1
            }
        }
        
        # Call handle_message (triggers capability rendering + shadow rendering)
        result = handle_message(
            text=text,
            user_id=user_id,
            luma_client=mock_luma_client,
            organization_client=mock_org_client,
            organization_id=1
        )
        
        # ASSERTION 1: Normalized API text == adapter-derived text (current behavior)
        api_text = result.get("text")
        assert api_text is not None, (
            f"[EQUIVALENCE] API text must be present. "
            f"Result keys: {list(result.keys())}"
        )
        assert isinstance(api_text, str), (
            f"[EQUIVALENCE] API text must be a string. Got: {type(api_text)}"
        )
        assert len(api_text) > 0, (
            f"[EQUIVALENCE] API text must be non-empty. Got: {api_text!r}"
        )
        
        # ASSERTION 2: shadow_renderer_text == normalized API text (exact match)
        outcome = result.get("outcome", {})
        debug = outcome.get("debug", {})
        shadow_renderer_text = debug.get("shadow_renderer_text")
        
        assert shadow_renderer_text is not None, (
            f"[EQUIVALENCE] shadow_renderer_text must be present in outcome.debug. "
            f"Outcome keys: {list(outcome.keys())}, debug keys: {list(debug.keys())}"
        )
        assert isinstance(shadow_renderer_text, str), (
            f"[EQUIVALENCE] shadow_renderer_text must be a string. Got: {type(shadow_renderer_text)}"
        )
        
        # Exact match assertion - this locks equivalence at API boundary
        assert shadow_renderer_text == api_text, (
            f"[EQUIVALENCE] shadow_renderer_text must exactly match API text. "
            f"shadow_renderer_text={shadow_renderer_text!r}, "
            f"api_text={api_text!r}"
        )
        
        # Additional validation: both texts should contain payment link
        assert "payment" in api_text.lower() or "link" in api_text.lower() or "http" in api_text.lower(), (
            f"[EQUIVALENCE] API text should contain payment-related content. Got: {api_text!r}"
        )
        assert "payment" in shadow_renderer_text.lower() or "link" in shadow_renderer_text.lower() or "http" in shadow_renderer_text.lower(), (
            f"[EQUIVALENCE] shadow_renderer_text should contain payment-related content. Got: {shadow_renderer_text!r}"
        )
        
    finally:
        # Cleanup
        clear_session(user_id)
        clear_registry()
        reset_payment_store()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

