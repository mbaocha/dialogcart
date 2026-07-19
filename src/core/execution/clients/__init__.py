"""
Execution Layer - Business Clients

This package provides HTTP clients for business execution operations.
These clients perform side effects: availability checks and booking operations.

Execution clients are called AFTER planning decides execution is allowed.
They must accept fully planned inputs and must NOT perform clarification logic.
"""

from .availability_client import AvailabilityClient
from .base_client import BaseClient
from .booking_client import BookingClient

__all__ = [
    "BaseClient",
    "AvailabilityClient",
    "BookingClient",
]
