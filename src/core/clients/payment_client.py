"""
Payment API Client

Thin HTTP client for calling payment internal API.
"""

from typing import Dict, Any, Optional

from core.clients.base_client import BaseClient


class PaymentClient(BaseClient):
    """HTTP client for payment internal API."""

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize payment client.

        Args:
            base_url: API base URL. Defaults to INTERNAL_API_BASE_URL env var.
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000"
        )

    def create_payment_intent(
        self,
        booking_id: int,
        amount: float,
        currency: str,
        method: str,
    ) -> Dict[str, Any]:
        """
        Create a payment intent.

        Args:
            booking_id: Booking identifier
            amount: Payment amount
            currency: Currency code (e.g., "USD", "GBP")
            method: Payment method

        Returns:
            Payment intent data as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        payload = {
            "booking_id": booking_id,
            "amount": amount,
            "currency": currency,
            "method": method,
        }

        path = "/api/internal/bookings/intent"
        return self._request("POST", path, json=payload)
