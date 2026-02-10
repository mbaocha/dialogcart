"""
Unit Tests: Capability Renderer Shadow Mode

Tests that shadow renderer produces equivalent text to adapter text at API boundary.
This validates equivalence before switching source of truth.
"""

import pytest
from unittest.mock import Mock
from core.orchestration.orchestrator import handle_message
from core.orchestration.nlu import LumaClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.session import clear_session, get_session, save_session
from capabilities.adapters.payment import PaymentAdapter
from capabilities.clients.payment import MockPaymentClient, reset_payment_store
from capabilities.registry import register_adapter, clear_registry
import os

# Set execution mode to test
os.environ["CORE_EXECUTION_MODE"] = "test"


@pytest.mark.xfail(
    reason="Core is not yet the source of truth for capability rendering"
)
def test_shadow_renderer_equivalence_payment():
    """
    Test that shadow renderer text matches adapter text at API boundary.
    
    This test validates:
    1. Adapter produces text (current behavior)
    2. Shadow renderer produces text (core rendering)
    3. shadow_renderer.text == adapter.text (exact match for equivalence)
    
    This locks equivalence at API boundary before switching source of truth.
    
    Fails if:
    - Shadow renderer returns None
    - Shadow renderer text does not match adapter text
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
        
        # Set up session with booking info (required for payment adapter)
        session_state = {
            "intent_name": "CREATE_RESERVATION",
            "slots": {
                "service_id": "room",
                "date_range": "2026-01-20 to 2026-01-22",
                "booking_id": 12345,
                "booking_code": "booking_12345",
                "total_amount": 200.0,
                "currency": "USD"
            },
            "missing_slots": [],
            "status": "READY",
            "active_capability": "payment"
        }
        save_session(user_id, session_state)
        
        # Call handle_message (triggers capability rendering + shadow rendering)
        result = handle_message(
            text=text,
            user_id=user_id,
            luma_client=mock_luma_client,
            organization_client=mock_org_client,
            organization_id=1
        )
        
        # ASSERTION 1: Adapter produces text (current behavior)
        # Adapter text may be promoted to result["text"] during normalization,
        # or stored in outcome["text"]. Check both locations.
        outcome = result.get("outcome", {})
        adapter_text = result.get("text") or outcome.get("text")
        assert adapter_text is not None, (
            f"[EQUIVALENCE] Adapter text must be present in result[\"text\"] or outcome[\"text\"]. "
            f"Result keys: {list(result.keys())}, outcome keys: {list(outcome.keys())}"
        )
        assert isinstance(adapter_text, str), (
            f"[EQUIVALENCE] Adapter text must be a string. Got: {type(adapter_text)}"
        )
        assert len(adapter_text) > 0, (
            f"[EQUIVALENCE] Adapter text must be non-empty. Got: {adapter_text!r}"
        )
        
        # ASSERTION 2: Shadow renderer produces text (core rendering)
        debug = outcome.get("debug", {})
        shadow_renderer = debug.get("shadow_renderer")
        
        assert shadow_renderer is not None, (
            f"[EQUIVALENCE] shadow_renderer must be present in outcome.debug. "
            f"Outcome keys: {list(outcome.keys())}, debug keys: {list(debug.keys())}"
        )
        assert isinstance(shadow_renderer, dict), (
            f"[EQUIVALENCE] shadow_renderer must be a dictionary. Got: {type(shadow_renderer)}"
        )
        
        shadow_renderer_text = shadow_renderer.get("text")
        assert shadow_renderer_text is not None, (
            f"[EQUIVALENCE] shadow_renderer.text must be present. "
            f"shadow_renderer keys: {list(shadow_renderer.keys())}"
        )
        assert isinstance(shadow_renderer_text, str), (
            f"[EQUIVALENCE] shadow_renderer.text must be a string. Got: {type(shadow_renderer_text)}"
        )
        
        # ASSERTION 3: Exact match - this locks equivalence at API boundary
        assert shadow_renderer_text == adapter_text, (
            f"[EQUIVALENCE] shadow_renderer.text must exactly match adapter.text. "
            f"shadow_renderer.text={shadow_renderer_text!r}, "
            f"adapter.text={adapter_text!r}"
        )
        
    finally:
        # Cleanup
        clear_session(user_id)
        clear_registry()
        reset_payment_store()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

