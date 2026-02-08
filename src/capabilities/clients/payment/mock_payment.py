"""
Mock Payment Client

In-memory mock implementation of PaymentClient for testing.
Moved from capabilities/tests/mocks.py to follow client interface pattern.
"""

from typing import Dict, Any
from .payment import PaymentClient


# In-memory store: booking_code -> payment_state
_PAYMENT_STATE: Dict[str, Dict[str, Any]] = {}


def reset_payment_store():
    """
    Reset the payment store (call in test setup/teardown).
    Ensures no shared state leaks across tests.
    """
    global _PAYMENT_STATE
    _PAYMENT_STATE.clear()


def _get_booking_code(booking_id: int) -> str:
    """
    Convert booking_id to booking_code for storage.
    In real system, this would query the database. For mocks, we use a simple mapping.
    """
    return f"booking_{booking_id}"


def mark_payment_as_paid(booking_code: str) -> None:
    """
    Marks booking as fully paid.
    
    Used by tests to simulate Stripe webhook completion.
    This updates the payment state to reflect successful payment.
    
    Args:
        booking_code: Booking code identifier (string)
    
    Raises:
        KeyError: If booking_code has no payment intent
    """
    if booking_code not in _PAYMENT_STATE:
        raise KeyError(f"No payment intent found for booking_code: {booking_code}")
    
    if not _PAYMENT_STATE[booking_code].get("intent_created"):
        raise ValueError(f"Payment intent not created for booking_code: {booking_code}")
    
    _PAYMENT_STATE[booking_code]["paid"] = True


class MockPaymentClient:
    """
    Mock implementation of PaymentClient for testing.
    
    Provides deterministic, in-memory payment state management.
    """
    
    def create_payment_intent(
        self,
        booking_id: int,
        amount: float,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        """
        Simulates POST /api/internal/bookings/intent
        
        Creates a payment intent if it doesn't exist.
        Idempotent per booking_id - returns existing intent if already created.
        
        Args:
            booking_id: Booking identifier (integer)
            amount: Payment amount (float, in major currency units)
            currency: Currency code (default: "USD")
        
        Returns:
            Response dict matching real API shape:
            {
                "success": True,
                "data": {
                    "payment_url": str,
                    "payment_intent_id": str
                }
            }
        """
        booking_code = _get_booking_code(booking_id)
        
        # Idempotent: if intent already exists, return it
        if booking_code in _PAYMENT_STATE and _PAYMENT_STATE[booking_code].get("intent_created"):
            existing = _PAYMENT_STATE[booking_code]
            return {
                "success": True,
                "data": {
                    "payment_url": existing["payment_url"],
                    "payment_intent_id": existing["payment_intent_id"],
                }
            }
        
        # Create new payment intent
        payment_intent_id = f"pi_test_{booking_id}_{len(_PAYMENT_STATE)}"
        payment_url = f"https://pay.test/{booking_code}"
        
        # Store payment state
        _PAYMENT_STATE[booking_code] = {
            "intent_created": True,
            "payment_intent_id": payment_intent_id,
            "payment_url": payment_url,
            "paid": False,
            "amount": int(amount * 100),  # Store in minor units (cents)
            "currency": currency.upper(),
            "booking_id": booking_id,
        }
        
        return {
            "success": True,
            "data": {
                "payment_url": payment_url,
                "payment_intent_id": payment_intent_id,
            }
        }
    
    def get_payment_url(self, booking_code: str) -> Dict[str, Any]:
        """
        Simulates GET /api/internal/bookings/{bookingCode}/payment-url
        
        Returns payment URL if intent exists, otherwise indicates no intent.
        
        Args:
            booking_code: Booking code identifier (string)
        
        Returns:
            Response dict matching real API shape:
            {
                "success": True,
                "data": {
                    "has_payment_intent": bool,
                    "payment_url": str (if has_payment_intent is True)
                }
            }
        """
        if booking_code not in _PAYMENT_STATE:
            return {
                "success": True,
                "data": {
                    "has_payment_intent": False,
                }
            }
        
        state = _PAYMENT_STATE[booking_code]
        if not state.get("intent_created"):
            return {
                "success": True,
                "data": {
                    "has_payment_intent": False,
                }
            }
        
        return {
            "success": True,
            "data": {
                "has_payment_intent": True,
                "payment_url": state["payment_url"],
            }
        }
    
    def get_payment_status(self, booking_code: str) -> Dict[str, Any]:
        """
        Simulates GET /api/internal/bookings/{bookingCode}/payment-status
        
        Returns payment status including whether payment is required,
        balance due, and current payment status.
        
        Args:
            booking_code: Booking code identifier (string)
        
        Returns:
            Response dict matching real API shape:
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
        if booking_code not in _PAYMENT_STATE:
            # No payment intent created yet
            return {
                "success": True,
                "data": {
                    "payment_required": False,
                    "payment_status": "none",
                    "payment_summary": {},
                }
            }
        
        state = _PAYMENT_STATE[booking_code]
        
        if not state.get("intent_created"):
            return {
                "success": True,
                "data": {
                    "payment_required": False,
                    "payment_status": "none",
                    "payment_summary": {},
                }
            }
        
        # Payment intent exists
        amount = state["amount"]
        paid = state.get("paid", False)
        
        if paid:
            payment_status = "paid"
            balance_due = 0
            payment_required = False
        else:
            payment_status = "unpaid"
            balance_due = amount
            payment_required = True
        
        return {
            "success": True,
            "data": {
                "payment_required": payment_required,
                "balance_due": balance_due,
                "payment_status": payment_status,
                "payment_summary": {
                    "total_amount": amount,
                    "currency": state["currency"],
                    "payment_intent_id": state["payment_intent_id"],
                }
            }
        }

