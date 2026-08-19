"""Core-owned customer-name readiness and persistence routing."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


PENDING_CUSTOMER_CONTACT_NAME = "CUSTOMER_CONTACT_NAME"
_NON_AUTHORITATIVE_NAMES = frozenset({
    "guest",
    "anonymous",
    "unknown",
    "none",
    "n/a",
    "na",
    "no name",
    "i don't know",
    "i do not know",
    "not sure",
})


@dataclass(frozen=True)
class CustomerNamePrerequisite:
    """Immutable workflow evidence for entering booking confirmation."""

    satisfied: bool
    required_input: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"satisfied": self.satisfied, "required_input": self.required_input}


@dataclass(frozen=True)
class AuthorizedCustomerContactName:
    """Request-bound, workflow-authorized profile evidence."""

    value: str


def normalize_authoritative_name(value: Any) -> Optional[str]:
    """Normalize structural name evidence without interpreting raw language."""
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or normalized.casefold() in _NON_AUTHORITATIVE_NAMES:
        return None
    return normalized


def customer_channel_fingerprint(
    *, phone: Optional[str] = None, email: Optional[str] = None
) -> Optional[str]:
    """Return a non-plaintext equality key for the Commerce contact channel."""
    if isinstance(phone, str) and phone.strip():
        material = f"phone:{phone.strip()}"
    elif isinstance(email, str) and email.strip():
        material = f"email:{email.strip().casefold()}"
    else:
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def reusable_authoritative_contact(
    session_state: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Validate the complete Core session projection before it may be reused."""
    if not isinstance(session_state, Mapping):
        return None
    customer_id = session_state.get("customer_id")
    contact = session_state.get("customer_contact")
    if not isinstance(customer_id, int) or customer_id <= 0:
        return None
    if not isinstance(contact, Mapping):
        return None
    if contact.get("customer_id") != customer_id:
        return None
    if contact.get("name_status") != "authoritative":
        return None
    if normalize_authoritative_name(contact.get("authoritative_name")) is None:
        return None
    fingerprint = contact.get("channel_fingerprint")
    if fingerprint is not None and (
        not isinstance(fingerprint, str) or len(fingerprint) != 64
    ):
        return None
    return contact


def customer_name_confirmation_prerequisite(
    session_state: Optional[Mapping[str, Any]],
) -> CustomerNamePrerequisite:
    """Classify only an authoritative name already persisted in Core context."""
    contact = reusable_authoritative_contact(session_state)
    satisfied = contact is not None
    return CustomerNamePrerequisite(
        satisfied=satisfied,
        required_input=None if satisfied else PENDING_CUSTOMER_CONTACT_NAME,
    )


def authoritative_contact(
    customer_id: Optional[int],
    name: Optional[str],
    *,
    channel_fingerprint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    value = normalize_authoritative_name(name)
    if not isinstance(customer_id, int) or customer_id <= 0 or value is None:
        return None
    contact = {
        "customer_id": customer_id,
        "authoritative_name": value,
        "name_status": "authoritative",
    }
    if channel_fingerprint is not None:
        contact["channel_fingerprint"] = channel_fingerprint
    return contact


def authorize_customer_contact_name(
    session_state: Optional[Mapping[str, Any]],
    response: Any,
) -> Optional[AuthorizedCustomerContactName]:
    """Authorize typed, request-scoped evidence for customer-profile persistence."""
    if not isinstance(session_state, Mapping) or not isinstance(response, Mapping):
        return None
    planning = session_state.get("planning")
    contact = session_state.get("customer_contact")
    awaiting_name = (
        isinstance(planning, Mapping)
        and planning.get("pending_profile_request") == PENDING_CUSTOMER_CONTACT_NAME
    )
    revising_name_before_confirmation = (
        session_state.get("confirmation_state") == "pending"
        and customer_name_confirmation_prerequisite(session_state).satisfied
        and isinstance(contact, Mapping)
    )
    if not (awaiting_name or revising_name_before_confirmation):
        return None
    schema = response.get("_entity_schema")
    fields = schema.get("fields") if isinstance(schema, Mapping) else None
    declared = any(
        isinstance(field, Mapping)
        and field.get("name") == "customer_contact_name"
        and field.get("type") == "text"
        for field in fields if isinstance(fields, list)
    )
    if not declared:
        return None
    evidence = response.get("_entity_resolution_evidence")
    item = evidence.get("customer_contact_name") if isinstance(evidence, Mapping) else None
    if not isinstance(item, Mapping) or item.get("resolution") != "RESOLVED":
        return None
    value = normalize_authoritative_name(item.get("value"))
    return AuthorizedCustomerContactName(value) if value is not None else None


def apply_pending_profile_projection(session: Dict[str, Any], outcome: Mapping[str, Any]) -> None:
    planning = session.setdefault("planning", {})
    awaiting = outcome.get("awaiting")
    plan = outcome.get("plan")
    if isinstance(plan, Mapping):
        awaiting = plan.get("awaiting", awaiting)
    planning["pending_profile_request"] = (
        PENDING_CUSTOMER_CONTACT_NAME
        if awaiting == PENDING_CUSTOMER_CONTACT_NAME
        else None
    )
