"""
Tests for non-core / non-durable intent pass-through via the live turn path.

Verifies that non-durable intents are short-circuited rather than rejected as
errors, and that core base-intent membership remains correct.
"""

from unittest.mock import Mock

from core.api.compat import handle_message
from core.planning.policy.base_intents import CORE_BASE_INTENTS, is_core_intent
from core.tests.harness.clients import stub_catalog_client


class TestNonCoreIntentPassthrough:
    """Test that non-durable intents are passed through correctly."""

    def test_non_durable_intent_passed_through_in_handle_message(self):
        """Verify handle_message short-circuits non-durable intents without error."""
        mock_luma_instance = Mock()
        mock_luma_instance.resolve.return_value = {
            "success": True,
            "entity_resolutions": {},
            "intent": {"name": "BOOKING_INQUIRY", "confidence": 0.95},
            "slots": {},
            "booking": {},
            "needs_clarification": False,
            "status": "ready",
        }

        mock_org_instance = Mock()
        mock_org_instance.get_details.return_value = {
            "organization": {"id": 1, "businessCategoryId": 1}
        }

        result = handle_message(
            user_id="test_user",
            text="what is my booking status?",
            luma_client=mock_luma_instance,
            organization_client=mock_org_instance,
            catalog_client=stub_catalog_client(),
            organization_id=1,
        )

        assert result["success"] is True
        plan = result["result"]
        assert (
            plan.get("status") == "NON_DURABLE_INTENT"
            or plan.get("intent_name") == "BOOKING_INQUIRY"
        )
        assert "facts" in plan or "slots" in plan

    def test_core_intents_still_recognized(self):
        """Verify core intents are still recognized as core."""
        for intent in CORE_BASE_INTENTS:
            assert is_core_intent(intent) is True


class TestNonCoreIntentInvariants:
    """Test invariants for non-core intent membership."""

    def test_core_intents_not_affected(self):
        """Verify core intents remain recognized as core."""
        for intent in CORE_BASE_INTENTS:
            assert is_core_intent(intent) is True
