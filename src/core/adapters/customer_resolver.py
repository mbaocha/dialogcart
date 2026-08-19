"""
Tenant customer resolve-or-create at Core ingress.

Core owns timing and session propagation. Commerce owns matching/upsert.
Never treats chat ``user_id`` as a commerce customer primary key.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Protocol

logger = logging.getLogger(__name__)


class CustomerLookupClient(Protocol):
    def lookup_by_contact(
        self,
        *,
        organization_id: int,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[Mapping[str, Any]]:
        ...

    def upsert(
        self,
        *,
        organization_id: int,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Mapping[str, Any]:
        ...

    def belongs_to_organization(
        self, customer_id: int, organization_id: int
    ) -> bool:
        ...


def coerce_positive_customer_id(value: Any) -> Optional[int]:
    """Return a positive int customer id, or None if missing/invalid."""
    if value is None or value is False:
        return None
    try:
        customer_id = int(value)
    except (TypeError, ValueError):
        return None
    if customer_id <= 0:
        return None
    return customer_id


def resolve_tenant_customer(
    *,
    organization_id: int,
    customer_client: CustomerLookupClient,
    session: Optional[Mapping[str, Any]] = None,
    customer_id: Optional[Any] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional[int]:
    """
    Resolve a canonical commerce ``customers.id`` for this tenant turn.

    Priority:
      1. Request ``customer_id`` when it belongs to ``organization_id``
      2. Session ``customer_id`` already persisted for this org session
      3. Commerce upsert from phone and/or email
      4. Unresolved (anonymous browse OK; commit gated later)

    Does not invent ids and does not map ``user_id`` to ``customer_id``.
    """
    projection = resolve_tenant_customer_projection(
        organization_id=organization_id,
        customer_client=customer_client,
        session=session,
        customer_id=customer_id,
        phone=phone,
        email=email,
        name=name,
    )
    return coerce_positive_customer_id(projection.get("id")) if projection else None


def resolve_tenant_customer_projection(
    *,
    organization_id: int,
    customer_client: CustomerLookupClient,
    session: Optional[Mapping[str, Any]] = None,
    customer_id: Optional[Any] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional[Mapping[str, Any]]:
    """Resolve the strongest customer projection supported by current clients."""
    from core.customer_identification import (
        customer_channel_fingerprint,
        normalize_authoritative_name,
        reusable_authoritative_contact,
    )

    org_id = int(organization_id)

    request_id = coerce_positive_customer_id(customer_id)
    if request_id is not None:
        try:
            if customer_client.belongs_to_organization(request_id, org_id):
                return {"id": request_id}
            logger.warning(
                "Ignoring customer_id=%s not belonging to organization_id=%s",
                request_id,
                org_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to validate customer_id=%s for organization_id=%s: %s",
                request_id,
                org_id,
                exc,
            )

    phone_value = _normalize_contact(phone)
    email_value = _normalize_contact(email)
    session_id = None
    if isinstance(session, Mapping):
        session_id = coerce_positive_customer_id(session.get("customer_id"))
        if session_id is not None:
            contact = reusable_authoritative_contact(session)
            incoming_fingerprint = customer_channel_fingerprint(
                phone=phone_value, email=email_value
            )
            stored_fingerprint = (
                contact.get("channel_fingerprint")
                if isinstance(contact, Mapping)
                else None
            )
            channel_matches = (
                incoming_fingerprint is None
                or stored_fingerprint == incoming_fingerprint
            )
            if isinstance(contact, Mapping) and channel_matches:
                return {"id": session_id, "name": contact.get("authoritative_name")}
            if not phone_value and not email_value:
                return {"id": session_id}

    if phone_value or email_value:
        try:
            customer = customer_client.lookup_by_contact(
                organization_id=org_id,
                phone=phone_value,
                email=email_value,
            )
        except Exception as exc:
            logger.warning(
                "Customer contact lookup failed for organization_id=%s: %s",
                org_id,
                exc,
            )
            return None
        if isinstance(customer, Mapping):
            resolved = coerce_positive_customer_id(customer.get("id"))
            returned_org_id = coerce_positive_customer_id(
                customer.get("organizationId")
            )
            if resolved is None or returned_org_id != org_id:
                logger.warning(
                    "Customer contact lookup returned invalid authority for "
                    "organization_id=%s",
                    org_id,
                )
                return None
            return customer

    authoritative_name = normalize_authoritative_name(name)
    if (not phone_value and not email_value) or authoritative_name is None:
        return None

    try:
        customer = customer_client.upsert(
            organization_id=org_id,
            name=authoritative_name,
            phone=phone_value,
            email=email_value,
        )
    except Exception as exc:
        logger.warning(
            "Customer upsert failed for organization_id=%s: %s",
            org_id,
            exc,
        )
        return None

    resolved = coerce_positive_customer_id(
        customer.get("id") if isinstance(customer, Mapping) else None
    )
    if resolved is None:
        logger.warning(
            "Customer upsert returned no id for organization_id=%s", org_id
        )
    return customer if resolved is not None and isinstance(customer, Mapping) else None


def _normalize_contact(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
