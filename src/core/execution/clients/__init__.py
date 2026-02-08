"""
Execution Layer - Business Clients

This package provides HTTP clients for business execution operations.
These clients perform side effects: availability checks, booking operations,
payment initiation, and staff lookups.

Execution clients are called AFTER planning decides execution is allowed.
They must accept fully planned inputs and must NOT perform clarification logic.
"""

from .base_client import BaseClient
from .availability_client import AvailabilityClient
from .booking_client import BookingClient
from .payment_client import PaymentClient
from .staff_client import StaffClient

__all__ = [
    "BaseClient",
    "AvailabilityClient",
    "BookingClient",
    "PaymentClient",
    "StaffClient",
]

