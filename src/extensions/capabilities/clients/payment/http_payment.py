"""
HTTP Payment Client

Production HTTP client for payment capability.
Implements PaymentClient Protocol by calling internal payments APIs.

This client:
- Mirrors internal payments APIs (POST /api/internal/bookings/intent, etc.)
- Is intentionally isolated from core (capability-owned)
- Provides pure transport layer (no business logic)
- Normalizes API responses to match PaymentClient contract
"""

from typing import Any, Dict, Optional

from core.adapters.clients.base_client import BaseClient

from .payment import PaymentClient


class HttpPaymentClient(BaseClient, PaymentClient):
    """
    HTTP client for payment capability APIs.

    Implements PaymentClient Protocol by making HTTP requests to internal
    payments APIs. This client is used by PaymentAdapter in production.

    Design:
    - Pure transport layer (no business logic)
    - Normalizes API responses to match Protocol contract
    - Handles HTTP errors via BaseClient error handling
    """

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize HTTP payment client.

        Args:
            base_url: API base URL (overrides INTERNAL_API_BASE_URL env var)
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )
    def create_payment_intent(
        self,
        organization_id: int,
        booking_id: int,
        amount: float,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        """
        Create a payment intent.

        HTTP: POST /api/internal/bookings/intent

        Args:
            booking_id: Booking identifier (integer)
            amount: Payment amount (float, in major currency units)
            currency: Currency code (default: "USD")

        Returns:
            Normalized response dict:
            {
                "success": bool,
                "data": {
                    "payment_intent_id": str,
                    "payment_url": str | None
                }
            }
        """
        payload = {
            "organization_id": organization_id,
            "booking_id": booking_id,
            "payment": {
                "amount": amount,
                "currency": currency.lower(),  # API expects lowercase
                "method": "stripe",
            },
        }

        # Make HTTP request
        response = self._request("POST", "/api/internal/bookings/intent", json=payload)

        # Normalize response to match Protocol contract
        # API may return different structure, normalize to expected shape
        if isinstance(response, dict):
            # Check if response already has success/data structure
            if "success" in response and "data" in response:
                return response

            # If API returns direct data, wrap it
            if "payment_intent_id" in response or "payment_url" in response:
                return {
                    "success": True,
                    "data": {
                        "payment_intent_id": response.get("payment_intent_id")
                        or response.get("id", ""),
                        "payment_url": response.get("payment_url")
                        or response.get("url"),
                    },
                }

            # If API returns nested structure, extract data
            if "data" in response:
                data = response["data"]
                return {
                    "success": response.get("success", True),
                    "data": {
                        "payment_intent_id": data.get("payment_intent_id")
                        or data.get("id", ""),
                        "payment_url": data.get("payment_url") or data.get("url"),
                    },
                }

        # Fallback: wrap unknown response structure
        return {"success": True, "data": {"payment_intent_id": "", "payment_url": None}}

    def get_payment_url(
        self, organization_id: int, booking_code: str
    ) -> Dict[str, Any]:
        """
        Get payment URL for a booking.

        HTTP: GET /api/internal/bookings/{booking_code}/payment-url

        Args:
            booking_code: Booking code identifier (string)

        Returns:
            Normalized response dict:
            {
                "success": bool,
                "data": {
                    "has_payment_intent": bool,
                    "payment_url": str | None,
                    "payment_intent_id": str | None
                }
            }
        """
        path = f"/api/internal/bookings/{booking_code}/payment-url"
        response = self._request(
            "GET", path, params={"organization_id": organization_id}
        )

        # Normalize response to match Protocol contract
        if isinstance(response, dict):
            # Check if response already has success/data structure
            if "success" in response and "data" in response:
                data = response["data"]
                return {
                    "success": response.get("success", True),
                    "data": {
                        "has_payment_intent": data.get("has_payment_intent", False),
                        "payment_url": data.get("payment_url"),
                        "payment_intent_id": data.get("payment_intent_id"),
                    },
                }

            # If API returns direct data, wrap it
            return {
                "success": True,
                "data": {
                    "has_payment_intent": response.get("has_payment_intent", False),
                    "payment_url": response.get("payment_url"),
                    "payment_intent_id": response.get("payment_intent_id"),
                },
            }

        # Fallback
        return {
            "success": True,
            "data": {
                "has_payment_intent": False,
                "payment_url": None,
                "payment_intent_id": None,
            },
        }

    def get_payment_status(
        self, organization_id: int, booking_code: str
    ) -> Dict[str, Any]:
        """
        Get payment status for a booking.

        HTTP: GET /api/internal/bookings/{booking_code}/payment-status

        Args:
            booking_code: Booking code identifier (string)

        Returns:
            Normalized response dict:
            {
                "success": bool,
                "data": {
                    "payment_required": bool,
                    "payment_status": str,
                    "payment_summary": {
                        "total_amount": float,
                        "paid_amount": float,
                        "balance_due": float,
                        "payment_intent_id": str | None
                    }
                }
            }
        """
        path = f"/api/internal/bookings/{booking_code}/payment-status"
        response = self._request(
            "GET", path, params={"organization_id": organization_id}
        )

        # Normalize response to match Protocol contract
        if isinstance(response, dict):
            # Check if response already has success/data structure
            if "success" in response and "data" in response:
                data = response["data"]
                payment_summary = data.get("payment_summary", {})

                return {
                    "success": response.get("success", True),
                    "data": {
                        "payment_required": data.get("payment_required", False),
                        "payment_status": data.get("payment_status", "none"),
                        "balance_due": data.get("balance_due", 0),
                        "payment_summary": {
                            "total_amount": payment_summary.get("total_amount", 0.0),
                            "paid_amount": payment_summary.get("paid_amount", 0.0),
                            "balance_due": payment_summary.get("balance_due", 0.0),
                            "payment_intent_id": payment_summary.get(
                                "payment_intent_id"
                            ),
                        },
                    },
                }

            # If API returns direct data, wrap it
            payment_summary = response.get("payment_summary", {})
            return {
                "success": True,
                "data": {
                    "payment_required": response.get("payment_required", False),
                    "payment_status": response.get("payment_status", "none"),
                    "balance_due": response.get("balance_due", 0),
                    "payment_summary": {
                        "total_amount": payment_summary.get("total_amount", 0.0),
                        "paid_amount": payment_summary.get("paid_amount", 0.0),
                        "balance_due": payment_summary.get("balance_due", 0.0),
                        "payment_intent_id": payment_summary.get("payment_intent_id"),
                    },
                },
            }

        # Fallback
        return {
            "success": True,
            "data": {
                "payment_required": False,
                "payment_status": "none",
                "balance_due": 0,
                "payment_summary": {
                    "total_amount": 0.0,
                    "paid_amount": 0.0,
                    "balance_due": 0.0,
                    "payment_intent_id": None,
                },
            },
        }
