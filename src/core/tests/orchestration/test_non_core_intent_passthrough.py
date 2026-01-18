"""
Tests for non-core intent pass-through behavior.

Verifies that non-core intents are passed through as non-orchestrated signals
rather than being rejected as errors.
"""

import pytest
from unittest.mock import Mock, patch

from core.orchestration.orchestrator import handle_message, _handle_non_core_intent
from core.routing.intents.base_intents import CORE_BASE_INTENTS


class TestNonCoreIntentPassthrough:
    """Test that non-core intents are passed through correctly."""
    
    def test_handle_non_core_intent_returns_non_orchestrated_outcome(self):
        """Verify _handle_non_core_intent returns correct outcome structure."""
        luma_response = {
            "success": True,
            "intent": {"name": "PAYMENT", "confidence": 0.9},
            "slots": {"amount": "100"},
            "booking": {},
            "needs_clarification": False,
        }
        decision = {
            "intent_name": "PAYMENT",
            "facts": {"slots": {"amount": "100"}},
            "booking": {},
        }
        user_id = "test_user_123"
        
        result = _handle_non_core_intent(luma_response, decision, user_id)
        
        assert result["success"] is True
        assert result["outcome"]["status"] == "NON_CORE_INTENT"
        assert result["outcome"]["intent_name"] == "PAYMENT"
        assert "facts" in result["outcome"]
        assert "slots" in result["outcome"]["facts"]
        assert result["outcome"]["facts"]["slots"]["amount"] == "100"
    
    @patch('core.orchestration.orchestrator.LumaClient')
    @patch('core.orchestration.orchestrator.BookingClient')
    @patch('core.orchestration.orchestrator.CustomerClient')
    @patch('core.orchestration.orchestrator.CatalogClient')
    @patch('core.orchestration.orchestrator.OrganizationClient')
    def test_non_core_intent_passed_through_in_handle_message(
        self,
        mock_org_client,
        mock_catalog_client,
        mock_customer_client,
        mock_booking_client,
        mock_luma_client
    ):
        """Verify handle_message passes through non-core intents."""
        # Setup mock Luma response with non-core intent
        mock_luma_instance = Mock()
        mock_luma_instance.resolve.return_value = {
            "success": True,
            "intent": {"name": "BOOKING_INQUIRY", "confidence": 0.95},
            "slots": {},
            "booking": {},
            "needs_clarification": False,
            "status": "ready",
        }
        mock_luma_client.return_value = mock_luma_instance
        
        # Mock organization client
        mock_org_instance = Mock()
        mock_org_instance.get_details.return_value = {
            "organization": {"id": 1, "domain": "service"}
        }
        mock_org_client.return_value = mock_org_instance
        
        result = handle_message(
            user_id="test_user",
            text="what is my booking status?",
            luma_client=mock_luma_instance,
            organization_client=mock_org_instance,
        )
        
        # Should pass through, not error
        assert result["success"] is True
        assert result["outcome"]["status"] == "NON_CORE_INTENT"
        assert result["outcome"]["intent_name"] == "BOOKING_INQUIRY"
        assert "facts" in result["outcome"]
        # Should NOT have executed any booking actions
        mock_booking_client.assert_not_called()
    
    def test_core_intents_still_orchestrated(self):
        """Verify core intents are still orchestrated normally."""
        # This is a sanity check - core intents should not be passed through
        for intent in CORE_BASE_INTENTS:
            # Just verify the intent is recognized as core
            from core.routing.intents.base_intents import is_core_intent
            assert is_core_intent(intent) is True
    
    def test_non_core_intent_preserves_luma_data(self):
        """Verify non-core intent handler preserves all Luma response data."""
        luma_response = {
            "success": True,
            "intent": {"name": "QUOTE", "confidence": 0.8},
            "slots": {"service_id": "haircut", "datetime_range": {"start": "2025-01-01T10:00:00Z"}},
            "booking": {"confirmation_state": "pending"},
            "needs_clarification": False,
            "clarification_reason": None,
            "issues": {},
        }
        decision = {
            "intent_name": "QUOTE",
            "facts": {"slots": {"service_id": "haircut"}},
            "booking": {"confirmation_state": "pending"},
        }
        
        result = _handle_non_core_intent(luma_response, decision, "test_user")
        
        # Verify facts structure preserves slots and context
        facts = result["outcome"]["facts"]
        assert facts["slots"]["service_id"] == "haircut"
        assert facts["slots"]["datetime_range"]["start"] == "2025-01-01T10:00:00Z"


class TestNonCoreIntentExamples:
    """Test specific non-core intent examples."""
    
    def test_payment_intent_passed_through(self):
        """Verify PAYMENT intent is passed through."""
        luma_response = {
            "success": True,
            "intent": {"name": "PAYMENT", "confidence": 1.0},
            "slots": {},
            "booking": {},
        }
        decision = {"intent_name": "PAYMENT", "facts": {}, "booking": {}}
        
        result = _handle_non_core_intent(luma_response, decision, "user")
        assert result["outcome"]["intent_name"] == "PAYMENT"
        assert result["outcome"]["status"] == "NON_CORE_INTENT"
    
    def test_booking_inquiry_passed_through(self):
        """Verify BOOKING_INQUIRY intent is passed through."""
        luma_response = {
            "success": True,
            "intent": {"name": "BOOKING_INQUIRY", "confidence": 0.9},
            "slots": {},
            "booking": {},
        }
        decision = {"intent_name": "BOOKING_INQUIRY", "facts": {}, "booking": {}}
        
        result = _handle_non_core_intent(luma_response, decision, "user")
        assert result["outcome"]["intent_name"] == "BOOKING_INQUIRY"
        assert result["outcome"]["status"] == "NON_CORE_INTENT"
    
    def test_availability_intent_passed_through(self):
        """Verify AVAILABILITY intent is passed through."""
        luma_response = {
            "success": True,
            "intent": {"name": "AVAILABILITY", "confidence": 0.85},
            "slots": {},
            "booking": {},
        }
        decision = {"intent_name": "AVAILABILITY", "facts": {}, "booking": {}}
        
        result = _handle_non_core_intent(luma_response, decision, "user")
        assert result["outcome"]["intent_name"] == "AVAILABILITY"
        assert result["outcome"]["status"] == "NON_CORE_INTENT"


class TestNonCoreIntentInvariants:
    """Test invariants for non-core intent handling."""
    
    def test_non_core_intents_never_error(self):
        """Verify non-core intents never return success=False."""
        luma_response = {
            "success": True,
            "intent": {"name": "PAYMENT", "confidence": 0.9},
            "slots": {},
        }
        decision = {"intent_name": "PAYMENT", "facts": {}}
        
        result = _handle_non_core_intent(luma_response, decision, "user")
        assert result["success"] is True
        assert "error" not in result
    
    def test_non_core_intents_preserve_facts_structure(self):
        """Verify facts structure includes slots, missing_slots, and context."""
        luma_response = {
            "success": True,
            "intent": {"name": "QUOTE", "confidence": 0.8},
            "slots": {"service_id": "haircut"},
            "missing_slots": ["date"],
            "context": {"previous_intent": "CREATE_APPOINTMENT"},
        }
        decision = {
            "intent_name": "QUOTE",
            "facts": {
                "slots": {"service_id": "haircut"},
                "missing_slots": ["date"],
                "context": {"previous_intent": "CREATE_APPOINTMENT"},
            }
        }
        
        result = _handle_non_core_intent(luma_response, decision, "user")
        facts = result["outcome"]["facts"]
        
        assert "slots" in facts
        assert "missing_slots" in facts
        assert "context" in facts
        assert facts["slots"]["service_id"] == "haircut"
        assert facts["missing_slots"] == ["date"]
        assert facts["context"]["previous_intent"] == "CREATE_APPOINTMENT"
    
    def test_core_intents_not_affected(self):
        """Verify core intents are not affected by non-core intent handling."""
        from core.routing.intents.base_intents import CORE_BASE_INTENTS
        
        # All core intents should still be recognized as core
        for intent in CORE_BASE_INTENTS:
            from core.routing.intents.base_intents import is_core_intent
            assert is_core_intent(intent) is True

