"""
Tests for Luma Contract Assertions
"""

import pytest

from core.contracts.luma_contracts import assert_luma_contract
from core.errors.exceptions import ContractViolation


def test_success_requires_intent_name():
    """Test that success=true requires intent.name."""
    response = {
        "success": True,
        "intent": {}  # Missing name
    }

    with pytest.raises(ContractViolation) as exc_info:
        assert_luma_contract(response)

    assert "intent.name is missing" in str(exc_info.value)


def test_needs_clarification_false_requires_resolved():
    """Test that needs_clarification=false requires RESOLVED state."""
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "booking_state": "PARTIAL"  # Should be RESOLVED
        }
    }

    with pytest.raises(ContractViolation) as exc_info:
        assert_luma_contract(response)

    assert "booking_state=PARTIAL (expected RESOLVED)" in str(exc_info.value)


def test_needs_clarification_true_requires_reason():
    """Test that needs_clarification=true requires clarification.reason."""
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": True,
        "clarification": {}  # Missing reason
    }

    with pytest.raises(ContractViolation) as exc_info:
        assert_luma_contract(response)

    assert "clarification.reason is missing" in str(exc_info.value)


def test_resolved_requires_datetime_range_start():
    """Test that RESOLVED state requires datetime_range.start."""
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "booking_state": "RESOLVED",
            "datetime_range": {}  # Missing start
        }
    }

    with pytest.raises(ContractViolation) as exc_info:
        assert_luma_contract(response)

    assert "datetime_range.start is missing" in str(exc_info.value)


def test_valid_resolved_booking():
    """Test valid resolved booking passes contract."""
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "services": [{"text": "haircut"}],
            "datetime_range": {"start": "2024-01-01T10:00:00Z"},
            "booking_state": "RESOLVED"
        }
    }

    # Should not raise
    assert_luma_contract(response)


def test_valid_partial_booking():
    """Test valid partial booking (clarification) passes contract."""
    response = {
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

    # Should not raise
    assert_luma_contract(response)
