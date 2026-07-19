"""
Payment Client Interface

Protocol defining the contract for payment API clients.
No implementation logic - contract only.
"""

from typing import Any, Dict, Protocol


class PaymentClient(Protocol):
    """
    Protocol for payment API clients.

    Defines the contract that payment clients must implement.
    Adapters depend on this interface, not concrete implementations.
    """

    def create_payment_intent(
        self,
        organization_id: int,
        booking_id: int,
        amount: float,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        """
        Create a payment intent.

        Args:
            booking_id: Booking identifier (integer)
            amount: Payment amount (float, in major currency units)
            currency: Currency code (default: "USD")

        Returns:
            Response dict matching API shape:
            {
                "success": True,
                "data": {
                    "payment_url": str,
                    "payment_intent_id": str
                }
            }
        """
        ...

    def get_payment_url(
        self, organization_id: int, booking_code: str
    ) -> Dict[str, Any]:
        """
        Get payment URL for a booking.

        Args:
            booking_code: Booking code identifier (string)

        Returns:
            Response dict matching API shape:
            {
                "success": True,
                "data": {
                    "has_payment_intent": bool,
                    "payment_url": str (if has_payment_intent is True)
                }
            }
        """
        ...

    def get_payment_status(
        self, organization_id: int, booking_code: str
    ) -> Dict[str, Any]:
        """
        Get payment status for a booking.

        Args:
            booking_code: Booking code identifier (string)

        Returns:
            Response dict matching API shape:
            {
                "success": True,
                "data": {
                    "payment_required": bool,
                    "balance_due": int (in minor units, if payment_required),
                    "payment_status": str ("unpaid", "paid", "partial"),
                    "payment_summary": dict
                }
            }
        """
        ...
