"""
Mock HTTP Endpoints for Core Tests

Mock implementations of internal API endpoints required for Core base intents.
These mocks enable end-to-end testing without calling real services.

Endpoints mocked:
- GET /api/internal/availability/services
- POST /api/internal/bookings
- POST /api/internal/bookings/{bookingCode}/confirm
"""

from .availability import mock_get_reservation_availability, mock_get_service_availability
from .bookings import mock_confirm_booking, mock_create_booking, reset_booking_counter
from .cancellations import mock_cancel_booking
from .discovery import mock_discovery_endpoints

__all__ = [
    "mock_get_service_availability",
    "mock_get_reservation_availability",
    "mock_create_booking",
    "mock_confirm_booking",
    "mock_cancel_booking",
    "mock_discovery_endpoints",
    "reset_booking_counter",
]
