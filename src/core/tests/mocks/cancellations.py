"""
Mock Cancellation Endpoints

Placeholder for cancellation endpoint mocks.
Currently not required for CREATE_APPOINTMENT flow.
"""

import logging
from typing import Any, Dict, Literal, Optional

logger = logging.getLogger(__name__)


def mock_cancel_booking(
    booking_code: str,
    organization_id: int,
    cancellation_type: Literal["cancelled", "no_show", "rescheduled", "user_initiated"],
    *,
    reason: Optional[str] = None,
    notes: Optional[str] = None,
    refund_method: Optional[str] = None,
    notify_customer: Optional[bool] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Mock POST /api/internal/bookings/{bookingCode}/cancel endpoint.

    Placeholder implementation - not required for CREATE_APPOINTMENT flow.

    Args:
        booking_code: Booking code identifier
        organization_id: Organization identifier
        cancellation_type: Type of cancellation
        reason: Cancellation reason (optional)
        notes: Additional notes (optional)
        refund_method: Refund method (optional)
        notify_customer: Whether to notify customer (optional)
        **kwargs: Additional parameters (ignored)

    Returns:
        Mock cancellation response
    """
    logger.debug(
        f"[MOCK] Cancelling booking: code={booking_code}, type={cancellation_type}"
    )

    return {
        "status": "cancelled",
        "booking_code": booking_code,
        "cancellation_type": cancellation_type,
    }
