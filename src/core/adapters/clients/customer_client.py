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

    def lookup_by_contact(
        self,
        *,
        organization_id: int,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read an existing tenant customer by exact phone/email contact."""
        organization_id = _positive_int(organization_id, "organization_id")
        if not phone and not email:
            raise ValueError("phone or email is required")
        params: Dict[str, Any] = {}
        if phone:
            params["phone"] = phone
        if email:
            params["email"] = email
        body = self._request_allow_404(
            "GET",
            f"/api/internal/organizations/{organization_id}/customers",
            params=params,
        )
        if body is None:
            return None
        if not isinstance(body, dict) or body.get("success") is not True:
            raise UpstreamError("Customer lookup response was not successful")
        customer = _extract_customer(body)
        if customer is None:
            raise UpstreamError("Customer lookup response missing customer record")
        returned_id = _positive_int_or_none(customer.get("id"))
        returned_org_id = _positive_int_or_none(customer.get("organizationId"))
        if returned_id is None:
            raise UpstreamError("Customer lookup returned an invalid customer id")
        if returned_org_id != organization_id:
            raise UpstreamError("Customer lookup returned a mismatched organization id")
        return customer

    def update_name_by_id(
        self,
        *,
        organization_id: int,
        customer_id: int,
        name: str,
    ) -> Dict[str, Any]:
        """Update a known tenant customer's name and return Commerce authority."""
        organization_id = _positive_int(organization_id, "organization_id")
        customer_id = _positive_int(customer_id, "customer_id")
        body = self._request(
            "PATCH",
            f"/api/internal/organizations/{organization_id}/customers/{customer_id}",
            json={"name": name},
        )
        if not isinstance(body, dict) or body.get("success") is not True:
            raise UpstreamError("Customer name update response was not successful")
        data = body.get("data")
        customer = data.get("customer") if isinstance(data, dict) else None
        if not isinstance(customer, dict):
            raise UpstreamError("Customer name update response missing customer record")

        returned_id = _positive_int_or_none(customer.get("id"))
        returned_org_id = _positive_int_or_none(customer.get("organizationId"))
        from core.customer_identification import normalize_authoritative_name

        returned_name = normalize_authoritative_name(customer.get("name"))
        if returned_id != customer_id:
            raise UpstreamError("Customer name update returned a mismatched customer id")
        if returned_org_id != organization_id:
            raise UpstreamError("Customer name update returned a mismatched organization id")
        if returned_name is None:
            raise UpstreamError("Customer name update returned an invalid customer name")
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


def _positive_int(value: Any, field: str) -> int:
    parsed = _positive_int_or_none(value)
    if parsed is None:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _positive_int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
