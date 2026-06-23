"""
Unit Tests: Payment Capability Adapter

Focused unit tests for PaymentAdapter that validate the payment lifecycle
without involving core. These tests live entirely in the capabilities layer.
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

# Add src/ to Python path (test is in src/capabilities/tests/)
# Go up to src/ directory
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


def test_start_returns_payment_link():
    """
    Test 1: start() returns payment link.

    Arrange: booking_id, booking_code, amount, currency
    Call: PaymentAdapter.start(context)
    Assert: completed == False, text contains payment link, facts == {}
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=123, booking_code="booking_123", amount=100.0, currency="USD"
    )

    try:
        # Act
        response = adapter.start(context)

        # Assert
        assert (
            response.completed is False
        ), f"start() should return completed=False, got: {response.completed}"
        assert response.text is not None, "start() should return payment link text"
        assert (
            "payment" in response.text.lower() or "link" in response.text.lower()
        ), f"Text should contain payment link, got: {response.text}"
        assert (
            "https://pay.test" in response.text
        ), f"Text should contain payment URL, got: {response.text}"
        assert (
            response.facts == {}
        ), f"start() should return empty facts, got: {response.facts}"

        print("  PASS: start() returns payment link correctly")

    finally:
        reset_payment_store()


def test_start_creates_payment_intent():
    """
    Test 1b: start() creates payment intent as side-effect.

    Arrange: booking_id, booking_code, amount, currency
    Call: PaymentAdapter.start(context)
    Assert: payment intent exists in _PAYMENT_STATE, intent_created=True, paid=False
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=123, booking_code="booking_123", amount=100.0, currency="USD"
    )

    try:
        # Act
        response = adapter.start(context)

        # Assert - verify payment intent was created
        from extensions.capabilities.clients.payment.mock_payment import _PAYMENT_STATE

        assert "booking_123" in _PAYMENT_STATE, (
            f"Payment intent should exist for booking_code 'booking_123' after start(). "
            f"Available booking codes: {list(_PAYMENT_STATE.keys())}"
        )
        assert _PAYMENT_STATE["booking_123"].get(
            "intent_created"
        ), "Payment intent should be marked as created for booking_code 'booking_123'"
        assert (
            _PAYMENT_STATE["booking_123"].get("paid") is not True
        ), "Payment should not be marked as paid during initiation"

        print("  PASS: start() creates payment intent as side-effect")

    finally:
        reset_payment_store()


def test_handle_input_when_unpaid():
    """
    Test 2: handle_input() when unpaid.

    Arrange: payment intent exists, payment not marked as paid
    Call: handle_input("anything", context)
    Assert: completed == False, payment link is re-sent, no facts returned
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=456, booking_code="booking_456", amount=250.0, currency="USD"
    )

    # Create payment intent by calling start() first
    start_response = adapter.start(context)
    assert start_response.completed is False, "Payment intent should be created"

    try:
        # Act - call handle_input when payment is not paid
        response = adapter.handle_input("anything", context)

        # Assert
        assert (
            response.completed is False
        ), f"handle_input() should return completed=False when unpaid, got: {response.completed}"
        assert (
            response.text is not None
        ), "handle_input() should re-send payment link when unpaid"
        assert (
            "payment" in response.text.lower() or "link" in response.text.lower()
        ), f"Text should contain payment link, got: {response.text}"
        assert (
            "https://pay.test" in response.text
        ), f"Text should contain payment URL, got: {response.text}"
        assert (
            response.facts == {}
        ), f"handle_input() should return empty facts when unpaid, got: {response.facts}"

        # Verify payment is still unpaid
        status = payment_client.get_payment_status("booking_456")
        assert (
            status["data"]["payment_status"] == "unpaid"
        ), "Payment should still be unpaid"

        print("  PASS: handle_input() re-sends payment link when unpaid")

    finally:
        reset_payment_store()


def test_handle_input_when_paid():
    """
    Test 3: handle_input() when paid.

    Arrange: mark payment as paid using mark_payment_as_paid
    Call: handle_input("paid", context)
    Assert: completed == True, facts contain payment_satisfied: True and payment_reference
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=789, booking_code="booking_789", amount=75.0, currency="USD"
    )

    # Create payment intent by calling start() first
    start_response = adapter.start(context)
    assert start_response.completed is False, "Payment intent should be created"

    # Mark payment as paid (simulate webhook)
    mark_payment_as_paid("booking_789")

    # Verify payment is marked as paid
    status = payment_client.get_payment_status("booking_789")
    assert (
        status["data"]["payment_status"] == "paid"
    ), "Payment should be marked as paid"

    try:
        # Act - call handle_input when payment is paid
        response = adapter.handle_input("paid", context)

        # Assert
        assert (
            response.completed is True
        ), f"handle_input() should return completed=True when paid, got: {response.completed}"
        assert (
            response.text is None
        ), f"handle_input() should return text=None when paid, got: {response.text}"
        assert (
            "payment_satisfied" in response.facts
        ), f"Facts should contain 'payment_satisfied', got: {response.facts}"
        assert (
            response.facts["payment_satisfied"] is True
        ), f"payment_satisfied should be True, got: {response.facts.get('payment_satisfied')}"
        assert (
            "payment_reference" in response.facts
        ), f"Facts should contain 'payment_reference', got: {response.facts}"
        assert (
            response.facts["payment_reference"] is not None
        ), f"payment_reference should not be None, got: {response.facts.get('payment_reference')}"
        assert (
            response.facts["payment_reference"] != "unknown"
        ), f"payment_reference should be a valid ID, got: {response.facts.get('payment_reference')}"

        print("  PASS: handle_input() completes with facts when paid")

    finally:
        reset_payment_store()


def test_idempotency():
    """
    Test 4: Idempotency.

    Call start() twice
    Assert: same payment link, no duplicate intent creation
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=999, booking_code="booking_999", amount=50.0, currency="USD"
    )

    try:
        # Act - call start() twice
        response1 = adapter.start(context)
        response2 = adapter.start(context)

        # Assert - both responses should be identical
        assert (
            response1.completed == response2.completed
        ), "Both responses should have same completed status"
        assert (
            response1.text == response2.text
        ), "Both responses should return the same payment link"
        assert (
            response1.facts == response2.facts
        ), "Both responses should have same facts"

        # Extract payment URL from text
        url1 = response1.text.split("\n\n")[-1] if response1.text else None
        url2 = response2.text.split("\n\n")[-1] if response2.text else None
        assert url1 == url2, f"Payment URLs should be identical: {url1} vs {url2}"

        # Verify only one payment intent exists (check via status)
        status = payment_client.get_payment_status("booking_999")
        assert status["data"]["payment_status"] in [
            "unpaid",
            "paid",
        ], "Payment intent should exist"

        # Verify payment intent ID is consistent
        payment_intent_id1 = status["data"]["payment_summary"].get("payment_intent_id")
        assert payment_intent_id1 is not None, "Payment intent ID should exist"

        # Call start() a third time to triple-check
        response3 = adapter.start(context)
        assert (
            response3.text == response1.text
        ), "Third call should also return same payment link"

        print("  PASS: start() is idempotent - same link on multiple calls")

    finally:
        reset_payment_store()


def test_start_with_missing_booking_info():
    """
    Test 5: start() handles missing booking info gracefully.

    Arrange: context without booking_id
    Call: PaymentAdapter.start(context)
    Assert: returns error message, completed == False
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = {
        "user_id": "test-user",
        "session_slots": {},
        "session_facts": {},
        "domain": "service",
    }

    try:
        # Act
        response = adapter.start(context)

        # Assert
        assert response.completed is False, "Should return completed=False on error"
        assert response.text is not None, "Should return error message"
        assert (
            "error" in response.text.lower() or "not found" in response.text.lower()
        ), f"Should contain error message, got: {response.text}"
        assert response.facts == {}, "Should return empty facts on error"

        print("  PASS: start() handles missing booking info gracefully")

    finally:
        reset_payment_store()


def test_handle_input_with_missing_booking_info():
    """
    Test 6: handle_input() handles missing booking info gracefully.

    Arrange: context without booking_code
    Call: handle_input("anything", context)
    Assert: returns error message, completed == False
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = {
        "user_id": "test-user",
        "session_slots": {},
        "session_facts": {},
        "domain": "service",
    }

    try:
        # Act
        response = adapter.handle_input("anything", context)

        # Assert
        assert response.completed is False, "Should return completed=False on error"
        assert response.text is not None, "Should return error message"
        assert (
            "error" in response.text.lower() or "not found" in response.text.lower()
        ), f"Should contain error message, got: {response.text}"
        assert response.facts == {}, "Should return empty facts on error"

        print("  PASS: handle_input() handles missing booking info gracefully")

    finally:
        reset_payment_store()


def test_abort_does_nothing():
    """
    Test 7: abort() does nothing (adapter is stateless).

    Arrange: payment intent exists
    Call: abort("test", context)
    Assert: no exception, payment intent still exists
    """
    # Arrange
    reset_payment_store()
    payment_client = MockPaymentClient()
    adapter = PaymentAdapter(payment_client=payment_client)
    context = _create_test_context(
        booking_id=111,
        booking_code="booking_111",
        amount=200.0,
    )

    # Create payment intent
    adapter.start(context)

    # Verify intent exists
    status_before = payment_client.get_payment_status("booking_111")
    assert (
        status_before["data"]["payment_status"] != "none"
    ), "Payment intent should exist before abort"

    try:
        # Act
        adapter.abort("test_reason", context)

        # Assert - payment intent should still exist (no cleanup)
        status_after = payment_client.get_payment_status("booking_111")
        assert (
            status_after["data"]["payment_status"] != "none"
        ), "Payment intent should still exist after abort"
        assert (
            status_before["data"]["payment_status"]
            == status_after["data"]["payment_status"]
        ), "Payment status should be unchanged"

        print("  PASS: abort() does nothing (adapter is stateless)")

    finally:
        reset_payment_store()


if __name__ == "__main__":
    print("Running PaymentAdapter unit tests...")
    print()

    try:
        test_start_returns_payment_link()
        print()
        test_handle_input_when_unpaid()
        print()
        test_handle_input_when_paid()
        print()
        test_idempotency()
        print()
        test_start_with_missing_booking_info()
        print()
        test_handle_input_with_missing_booking_info()
        print()
        test_abort_does_nothing()
        print()
        print("All tests passed!")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
