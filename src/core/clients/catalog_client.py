"""
Catalog Discovery API Client

Read-only client for fetching tenant offerings (services, reservation offerings).
"""

from typing import Any, Dict, Optional

from core.clients.base_client import BaseClient


class CatalogClient(BaseClient):
    """HTTP client for catalog discovery (read-only)."""

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize catalog client.

        Args:
            base_url: API base URL. Defaults to INTERNAL_API_BASE_URL env var.
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000"
        )

    def get_services(self, organization_id: int) -> Dict[str, Any]:
        """
        Fetch service catalog for an organization.

        GET /api/internal/organizations/{orgId}/services

        Returns:
            {
                "catalog_last_updated_at": "...",
                "business_category_id": ...,
                "services": [...]
            }
        """
        path = f"/api/internal/organizations/{organization_id}/services"
        return self._request("GET", path)

    def get_reservation(self, organization_id: int) -> Dict[str, Any]:
        """
        Fetch reservation catalog (room types, extras) for an organization.

        GET /api/internal/organizations/{orgId}/reservation

        Returns:
            {
                "catalog_last_updated_at": "...",
                "business_category_id": ...,
                "room_types": [...],
                "extras": [...]
            }
        """
        path = f"/api/internal/organizations/{organization_id}/reservation"
        return self._request("GET", path)

