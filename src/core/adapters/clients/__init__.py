"""
Adapter context clients.

HTTP clients for tenant context needed during planning (catalog, organization).

Execution clients (booking, availability) live in core.execution.clients.
Luma NLU client lives in core.adapters.nlu.
"""

from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient

__all__ = [
    "CatalogClient",
    "OrganizationClient",
]
