"""Pytest fixtures for E2E conversation tests (RecordingLumaClient only).

E2E replays real production ``/resolve`` responses via
:class:`RecordingLumaClient`. Handwritten NLU payloads are not used.
Availability clients remain mocked with deterministic slot layouts.
"""

from __future__ import annotations

import copy
import os
import uuid
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock

import httpx
import pytest

from core.api import message as message_api
from core.adapters.cache.catalog_cache import catalog_cache
from core.adapters.cache.org_domain_cache import BUSINESS_CATEGORY_IDS, org_domain_cache
from core.config.business_category_loader import (
    get_booking_domain,
    get_catalog_collection_keys,
    get_category_entities,
)
from core.execution.clients.availability_client import AvailabilityClient
from core.api.compat import handle_message as real_handle_message
from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import (
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    HAIRCUT_ITEM_IDS,
    ORG_ID,
    BookingConversation,
    _offer_date_for_availability_request,
    _resolve_search_date,
    create_empty_availability_client,
    create_paginated_availability_client,
    create_slot_availability_client,
)
from core.tests.harness.car_service_catalog import (
    CAR_SERVICE_ALIASES,
    CAR_SERVICE_COLLECTIONS,
    CAR_SERVICE_SERVICE_RECORDS,
    CAR_SERVICE_STRUCTURED_CONTEXT,
)
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.recording_luma_client import (
    RecordingLumaClient,
    live_luma_calls_enabled,
)
from core.tests.harness.recording_render_client import RecordingRenderClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_category
from core.tests.mocks import reset_booking_counter

# Default booking E2E vertical when a scenario module omits BUSINESS_CATEGORY.
DEFAULT_BUSINESS_CATEGORY = "beauty_salon"

BEAUTY_SALON_SERVICE_RECORDS = [
    {
        "id": HAIRCUT_ITEM_IDS[display_name],
        "canonical": canonical_id,
        "name": display_name,
        "aliases": [display_name],
        "duration": 60,
        "is_active": True,
    }
    for display_name, canonical_id in HAIRCUT_CATALOG.items()
]

BEAUTY_SALON_STRUCTURED_CONTEXT = {
    "business_name": "Glamour Studio",
    "business_phone": "+1 555 000 1234",
    "services": [
        {
            "name": "Haircut",
            "type": "service",
            "config": {"price": 25, "duration": 30},
        }
    ],
    "hours": {"mon": "9am-6pm"},
    "cancellation_policy": {"notice_hours": 24, "fee": "50%"},
    "rescheduling_policy": None,
    "reservations": [],
}

# Static per-category infrastructure for Booking E2E bundles.
# Keys must match business_categories.yaml / OrgDomainCache ids.
CATEGORY_FIXTURES: Dict[str, Dict[str, Any]] = {
    "beauty_salon": {
        "aliases": HAIRCUT_CATALOG,
        "service_records": BEAUTY_SALON_SERVICE_RECORDS,
        "structured_context": BEAUTY_SALON_STRUCTURED_CONTEXT,
        "catalog_data": {},
    },
    "car_service": {
        "aliases": CAR_SERVICE_ALIASES,
        "service_records": CAR_SERVICE_SERVICE_RECORDS,
        "structured_context": CAR_SERVICE_STRUCTURED_CONTEXT,
        "catalog_data": CAR_SERVICE_COLLECTIONS,
    },
    "hotel": {
        "aliases": {
            "Standard Room": "standard-room",
            "Deluxe Room": "deluxe-room",
        },
        "service_records": [],
        "structured_context": {},
        "catalog_data": {},
    },
}


def resolve_category_fixture(business_category: Optional[str] = None) -> Dict[str, Any]:
    """Combine explicit product data with the configured business schema."""
    category = (business_category or DEFAULT_BUSINESS_CATEGORY).strip() or (
        DEFAULT_BUSINESS_CATEGORY
    )
    product_fixture = CATEGORY_FIXTURES.get(category)
    if product_fixture is None:
        known = ", ".join(sorted(CATEGORY_FIXTURES))
        raise ValueError(
            f"Unknown booking E2E business_category={category!r}. Known: {known}"
        )
    booking_domain = get_booking_domain(category)
    if not booking_domain:
        raise ValueError(
            f"Booking E2E category {category!r} has no configured booking_domain"
        )

    entities = copy.deepcopy(get_category_entities(category))
    catalog_collection_keys = get_catalog_collection_keys(category)
    primary_catalog_candidates = [
        entity.get("catalog")
        for entity in entities
        if entity.get("type") == "catalog"
        and entity.get("role") == "bookable_item"
        and isinstance(entity.get("catalog"), str)
        and entity.get("catalog")
    ]
    if len(primary_catalog_candidates) != 1:
        raise ValueError(
            f"Booking E2E category {category!r} must configure exactly one "
            "catalog entity with role='bookable_item'"
        )
    primary_catalog_collection = primary_catalog_candidates[0]
    configured_collections = set(catalog_collection_keys)
    catalog_data = product_fixture.get("catalog_data") or {}
    collections = {
        key: value
        for key, value in catalog_data.items()
        if key in configured_collections
    }
    business_category_id = next(
        (
            key
            for key, configured_category in BUSINESS_CATEGORY_IDS.items()
            if isinstance(key, int) and configured_category == category
        ),
        None,
    )
    if business_category_id is None:
        raise ValueError(
            f"Booking E2E category {category!r} has no numeric category id mapping"
        )

    return {
        **copy.deepcopy(product_fixture),
        "business_category": category,
        "business_category_id": business_category_id,
        "booking_domain": booking_domain,
        "entities": entities,
        "required_entities": [
            entity["name"]
            for entity in entities
            if entity.get("required") is True and isinstance(entity.get("name"), str)
        ],
        "availability_criteria": [
            entity["name"]
            for entity in entities
            if entity.get("availability_criteria") is True
            and isinstance(entity.get("name"), str)
        ],
        "catalog_collection_keys": catalog_collection_keys,
        "primary_catalog_collection": primary_catalog_collection,
        "collections": copy.deepcopy(collections),
    }

TARGET_DATE = _resolve_search_date(None)

LIVE_LUMA_SKIP_REASON = "Live Luma unavailable"

# Marker for tests backed by Luma recordings. Live reachability is checked only
# in explicit record or recache mode.
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
    """Probe live Luma only when recording was explicitly enabled."""
    if not live_luma_calls_enabled():
        return
    if not live_luma_available():
        pytest.skip(LIVE_LUMA_SKIP_REASON)


@pytest.fixture(autouse=True)
def _skip_if_live_luma_unavailable(request):
    """Auto-skip any test marked ``live_luma`` when the service is down."""
    if request.node.get_closest_marker("live_luma") is None:
        return
    if not live_luma_calls_enabled():
        return
    if not live_luma_available():
        pytest.skip(LIVE_LUMA_SKIP_REASON)


# Availability layouts for RecordingLumaClient E2E bundles (no NLU payloads).
E2E_FIXTURE_PARAMS: Dict[str, Dict[str, Any]] = {
    "scripted": {},
    "scripted_empty": {"empty": True},
    "car_service_proposal_continuity": {
        "handler_render_recordings": {
            "Hi, my car is making a weird rattling noise when I start it. Not sure what I need.": {
                "text": "For your rattling noise, the Premium Full Service would be the better "
                "choice since it includes the kind of checks that would help us pinpoint "
                "what's actually causing it. The oil change alone might resolve it if low "
                "oil is the culprit, but we wouldn't know without that fuller inspection.\n\n"
                "Would you like to book the Premium Full Service so we can diagnose that "
                "rattle for you?",
                "selected_entities": [{
                    "entity_type": "service",
                    "catalog_id": 27,
                    "display_name": "Premium Full Service",
                }],
                "metadata": {"source": "recording"},
            }
        }
    },
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
    business_category: Optional[str] = None,
    empty: bool = False,
    fixed_slots: Optional[List[Dict[str, Any]]] = None,
    absolute_slots: Optional[List[Dict[str, Any]]] = None,
    start_hours: tuple[int, ...] = (9, 10),
    trace_availability: bool = False,
    handler_render_recordings: Optional[Dict[str, Any]] = None,
    catalog_service_records: Optional[List[Dict[str, Any]]] = None,
    **_ignored: Any,
) -> Tuple[BookingConversation, Any, Any, str]:
    """E2E booking bundle: RecordingLumaClient + configurable availability.

    ``business_category`` selects catalog/org/aliases/booking_domain via
    ``CATEGORY_FIXTURES`` (default ``beauty_salon``).

    ``**_ignored`` accepts legacy fixture keys (``nlu``, ``extra_scripts``, …)
    so call sites can splat ``E2E_FIXTURE_PARAMS`` without crashing; those keys
    are ignored — NLU is always recorded ``/resolve`` replay.
    """
    category = business_category or DEFAULT_BUSINESS_CATEGORY
    cat_fx = resolve_category_fixture(category)
    booking_domain = str(cat_fx["booking_domain"])

    user_id = f"e2e-recorded-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    org_domain_cache._mem_cache.pop(ORG_ID, None)
    setup_test_org_category(category)
    catalog_cache._mem_cache.pop((ORG_ID, booking_domain), None)

    availability_client = _availability_client_from_params(
        empty=empty,
        fixed_slots=fixed_slots,
        absolute_slots=absolute_slots,
        start_hours=start_hours,
        trace_availability=trace_availability,
    )
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch,
        availability_client=availability_client,
        business_category=category,
        handler_render_recordings=handler_render_recordings,
        catalog_service_records=catalog_service_records,
    )
    conv = BookingConversation(api_client, user_id, domain=booking_domain)
    conv.structured_business_context = copy.deepcopy(
        cat_fx.get("structured_context") or {}
    )
    conv.faq_chunks = copy.deepcopy(cat_fx.get("faq_chunks") or [])
    conv.luma_client = luma_client
    conv.handler_render_client = getattr(
        message_api, "_e2e_handler_render_client", None
    )
    return conv, booking_client, availability_client, user_id


# Backward-compatible name used by older test modules.
build_scripted_bundle = build_recorded_bundle


def _wire_booking_deps(
    monkeypatch,
    *,
    availability_client,
    business_category: Optional[str] = None,
    handler_render_recordings: Optional[Dict[str, Any]] = None,
    catalog_service_records: Optional[List[Dict[str, Any]]] = None,
):
    """Wire RecordingLumaClient + catalog/org/booking mocks into the engine."""
    category = business_category or DEFAULT_BUSINESS_CATEGORY
    cat_fx = resolve_category_fixture(category)
    aliases = cat_fx["aliases"]
    collections = cat_fx.get("collections") or {}
    service_records = (
        catalog_service_records
        if catalog_service_records is not None
        else (cat_fx.get("service_records") or [])
    )
    if catalog_service_records is not None:
        aliases = {
            str(record["name"]): str(record["id"])
            for record in catalog_service_records
            if record.get("id") is not None and record.get("name")
        }
    catalog_collection_keys = cat_fx.get("catalog_collection_keys") or []
    primary_catalog_collection = cat_fx.get("primary_catalog_collection")
    booking_domain = str(cat_fx["booking_domain"])
    business_category_id = int(cat_fx["business_category_id"])

    luma_client = RecordingLumaClient(
        live_client_factory=lambda: TestLumaClient(test_aliases=aliases)
    )
    catalog_client = TestCatalogClient(
        test_aliases=aliases,
        domain=booking_domain,
        collections=collections if isinstance(collections, dict) else {},
        business_category_id=business_category_id,
        service_records=(
            service_records if isinstance(service_records, list) else []
        ),
        catalog_collection_keys=(
            catalog_collection_keys
            if isinstance(catalog_collection_keys, list)
            else []
        ),
        primary_catalog_collection=(
            str(primary_catalog_collection) if primary_catalog_collection else None
        ),
    )
    org_client = create_mock_organization_client(
        business_category_id=business_category_id
    )
    booking_client = create_mock_booking_client()
    customer_client = _FakeE2ECustomerClient()

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client",
                        availability_client)
    monkeypatch.setattr(message_api, "_customer_client", customer_client)
    if handler_render_recordings is not None:
        handler_render_client = RecordingRenderClient(handler_render_recordings)
        monkeypatch.setattr(
            message_api, "render_handler_response", handler_render_client.render
        )
        monkeypatch.setattr(
            message_api,
            "_e2e_handler_render_client",
            handler_render_client,
            raising=False,
        )

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
        self.update_name_by_id_calls = []

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

    def lookup_by_contact(
        self,
        *,
        organization_id: int,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        key = (int(organization_id), phone or "", email or "")
        customer_id = self._by_contact.get(key)
        if customer_id is None:
            return None
        return {
            "id": customer_id,
            "organizationId": int(organization_id),
            "name": "Existing Customer",
            "phone": phone,
            "email": email,
        }

    def belongs_to_organization(
        self, customer_id: int, organization_id: int
    ) -> bool:
        return self._org_ids.get(int(customer_id)) == int(organization_id)

    def update_name_by_id(
        self, *, organization_id: int, customer_id: int, name: str
    ) -> Dict[str, Any]:
        self.update_name_by_id_calls.append(
            {
                "organization_id": organization_id,
                "customer_id": customer_id,
                "name": name,
            }
        )
        if self._org_ids.get(int(customer_id)) != int(organization_id):
            raise RuntimeError("customer not found in organization")
        return {
            "id": int(customer_id),
            "organizationId": int(organization_id),
            "name": name,
        }


@pytest.fixture
def booking_conversation(api_client, monkeypatch, require_live_luma):
    """RecordingLumaClient orchestration with mocked booking/availability."""
    conv, booking_client, availability_client, user_id = build_recorded_bundle(
        api_client,
        monkeypatch,
        business_category=DEFAULT_BUSINESS_CATEGORY,
        start_hours=(10, 11),
    )
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def paginated_booking_conversation(api_client, monkeypatch, require_live_luma):
    """Nine-slot paginated availability for browse/pagination E2E."""
    category = DEFAULT_BUSINESS_CATEGORY
    cat_fx = resolve_category_fixture(category)
    booking_domain = str(cat_fx["booking_domain"])

    user_id = f"e2e-paginate-appt-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    org_domain_cache._mem_cache.pop(ORG_ID, None)
    setup_test_org_category(category)
    catalog_cache._mem_cache.pop((ORG_ID, booking_domain), None)

    availability_client = create_paginated_availability_client()
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch,
        availability_client=availability_client,
        business_category=category,
    )
    conv = BookingConversation(api_client, user_id, domain=booking_domain)
    conv.luma_client = luma_client
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def traced_recorded_conversation(api_client, monkeypatch, require_live_luma):
    """RecordingLumaClient with availability tracing for forensic validation."""
    conv, booking_client, availability_client, user_id = build_recorded_bundle(
        api_client,
        monkeypatch,
        business_category=DEFAULT_BUSINESS_CATEGORY,
        trace_availability=True,
    )
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


# Backward-compatible alias.
traced_scripted_conversation = traced_recorded_conversation
