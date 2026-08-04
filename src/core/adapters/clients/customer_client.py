"""
Customer API Client

Thin HTTP client for commerce customer resolve/upsert. Core owns *when*
to call this; commerce owns matching and persistence.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.adapters.clients.base_client import BaseClient
from core.adapters.errors import UpstreamError


class CustomerClient(BaseClient):
    """HTTP client for commerce internal customer APIs."""

    def __init__(self, base_url: Optional[str] = None):
        super().__init__(
            base_url=base_url,
            env_var="INTERNAL_API_BASE_URL",
            default_url="http://localhost:3000",
        )

    def upsert(
        self,
        *,
        organization_id: int,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Resolve-or-create a tenant customer via commerce upsert.

        Commerce matches within the organization by phone and/or email.
        Requires ``name`` and at least one of ``phone`` or ``email``.
        """
        payload: Dict[str, Any] = {
            "organization_id": int(organization_id),
            "name": name,
        }
        if phone:
            payload["phone"] = phone
        if email:
            payload["email"] = email

        body = self._request("POST", "/api/internal/customers", json=payload)
        customer = _extract_customer(body)
        if customer is None:
            raise UpstreamError(
                "Customer upsert response missing customer record"
            )
        return customer

    def belongs_to_organization(
        self, customer_id: int, organization_id: int
    ) -> bool:
        """
        Return True when commerce confirms ``customer_id`` exists in the org.

        Uses the org-scoped financial-summary endpoint (404 when not in org).
        """
        path = f"/api/internal/customers/{int(customer_id)}/financial-summary"
        body = self._request_allow_404(
            "GET",
            path,
            params={"organization_id": int(organization_id)},
        )
        return body is not None


def _extract_customer(body: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(body, dict):
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if not isinstance(data, dict):
        return None
    customer = data.get("customer")
    if isinstance(customer, dict) and customer.get("id") is not None:
        return customer
    if data.get("id") is not None:
        return data
    return None
