"""Availability browse and pagination E2E behaviour."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict

import pytest

from core.api import message as message_api
from core.workflows.availability.browse import resolve_availability_browse
from core.workflows.availability.fingerprint import compute_availability_fingerprint
from core.adapters.cache.catalog_cache import catalog_cache
from core.api.compat import handle_message as real_handle_message
from core.session.session_manager import clear_session
from core.tests.e2e.framework.conversation import (
    FROZEN_TIME,
    HAIRCUT_CATALOG,
    ORG_ID,
    PREMIUM_SERVICE,
    BookingConversation,
    _presentation_page_index,
    _reach_july_9_availability,
    _response_indicates_no_more_times,
    _response_pagination_page_index,
    _response_text,
    assert_different_availability_page,
    assert_no_booking_execution,
    create_paginated_availability_client,
    extract_presented_times,
)
from core.tests.e2e.framework.fixtures import requires_luma
from core.tests.harness.clients import ScriptedLumaClient, TestCatalogClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter

pytestmark = requires_luma

JULY_9 = "2026-07-09"
FULL_SLOT_COUNT = 9
PAGE_SIZE = 6


def _turn1_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "needs_clarification": True,
        "missing_slots": ["date", "service_id", "time"],
        "service_candidates": [
            {"text": PREMIUM_SERVICE},
            {"text": "flexi haircut + prunning"},
        ],
    }


def _turn2_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "service_id": PREMIUM_SERVICE,
            "slots": {"service_id": PREMIUM_SERVICE},
            "missing_slots": ["date", "time"],
        },
        "slots": {"service_id": PREMIUM_SERVICE},
        "missing_slots": ["date", "time"],
    }


def _turn3_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {
            "dates": [JULY_9],
            "service_id": PREMIUM_SERVICE,
            "slots": {"service_id": PREMIUM_SERVICE},
        },
        "slots": {},
        "date_proposal": {"mode": "single_day", "start": JULY_9},
        "missing_slots": ["time"],
    }


def _turn4_script() -> Dict[str, Any]:
    return {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "operation": "browse_next",
        "facts": {
            "service_id": PREMIUM_SERVICE,
            "slots": {"service_id": PREMIUM_SERVICE},
            "missing_slots": ["time"],
        },
        "slots": {"service_id": PREMIUM_SERVICE},
        "missing_slots": ["time"],
    }


def _browse_scripts() -> Dict[str, Dict[str, Any]]:
    return {
        "book haircut": _turn1_script(),
        "premium": _turn2_script(),
        "actually july 9": _turn3_script(),
        "show me additional times": _turn4_script(),
    }


@pytest.fixture
def browse_api_conversation(api_client, monkeypatch):
    user_id = f"e2e-browse-api-{uuid.uuid4().hex[:10]}"
    clear_session(user_id)
    reset_booking_counter()

    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    luma_client = ScriptedLumaClient(_browse_scripts())
    catalog_client = TestCatalogClient(
        test_aliases=HAIRCUT_CATALOG, domain="service")
    org_client = create_mock_organization_client(business_category_id=1)
    booking_client = create_mock_booking_client()
    availability_client = create_paginated_availability_client()

    monkeypatch.setattr(message_api, "_booking_client", booking_client)
    monkeypatch.setattr(message_api, "_availability_client",
                        availability_client)

    def handle_message_with_test_deps(**kwargs):
        kwargs.setdefault("luma_client", luma_client)
        kwargs.setdefault("organization_client", org_client)
        kwargs.setdefault("catalog_client", catalog_client)
        kwargs.setdefault("frozen_time", FROZEN_TIME)
        return real_handle_message(**kwargs)

    monkeypatch.setattr(message_api, "handle_message",
                        handle_message_with_test_deps)

    conv = BookingConversation(api_client, user_id)
    yield conv, booking_client, availability_client, luma_client

    clear_session(user_id)


def _cached_slot_count(session: Dict[str, Any]) -> int:
    last = session.get("last_execution_result") or {}
    return len(last.get("slots") or [])


def _presented_slot_count(session: Dict[str, Any]) -> int:
    presented = session.get("presented_availability") or {}
    return len(presented.get("slots") or [])


def test_browse_pagination_full_api_path_validation(browse_api_conversation):
    conv, booking_client, availability_client, luma_client = browse_api_conversation

    conv.send("book haircut")
    conv.assert_http_ok()
    conv.assert_turn(
        response_status="NEEDS_CLARIFICATION",
        intent="CREATE_APPOINTMENT",
    )

    conv.send("premium")
    conv.assert_http_ok()
    sess2 = conv.session() or {}
    assert conv.outcome.get("status") != "NEEDS_CLARIFICATION"
    assert sess2.get("slots", {}).get("service_id") == PREMIUM_SERVICE
    searches_after_turn2 = availability_client.get_service_availability.call_count
    assert searches_after_turn2 >= 1

    conv.send("actually July 9")
    conv.assert_http_ok()
    sess3 = conv.session() or {}
    searches_after_turn3 = availability_client.get_service_availability.call_count
    assert searches_after_turn3 == searches_after_turn2 + 1
    stored_fp = sess3.get("availability_fingerprint")
    assert stored_fp
    full_cached = _cached_slot_count(sess3)
    assert full_cached == FULL_SLOT_COUNT
    search_fp = compute_availability_fingerprint(
        {
            "organization_id": ORG_ID,
            "service_id": PREMIUM_SERVICE,
            "date": JULY_9,
        }
    )
    assert stored_fp == search_fp
    first_page = extract_presented_times(conv.last_body, sess3)
    assert first_page
    assert _presentation_page_index(sess3) == 0

    merged_probe = copy.deepcopy(_turn4_script())
    merged_probe["_source_text"] = "show me additional times"
    merged_probe["_raw_luma_response"] = copy.deepcopy(_turn4_script())
    resolved = resolve_availability_browse(merged_probe, sess3)
    assert resolved == {"direction": "next"}

    conv.send("show me additional times")
    conv.assert_http_ok()
    conv.assert_turn(intent="CREATE_APPOINTMENT", action=None)
    assert_no_booking_execution(conv, booking_client)
    sess4 = conv.session() or {}

    assert _presentation_page_index(sess4) == 1
    assert _response_pagination_page_index(conv.last_body) == 1
    pagination = (conv.outcome or {}).get("availability_pagination") or conv.last_body.get(
        "availability_pagination"
    ) or {}
    assert pagination.get("direction") == "next"
    assert pagination.get("page_index") == 1
    assert availability_client.get_service_availability.call_count == searches_after_turn3

    page1_slots = extract_presented_times(conv.last_body, sess4)
    assert_different_availability_page(
        first_page,
        page1_slots,
        response_text=_response_text(conv.last_body),
        turn=conv.turn,
    )
    assert _presented_slot_count(sess4) == FULL_SLOT_COUNT - PAGE_SIZE
    assert _cached_slot_count(sess4) == FULL_SLOT_COUNT
    assert luma_client.last_text == "show me additional times"


def test_show_more_times_paginates_existing_availability(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    july_9_first_page = _reach_july_9_availability(conv)
    searches_before_show_more = availability_client.get_service_availability.call_count

    conv.send("show me additional times")
    conv.assert_turn(
        intent="CREATE_APPOINTMENT",
        confirmation=None,
        action=None,
    )
    conv.assert_slots(session={"service_id": PREMIUM_SERVICE})
    conv.assert_date_proposal("2026-07-09")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before_show_more

    pagination_page = _response_pagination_page_index(conv.last_body)
    assert pagination_page == 1

    show_more_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(
        july_9_first_page,
        show_more_page,
        response_text=_response_text(conv.last_body),
        turn=conv.turn,
    )
    assert _presentation_page_index(conv.session()) == 1


def test_show_more_at_last_page_says_no_more(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    july_9_first_page = _reach_july_9_availability(conv)

    conv.send("show me additional times")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(
        july_9_first_page,
        second_page,
        turn=conv.turn,
    )
    searches_before = availability_client.get_service_availability.call_count

    conv.send("show more times")
    conv.assert_turn(intent="CREATE_APPOINTMENT", action=None)
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    third_page = extract_presented_times(conv.last_body, conv.session())
    assert third_page == second_page
    pagination = (conv.outcome or {}).get("availability_pagination") or conv.last_body.get(
        "availability_pagination"
    ) or {}
    assert pagination.get("exhausted") is True
    assert pagination.get("direction") == "next"
    assert pagination.get("page_index") == 1
    assert _presentation_page_index(conv.session()) == 1
    assert len((conv.session() or {}).get(
        "last_execution_result", {}).get("slots") or []) >= 9
    assert _response_indicates_no_more_times(_response_text(conv.last_body))


def test_previous_page_returns_earlier_availability(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)

    conv.send("show more times")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)
    searches_before = availability_client.get_service_availability.call_count

    conv.send("earlier times")
    conv.assert_turn(intent="CREATE_APPOINTMENT", action=None)
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert extract_presented_times(
        conv.last_body, conv.session()) == first_page
    assert _presentation_page_index(conv.session()) == 0


def test_pagination_resets_on_service_change(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    _reach_july_9_availability(conv)
    conv.send("show more times")
    assert _presentation_page_index(conv.session()) == 1

    conv.send("rather book flexi haircut")
    conv.assert_turn(intent="CREATE_APPOINTMENT", action="SEARCH_AVAILABILITY")
    assert _presentation_page_index(conv.session()) == 0
    assert not booking_client.create_booking.called


def test_pagination_resets_on_date_change(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    _reach_july_9_availability(conv)
    conv.send("show more times")
    assert _presentation_page_index(conv.session()) == 1

    conv.send("actually July 11")
    conv.assert_turn(
        intent="CREATE_APPOINTMENT",
        action="SEARCH_AVAILABILITY",
        date_proposal_start="2026-07-11",
    )
    assert _presentation_page_index(conv.session()) == 0
    assert not booking_client.create_booking.called


def test_time_on_page_two_binds_not_page_one_slot(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    conv.send("show more times")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)

    conv.send("9am")
    conv.assert_turn(intent="CREATE_APPOINTMENT")
    assert conv.outcome.get("status") != "AWAITING_CONFIRMATION"
    assert not booking_client.create_booking.called

    searches_before_5pm = availability_client.get_service_availability.call_count
    full_cache = len((conv.session() or {}).get(
        "last_execution_result", {}).get("slots") or [])
    conv.send("5pm")
    conv.assert_turn(
        response_status="AWAITING_CONFIRMATION",
        stage="CONFIRM",
        awaiting="USER_CONFIRMATION",
        action=None,
        confirmation="pending",
    )
    assert availability_client.get_service_availability.call_count == searches_before_5pm
    sess = conv.session() or {}
    bound = sess.get("resolved_datetime_range") or {}
    assert bound.get("start") == "2026-07-09T17:00:00Z"
    assert len(sess.get("last_execution_result", {}).get(
        "slots") or []) == full_cache
    conv.assert_slot_contains("time", "17", in_session=True)
