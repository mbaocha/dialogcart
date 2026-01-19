"""
Customer API Client

Thin HTTP client for calling customer internal API.
"""

from typing import Dict, Any, Optional

from core.orchestration.clients.base_client import BaseClient


class CustomerClient(BaseClient):
    """HTTP client for customer internal API."""

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize customer client.

        Args:
            base_url: API base URL. Defaults to INTERNAL_API_BASE_URL env var.
        """
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000"
        )

    def get_customer(
        self,
        organization_id: int,
        email: Optional[str] = None,
        phone: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get customer by email or phone.

        Args:
            organization_id: Organization identifier
            email: Customer email (optional)
            phone: Customer phone (optional)

        Returns:
            Customer data as dict, or None if not found (404)

        Raises:
            UpstreamError: On network failures or HTTP errors (except 404)
        """
        path = f"/api/internal/organizations/{organization_id}/customers"

        params = {}
        if email:
            params["email"] = email
        if phone:
            params["phone"] = phone

        return self._request_allow_404("GET", path, params=params)

    def create_customer(
        self,
        organization_id: int,
        name: str,
        *,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new customer.

        Args:
            organization_id: Organization identifier
            name: Customer name
            email: Customer email (optional, but either email or phone required)
            phone: Customer phone (optional, but either email or phone required)

        Returns:
            Created customer data as dict

        Raises:
            UpstreamError: On network failures or HTTP errors
            ValueError: If neither email nor phone is provided
        """
        if not email and not phone:
            raise ValueError("Either email or phone must be provided")

        payload = {
            "organization_id": organization_id,
            "name": name,
        }
        if email:
            payload["email"] = email
        if phone:
            payload["phone"] = phone

        path = "/api/internal/customers"
        return self._request("POST", path, json=payload)

    def list_customer_bookings(
        self,
        organization_id: int,
        customer_id: int,
        *,
        booking_type: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List bookings for a customer within an organization.
        """
        params: Dict[str, Any] = {}
        if booking_type:
            params["booking_type"] = booking_type
        if extra_params:
            params.update(extra_params)
        path = f"/api/internal/organizations/{organization_id}/customers/{customer_id}/bookings"
        return self._request("GET", path, params=params)