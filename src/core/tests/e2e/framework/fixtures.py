"""Pytest fixtures for E2E conversation tests (RecordingLumaClient only).

E2E replays real production ``/resolve`` responses via
:class:`RecordingLumaClient`. Handwritten NLU payloads are not used.
Availability clients remain mocked with deterministic slot layouts.
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock

import httpx
import pytest

from core.api import message as message_api
from core.adapters.cache.catalog_cache import catalog_cache
from core.execution.clients.availability_client import AvailabilityClient
from core.api.compat import handle_message as real_handle_message
from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import (
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    ORG_ID,
    BookingConversation,
    _offer_date_for_availability_request,
    _resolve_search_date,
    create_empty_availability_client,
    create_multi_slot_availability_client,
    create_paginated_availability_client,
    create_slot_availability_client,
)
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.recording_luma_client import RecordingLumaClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter

TARGET_DATE = _resolve_search_date(None)

LIVE_LUMA_SKIP_REASON = "Live Luma unavailable"

# Marker for tests that may call live NLU on RecordingLumaClient cache miss.
# Skip behaviour is applied by ``_skip_if_live_luma_unavailable`` (autouse).
live_luma = pytest.mark.live_luma
# Backward-compatible alias — prefer ``live_luma``.
requires_luma = live_luma


@lru_cache(maxsize=1)
def live_luma_available() -> bool:
    """Return True when the live NLU service can currently be used.

    Treats timeouts, connection/DNS/transport errors, and HTTP 5xx as
    unavailable. HTTP 4xx means the service is reachable and is not treated
    as an infrastructure outage.
    """
    base = os.getenv("LUMA_BASE_URL", "http://localhost:9002").rstrip("/")
    try:
        with httpx.Client(timeout=3.0) as client:
            for path in ("/health", "/resolve"):
                try:
                    if path == "/health":
                        resp = client.get(f"{base}{path}")
                    else:
                        resp = client.post(
                            f"{base}{path}",
                            json={
                                "user_id": "e2e-probe",
                                "text": "hello",
                                "domain": "service",
                                "timezone": "UTC",
                            },
                        )
                    if resp.status_code < 500:
                        return True
                except (
                    httpx.TimeoutException,
                    httpx.TransportError,
                    httpx.HTTPError,
                ):
                    continue
    except Exception:
        return False
    return False


def luma_reachable() -> bool:
    """Compatibility alias for :func:`live_luma_available`."""
    return live_luma_available()


@pytest.fixture
def require_live_luma():
    """Skip the current test when Live Luma is not usable."""
    if not live_luma_available():
        pytest.skip(LIVE_LUMA_SKIP_REASON)


@pytest.fixture(autouse=True)
def _skip_if_live_luma_unavailable(request):
    """Auto-skip any test marked ``live_luma`` when the service is down."""
    if request.node.get_closest_marker("live_luma") is None:
        return
    if not live_luma_available():
        pytest.skip(LIVE_LUMA_SKIP_REASON)


# Availability layouts for RecordingLumaClient E2E bundles (no NLU payloads).
E2E_FIXTURE_PARAMS: Dict[str, Dict[str, Any]] = {
    "scripted": {},
    "scripted_empty": {"empty": True},
    "scripted_mismatch": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z", "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z", "end": f"{TARGET_DATE}T10:00:00Z"},
        ]
    },
    "scripted_mismatch_pick": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z", "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z", "end": f"{TARGET_DATE}T10:00:00Z"},
        ],
    },
    "scripted_confirm": {
        "start_hours": (9, 10, 11, 12),
    },
    "scripted_unavailable_time": {
        "start_hours": (10, 11),
    },
    "scripted_service_revision": {
        "start_hours": (10, 11),
    },
    "scripted_date_revision": {
        "start_hours": (10, 11),
    },
    "scripted_confirmation_interruption": {
        "start_hours": (10, 11),
    },
    "scripted_availability_supersession": {
        "start_hours": (9, 10, 11),
    },
    "scripted_confirmation_time_revision": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z", "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z", "end": f"{TARGET_DATE}T10:00:00Z"},
            {"start": f"{TARGET_DATE}T10:00:00Z", "end": f"{TARGET_DATE}T10:30:00Z"},
        ],
    },
    "scripted_july_confirm_date_shift": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z", "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z", "end": f"{TARGET_DATE}T10:00:00Z"},
            {"start": f"{TARGET_DATE}T10:00:00Z", "end": f"{TARGET_DATE}T10:30:00Z"},
            {"start": f"{TARGET_DATE}T11:00:00Z", "end": f"{TARGET_DATE}T11:30:00Z"},
        ],
    },
    "scripted_availability_service_revision": {
        "start_hours": (10, 11),
    },
    "scripted_browse_exhaustion_search": {
        "start_hours": (10, 11),
    },
    "scripted_turn_understanding": {
        "start_hours": (9, 10, 11, 12, 13, 14, 15, 16),
    },
    "scripted_off_topic": {
        "start_hours": (9, 10, 11, 12, 13, 14, 15, 16),
    },
    # Afternoon offers (13:30+) for post-availability time selection.
    "scripted_dotted_time_selection": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T13:30:00Z", "end": f"{TARGET_DATE}T14:00:00Z"},
            {"start": f"{TARGET_DATE}T14:00:00Z", "end": f"{TARGET_DATE}T14:30:00Z"},
            {"start": f"{TARGET_DATE}T14:30:00Z", "end": f"{TARGET_DATE}T15:00:00Z"},
            {"start": f"{TARGET_DATE}T15:00:00Z", "end": f"{TARGET_DATE}T15:30:00Z"},
            {"start": f"{TARGET_DATE}T15:30:00Z", "end": f"{TARGET_DATE}T16:00:00Z"},
            {"start": f"{TARGET_DATE}T16:00:00Z", "end": f"{TARGET_DATE}T16:30:00Z"},
        ],
    },
    # Multi-day provider surplus for single-day search shaping / date-revision tests.
    "scripted_multi_day_july23": {
        "absolute_slots": [
            {
                "start": "2026-07-23T09:00:00Z",
                "end": "2026-07-23T09:30:00Z",
                "available": True,
            },
            {
                "start": "2026-07-23T10:00:00Z",
                "end": "2026-07-23T10:30:00Z",
                "available": True,
            },
            {
                "start": "2026-07-24T09:00:00Z",
                "end": "2026-07-24T09:30:00Z",
                "available": True,
            },
            {
                "start": "2026-07-24T10:00:00Z",
                "end": "2026-07-24T10:30:00Z",
                "available": True,
            },
        ],
    },
}

# Backward-compatible alias (fixture keys still used by scenario modules).
SCRIPTED_FIXTURE_PARAMS = E2E_FIXTURE_PARAMS


def _fixed_slots_availability_client(slots: List[Dict[str, Any]]) -> Mock:
    """Return template slots rewritten onto the requested or first-available day."""
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        date = _offer_date_for_availability_request(kwargs.get("date"))
        rewritten: List[Dict[str, Any]] = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            new_slot = dict(slot)
            for key in ("start", "end"):
                val = new_slot.get(key)
                if isinstance(val, str) and len(val) >= 10 and val[4] == "-" and val[7] == "-":
                    new_slot[key] = date + val[10:]
            rewritten.append(new_slot)
        return {"slots": rewritten}

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def _absolute_slots_availability_client(slots: List[Dict[str, Any]]) -> Mock:
    """Return multi-day slots with calendar dates preserved (browse date-axis)."""
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**_kwargs):
        return {"slots": [dict(slot) for slot in slots if isinstance(slot, dict)]}

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def instrument_availability_tracing(mock_client: Mock) -> Mock:
    from core.tracing.availability import (
        begin_availability_request,
        finalize_availability_response,
    )

    original = mock_client.get_service_availability.side_effect

    def traced_get_service_availability(**kwargs):
        params = {key: value for key,
                  value in kwargs.items() if value is not None}
        begin_availability_request(
            endpoint="/api/internal/availability/service",
            method="GET",
            organization_id=int(kwargs.get("organization_id") or ORG_ID),
            params=params,
            service_id=kwargs.get("service_id"),
            date=kwargs.get("date"),
        )
        result = original(**kwargs) if callable(original) else {"slots": []}
        finalize_availability_response(
            raw_response=result,
            normalized={
                "type": "availability",
                "status": "success",
                "slots": (result or {}).get("slots") or [],
            },
        )
        return result

    mock_client.get_service_availability.side_effect = traced_get_service_availability
    return mock_client


def _availability_client_from_params(
    *,
    empty: bool = False,
    fixed_slots: Optional[List[Dict[str, Any]]] = None,
    absolute_slots: Optional[List[Dict[str, Any]]] = None,
    start_hours: tuple[int, ...] = (9, 10),
    trace_availability: bool = False,
) -> Any:
    if empty:
        availability_client = create_empty_availability_client()
    elif absolute_slots is not None:
        availability_client = _absolute_slots_availability_client(absolute_slots)
    elif fixed_slots is not None:
        availability_client = _fixed_slots_availability_client(fixed_slots)
    else:
        availability_client = create_slot_availability_client(
            start_hours=start_hours)
    if trace_availability:
        availability_client = instrument_availability_tracing(
            availability_client)
    return availability_client


def build_recorded_bundle(
    api_client,
    monkeypatch,
    *,
    empty: bool = False,
    fixed_slots: Optional[List[Dict[str, Any]]] = None,
    absolute_slots: Optional[List[Dict[str, Any]]] = None,
    start_hours: tuple[int, ...] = (9, 10),
    trace_availability: bool = False,
    **_ignored: Any,
) -> Tuple[BookingConversation, Any, Any, str]:
    """E2E booking bundle: RecordingLumaClient + configurable availability.

    ``**_ignored`` accepts legacy fixture keys (``nlu``, ``extra_scripts``, …)
    so call sites can splat ``E2E_FIXTURE_PARAMS`` without crashing; those keys
    are ignored — NLU is always recorded ``/resolve`` replay.
    """
    user_id = f"e2e-recorded-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    availability_client = _availability_client_from_params(
        empty=empty,
        fixed_slots=fixed_slots,
        absolute_slots=absolute_slots,
        start_hours=start_hours,
        trace_availability=trace_availability,
    )
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    return conv, booking_client, availability_client, user_id


# Backward-compatible name used by older test modules.
build_scripted_bundle = build_recorded_bundle


def _wire_booking_deps(monkeypatch, *, availability_client):
    """Wire RecordingLumaClient + catalog/org/booking mocks into the engine."""
    luma_client = RecordingLumaClient(
        TestLumaClient(test_aliases=HAIRCUT_CATALOG)
    )
    catalog_client = TestCatalogClient(
        test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()
    customer_client = _FakeE2ECustomerClient()

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client",
                        availability_client)
    monkeypatch.setattr(message_api, "_customer_client", customer_client)

    def handle_message_with_test_deps(**kwargs):
        kwargs.setdefault("luma_client", luma_client)
        kwargs.setdefault("organization_client", org_client)
        kwargs.setdefault("catalog_client", catalog_client)
        kwargs.setdefault("frozen_time", FROZEN_TIME)
        return real_handle_message(**kwargs)

    monkeypatch.setattr(
        message_api._engine, "process_turn", handle_message_with_test_deps
    )
    monkeypatch.setattr(message_api, "_e2e_luma_client", luma_client, raising=False)
    return booking_client, availability_client, luma_client


class _FakeE2ECustomerClient:
    """Org-scoped resolve-or-create stand-in for E2E (no live commerce)."""

    def __init__(self) -> None:
        self._next_id = 91001
        self._by_contact: Dict[tuple, int] = {}
        self._org_ids: Dict[int, int] = {}

    def upsert(
        self,
        *,
        organization_id: int,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = (int(organization_id), phone or "", email or "")
        existing = self._by_contact.get(key)
        if existing is not None:
            return {"id": existing, "name": name, "phone": phone, "email": email}
        customer_id = self._next_id
        self._next_id += 1
        self._by_contact[key] = customer_id
        self._org_ids[customer_id] = int(organization_id)
        return {"id": customer_id, "name": name, "phone": phone, "email": email}

    def belongs_to_organization(
        self, customer_id: int, organization_id: int
    ) -> bool:
        return self._org_ids.get(int(customer_id)) == int(organization_id)


@pytest.fixture
def booking_conversation(api_client, monkeypatch, require_live_luma):
    """RecordingLumaClient orchestration with mocked booking/availability."""
    user_id = f"e2e-create-appt-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    availability_client = create_multi_slot_availability_client()
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def paginated_booking_conversation(api_client, monkeypatch, require_live_luma):
    """Nine-slot paginated availability for browse/pagination E2E."""
    user_id = f"e2e-paginate-appt-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    availability_client = create_paginated_availability_client()
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def traced_recorded_conversation(api_client, monkeypatch, require_live_luma):
    """RecordingLumaClient with availability tracing for forensic validation."""
    conv, booking_client, availability_client, user_id = build_recorded_bundle(
        api_client, monkeypatch, trace_availability=True
    )
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


# Backward-compatible alias.
traced_scripted_conversation = traced_recorded_conversation
