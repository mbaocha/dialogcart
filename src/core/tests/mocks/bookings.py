"""
Mock Booking Endpoints

Mocks booking creation and confirmation endpoints:
- POST /api/internal/bookings
- POST /api/internal/bookings/{bookingCode}/confirm

Response formats match real API.
"""

from typing import Dict, Any, Optional, Literal
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Counter for generating unique booking codes
_booking_counter = 0

# In-memory store for booking state (for confirm endpoint)
_booking_store: Dict[str, Dict[str, Any]] = {}

# Default service duration in minutes
DEFAULT_SERVICE_DURATION_MINUTES = 60


def _generate_booking_code() -> str:
    """Generate a deterministic test booking code."""
    global _booking_counter
    _booking_counter += 1
    return f"MOCK-BOOKING-{_booking_counter:03d}"


def reset_booking_counter() -> None:
    """Reset booking counter for test isolation."""
    global _booking_counter
    _booking_counter = 0
    _booking_store.clear()


def mock_create_booking(
    organization_id: int,
    customer_id: int,
    booking_type: Literal["service", "reservation"],
    item_id: int,
    *,
    # Service booking parameters
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    staff_id: Optional[int] = None,
    addons: Optional[list] = None,
    # Reservation booking parameters
    check_in: Optional[str] = None,
    check_out: Optional[str] = None,
    guests: int = 1,
    extras: Optional[list] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Mock POST /api/internal/bookings endpoint.
    
    Generates deterministic booking response based on:
    - start_time/end_time for service bookings
    - check_in/check_out for reservation bookings
    
    Rules:
    - starts_at = resolved date + time_constraint.start (from request)
    - ends_at = starts_at + service duration
    - Do NOT include pricing, staff, payments, or metadata
    
    Args:
        organization_id: Organization identifier
        customer_id: Customer identifier
        booking_type: Type of booking ("service" or "reservation")
        item_id: Service or room item identifier
        start_time: Service booking start time (ISO-8601 with timezone)
        end_time: Service booking end time (ISO-8601 with timezone)
        staff_id: Staff member ID (ignored in mock)
        addons: Service booking addons (ignored in mock)
        check_in: Reservation check-in time (ISO-8601 with timezone)
        check_out: Reservation check-out time (ISO-8601 with timezone)
        guests: Number of guests (ignored in mock)
        extras: Reservation extras (ignored in mock)
        **kwargs: Additional parameters (ignored)
    
    Returns:
        Mock booking creation response
    """
    booking_code = _generate_booking_code()
    
    if booking_type == "service":
        if not start_time or not end_time:
            # If end_time not provided, calculate from start_time + duration
            if start_time:
                try:
                    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    end_dt = start_dt + timedelta(minutes=DEFAULT_SERVICE_DURATION_MINUTES)
                    end_time = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    logger.warning(f"Could not parse start_time '{start_time}', using default")
                    end_time = start_time  # Fallback
            else:
                raise ValueError("start_time is required for service bookings")
        
        starts_at = start_time
        ends_at = end_time
    else:  # reservation
        if not check_in or not check_out:
            raise ValueError("check_in and check_out are required for reservation bookings")
        starts_at = check_in
        ends_at = check_out
    
    logger.debug(
        f"[MOCK] Creating booking: code={booking_code}, type={booking_type}, "
        f"starts_at={starts_at}, ends_at={ends_at}"
    )
    
    # Store booking state for confirm endpoint
    booking_data = {
        "id": _booking_counter,
        "booking_code": booking_code,
        "status": "pending",
        "starts_at": starts_at,
        "ends_at": ends_at
    }
    _booking_store[booking_code] = booking_data.copy()
    
    return {
        "booking": booking_data
    }


def mock_confirm_booking(
    booking_code: str,
    organization_id: int,
    **kwargs
) -> Dict[str, Any]:
    """
    Mock POST /api/internal/bookings/{bookingCode}/confirm endpoint.
    
    Generates deterministic confirmation response.
    
    Rules:
    - Must preserve starts_at / ends_at from original booking
    - Only status changes to confirmed
    
    Args:
        booking_code: Booking code identifier
        organization_id: Organization identifier (unused in mock)
        **kwargs: Additional parameters (ignored)
    
    Returns:
        Mock booking confirmation response
    """
    # Look up original booking to preserve starts_at/ends_at
    if booking_code in _booking_store:
        original_booking = _booking_store[booking_code]
        starts_at = original_booking["starts_at"]
        ends_at = original_booking["ends_at"]
    else:
        # Fallback: use default values if booking not found
        logger.warning(
            f"[MOCK] Booking {booking_code} not found in store, using defaults"
        )
        starts_at = "2026-01-16T10:00:00Z"
        ends_at = "2026-01-16T11:00:00Z"
    
    logger.debug(
        f"[MOCK] Confirming booking: code={booking_code}, "
        f"starts_at={starts_at}, ends_at={ends_at}"
    )
    
    # Update stored booking status
    if booking_code in _booking_store:
        _booking_store[booking_code]["status"] = "confirmed"
    
    return {
        "booking": {
            "booking_code": booking_code,
            "status": "confirmed",
            "starts_at": starts_at,
            "ends_at": ends_at
        }
    }

