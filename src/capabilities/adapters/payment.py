"""
Payment Capability Adapter

Handles payment intent creation, payment link delivery, and payment status checking.
Runs entirely within the capabilities layer using payment clients.

The adapter:
- Creates payment intent on start() (idempotent)
- Delivers payment link to user
- Polls payment status on handle_input()
- Completes when payment is confirmed
- Emits facts: payment_satisfied, payment_reference
"""

from typing import Dict, Any
from ..base import CapabilityAdapter, AdapterResponse
from ..clients.payment import PaymentClient


class PaymentAdapter(CapabilityAdapter):
    """
    Payment capability adapter.

    Manages payment flow:
    1. Creates payment intent (idempotent)
    2. Delivers payment link
    3. Checks payment status
    4. Completes when payment confirmed
    """

    def __init__(self, payment_client: PaymentClient):
        """
        Initialize payment adapter with a payment client.

        Args:
            payment_client: Payment client implementation (mock or real)
        """
        self.payment_client = payment_client

    @property
    def name(self) -> str:
        """Capability name: 'payment'"""
        return "payment"

    def _extract_booking_info(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract booking information from context.

        Looks in session_facts and session_slots for:
        - booking_id (int)
        - booking_code (str)
        - amount (float) - total amount to pay
        - currency (str) - currency code, defaults to "USD"

        Args:
            context: Context dictionary

        Returns:
            Dict with booking_id, booking_code, amount, currency

        Raises:
            ValueError: If required fields are missing
        """
        session_facts = context.get("session_facts", {})
        session_slots = context.get("session_slots", {})

        # Try to extract booking_id (int)
        booking_id = None
        if "booking_id" in session_slots:
            booking_id = session_slots["booking_id"]
        elif "booking_id" in session_facts:
            booking_id = session_facts["booking_id"]

        # Try to extract booking_code (str)
        booking_code = None
        if "booking_code" in session_slots:
            booking_code = session_slots["booking_code"]
        elif "booking_code" in session_facts:
            booking_code = session_facts["booking_code"]

        # If we have booking_id but not booking_code, derive it
        # (matches the mock's internal mapping)
        if booking_id and not booking_code:
            booking_code = f"booking_{booking_id}"

        # Try to extract amount (float)
        amount = None
        if "total_amount" in session_slots:
            amount = session_slots["total_amount"]
        elif "total_amount" in session_facts:
            amount = session_facts["total_amount"]
        elif "amount" in session_slots:
            amount = session_slots["amount"]
        elif "amount" in session_facts:
            amount = session_facts["amount"]

        # Extract currency (str), default to "USD"
        currency = "USD"
        if "currency" in session_slots:
            currency = session_slots["currency"]
        elif "currency" in session_facts:
            currency = session_facts["currency"]

        # Validate required fields
        if not booking_id:
            raise ValueError(
                "booking_id not found in session_facts or session_slots. "
                "Payment adapter requires booking_id to create payment intent."
            )

        if not booking_code:
            raise ValueError(
                "booking_code not found in session_facts or session_slots. "
                "Payment adapter requires booking_code to check payment status."
            )

        if amount is None:
            raise ValueError(
                "amount not found in session_facts or session_slots. "
                "Payment adapter requires amount to create payment intent."
            )

        # Ensure booking_id is int
        try:
            booking_id = int(booking_id)
        except (ValueError, TypeError):
            raise ValueError(
                f"booking_id must be an integer, got: {booking_id}")

        # Ensure amount is float
        try:
            amount = float(amount)
        except (ValueError, TypeError):
            raise ValueError(f"amount must be a number, got: {amount}")

        # Ensure booking_code is str
        booking_code = str(booking_code)

        return {
            "booking_id": booking_id,
            "booking_code": booking_code,
            "amount": amount,
            "currency": currency,
        }

    def start(self, context: Dict[str, Any]) -> AdapterResponse:
        """
        Start payment capability.

        Ensures payment intent exists and presents payment link to user.
        Idempotent: calling start() twice returns the same link.

        Args:
            context: Context dictionary with session_facts and session_slots

        Returns:
            AdapterResponse with:
                - completed: False (payment just started)
                - text: Payment link message
                - facts: Empty dict
        """
        try:
            booking_info = self._extract_booking_info(context)
        except ValueError as e:
            # If we can't extract booking info, return error message
            return AdapterResponse(
                completed=False,
                text=f"Payment setup error: {str(e)}",
                facts={}
            )

        booking_id = booking_info["booking_id"]
        booking_code = booking_info["booking_code"]
        amount = booking_info["amount"]
        currency = booking_info["currency"]

        # Create payment intent (idempotent - won't duplicate if already exists)
        try:
            intent_response = self.payment_client.create_payment_intent(
                booking_id=booking_id,
                amount=amount,
                currency=currency
            )

            if not intent_response.get("success"):
                return AdapterResponse(
                    completed=False,
                    text="Failed to create payment intent. Please try again.",
                    facts={}
                )

            payment_url = intent_response["data"]["payment_url"]

        except Exception as e:
            return AdapterResponse(
                completed=False,
                text=f"Payment setup error: {str(e)}",
                facts={}
            )

        # Fetch payment URL (should exist after intent creation)
        url_response = self.payment_client.get_payment_url(booking_code)

        if not url_response.get("success") or not url_response["data"].get("has_payment_intent"):
            return AdapterResponse(
                completed=False,
                text="Payment link not available. Please try again.",
                facts={}
            )

        # Use payment_url from URL response if available, otherwise use intent response
        final_payment_url = url_response["data"].get(
            "payment_url") or payment_url

        # Return payment link message
        return AdapterResponse(
            completed=False,
            text=(
                "Your booking is being held for 30 minutes.\n"
                "Please complete payment using the link below:\n\n"
                f"{final_payment_url}"
            ),
            facts={}
        )

    def handle_input(self, user_input: str, context: Dict[str, Any]) -> AdapterResponse:
        """
        Handle user input - check payment status.

        Polls payment status and either:
        - Re-sends payment link if not paid
        - Completes with payment_satisfied fact if paid

        Args:
            user_input: User message (unused, we poll status)
            context: Context dictionary with session_facts and session_slots

        Returns:
            AdapterResponse with:
                - completed: True if payment confirmed, False otherwise
                - text: Payment link (if not paid) or None (if paid)
                - facts: payment_satisfied and payment_reference (if paid)
        """
        try:
            booking_info = self._extract_booking_info(context)
        except ValueError as e:
            return AdapterResponse(
                completed=False,
                text=f"Payment error: {str(e)}",
                facts={}
            )

        booking_code = booking_info["booking_code"]

        # Check payment status
        try:
            status_response = self.payment_client.get_payment_status(
                booking_code)

            if not status_response.get("success"):
                return AdapterResponse(
                    completed=False,
                    text="Unable to check payment status. Please try again.",
                    facts={}
                )

            status_data = status_response["data"]
            payment_status = status_data.get("payment_status", "none")
            payment_required = status_data.get("payment_required", True)
            payment_summary = status_data.get("payment_summary", {})
            payment_intent_id = payment_summary.get("payment_intent_id")

        except Exception as e:
            return AdapterResponse(
                completed=False,
                text=f"Payment status error: {str(e)}",
                facts={}
            )

        # If payment is complete, return completion
        completed = payment_status == "paid" or not payment_required

        # LOG: Payment completion status (as requested in investigation prompt)
        import logging
        adapter_logger = logging.getLogger(__name__)
        adapter_logger.info(
            f"[PAYMENT_ADAPTER_DEBUG] payment_status={payment_status}, "
            f"payment_required={payment_required}, -> completed={completed}"
        )

        if completed:
            completion_facts = {
                "payment_satisfied": True,
                "payment_reference": payment_intent_id or "unknown",
            }
            adapter_logger.error(
                f"[PAYMENT_ADAPTER_DEBUG] Payment completed for booking_code={booking_code}: "
                f"payment_status={payment_status}, payment_required={payment_required}, "
                f"payment_intent_id={payment_intent_id}, "
                f"returning AdapterResponse(completed=True, facts={completion_facts})"
            )

            return AdapterResponse(
                completed=True,
                text=None,
                facts=completion_facts
            )

        # Payment not complete - re-send payment link
        url_response = self.payment_client.get_payment_url(booking_code)

        if url_response.get("success") and url_response["data"].get("has_payment_intent"):
            payment_url = url_response["data"].get("payment_url")
            return AdapterResponse(
                completed=False,
                text=(
                    "Payment is still pending.\n"
                    "Please complete payment using the link below:\n\n"
                    f"{payment_url}"
                ),
                facts={}
            )
        else:
            # Payment intent doesn't exist - try to recreate
            return self.start(context)

    def abort(self, reason: str, context: Dict[str, Any]) -> None:
        """
        Abort payment capability.

        No cleanup needed - payment state is managed by the client.
        Adapter does not maintain local state.

        Args:
            reason: Reason for abortion (unused)
            context: Context dictionary (unused)
        """
        # No cleanup needed - adapter is stateless
        # Payment intents remain in client store (tests can reset if needed)
        return
