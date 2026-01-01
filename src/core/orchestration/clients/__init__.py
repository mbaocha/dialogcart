"""
Orchestration Layer - Clients Package

External API client integrations for orchestration layer.

This package contains HTTP clients for external services:
- Luma API (NLP/intent resolution)
- Booking API (booking management)
- Customer API (customer management)
- Organization API (organization details)
- Catalog API (service catalog)
- Payment API, Availability API, Staff API

These clients perform external side-effects and are owned by the orchestration layer.
"""

from core.orchestration.clients.base_client import BaseClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.clients.customer_client import CustomerClient
from core.orchestration.clients.booking_client import BookingClient
from core.orchestration.clients.payment_client import PaymentClient
from core.orchestration.clients.luma_client import LumaClient
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.availability_client import AvailabilityClient
from core.orchestration.clients.staff_client import StaffClient

__all__ = [
    "BaseClient",
    "OrganizationClient",
    "CustomerClient",
    "BookingClient",
    "PaymentClient",
    "LumaClient",
    "CatalogClient",
    "AvailabilityClient",
    "StaffClient",
]
