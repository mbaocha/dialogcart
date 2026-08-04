"""Availability browse and pagination E2E behaviour."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict

import pytest

from core.api import message as message_api
from core.workflows.availability.browse import resolve_availability_browse
from core.workflows.availability.fingerprint import compute_availability_fingerprint
from core.workflows.availability.presentation import (
    availability_cache_from_session,
    availability_fingerprint_from_session,
    presented_availability_from_session,
)
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
from core.tests.e2e.framework.fixtures import live_luma
from core.tests.harness.clients import TestCatalogClient, TestLumaClient
from core.tests.harness.recording_luma_client import RecordingLumaClient
from core.tests.harness.mock_clients import (
    create_mock_booking_client,
    create_mock_organization_client,
)
from core.tests.harness.org_setup import setup_test_org_domain
from core.tests.mocks import reset_booking_counter

JULY_9 = "2026-07-09"
FULL_SLOT_COUNT = 9
PAGE_SIZE = 6


@pytest.fixture(autouse=True)
def _deterministic_availability_llm(monkeypatch):
    """Avoid live LLM; keep browse exhaustion / nav-hint wording deterministic."""
    from core.rendering.availability_renderer import (
        browse_navigation_hint_text,
        resolve_browse_status_text,
        resolve_time_mismatch_text,
    )

    def _fake_render(request):
        facts = getattr(request, "facts", None) or {}
        if not isinstance(facts, dict):
            facts = {}
        availability = facts.get("availability")
        if not isinstance(availability, dict):
            availability = {}
        time_resolution = facts.get("time_resolution")
        if isinstance(time_resolution, dict):
            outcome = time_resolution.get("outcome") or time_resolution.get("status")
            if outcome in ("TIME_MATCH_MISMATCH", "no_match"):
                return resolve_time_mismatch_text(
                    requested_time=(
                        str(time_resolution["requested_time"])
                        if time_resolution.get("requested_time") is not None
                        else None
                    ),
                    times=(
                        list(availability.get("times") or [])
                        if isinstance(availability.get("times"), list)
                        else None
                    ),
                    alternatives=(
                        list(time_resolution.get("alternatives") or [])
                        if isinstance(time_resolution.get("alternatives"), list)
                        else None
                    ),
                    mismatch_location=(
                        str(time_resolution["mismatch_location"])
                        if time_resolution.get("mismatch_location") is not None
                        else None
                    ),
                    search_date=(
                        str(availability["date"])
                        if availability.get("date") is not None
                        else (
                            str(availability["search_date"])
                            if availability.get("search_date") is not None
                            else None
                        )
                    ),
                    browse_hints=(
                        availability.get("browse_hints")
                        if isinstance(availability.get("browse_hints"), dict)
                        else None
                    ),
                    recovery_actions=(
                        time_resolution.get("recovery_actions")
                        if isinstance(time_resolution.get("recovery_actions"), list)
                        else (
                            availability.get("recovery_actions")
                            if isinstance(availability.get("recovery_actions"), list)
                            else None
                        )
                    ),
                )
        browse_status = str(
            facts.get("browse_status")
            or availability.get("browse_status")
            or ""
        ).strip()
        if browse_status:
            return resolve_browse_status_text(
                browse_status=browse_status,
                direction=str(facts.get("direction") or "next"),
                browse_hints=(
                    facts.get("browse_hints")
                    if isinstance(facts.get("browse_hints"), dict)
                    else None
                ),
                search_date=(
                    str(facts["search_date"])
                    if facts.get("search_date") is not None
                    else None
                ),
                recovery_actions=(
                    facts.get("recovery_actions")
                    if isinstance(facts.get("recovery_actions"), list)
                    else None
                ),
            )
        date_label = str(availability.get("date") or "").strip()
        times = availability.get("times") or []
        browse_hints = availability.get("browse_hints")
        if not isinstance(browse_hints, dict):
            browse_hints = (
                facts.get("browse_hints")
                if isinstance(facts.get("browse_hints"), dict)
                else None
            )
        recovery_actions = (
            availability.get("recovery_actions")
            if isinstance(availability.get("recovery_actions"), list)
            else None
        )
        nav = browse_navigation_hint_text(
            browse_hints, recovery_actions=recovery_actions
        )
        nav_suffix = f"\n\n{nav}" if nav else ""
        if times:
            lines = "\n".join(f"- {t}" for t in times[:5])
            if date_label:
                return (
                    f"Here are the available times for {date_label}:\n"
                    f"{lines}\n\nWhich time works for you?{nav_suffix}"
                )
            return (
                f"Here are the available times:\n{lines}\n\n"
                f"Which time works for you?{nav_suffix}"
            )
        if date_label:
            return (
                f"Here are the available appointment times for {date_label}. "
                f"Which time works for you?{nav_suffix}"
            )
        return (
            f"Here are the available appointment times. "
            f"Which time works for you?{nav_suffix}"
        )

    monkeypatch.setattr("core.rendering.llm_renderer.render_llm", _fake_render)
    monkeypatch.setattr("core.rendering.response_renderer.render_llm", _fake_render)
    monkeypatch.setattr(
        "core.workflows.availability.pagination.render_llm",
        _fake_render,
    )


def _browse_next_probe_payload() -> Dict[str, Any]:
    """Minimal Core-side probe for resolve_availability_browse (not an NLU script)."""
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


@pytest.fixture
def browse_api_conversation(api_client, monkeypatch):
    user_id = f"e2e-browse-api-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()

    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    luma_client = RecordingLumaClient(TestLumaClient(test_aliases=HAIRCUT_CATALOG))
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

    monkeypatch.setattr(
        message_api._engine, "process_turn", handle_message_with_test_deps
    )

    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    yield conv, booking_client, availability_client, luma_client

    clear_session(ORG_ID, user_id)


def _cached_slot_count(session: Dict[str, Any]) -> int:
    last = availability_cache_from_session(session) or {}
    return len(last.get("slots") or [])


def _presented_slot_count(session: Dict[str, Any]) -> int:
    presented = presented_availability_from_session(session) or {}
    return len(presented.get("slots") or [])


@live_luma
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
    stored_fp = availability_fingerprint_from_session(sess3)
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

    merged_probe = copy.deepcopy(_browse_next_probe_payload())
    merged_probe["_source_text"] = "show me additional times"
    merged_probe["_raw_luma_response"] = copy.deepcopy(_browse_next_probe_payload())
    resolved = resolve_availability_browse(merged_probe, sess3)
    assert resolved is not None and resolved.get("direction") == "next"

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
    assert isinstance(luma_client.last_response, dict)


@live_luma
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


def _assert_browse_nav_advertising(
    text: str,
    *,
    advertise_next: bool,
    advertise_previous: bool,
    turn: int = 0,
) -> None:
    """Assert only valid page-navigation directions are advertised in copy."""
    lowered = text.lower()
    has_next = "`next`" in lowered
    has_previous = "`previous`" in lowered
    if advertise_next and not has_next:
        pytest.fail(f"turn {turn}: expected `next` advertised in {text!r}")
    if not advertise_next and has_next:
        pytest.fail(f"turn {turn}: must not advertise `next` in {text!r}")
    if advertise_previous and not has_previous:
        pytest.fail(f"turn {turn}: expected `previous` advertised in {text!r}")
    if not advertise_previous and has_previous:
        pytest.fail(f"turn {turn}: must not advertise `previous` in {text!r}")


@live_luma
def test_browse_navigation_hints_first_last_and_exhaustion(
    paginated_booking_conversation,
):
    """Advertise only valid nav directions for first/last page and exhaustion."""
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    assert first_page
    _assert_browse_nav_advertising(
        _response_text(conv.last_body),
        advertise_next=True,
        advertise_previous=False,
        turn=conv.turn,
    )

    searches_before = availability_client.get_service_availability.call_count
    conv.send("next")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)
    assert _presentation_page_index(conv.session()) == 1
    # 9 slots / page size 6 → page 1 is the last page.
    _assert_browse_nav_advertising(
        _response_text(conv.last_body),
        advertise_next=False,
        advertise_previous=True,
        turn=conv.turn,
    )
    assert availability_client.get_service_availability.call_count == searches_before

    conv.send("next")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    exhausted_text = _response_text(conv.last_body)
    assert _response_indicates_no_more_times(exhausted_text)
    assert "another date" in exhausted_text.lower()
    _assert_browse_nav_advertising(
        exhausted_text,
        advertise_next=False,
        advertise_previous=True,
        turn=conv.turn,
    )


@live_luma
def test_browse_navigation_hints_middle_page(api_client, monkeypatch, require_live_luma):
    """Middle page advertises both `next` and `previous`."""
    from core.tests.e2e.framework.fixtures import _wire_booking_deps

    user_id = f"e2e-browse-nav-mid-{uuid.uuid4().hex[:10]}"
    clear_session(ORG_ID, user_id)
    reset_booking_counter()
    setup_test_org_domain("service")
    catalog_cache._mem_cache.pop((ORG_ID, "service"), None)

    # 15 hourly slots → 3 pages at page size 6 (first / middle / last).
    availability_client = create_paginated_availability_client(
        slot_hours=tuple(range(9, 24)),
    )
    booking_client, availability_client, luma_client = _wire_booking_deps(
        monkeypatch, availability_client=availability_client
    )
    conv = BookingConversation(api_client, user_id)
    conv.luma_client = luma_client
    try:
        first_page = _reach_july_9_availability(conv)
        _assert_browse_nav_advertising(
            _response_text(conv.last_body),
            advertise_next=True,
            advertise_previous=False,
            turn=conv.turn,
        )

        conv.send("next")
        middle_page = extract_presented_times(conv.last_body, conv.session())
        assert_different_availability_page(first_page, middle_page, turn=conv.turn)
        assert _presentation_page_index(conv.session()) == 1
        _assert_browse_nav_advertising(
            _response_text(conv.last_body),
            advertise_next=True,
            advertise_previous=True,
            turn=conv.turn,
        )

        conv.send("next")
        last_page = extract_presented_times(conv.last_body, conv.session())
        assert_different_availability_page(middle_page, last_page, turn=conv.turn)
        assert _presentation_page_index(conv.session()) == 2
        _assert_browse_nav_advertising(
            _response_text(conv.last_body),
            advertise_next=False,
            advertise_previous=True,
            turn=conv.turn,
        )
    finally:
        clear_session(ORG_ID, user_id)


@live_luma
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

    conv.send("show more")
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
    assert _cached_slot_count(conv.session() or {}) >= 9
    assert _response_indicates_no_more_times(_response_text(conv.last_body))
    exhausted = _response_text(conv.last_body)
    assert "another date" in exhausted.lower()
    _assert_browse_nav_advertising(
        exhausted,
        advertise_next=False,
        advertise_previous=True,
        turn=conv.turn,
    )


@live_luma
def test_previous_page_returns_earlier_availability(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)

    conv.send("show more")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)
    searches_before = availability_client.get_service_availability.call_count

    conv.send("previous")
    conv.assert_turn(intent="CREATE_APPOINTMENT", action=None)
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert extract_presented_times(
        conv.last_body, conv.session()) == first_page
    assert _presentation_page_index(conv.session()) == 0


@live_luma
def test_previous_on_first_page_advertises_next(paginated_booking_conversation):
    """Previous-boundary status advertises valid `next` and another date."""
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    assert first_page
    searches_before = availability_client.get_service_availability.call_count

    conv.send("previous")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert _presentation_page_index(conv.session()) == 0
    pagination = (conv.outcome or {}).get("availability_pagination") or conv.last_body.get(
        "availability_pagination"
    ) or {}
    assert pagination.get("exhausted") is True
    assert pagination.get("direction") == "previous"
    text = _response_text(conv.last_body)
    assert "another date" in text.lower()
    assert "earlier" in text.lower()
    _assert_browse_nav_advertising(
        text,
        advertise_next=True,
        advertise_previous=False,
        turn=conv.turn,
    )


@live_luma
def test_pagination_resets_on_service_change(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    _reach_july_9_availability(conv)
    conv.send("show more")
    assert _presentation_page_index(conv.session()) == 1

    conv.send("rather book flexi haircut")
    conv.assert_turn(intent="CREATE_APPOINTMENT", action="SEARCH_AVAILABILITY")
    assert _presentation_page_index(conv.session()) == 0
    assert not booking_client.create_booking.called


@live_luma
def test_pagination_resets_on_date_change(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    _reach_july_9_availability(conv)
    conv.send("show more")
    assert _presentation_page_index(conv.session()) == 1

    conv.send("actually July 11")
    conv.assert_turn(
        intent="CREATE_APPOINTMENT",
        action="SEARCH_AVAILABILITY",
        date_proposal_start="2026-07-11",
    )
    assert _presentation_page_index(conv.session()) == 0
    assert not booking_client.create_booking.called


@live_luma
def test_time_on_page_two_binds_not_page_one_slot(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    conv.send("show more")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)

    conv.send("9am")
    conv.assert_turn(intent="CREATE_APPOINTMENT")
    assert conv.outcome.get("status") != "AWAITING_CONFIRMATION"
    assert not booking_client.create_booking.called

    searches_before_5pm = availability_client.get_service_availability.call_count
    full_cache = _cached_slot_count(conv.session() or {})
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
    assert _cached_slot_count(sess) == full_cache
    conv.assert_slot_contains("time", "17", in_session=True)


@live_luma
def test_off_page_earlier_time_explains_previous(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    searches_before = availability_client.get_service_availability.call_count
    full_cache = _cached_slot_count(conv.session() or {})

    conv.send("next")
    second_page = extract_presented_times(conv.last_body, conv.session())
    assert_different_availability_page(first_page, second_page, turn=conv.turn)

    conv.send("9am")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert conv.outcome.get("status") != "AWAITING_CONFIRMATION"
    sess = conv.session() or {}
    assert not (sess.get("slots") or {}).get("time")
    assert _cached_slot_count(sess) == full_cache
    resolution = (
        sess.get("time_resolution")
        or conv.outcome.get("time_resolution")
        or (conv.outcome.get("facts") or {}).get("time_resolution")
        or (conv.plan or {}).get("time_resolution")
        or {}
    )
    assert resolution.get("mismatch_location") == "EARLIER_PAGE"
    text = _response_text(conv.last_body).lower()
    assert "earlier page" in text
    assert "`previous`" in text
    assert "`next`" not in text


@live_luma
def test_off_page_later_time_explains_next(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    first_page = _reach_july_9_availability(conv)
    searches_before = availability_client.get_service_availability.call_count
    full_cache = _cached_slot_count(conv.session() or {})

    conv.send("next")
    assert_different_availability_page(
        first_page,
        extract_presented_times(conv.last_body, conv.session()),
        turn=conv.turn,
    )
    conv.send("previous")
    assert extract_presented_times(conv.last_body, conv.session()) == first_page
    assert _presentation_page_index(conv.session()) == 0

    conv.send("5pm")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert conv.outcome.get("status") != "AWAITING_CONFIRMATION"
    sess = conv.session() or {}
    assert not (sess.get("slots") or {}).get("time")
    assert _cached_slot_count(sess) == full_cache
    resolution = (
        sess.get("time_resolution")
        or conv.outcome.get("time_resolution")
        or (conv.outcome.get("facts") or {}).get("time_resolution")
        or (conv.plan or {}).get("time_resolution")
        or {}
    )
    assert resolution.get("mismatch_location") == "LATER_PAGE"
    text = _response_text(conv.last_body).lower()
    assert "later page" in text
    assert "`next`" in text
    assert "`previous`" not in text


@live_luma
def test_time_absent_from_cache_explains_unavailable(paginated_booking_conversation):
    conv, booking_client, availability_client = paginated_booking_conversation
    _reach_july_9_availability(conv)
    searches_before = availability_client.get_service_availability.call_count
    full_cache = _cached_slot_count(conv.session() or {})

    conv.send("8pm")
    assert_no_booking_execution(conv, booking_client)
    assert availability_client.get_service_availability.call_count == searches_before
    assert conv.outcome.get("status") != "AWAITING_CONFIRMATION"
    sess = conv.session() or {}
    assert not (sess.get("slots") or {}).get("time")
    assert _cached_slot_count(sess) == full_cache
    resolution = (
        sess.get("time_resolution")
        or conv.outcome.get("time_resolution")
        or (conv.outcome.get("facts") or {}).get("time_resolution")
        or (conv.plan or {}).get("time_resolution")
        or {}
    )
    assert resolution.get("mismatch_location") == "NOT_IN_CACHE"
    text = _response_text(conv.last_body).lower()
    assert "isn't available" in text
    assert "another date" in text
    assert "earlier page" not in text
    assert "later page" not in text
    assert "`next`" not in text
    assert "`previous`" not in text
