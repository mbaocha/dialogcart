"""Tenant customer resolve-or-create (ingress) unit tests."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.adapters.customer_resolver import (
    coerce_positive_customer_id,
    resolve_tenant_customer,
)
from core.adapters.clients.customer_client import _extract_customer


class _FakeCustomerClient:
    def __init__(
        self,
        *,
        valid_ids: Optional[set[int]] = None,
        upsert_id: int = 42,
    ) -> None:
        self.valid_ids = valid_ids or set()
        self.upsert_id = upsert_id
        self.upsert_calls: list[Dict[str, Any]] = []
        self.validate_calls: list[tuple[int, int]] = []

    def belongs_to_organization(self, customer_id: int, organization_id: int) -> bool:
        self.validate_calls.append((customer_id, organization_id))
        return customer_id in self.valid_ids

    def upsert(
        self,
        *,
        organization_id: int,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.upsert_calls.append(
            {
                "organization_id": organization_id,
                "name": name,
                "phone": phone,
                "email": email,
            }
        )
        return {"id": self.upsert_id, "organizationId": organization_id, "name": name}


def test_coerce_positive_customer_id():
    assert coerce_positive_customer_id(7) == 7
    assert coerce_positive_customer_id("12") == 12
    assert coerce_positive_customer_id(0) is None
    assert coerce_positive_customer_id(-1) is None
    assert coerce_positive_customer_id(None) is None
    assert coerce_positive_customer_id("x") is None


def test_request_customer_id_validated_wins():
    client = _FakeCustomerClient(valid_ids={99})
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        session={"customer_id": 5},
        customer_id=99,
        phone="+15550001111",
    )
    assert resolved == 99
    assert client.validate_calls == [(99, 2)]
    assert client.upsert_calls == []


def test_invalid_request_id_falls_back_to_session():
    client = _FakeCustomerClient(valid_ids=set())
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        session={"customer_id": 5},
        customer_id=99,
    )
    assert resolved == 5
    assert client.upsert_calls == []


def test_session_customer_id_without_channel_identity():
    client = _FakeCustomerClient()
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        session={"customer_id": 17},
    )
    assert resolved == 17
    assert client.upsert_calls == []
    assert client.validate_calls == []


def test_upsert_from_phone_when_unresolved():
    client = _FakeCustomerClient(upsert_id=88)
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        phone="+15550002222",
        name="Ada",
    )
    assert resolved == 88
    assert client.upsert_calls == [
        {
            "organization_id": 2,
            "name": "Ada",
            "phone": "+15550002222",
            "email": None,
        }
    ]


def test_unresolved_without_identity_returns_none():
    client = _FakeCustomerClient()
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        session={},
    )
    assert resolved is None
    assert client.upsert_calls == []


def test_does_not_use_user_id_as_customer_id():
    """Regression: chat user_id must never become commerce customer_id."""
    client = _FakeCustomerClient(upsert_id=1)
    resolved = resolve_tenant_customer(
        organization_id=2,
        customer_client=client,
        session={"user_id": "fk-abc", "customer_id": None},
        customer_id=None,
    )
    assert resolved is None
    assert client.upsert_calls == []


def test_extract_customer_from_wrapped_response():
    assert _extract_customer({"data": {"customer": {"id": 3}}}) == {"id": 3}
    assert _extract_customer({"customer": {"id": 4}}) == {"id": 4}
    assert _extract_customer({"data": {"id": 5}}) == {"id": 5}
    assert _extract_customer({}) is None
