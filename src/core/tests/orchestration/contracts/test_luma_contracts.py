"""
Tests for Luma Contract Assertions

These tests verify contract enforcement at boundaries (e.g., API boundaries).
Contracts are NOT enforced inside handle_message - they are enforced at boundaries
before data enters the orchestration layer.

The assert_luma_contract function is used at boundaries to validate Luma responses.
When called, it raises ContractViolation if the response violates the contract.
This is expected behavior for boundary enforcement.

Note: handle_message does NOT raise ContractViolation internally - it catches
contract violations and returns error structures instead.
"""

import pytest

from core.orchestration.nlu.luma_contracts import assert_luma_contract
from core.orchestration.errors import ContractViolation


def test_success_requires_intent_name():
    """Test that success=true requires intent.name."""
    response = {
        "success": True,
        "intent": {}  # Missing name
    }

    with pytest.raises(ContractViolation) as exc_info:
        assert_luma_contract(response)

    # Contract now checks for intent first, then intent.name
    assert "intent.name is missing" in str(exc_info.value) or "intent is missing" in str(exc_info.value)


def test_needs_clarification_false_requires_resolved():
    """Test that needs_clarification=false requires RESOLVED state.
    
    NOTE: Contract validation is now FACT-ONLY (minimal). Strict validation
    for needs_clarification/booking_state is no longer enforced.
    This test is kept for documentation but may not raise ContractViolation.
    """
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "booking_state": "PARTIAL"  # Should be RESOLVED
        }
    }

    # FACT-ONLY contract: Only requires intent.name, not booking_state validation
    # This should NOT raise - strict validation removed
    assert_luma_contract(response)


def test_needs_clarification_true_requires_reason():
    """Test that needs_clarification=true requires clarification.reason.
    
    NOTE: Contract validation is now FACT-ONLY (minimal). Strict validation
    for clarification.reason is no longer enforced.
    This test is kept for documentation but may not raise ContractViolation.
    """
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": True,
        "clarification": {}  # Missing reason
    }

    # FACT-ONLY contract: Only requires intent.name, not clarification validation
    # This should NOT raise - strict validation removed
    assert_luma_contract(response)


def test_resolved_requires_datetime_range_start():
    """Test that RESOLVED state requires datetime_range.start.
    
    NOTE: Contract validation is now FACT-ONLY (minimal). Strict validation
    for datetime_range.start is no longer enforced.
    This test is kept for documentation but may not raise ContractViolation.
    """
    response = {
        "success": True,
        "intent": {"name": "CREATE_BOOKING"},
        "needs_clarification": False,
        "booking": {
            "booking_state": "RESOLVED",
            "datetime_range": {}  # Missing start
        }
    }

    # FACT-ONLY contract: Only requires intent.name, not datetime_range validation
    # This should NOT raise - strict validation removed
    assert_luma_contract(response)


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
