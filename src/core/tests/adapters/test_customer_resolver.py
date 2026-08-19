"""Tenant customer resolve-or-create (ingress) unit tests."""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
import pytest

from core.adapters.customer_resolver import (
    coerce_positive_customer_id,
    resolve_tenant_customer,
    resolve_tenant_customer_projection,
)
from core.adapters.clients.customer_client import CustomerClient, _extract_customer
from core.adapters.errors import UpstreamError
from core.customer_identification import customer_channel_fingerprint


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
        self.lookup_calls: list[Dict[str, Any]] = []
        self.lookup_response: Optional[Dict[str, Any]] = None
        self.validate_calls: list[tuple[int, int]] = []

    def lookup_by_contact(self, **kwargs):
        self.lookup_calls.append(kwargs)
        return self.lookup_response

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


def test_valid_channel_bound_session_contact_skips_commerce_lookup():
    client = _FakeCustomerClient()
    phone = "+15550002222"
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        session={
            "customer_id": 17,
            "customer_contact": {
                "customer_id": 17,
                "authoritative_name": "Existing Customer",
                "name_status": "authoritative",
                "channel_fingerprint": customer_channel_fingerprint(phone=phone),
            },
        },
        phone=phone,
    )
    assert projection == {"id": 17, "name": "Existing Customer"}
    assert client.lookup_calls == []


def test_changed_phone_does_not_reuse_session_contact():
    client = _FakeCustomerClient()
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        session={
            "customer_id": 17,
            "customer_contact": {
                "customer_id": 17,
                "authoritative_name": "Existing Customer",
                "name_status": "authoritative",
                "channel_fingerprint": customer_channel_fingerprint(
                    phone="+15550001111"
                ),
            },
        },
        phone="+15550002222",
    )
    assert projection is None
    assert len(client.lookup_calls) == 1


def test_session_customer_id_without_contact_is_hydrated_by_phone_lookup():
    client = _FakeCustomerClient()
    client.lookup_response = {
        "id": 17,
        "organizationId": 2,
        "name": "Existing Customer",
    }
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        session={"customer_id": 17},
        phone="+15550002222",
    )
    assert projection == client.lookup_response


def test_changed_phone_lookup_rebinds_to_resolved_customer():
    client = _FakeCustomerClient()
    client.lookup_response = {
        "id": 99,
        "organizationId": 2,
        "name": "Different Customer",
    }
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        session={"customer_id": 17},
        phone="+15550002222",
    )
    assert projection == client.lookup_response


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


def test_missing_or_placeholder_name_never_upserts_customer():
    client = _FakeCustomerClient()
    for name in (None, "", "   ", "Guest", "gUeSt", "anonymous"):
        projection = resolve_tenant_customer_projection(
            organization_id=2,
            customer_client=client,
            phone="+15550002222",
            name=name,
        )
        assert projection is None
    assert client.upsert_calls == []


def test_phone_only_lookup_returns_existing_customer_without_upsert():
    client = _FakeCustomerClient()
    client.lookup_response = {
        "id": 231,
        "organizationId": 2,
        "name": "Existing Customer",
    }
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        phone="+15550002222",
    )
    assert projection == client.lookup_response
    assert client.lookup_calls == [{
        "organization_id": 2,
        "phone": "+15550002222",
        "email": None,
    }]
    assert client.upsert_calls == []


def test_phone_lookup_miss_then_named_identity_upserts():
    client = _FakeCustomerClient(upsert_id=88)
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        phone="+15550002222",
        name="Ada",
    )
    assert projection == {"id": 88, "organizationId": 2, "name": "Ada"}
    assert len(client.lookup_calls) == 1
    assert len(client.upsert_calls) == 1


def test_customer_client_lookup_by_contact_contract(monkeypatch):
    client = CustomerClient(base_url="http://commerce.test")
    calls = []

    def request(method, path, json=None, params=None):
        calls.append((method, path, json, params))
        return {
            "success": True,
            "data": {
                "customer": {
                    "id": 231,
                    "organizationId": 2,
                    "name": "Existing Customer",
                }
            },
        }

    monkeypatch.setattr(client, "_request_allow_404", request)
    customer = client.lookup_by_contact(
        organization_id=2, phone="+15550002222"
    )
    assert customer["name"] == "Existing Customer"
    assert calls == [(
        "GET",
        "/api/internal/organizations/2/customers",
        None,
        {"phone": "+15550002222"},
    )]


def test_projection_retains_commerce_returned_name():
    client = _FakeCustomerClient(upsert_id=88)
    projection = resolve_tenant_customer_projection(
        organization_id=2,
        customer_client=client,
        phone="+15550002222",
        name=" Ada ",
    )
    assert projection == {"id": 88, "organizationId": 2, "name": "Ada"}


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


def test_customer_client_update_name_by_id_contract(monkeypatch):
    client = CustomerClient(base_url="http://commerce.test")
    calls = []

    def request(method, path, json=None, params=None):
        calls.append((method, path, json, params))
        return {
            "success": True,
            "data": {
                "customer": {
                    "id": 2,
                    "organizationId": 1,
                    "name": "Commerce Name",
                    "updatedAt": "unchanged-is-valid",
                }
            },
        }

    monkeypatch.setattr(client, "_request", request)
    customer = client.update_name_by_id(
        organization_id=1, customer_id=2, name="Submitted Name"
    )
    assert calls == [
        (
            "PATCH",
            "/api/internal/organizations/1/customers/2",
            {"name": "Submitted Name"},
            None,
        )
    ]
    assert customer["name"] == "Commerce Name"


def test_customer_client_update_name_by_id_rejects_malformed_authority(monkeypatch):
    client = CustomerClient(base_url="http://commerce.test")
    invalid = [
        {"success": False, "data": None},
        {"success": True, "data": {}},
        {"success": True, "data": {"customer": {"id": 3, "organizationId": 1, "name": "Ada"}}},
        {"success": True, "data": {"customer": {"id": 2, "organizationId": 9, "name": "Ada"}}},
        {"success": True, "data": {"customer": {"id": 2, "organizationId": 1, "name": "Guest"}}},
    ]
    for body in invalid:
        monkeypatch.setattr(client, "_request", lambda *args, _body=body, **kwargs: _body)
        try:
            client.update_name_by_id(organization_id=1, customer_id=2, name="Ada")
        except UpstreamError:
            continue
        raise AssertionError(f"accepted malformed customer authority: {body!r}")


@pytest.mark.parametrize("status_code", [400, 404, 422])
def test_customer_client_update_name_by_id_propagates_http_failures(
    monkeypatch, status_code
):
    client = CustomerClient(base_url="http://commerce.test")
    request = httpx.Request(
        "PATCH", "http://commerce.test/api/internal/organizations/1/customers/2"
    )
    response = httpx.Response(
        status_code,
        request=request,
        json={"success": False, "message": "rejected", "data": None},
    )
    monkeypatch.setattr(client._client, "request", lambda **kwargs: response)
    with pytest.raises(UpstreamError):
        client.update_name_by_id(organization_id=1, customer_id=2, name="Ada")


def test_customer_client_update_name_by_id_rejects_malformed_json(monkeypatch):
    client = CustomerClient(base_url="http://commerce.test")
    request = httpx.Request(
        "PATCH", "http://commerce.test/api/internal/organizations/1/customers/2"
    )
    response = httpx.Response(200, request=request, content=b"not-json")
    monkeypatch.setattr(client._client, "request", lambda **kwargs: response)
    with pytest.raises(UpstreamError):
        client.update_name_by_id(organization_id=1, customer_id=2, name="Ada")
