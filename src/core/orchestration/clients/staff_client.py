"""
Staff API Client

Thin client for staff listing and details.
"""

from typing import Any, Dict, Optional

from core.orchestration.clients.base_client import BaseClient


class StaffClient(BaseClient):
    """HTTP client for staff endpoints."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )

    def list_staff(self, organization_id: int, extra_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"organization_id": organization_id}
        if extra_params:
            params.update(extra_params)
        path = f"/api/internal/organizations/{organization_id}/staff"
        return self._request("GET", path, params=params)

    def get_staff(self, staff_id: int, organization_id: Optional[int] = None, extra_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if organization_id is not None:
            params["organization_id"] = organization_id
        if extra_params:
            params.update(extra_params)
        path = f"/api/internal/staff/{staff_id}"
        return self._request("GET", path, params=params)

