"""
Unit Tests: Capability Runner

Tests CapabilityRunner passthrough behavior and adapter lifecycle management.
These tests validate runner internals without involving core.
"""

import os
import sys
from pathlib import Path

from extensions.capabilities.adapters.payment import PaymentAdapter
from extensions.capabilities.clients.payment import (
    MockPaymentClient,
    mark_payment_as_paid,
    reset_payment_store,
)
from extensions.capabilities.registry import clear_registry, register_adapter
from extensions.capabilities.runner import CapabilityRunner, RunnerResult

# Add src/ to Python path (test is in src/capabilities/tests/)
src_path = Path(__file__).resolve().parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Set execution mode to test
os.environ["CORE_EXECUTION_MODE"] = "test"


def _create_test_context(
    booking_id=123, booking_code="booking_123", amount=100.0, currency="USD"
):
    """Helper to create test context with booking information."""
    return {
        "user_id": "test-user",
        "session_slots": {
            "booking_id": booking_id,
            "booking_code": booking_code,
            "total_amount": amount,
            "currency": currency,
        },
        "session_facts": {},
        "domain": "service",
        "timezone": "UTC",
        "organization_id": 1,
        "transaction_id": "test-001",
    }


def test_runner_passthrough_on_initiation():
    """
    Test: Runner returns passthrough=False on first activation when adapter is active.

    Arrange: Payment adapter registered, core outcome with AWAITING_CAPABILITY
    Call: runner.handle(user_input=None, core_outcome, context)
    Assert: passthrough=False, active_capability="payment", text is not None
    """
    # Arrange
    reset_payment_store()
    clear_registry()
    payment_client = MockPaymentClient()
    register_adapter(PaymentAdapter(payment_client=payment_client))

    runner = CapabilityRunner()
    core_outcome = {"status": "AWAITING_CAPABILITY", "active_capability": "payment"}
    context = _create_test_context(
        booking_id=123, booking_code="booking_123", amount=100.0, currency="USD"
    )

    try:
        # Act - first activation
        result = runner.handle(
            user_input=None, core_outcome=core_outcome, context=context
        )

        # Assert
        assert isinstance(
            result, RunnerResult
        ), f"Result should be RunnerResult, got: {type(result)}"
        assert (
            result.passthrough is False
        ), f"Runner should return passthrough=False on first activation when adapter is active, got: {result.passthrough}"
        assert (
            result.active_capability == "payment"
        ), f"active_capability should be 'payment', got: {result.active_capability}"
        assert (
            result.text is not None
        ), "Runner should return adapter text on first activation"
        assert (
            result.facts is None
        ), f"Facts should be None during initiation, got: {result.facts}"

        print("  PASS: Runner returns passthrough=False on first activation")

    finally:
        reset_payment_store()
        clear_registry()


def test_runner_passthrough_on_completion():
    """
    Test: Runner returns passthrough=True when adapter completes.

    Arrange: Payment adapter registered, payment marked as paid, core outcome with AWAITING_CAPABILITY
    Call: runner.handle(user_input="ok", core_outcome, context) after payment is paid
    Assert: passthrough=True, active_capability=None, facts contain payment_satisfied
    """
    # Arrange
    reset_payment_store()
    clear_registry()
    payment_client = MockPaymentClient()
    register_adapter(PaymentAdapter(payment_client=payment_client))

    runner = CapabilityRunner()
    core_outcome = {"status": "AWAITING_CAPABILITY", "active_capability": "payment"}
    context = _create_test_context(
        booking_id=789, booking_code="booking_789", amount=75.0, currency="USD"
    )

    # Create payment intent by calling start() first
    first_result = runner.handle(
        user_input=None, core_outcome=core_outcome, context=context
    )
    assert (
        first_result.passthrough is False
    ), "Payment should be active after initiation"

    # Mark payment as paid (simulate webhook)
    mark_payment_as_paid(1, "booking_789")

    try:
        # Act - reconciliation (adapter should complete)
        result = runner.handle(
            user_input="ok", core_outcome=core_outcome, context=context
        )

        # Assert
        assert isinstance(
            result, RunnerResult
        ), f"Result should be RunnerResult, got: {type(result)}"
        assert (
            result.passthrough is True
        ), f"Runner should return passthrough=True when adapter completes, got: {result.passthrough}"
        assert (
            result.active_capability is None
        ), f"active_capability should be None after completion, got: {result.active_capability}"
        assert (
            result.facts is not None
        ), "Runner should return facts when adapter completes"
        assert (
            "payment_satisfied" in result.facts
        ), f"Facts should contain 'payment_satisfied', got: {list(result.facts.keys())}"
        assert (
            result.facts["payment_satisfied"] is True
        ), f"payment_satisfied should be True, got: {result.facts.get('payment_satisfied')}"

        print("  PASS: Runner returns passthrough=True when adapter completes")

    finally:
        reset_payment_store()
        clear_registry()


def test_runner_passthrough_when_no_capability():
    """
    Test: Runner returns passthrough=True when no capability is active.

    Arrange: Core outcome with status != AWAITING_CAPABILITY
    Call: runner.handle(user_input=None, core_outcome, context)
    Assert: passthrough=True, active_capability=None
    """
    # Arrange
    runner = CapabilityRunner()
    core_outcome = {"status": "READY", "active_capability": None}
    context = _create_test_context()

    # Act
    result = runner.handle(user_input=None, core_outcome=core_outcome, context=context)

    # Assert
    assert isinstance(
        result, RunnerResult
    ), f"Result should be RunnerResult, got: {type(result)}"
    assert (
        result.passthrough is True
    ), f"Runner should return passthrough=True when no capability is active, got: {result.passthrough}"
    assert (
        result.active_capability is None
    ), f"active_capability should be None, got: {result.active_capability}"
    assert (
        result.text is None
    ), f"Text should be None when no capability is active, got: {result.text}"
    assert (
        result.facts is None
    ), f"Facts should be None when no capability is active, got: {result.facts}"

    print("  PASS: Runner returns passthrough=True when no capability is active")


if __name__ == "__main__":
    print("Running CapabilityRunner unit tests...")
    print()

    try:
        test_runner_passthrough_on_initiation()
        print()
        test_runner_passthrough_on_completion()
        print()
        test_runner_passthrough_when_no_capability()
        print()
        print("All tests passed!")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
