"""Pytest fixtures and scripted NLU bundles for E2E conversation tests."""

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
    FLEXI_SERVICE,
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    ORG_ID,
    PREMIUM_SERVICE,
    BookingConversation,
    _resolve_search_date,
    create_empty_availability_client,
    create_multi_slot_availability_client,
    create_paginated_availability_client,
    create_slot_availability_client,
)
from core.tests.harness.clients import ScriptedLumaClient, TestCatalogClient, TestLumaClient
from core.tests.harness.recording_luma_client import RecordingLumaClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter

TARGET_DATE = _resolve_search_date(None)

LIVE_LUMA_SKIP_REASON = "Live Luma unavailable"

# Marker for tests that intentionally call the real NLU ``/resolve`` endpoint.
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


SCRIPTED_FIXTURE_PARAMS: Dict[str, Dict[str, Any]] = {
    "scripted": {},
    "scripted_empty": {"empty": True},
    "scripted_mismatch": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z",
                "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z",
                "end": f"{TARGET_DATE}T10:00:00Z"},
        ]
    },
    "scripted_mismatch_pick": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z",
                "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z",
                "end": f"{TARGET_DATE}T10:00:00Z"},
        ],
        "include_time_pick_script": True,
    },
    "scripted_confirm": {
        "start_hours": (9, 10, 11, 12),
    },
    # 10:00 + 11:00 only — 12:00 is unavailable for post-bind mismatch flows.
    "scripted_unavailable_time": {
        "start_hours": (10, 11),
    },
    # Deterministic service / date revision while confirmation is pending.
    "scripted_service_revision": {
        "start_hours": (10, 11),
    },
    "scripted_date_revision": {
        "start_hours": (10, 11),
    },
    "scripted_confirmation_interruption": {
        "start_hours": (10, 11),
    },
    # 09:00+ for availability-supersedes-confirmation regressions (9am bind).
    "scripted_availability_supersession": {
        "start_hours": (9, 10, 11),
    },
    # 09:00 / 09:30 / 10:00 for confirmation time-revision regression.
    "scripted_confirmation_time_revision": {
        "fixed_slots": [
            {"start": f"{TARGET_DATE}T09:00:00Z",
                "end": f"{TARGET_DATE}T09:30:00Z"},
            {"start": f"{TARGET_DATE}T09:30:00Z",
                "end": f"{TARGET_DATE}T10:00:00Z"},
            {"start": f"{TARGET_DATE}T10:00:00Z",
                "end": f"{TARGET_DATE}T10:30:00Z"},
        ],
    },
    # Premium then Flexi availability revision (NLU service post-process path).
    "scripted_availability_service_revision": {
        "start_hours": (10, 11),
        "apply_nlu_service_resolution": True,
    },
    # ≤6 slots so first browse_next exhausts immediately (no second page).
    "scripted_browse_exhaustion_search": {
        "start_hours": (10, 11),
    },
}


def _service_disambiguation_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "needs_clarification": True,
        "missing_slots": ["service_id"],
        "service_candidates": [
            {"text": PREMIUM_SERVICE},
            {"text": "flexi haircut + prunning"},
        ],
    }


def _temporal_turn_script(*, times: List[str]) -> Dict[str, Any]:
    script = _service_disambiguation_script()
    script["facts"] = {"dates": [TARGET_DATE], "times": times}
    script["time_constraint"] = {
        "mode": "exact",
        "start": times[0],
        "end": times[0],
    }
    return script


def _premium_turn_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "service_id": PREMIUM_SERVICE,
            "slots": {"service_id": PREMIUM_SERVICE},
        },
        "slots": {"service_id": PREMIUM_SERVICE},
        "missing_slots": ["time"],
    }


def _confirm_action_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CONFIRM_ACTION"},
    }


def _time_selection_script(time_value: str) -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"times": [time_value]},
        "time_constraint": {
            "mode": "exact",
            "start": time_value,
            "end": time_value,
        },
    }


def _service_revision_script(service_id: str) -> Dict[str, Any]:
    """Luma payload that reliably triggers detect_booking_revision(service=True)."""
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "service_id": service_id,
            "slots": {"service_id": service_id},
        },
        "slots": {"service_id": service_id},
        "missing_slots": [],
        "needs_clarification": False,
    }


def _date_revision_script(date_value: str) -> Dict[str, Any]:
    """Luma payload that reliably triggers detect_booking_revision(date=True)."""
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "dates": [date_value],
            "service_id": PREMIUM_SERVICE,
        },
        "date_proposal": {"mode": "single_day", "start": date_value},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }


def _fixed_slots_availability_client(slots: List[Dict[str, Any]]) -> Mock:
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        return {"slots": list(slots)}

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


def build_scripted_bundle(
    api_client,
    monkeypatch,
    *,
    empty: bool = False,
    fixed_slots: Optional[List[Dict[str, Any]]] = None,
    start_hours: tuple[int, ...] = (9, 10),
    include_time_pick_script: bool = False,
    trace_availability: bool = False,
    extra_scripts: Optional[Dict[str, Any]] = None,
    apply_nlu_service_resolution: bool = False,
) -> Tuple[BookingConversation, Any, Any, str]:
    user_id = f"e2e-scripted-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    scripts = {
        "book haircut": _service_disambiguation_script(),
        "book me a haircut": _service_disambiguation_script(),
        "book haircut tomorrow by 9am": _temporal_turn_script(times=["09:00"]),
        "book haircut tomorrow by 12pm": _temporal_turn_script(times=["12:00"]),
        "book me haircut tomorrow by 12pm": _temporal_turn_script(times=["12:00"]),
        "book haircut tomorrow at 10am": _temporal_turn_script(times=["10:00"]),
        "book haircut tomorrow at 9:15am": _temporal_turn_script(times=["09:15"]),
        "premium": _premium_turn_script(),
        "10am": _time_selection_script("10:00"),
        "12pm": _time_selection_script("12:00"),
        "rather book flexi haircut": _service_revision_script(FLEXI_SERVICE),
        "actually july 11": _date_revision_script("2026-07-11"),
        "yes": _confirm_action_script(),
        # Stage-2 AVAILABILITY shape: facts.service_id set, service_term absent.
        "show availability for flexi": {
            "success": True,
            "intent": {"name": "AVAILABILITY"},
            "facts": {"service_id": FLEXI_SERVICE},
        },
    }
    if extra_scripts:
        scripts.update(extra_scripts)
    if include_time_pick_script:
        scripts["9:30am"] = _time_selection_script("09:30")

    if apply_nlu_service_resolution:
        from core.tests.harness.clients import NluServiceResolutionScriptedLumaClient

        luma_client = NluServiceResolutionScriptedLumaClient(scripts)
    else:
        luma_client = ScriptedLumaClient(scripts)
    catalog_client = TestCatalogClient(
        test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()

    if empty:
        availability_client = create_empty_availability_client()
    elif fixed_slots is not None:
        availability_client = _fixed_slots_availability_client(fixed_slots)
    else:
        availability_client = create_slot_availability_client(
            start_hours=start_hours)
    if trace_availability:
        availability_client = instrument_availability_tracing(
            availability_client)

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client",
                        availability_client)

    def handle_message_with_test_deps(**kwargs):
        kwargs.setdefault("luma_client", luma_client)
        kwargs.setdefault("organization_client", org_client)
        kwargs.setdefault("catalog_client", catalog_client)
        kwargs.setdefault("frozen_time", FROZEN_TIME)
        return real_handle_message(**kwargs)

    monkeypatch.setattr(
        message_api._engine, "process_turn", handle_message_with_test_deps
    )
    conv = BookingConversation(api_client, user_id)
    return conv, booking_client, availability_client, user_id


def _wire_booking_deps(monkeypatch, *, availability_client):
    # Live NLU path: record/replay production /resolve bodies for determinism.
    luma_client = RecordingLumaClient(
        TestLumaClient(test_aliases=HAIRCUT_CATALOG)
    )
    catalog_client = TestCatalogClient(
        test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client",
                        availability_client)

    def handle_message_with_test_deps(**kwargs):
        kwargs.setdefault("luma_client", luma_client)
        kwargs.setdefault("organization_client", org_client)
        kwargs.setdefault("catalog_client", catalog_client)
        kwargs.setdefault("frozen_time", FROZEN_TIME)
        return real_handle_message(**kwargs)

    monkeypatch.setattr(
        message_api._engine, "process_turn", handle_message_with_test_deps
    )
    return booking_client, availability_client


@pytest.fixture
def booking_conversation(api_client, monkeypatch, require_live_luma):
    """Live-Luma orchestration with mocked booking/availability clients."""
    user_id = f"e2e-create-appt-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    availability_client = create_multi_slot_availability_client()
    booking_client, availability_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def paginated_booking_conversation(api_client, monkeypatch, require_live_luma):
    """Nine-slot paginated availability for live-Luma browse/pagination tests."""
    user_id = f"e2e-paginate-appt-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    availability_client = create_paginated_availability_client()
    booking_client, availability_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)


@pytest.fixture
def traced_scripted_conversation(api_client, monkeypatch):
    """Scripted NLU with availability tracing for forensic validation."""
    conv, booking_client, availability_client, user_id = build_scripted_bundle(
        api_client, monkeypatch, trace_availability=True
    )
    yield conv, booking_client, availability_client
    clear_session(ORG_ID, user_id)
