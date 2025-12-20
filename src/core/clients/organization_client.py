"""
Organization API Client

Thin HTTP client for calling organization internal API.
"""

from typing import Dict, Any, Optional

from core.clients.base_client import BaseClient


class OrganizationClient(BaseClient):
    """HTTP client for organization internal API."""

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize organization client.

        Args:
            base_url: API base URL. Defaults to INTERNAL_API_BASE_URL env var.
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000"
        )

    def get_details(self, organization_id: int) -> Dict[str, Any]:
        """
        Get organization details.

        Args:
            organization_id: Organization identifier

        Returns:
            Organization details as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
        """
        path = f"/api/internal/organizations/{organization_id}/details"
        return self._request("GET", path)
