"""
End-to-End Test: Capability Text Preserved When Missing Slots Present

Tests that capability rendering text is NOT overwritten by clarification rendering
when both conditions are true:
- status == "AWAITING_CAPABILITY"
- active_capability is set (e.g., "payment")
- missing_slots is non-empty (e.g., ["time"])

Expected behavior:
- result["text"] MUST contain capability text (payment link message)
- result["text"] MUST NOT contain clarification wording

This test validates the bug fix for AWAITING_CAPABILITY + missing_slots overwrite.
"""

import os
from datetime import datetime, timezone
from unittest.mock import Mock

from capabilities.adapters.payment import PaymentAdapter
from capabilities.clients.payment import MockPaymentClient, reset_payment_store
from capabilities.registry import clear_registry, register_adapter
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session

# Set execution mode to test
os.environ["CORE_EXECUTION_MODE"] = "test"

# Imports assume pytest has added src/ to PYTHONPATH (via pytest.ini)
# This test MUST be run with pytest, not directly with python


def test_capability_text_preserved_when_missing_slots_present():
    """
    Test that capability text is preserved when missing_slots is non-empty.

    Scenario:
    - User books appointment with payment required
    - Some slots are missing (e.g., time)
    - Status becomes AWAITING_CAPABILITY (payment gating)
    - Missing slots trigger clarification rendering path

    Expected:
    - result["text"] contains payment capability text
    - result["text"] does NOT contain clarification wording
    - result["_rendered_by"] == "CAPABILITY" (internal flag)
    """
    user_id = "test-capability-text-preserved"
    frozen_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Cleanup
    clear_session(user_id)
    clear_registry()
    reset_payment_store()

    try:
        # Register payment adapter
        payment_client = MockPaymentClient()
        payment_adapter = PaymentAdapter(payment_client=payment_client)
        register_adapter(payment_adapter)

        # Mock Luma response: booking with payment required, but missing time slot
        # This should trigger AWAITING_CAPABILITY (payment gating) AND missing_slots
        mock_luma_response = {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
            "needs_clarification": True,  # Missing time slot
            "booking": {
                "booking_type": "service",
                "booking_id": "test-booking-123",
                "code": "BOOK123",
                "services": [
                    {"text": "haircut", "canonical": "beauty_and_wellness.haircut"}
                ],
                "datetime_range": {
                    "start": "2026-01-16T14:00:00Z",
                    "end": "2026-01-16T14:30:00Z",
                },
                "confirmation_state": "pending",
                "booking_state": "INCOMPLETE",
                "total_amount": 50.00,
                "currency": "USD",
            },
            "facts": {"service_id": "haircut", "dates": ["2026-01-16"]},
            "slots": {
                "service_id": "haircut",
                "date": "2026-01-16",
                # time is missing - this triggers missing_slots
            },
            "missing_slots": ["time"],  # This would normally trigger clarification
            "context": {},
        }

        # Mock Luma client
        mock_luma_client = Mock(spec=LumaClient)
        mock_luma_client.resolve.return_value = mock_luma_response

        # Mock organization client - payment required
        mock_org_client = Mock(spec=OrganizationClient)
        mock_org_client.get_details.return_value = {
            "organization": {
                "payment_required": True,  # This triggers payment capability gating
                "businessCategoryId": 1,
            }
        }

        # Create payment intent in mock store (required for capability rendering)
        # The capability renderer will look for booking_code="BOOK123" from the mock response
        # But the mock's create_payment_intent uses booking_id and stores as "booking_{booking_id}"
        # So we directly add to the payment store with the correct booking_code key
        from capabilities.clients.payment.mock_payment import _PAYMENT_STATE

        booking_code = "BOOK123"  # From mock response booking.code
        _PAYMENT_STATE[booking_code] = {
            "intent_created": True,
            "payment_intent_id": "pi_test_123",
            "payment_url": f"https://pay.test/{booking_code}",
            "paid": False,
            "amount": 5000,  # 50.00 in minor units (cents)
            "currency": "USD",
            "booking_id": 123,
        }

        # Call handle_message
        result = handle_message(
            text="book haircut tomorrow",
            user_id=user_id,
            luma_client=mock_luma_client,
            organization_client=mock_org_client,
            frozen_time=frozen_time,
            organization_id=1,
        )

        # Assert: Result structure
        assert result is not None, "Result should not be None"
        assert result.get("success") is True, "Result should be successful"

        # Assert: Status is AWAITING_CAPABILITY
        outcome = result.get("outcome", {})
        assert (
            outcome.get("status") == "AWAITING_CAPABILITY"
        ), f"Expected status AWAITING_CAPABILITY, got {outcome.get('status')}"

        # Assert: Active capability is payment
        assert (
            outcome.get("active_capability") == "payment"
        ), f"Expected active_capability 'payment', got {outcome.get('active_capability')}"

        # Assert: Missing slots are present (this would trigger clarification normally)
        missing_slots = outcome.get("missing_slots", [])
        assert (
            "time" in missing_slots
        ), f"Expected 'time' in missing_slots, got {missing_slots}"

        # Assert: Text is present
        assert (
            "text" in result
        ), f"Expected 'text' in result, got keys: {list(result.keys())}"
        assert (
            result["text"] is not None
        ), f"Expected non-empty text, got: {result.get('text')}"
        assert (
            len(result["text"]) > 0
        ), f"Expected non-empty text, got: {result.get('text')}"

        text = result["text"]

        # Assert: Text contains capability/payment wording (not clarification)
        # Capability text should contain payment-related keywords
        text_lower = text.lower()

        # Payment capability text typically contains:
        # - "payment" or "pay"
        # - "link" or "url" or "complete"
        # - Or booking-related terms
        has_payment_keywords = (
            "payment" in text_lower
            or "pay" in text_lower
            or "link" in text_lower
            or "complete" in text_lower
            or "booking" in text_lower
        )
        assert (
            has_payment_keywords
        ), f"Expected payment/capability keywords in text, got: {text}"

        # Assert: Text does NOT contain clarification wording
        # Clarification text typically asks for missing information
        clarification_keywords = [
            "what time",
            "when",
            "which time",
            "please provide",
            "need to know",
            "missing",
        ]
        has_clarification_keywords = any(
            keyword in text_lower for keyword in clarification_keywords
        )
        assert (
            not has_clarification_keywords
        ), f"Text should NOT contain clarification wording, but got: {text}"

        # Assert: Internal flag indicates capability rendering
        assert (
            result.get("_rendered_by") == "CAPABILITY"
        ), f"Expected _rendered_by='CAPABILITY', got {result.get('_rendered_by')}"

    finally:
        # Cleanup
        clear_session(user_id)
        clear_registry()
        reset_payment_store()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
