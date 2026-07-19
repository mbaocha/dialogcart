"""
Tests for core-owned base intents and boundary enforcement.

These tests verify that:
1. Core base intents are correctly defined
2. is_core_intent membership works correctly
"""

from core.planning.policy.base_intents import (
    CORE_BASE_INTENTS,
    is_core_intent,
)


class TestCoreBaseIntents:
    """Test core base intent definitions."""

    def test_core_base_intents_defined(self):
        """Verify all expected core base intents are defined."""
        expected_intents = {
            "CREATE_APPOINTMENT",
            "CREATE_RESERVATION",
            "MODIFY_BOOKING",
            "CANCEL_BOOKING",
        }
        assert CORE_BASE_INTENTS == expected_intents

    def test_core_base_intents_is_set(self):
        """Verify CORE_BASE_INTENTS is a set."""
        assert isinstance(CORE_BASE_INTENTS, set)
        assert len(CORE_BASE_INTENTS) == 4


class TestIntentValidation:
    """Test intent membership helpers."""

    def test_is_core_intent_returns_true_for_core_intents(self):
        """Verify is_core_intent returns True for all core intents."""
        for intent in CORE_BASE_INTENTS:
            assert is_core_intent(intent) is True

    def test_is_core_intent_returns_false_for_non_core_intents(self):
        """Verify is_core_intent returns False for non-core intents."""
        non_core_intents = [
            "BOOKING_INQUIRY",
            "AVAILABILITY",
            "QUOTE",
            "DETAILS",
            "DISCOVERY",
            "RECOMMENDATION",
            "PAYMENT",
            "UNKNOWN",
            "",
            "INVALID_INTENT",
        ]
        for intent in non_core_intents:
            assert is_core_intent(intent) is False


class TestBoundaryEnforcement:
    """Test that boundary enforcement works correctly."""

    def test_core_intents_are_explicitly_defined(self):
        """Verify core intents are explicitly declared (not inferred)."""
        assert "CREATE_APPOINTMENT" in CORE_BASE_INTENTS
        assert "CREATE_RESERVATION" in CORE_BASE_INTENTS
        assert "MODIFY_BOOKING" in CORE_BASE_INTENTS
        assert "CANCEL_BOOKING" in CORE_BASE_INTENTS

    def test_no_implicit_intents(self):
        """Verify we don't accidentally include intents that shouldn't be core."""
        non_core_intents = [
            "BOOKING_INQUIRY",
            "AVAILABILITY",
            "QUOTE",
            "DETAILS",
            "DISCOVERY",
            "RECOMMENDATION",
            "PAYMENT",
        ]
        for intent in non_core_intents:
            assert intent not in CORE_BASE_INTENTS
