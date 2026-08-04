"""Declarative booking conversation scenarios (real Luma + scripted time resolution)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from core.session.session_manager import get_session, save_session
from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FROZEN_TIME,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _resolve_search_date,
    _response_text,
)

TARGET_DATE = _resolve_search_date(None)
# Relative "tomorrow" against the shared E2E clock (not TARGET_DATE = frozen+2).
_TOMORROW = (FROZEN_TIME + timedelta(days=1)).strftime("%Y-%m-%d")
SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _assert_booking_created(conv, booking_client, _availability=None) -> None:
    assert booking_client.create_booking.called, (
        f"turn {conv.turn}: expected create_booking after confirmation"
    )
    assert booking_client.create_booking.call_count == 1, (
        f"turn {conv.turn}: expected exactly one CONFIRM_APPOINTMENT/"
        f"create_booking, got {booking_client.create_booking.call_count}"
    )
    call = booking_client.create_booking.call_args
    kwargs = call.kwargs if call else {}
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    session_customer_id = sess.get("customer_id")
    assert payload_customer_id and int(payload_customer_id) > 0, (
        f"turn {conv.turn}: booking payload must use resolved commerce "
        f"customer_id, got {payload_customer_id!r}"
    )
    assert session_customer_id == payload_customer_id, (
        f"turn {conv.turn}: session customer_id {session_customer_id!r} "
        f"must match booking payload {payload_customer_id!r}"
    )
    assert str(payload_customer_id) != str(conv.user_id), (
        f"turn {conv.turn}: customer_id must not be chat user_id "
        f"({conv.user_id!r})"
    )
    slots = sess.get("slots") or {}
    booking = sess.get("booking") or {}
    booking_id = booking.get("booking_id") or slots.get("booking_id")
    booking_code = booking.get("booking_code") or slots.get("booking_code")
    assert booking_id, (
        f"turn {conv.turn}: expected booking_id in session "
        f"(booking={booking!r}, slots_keys={list(slots.keys())})"
    )
    assert booking_code, (
        f"turn {conv.turn}: expected booking_code in session "
        f"(booking={booking!r}, slots_keys={list(slots.keys())})"
    )


def _assert_no_booking(conv, booking_client, _availability=None) -> None:
    assert not booking_client.create_booking.called, (
        f"turn {conv.turn}: booking should not have been created"
    )


def _assert_no_booking_and_date_kept(conv, booking_client, _availability=None) -> None:
    _assert_no_booking(conv, booking_client)
    sess = conv.session() or {}
    assert (sess.get("slots") or {}).get("date"), (
        f"turn {conv.turn}: expected date retained after rejection"
    )


# Per-scenario scratch pads (runner is single-threaded / sequential per test).
_SEARCH_STATE: Dict[str, Any] = {}


def _capture_searches(_conv, _booking, availability) -> None:
    _SEARCH_STATE["count"] = availability.get_service_availability.call_count


def _assert_no_extra_search(conv, booking, availability) -> None:
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count == _SEARCH_STATE.get(
        "count")
    last = (conv.session() or {}).get("last_execution_result") or {}
    assert last.get("type") == "availability"
    assert len(last.get("slots") or []) >= 1
    missing = (conv.session() or {}).get("missing_slots") or []
    assert "service_id" not in missing


def _assert_unavailable_time_mismatch(conv, booking, availability) -> None:
    """12pm did not bind against presented offers; post-bind mismatch ran instead."""
    _assert_no_extra_search(conv, booking, availability)

    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("time") in (None, ""), (
        f"turn {conv.turn}: try_bind must not persist time for unavailable 12pm, "
        f"got {slots.get('time')!r}"
    )
    assert slots.get("date") in (None, ""), (
        f"turn {conv.turn}: date must not bind on mismatch, got {slots.get('date')!r}"
    )

    time_match = (
        conv.plan.get("time_match_outcome")
        or conv.outcome.get("time_match_outcome")
        or (conv.outcome.get("facts") or {}).get("time_match_outcome")
        or (sess.get("time_match_outcome"))
    )
    assert time_match == TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: expected TIME_MATCH_MISMATCH, got {time_match!r}"
    )

    time_resolution = (
        sess.get("time_resolution")
        or conv.outcome.get("time_resolution")
        or (conv.outcome.get("facts") or {}).get("time_resolution")
    )
    assert isinstance(time_resolution, dict), (
        f"turn {conv.turn}: expected time_resolution from apply_post_bind_time_resolution, "
        f"got {time_resolution!r}"
    )
    assert time_resolution.get("outcome") == TIME_MATCH_MISMATCH
    alternatives = time_resolution.get("alternatives")
    assert isinstance(alternatives, list) and len(alternatives) >= 1, (
        f"turn {conv.turn}: expected mismatch alternatives, got {alternatives!r}"
    )

    text = conv.last_body.get("text")
    assert isinstance(text, str) and text.strip(), (
        f"turn {conv.turn}: expected conversational mismatch text, got {text!r}"
    )


def _clear_sticky_temporal_facts(conv, _booking=None, _availability=None) -> None:
    """Drop prior-turn time facts so revision is not immediately re-bound.

    Scripted bind turns persist ``facts.times`` / ``time_proposal``. Without
    clearing them, a service/date revision rebinds the old exact time from
    sticky session proposals and re-enters confirmation — masking invalidation.
    """
    sess = conv.session()
    if not isinstance(sess, dict):
        return
    sess = dict(sess)
    facts = sess.get("facts")
    if isinstance(facts, dict):
        facts = dict(facts)
        facts.pop("times", None)
        facts.pop("time_proposal", None)
        facts.pop("time_constraint", None)
        sess["facts"] = facts
    sess.pop("time_proposal", None)
    sess.pop("time_constraint", None)
    save_session(conv.organization_id, conv.user_id, sess)


def _assert_service_revision(conv, booking, availability) -> None:
    assert availability.get_service_availability.call_count > _SEARCH_STATE.get(
        "count", 0)
    conv.assert_execution(has_availability_slots=True)
    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("time") in (None, ""), (
        f"expected prior time discarded, got {slots.get('time')!r}"
    )
    assert slots.get("date") in (None, ""), (
        f"expected prior date discarded on service revision, got {slots.get('date')!r}"
    )
    # New SEARCH_AVAILABILITY may write fresh presented/fingerprint; bound
    # datetime from the prior confirmation must not survive.
    assert not sess.get("resolved_datetime_range"), (
        f"expected resolved_datetime_range cleared, "
        f"got {sess.get('resolved_datetime_range')!r}"
    )
    _assert_no_booking(conv, booking)


def _assert_date_revision(_conv, booking, availability) -> None:
    assert availability.get_service_availability.call_count > _SEARCH_STATE.get(
        "count", 0)
    _assert_no_booking(_conv, booking)


def _assert_booking_called(conv, booking_client, _availability=None) -> None:
    assert booking_client.create_booking.called, (
        f"turn {conv.turn}: expected booking after yes"
    )
    call = booking_client.create_booking.call_args
    kwargs = call.kwargs if call else {}
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    assert payload_customer_id and int(payload_customer_id) > 0, (
        f"turn {conv.turn}: booking payload must use resolved customer_id, "
        f"got {payload_customer_id!r}"
    )
    assert sess.get("customer_id") == payload_customer_id, (
        f"turn {conv.turn}: session/payload customer_id mismatch "
        f"{sess.get('customer_id')!r} vs {payload_customer_id!r}"
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

_register(
    Scenario(
        "Happy path create appointment",
        Turn(
            "book me a haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                missing_slots=["service_id", "date", "time"],
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                execution="availability",
                has_availability_slots=True,
                missing_slots=["date", "time"],
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation="pending",
                slot_contains={"time": "10"},
                missing_slots=[],
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                slot_contains={"time": "10"},
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_assert_booking_created,
        ),
        tags=["booking", "happy-path"],
        requires_customer_identity=True,
    )
)


# ---------------------------------------------------------------------------
# Reject then revise time
# ---------------------------------------------------------------------------

_register(
    Scenario(
        "Reject then revise time",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
        ),
        Turn(
            "no",
            Expect(
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_assert_no_booking_and_date_kept,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "11"},
            ),
        ),
        Turn(
            "yes",
            Expect(action="CONFIRM_APPOINTMENT", slot_contains={"time": "11"}),
            after=_assert_booking_called,
        ),
        tags=["booking", "revise"],
        requires_customer_identity=True,
        id="reject-then-revise-time",
    )
)


# ---------------------------------------------------------------------------
# Unavailable time → TIME_MATCH_MISMATCH
# ---------------------------------------------------------------------------

_register(
    Scenario(
        "Unavailable time keeps booking flow",
        Turn(
            "book me a haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
            after=_capture_searches,
        ),
        Turn(
            "12pm",
            Expect(
                intent="CREATE_APPOINTMENT",
                time_match="TIME_MATCH_MISMATCH",
                planner="NEEDS_CLARIFICATION",
                action=None,
                awaiting="TIME_SELECTION",
                response_text_present=True,
                session_slots={"service_id": PREMIUM_SERVICE},
                time_proposal="12",
            ),
            after=_assert_unavailable_time_mismatch,
        ),
        fixture="scripted_unavailable_time",
        tags=["booking", "time-mismatch"],
    )
)


# ---------------------------------------------------------------------------
# Service revision invalidates availability
# ---------------------------------------------------------------------------

_SEARCH_FLEXI_STATE: Dict[str, Any] = {}


def _capture_searches_before_flexi(_conv, _booking, availability) -> None:
    _SEARCH_FLEXI_STATE["count"] = availability.get_service_availability.call_count


def _assert_availability_searched_flexi(conv, booking, availability) -> None:
    """Bug 2: AvailabilityClient must search Flexi, not the prior Premium session service."""
    baseline = _SEARCH_FLEXI_STATE.get("count", 0)
    assert availability.get_service_availability.call_count == baseline + 1, (
        f"turn {conv.turn}: expected exactly one SEARCH_AVAILABILITY for flexi, "
        f"got {availability.get_service_availability.call_count - baseline}"
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = kwargs.get("service_id")
    assert searched == FLEXI_SERVICE, (
        f"turn {conv.turn}: AvailabilityClient must receive Flexi service_id, "
        f"got {searched!r} (Premium overwrite is Bug 2)"
    )
    assert searched != PREMIUM_SERVICE
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    effective = planning_slots.get("service_id") or slots.get("service_id")
    assert effective == FLEXI_SERVICE, (
        f"turn {conv.turn}: session service_id expected Flexi, got {effective!r}"
    )
    assert not booking.create_booking.called


_register(
    Scenario(
        "Availability service revision searches Flexi not Premium",
        Turn("book haircut"),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
            ),
            after=_capture_searches_before_flexi,
        ),
        Turn(
            "show availability for flexi",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                availability_invalidated=True,
            ),
            after=_assert_availability_searched_flexi,
        ),
        fixture="scripted_availability_service_revision",
        tags=["booking", "availability", "service-revision", "bug2"],
        id="availability-service-revision-flexi",
    )
)


_register(
    Scenario(
        "Service revision invalidates availability",
        Turn("book haircut"),
        Turn("premium"),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_searches,
        ),
        Turn(
            "rather book flexi haircut",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_service_revision,
        ),
        fixture="scripted_service_revision",
        tags=["booking", "invalidation"],
    )
)


# ---------------------------------------------------------------------------
# Date revision invalidates availability
# ---------------------------------------------------------------------------

_register(
    Scenario(
        "Date revision invalidates availability",
        Turn("book haircut"),
        Turn("premium"),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_searches,
        ),
        Turn(
            "actually July 11",
            Expect(
                intent="CREATE_APPOINTMENT",
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                execution="availability",
                has_availability_slots=True,
                date_proposal="2026-07-11",
                slot_absent=["date", "time"],
                availability_invalidated=True,
            ),
            before=_clear_sticky_temporal_facts,
            after=_assert_date_revision,
        ),
        fixture="scripted_date_revision",
        tags=["booking", "invalidation"],
    )
)


# ---------------------------------------------------------------------------
# Scripted time-resolution scenarios
# ---------------------------------------------------------------------------

_TIME_STATE: Dict[str, Any] = {}


def _assert_no_search_yet(_conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 0


def _assert_exact_search_side_effects(conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 1
    call = availability.get_service_availability.call_args
    assert call.kwargs.get("date") == TARGET_DATE
    conv.assert_availability_search_without_time_constraint(availability)
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert sess.get("resolved_datetime_range")
    last = sess.get("last_execution_result") or {}
    assert last.get("type") == "availability"


def _assert_exact_search_recorded_tomorrow(conv, booking, availability) -> None:
    """Exact-match side effects with search date = shared-clock 'tomorrow'."""
    assert availability.get_service_availability.call_count == 1
    call = availability.get_service_availability.call_args
    assert call.kwargs.get("date") == _TOMORROW, (
        f"expected shared-clock tomorrow {_TOMORROW!r}, "
        f"got {call.kwargs.get('date')!r}"
    )
    conv.assert_availability_search_without_time_constraint(availability)
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert sess.get("resolved_datetime_range")
    last = sess.get("last_execution_result") or {}
    assert last.get("type") == "availability"


def _assert_no_booking_single_search(conv, booking, availability) -> None:
    assert not booking.create_booking.called
    assert availability.get_service_availability.call_count == 1


def _capture_search_count(_c, _b, availability) -> None:
    _TIME_STATE["searches"] = availability.get_service_availability.call_count


def _assert_mismatch_side_effects(conv, booking, availability) -> None:
    assert availability.get_service_availability.call_count == _TIME_STATE.get(
        "searches", 0) + 1
    assert not booking.create_booking.called
    last = (conv.session() or {}).get("last_execution_result") or {}
    assert last.get("type") == "availability"
    assert conv.plan.get("status") != "READY" or conv.plan.get(
        "action") is not None


def _assert_empty_slots(conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 1
    last = (conv.session() or {}).get("last_execution_result") or {}
    assert last.get("slots") == []


def _assert_proposals_persisted(conv, _booking, availability) -> None:
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert isinstance(sess.get("last_execution_result"), dict)
    assert availability.get_service_availability.call_count == 1


_register(
    Scenario(
        "Tomorrow by 9am then premium exact match",
        Turn(
            "book haircut tomorrow by 9am",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id"],
                date_proposal=_TOMORROW,
                time_proposal="09",
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=True,
                time_match=TIME_MATCH_EXACT,
                planner="AWAITING_CONFIRMATION",
                action=None,
                awaiting="USER_CONFIRMATION",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
                slot_contains={"time": "09"},
            ),
            after=_assert_exact_search_recorded_tomorrow,
        ),
        fixture="scripted",
        tags=["time-resolution", "exact"],
        id="tomorrow-by-9am-premium-exact",
    )
)


_register(
    Scenario(
        "Tomorrow by 12pm premium then confirm",
        Turn(
            "book me haircut tomorrow by 12pm",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id"],
                date_proposal=_TOMORROW,
                time_proposal="12",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=True,
                time_match=TIME_MATCH_EXACT,
                planner="AWAITING_CONFIRMATION",
                action=None,
                awaiting="USER_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
                slot_contains={"time": "12"},
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "12"},
            ),
            after=_assert_booking_created,
        ),
        fixture="scripted_confirm",
        tags=["time-resolution", "exact", "confirm"],
        id="tomorrow-by-12pm-premium-yes",
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Book haircut premium 10am then confirm",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                missing_slots=[],
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                missing_slots=[],
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_booking_created,
        ),
        fixture="booking",
        tags=["booking", "happy-path", "confirm"],
        id="book-haircut-premium-10am-yes",
        requires_customer_identity=True,
    )
)


_register(
    Scenario(
        "Time match exact after service selection",
        Turn(
            "book haircut tomorrow at 10am",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                time_match=TIME_MATCH_EXACT,
                planner="AWAITING_CONFIRMATION",
                action=None,
                awaiting="USER_CONFIRMATION",
                response_text_present=True,
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted",
        tags=["time-resolution", "exact"],
        id="time-match-exact-same-turn",
    )
)


_register(
    Scenario(
        "Time match mismatch conversational response",
        Turn(
            "book haircut tomorrow at 9:15am",
            Expect(response_status="NEEDS_CLARIFICATION"),
            after=_capture_search_count,
        ),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                time_match=TIME_MATCH_MISMATCH,
                planner="NEEDS_CLARIFICATION",
                action=None,
                awaiting="TIME_SELECTION",
                response_text_present=True,
                has_availability_slots=True,
                time_proposal="09:15",
            ),
            after=_assert_mismatch_side_effects,
        ),
        fixture="scripted_mismatch",
        tags=["time-resolution", "mismatch"],
        id="time-match-mismatch-conversational",
    )
)


_register(
    Scenario(
        "Empty availability search records no slots",
        Turn("book haircut tomorrow by 9am"),
        Turn(
            "premium",
            Expect(
                response_status="succeeded",
                execution="availability",
                has_availability_slots=False,
                time_match=TIME_MATCH_MISMATCH,
                planner="NEEDS_CLARIFICATION",
                action=None,
                time_proposal="09",
            ),
            after=_assert_empty_slots,
        ),
        fixture="scripted_empty",
        tags=["time-resolution", "empty"],
        id="empty-availability-no-slots",
    )
)


_register(
    Scenario(
        "Mismatch then user picks alternative",
        Turn("book haircut tomorrow at 9:15am"),
        Turn(
            "premium",
            Expect(time_match=TIME_MATCH_MISMATCH),
        ),
        Turn(
            "9:30am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                slot_contains={"time": "09:30"},
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted_mismatch_pick",
        tags=["time-resolution", "mismatch", "bind"],
        id="mismatch-then-pick-alternative",
    )
)


_register(
    Scenario(
        "Time resolution persists proposals on mismatch",
        Turn("book haircut tomorrow at 9:15am"),
        Turn(
            "premium",
            Expect(time_match=TIME_MATCH_MISMATCH),
            after=_assert_proposals_persisted,
        ),
        fixture="scripted_mismatch",
        tags=["time-resolution", "persistence"],
        id="time-resolution-persists-across-turns",
    )
)


# ---------------------------------------------------------------------------
# Post-availability time selection regressions
# ---------------------------------------------------------------------------
# Post-availability time selection (RecordingLumaClient /resolve replay)
# ---------------------------------------------------------------------------
#
# Shared start: book premium → availability presented (includes 1:30 PM).
# NLU bodies come from recorded production /resolve — not handwritten scripts.
#
_POST_AVAIL_SEARCH: Dict[str, Any] = {}
_POST_AVAIL_BASELINE: Dict[str, Any] = {}
_DOTTED_TIME_BIND: Dict[str, Any] = {}


def _capture_post_availability_baseline(conv, _booking, availability) -> None:
    """After SEARCH_AVAILABILITY: remember search count, service, date, offers."""
    _POST_AVAIL_SEARCH["count"] = availability.get_service_availability.call_count
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    _POST_AVAIL_BASELINE["service_id"] = (
        planning_slots.get("service_id") or slots.get("service_id")
    )
    _POST_AVAIL_BASELINE["date"] = planning_slots.get("date") or slots.get("date")
    if not _POST_AVAIL_BASELINE["date"]:
        proposal = sess.get("date_proposal") or planning.get("date_proposal")
        if isinstance(proposal, dict):
            _POST_AVAIL_BASELINE["date"] = proposal.get("start") or proposal.get("value")
        elif proposal:
            _POST_AVAIL_BASELINE["date"] = proposal
    presented = sess.get("presented_availability")
    times: List[str] = []
    if isinstance(presented, dict):
        raw_times = presented.get("times") or []
        if isinstance(raw_times, list):
            times = [str(t) for t in raw_times if t]
        if not times:
            for slot in presented.get("slots") or []:
                if isinstance(slot, dict):
                    start = slot.get("starts_at") or slot.get("start")
                    if isinstance(start, str) and "T" in start:
                        times.append(start.split("T", 1)[1][:5])
    _POST_AVAIL_BASELINE["presented_times"] = times
    text = _response_text(conv.last_body or {})
    _POST_AVAIL_BASELINE["availability_text"] = text


def _assert_no_extra_availability_search(conv, availability) -> None:
    baseline = _POST_AVAIL_SEARCH.get("count", 1)
    assert availability.get_service_availability.call_count == baseline, (
        f"turn {conv.turn}: must not trigger another availability search "
        f"(baseline={baseline}, "
        f"got={availability.get_service_availability.call_count})"
    )


def _assert_booking_context_preserved(conv) -> None:
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    service_id = planning_slots.get("service_id") or slots.get("service_id")
    date_value = planning_slots.get("date") or slots.get("date")
    if not date_value:
        proposal = sess.get("date_proposal") or planning.get("date_proposal")
        if isinstance(proposal, dict):
            date_value = proposal.get("start") or proposal.get("value")
        elif proposal:
            date_value = proposal

    expected_service = _POST_AVAIL_BASELINE.get("service_id") or PREMIUM_SERVICE
    assert service_id == expected_service, (
        f"turn {conv.turn}: service_id must remain {expected_service!r}, "
        f"got {service_id!r}"
    )
    expected_date = _POST_AVAIL_BASELINE.get("date")
    if expected_date:
        assert date_value and str(expected_date)[:10] in str(date_value), (
            f"turn {conv.turn}: date must remain {expected_date!r}, got {date_value!r}"
        )
    else:
        assert date_value, (
            f"turn {conv.turn}: date must remain selected, got {date_value!r}"
        )


def _assert_invalid_time_explains_and_reshows(conv, booking, availability) -> None:
    """xxxxx after availability — clarify without redundant SEARCH."""
    _assert_production_xxxxx_after_availability(conv, booking, availability)
    _assert_no_extra_availability_search(conv, availability)


def _turn_understanding(conv) -> Any:
    for source in (conv.outcome or {}, conv.plan or {}, conv.last_body or {}):
        if not isinstance(source, dict):
            continue
        turn = source.get("turn")
        if isinstance(turn, dict) and turn.get("understanding"):
            return turn.get("understanding")
        nested = source.get("plan")
        if isinstance(nested, dict):
            turn = nested.get("turn")
            if isinstance(turn, dict) and turn.get("understanding"):
                return turn.get("understanding")
        outcome = source.get("outcome")
        if isinstance(outcome, dict):
            turn = outcome.get("turn")
            if isinstance(turn, dict) and turn.get("understanding"):
                return turn.get("understanding")
    return None


def _assert_production_xxxxx_after_availability(conv, booking, availability) -> None:
    """Unrecognized / unusable time after offers → recovery presentation, no SEARCH."""
    _assert_no_booking(conv, booking)
    _assert_booking_context_preserved(conv)

    understanding = _turn_understanding(conv)
    assert understanding == "UNRECOGNIZED_INPUT", (
        f"turn {conv.turn}: expected turn.understanding=UNRECOGNIZED_INPUT "
        f"from recorded /resolve, got {understanding!r}"
    )

    sess = conv.session() or {}
    confirmation = _confirmation_state(sess)
    assert confirmation in (None, "", False), (
        f"turn {conv.turn}: must not enter confirmation, got {confirmation!r}"
    )

    plan = conv.plan or {}
    outcome = conv.outcome or {}
    action = plan.get("action") if "action" in plan else outcome.get("action")
    assert action in (None, "", False), (
        f"turn {conv.turn}: cached offers are authoritative — must not SEARCH, "
        f"got action={action!r}"
    )
    status = plan.get("status") or outcome.get("status")
    assert status == "READY", (
        f"turn {conv.turn}: expected READY recovery presentation for unrecognized "
        f"reply, got status={status!r}"
    )
    # Observable recovery vs reshow discriminator (action_branch not on HTTP plan).
    assert plan.get("availability_reshow") not in (True,), (
        f"turn {conv.turn}: recovery presentation must not set availability_reshow, "
        f"got plan.availability_reshow={plan.get('availability_reshow')!r}"
    )
    missing = (
        plan.get("missing_slots")
        or outcome.get("missing_slots")
        or sess.get("missing_slots")
        or []
    )
    assert "time" in (missing if isinstance(missing, list) else []), (
        f"turn {conv.turn}: expected missing time, got {missing!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    assert text.strip(), (
        f"turn {conv.turn}: expected recovery text, got {text!r}"
    )
    assert (
        "understand" in lowered
        or "didn't" in lowered
        or "did not" in lowered
        or "catch" in lowered
    ), (
        f"turn {conv.turn}: must acknowledge unrecognized input, got {text!r}"
    )
    ask_ok = (
        "time" in lowered
        or "available" in lowered
        or "which" in lowered
        or "choose" in lowered
        or "continue" in lowered
    )
    assert ask_ok, (
        f"turn {conv.turn}: expected guidance back to time selection, got {text!r}"
    )
    # Must not be a bare availability reshow that skips acknowledgment.
    # Repeating offered times after the acknowledgement is allowed.
    assert not (
        lowered.strip().startswith("here are the available times")
        and "understand" not in lowered
        and "didn't" not in lowered
        and "did not" not in lowered
        and "catch" not in lowered
    ), (
        f"turn {conv.turn}: availability reshow must not suppress recovery, got {text!r}"
    )
    sess_presented = sess.get("presented_availability")
    assert isinstance(sess_presented, dict) and (
        sess_presented.get("times") or sess_presented.get("slots")
    ), (
        f"turn {conv.turn}: presented_availability must remain without re-search"
    )


_MISMATCH_UNAVAILABLE_PHRASES = (
    "isn't available",
    "is not available",
    "not available",
    "that time isn't",
    "that time is not",
    "requested time",
)


def _time_match_from_conv(conv) -> Any:
    sess = conv.session() or {}
    return (
        conv.plan.get("time_match_outcome")
        or conv.outcome.get("time_match_outcome")
        or (conv.outcome.get("facts") or {}).get("time_match_outcome")
        or sess.get("time_match_outcome")
    )


def _assert_malformed_clock_not_mismatch(conv, booking, availability) -> None:
    """5.xyz after availability: clarify unusable input, not TIME_MATCH_MISMATCH.

    Recorded /resolve marks UNRECOGNIZED_INPUT with no clock facts. Cached offers
    remain authoritative — planner clarifies rather than re-SEARCH or mismatch.
    """
    _assert_production_xxxxx_after_availability(conv, booking, availability)
    _assert_no_extra_availability_search(conv, availability)
    time_match = _time_match_from_conv(conv)
    assert time_match != TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: malformed '5.xyz' must not be classified as "
        f"TIME_MATCH_MISMATCH (unavailable time), got {time_match!r}"
    )
    text = _response_text(conv.last_body or {}).lower()
    flat = text.replace("\u2019", "'").replace("\u2018", "'")
    for phrase in _MISMATCH_UNAVAILABLE_PHRASES:
        assert phrase not in flat, (
            f"turn {conv.turn}: must not use unavailable-time mismatch wording "
            f"{phrase!r}, got {_response_text(conv.last_body or {})!r}"
        )


def _assert_unavailable_5pm_mismatch_wording(conv, booking, availability) -> None:
    """5pm after afternoon offers: unavailable-time mismatch, not 'couldn't understand'."""
    _assert_no_booking(conv, booking)
    _assert_no_extra_availability_search(conv, availability)
    _assert_booking_context_preserved(conv)

    sess = conv.session() or {}
    confirmation = _confirmation_state(sess)
    assert confirmation in (None, "", False), (
        f"turn {conv.turn}: unavailable 5pm must not confirm, "
        f"got confirmation_state={confirmation!r}"
    )

    time_match = _time_match_from_conv(conv)
    assert time_match == TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: expected TIME_MATCH_MISMATCH for unavailable 5pm, "
        f"got {time_match!r}"
    )
    awaiting = (
        conv.plan.get("awaiting")
        or conv.outcome.get("awaiting")
        or sess.get("awaiting")
    )
    assert awaiting == "TIME_SELECTION", (
        f"turn {conv.turn}: expected awaiting TIME_SELECTION, got {awaiting!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    assert text.strip(), (
        f"turn {conv.turn}: expected mismatch clarification text, got {text!r}"
    )
    unavailable_ok = (
        "not available" in lowered
        or "isn't available" in lowered
        or "5:00" in text
        or "5pm" in lowered
        or "17:00" in text
    )
    assert unavailable_ok, (
        f"turn {conv.turn}: expected unavailable-time wording for 5pm, got {text!r}"
    )
    for phrase in (
        "couldn't understand",
        "could not understand",
        "didn't understand",
        "did not understand",
        "as a time",
        "as a valid time",
    ):
        assert phrase not in lowered, (
            f"turn {conv.turn}: unavailable 5pm must not use unparseable-time "
            f"wording {phrase!r}, got {text!r}"
        )


def _assert_dotted_time_bound(label: str):
    """Assert confirmation bind for a dotted clock form; prove parity across labels."""

    def _after(conv, booking, availability) -> None:
        _assert_no_booking(conv, booking)
        _assert_no_extra_availability_search(conv, availability)
        _assert_booking_context_preserved(conv)

        sess = conv.session() or {}
        slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
        planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
        planning_slots = (
            planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
        )
        time_value = planning_slots.get("time") or slots.get("time")
        confirmation = _confirmation_state(sess)

        assert time_value and "13:30" in str(time_value), (
            f"turn {conv.turn}: expected bound time 13:30 from presented 1:30 PM "
            f"for '{label}', got {time_value!r}"
        )
        assert confirmation == "pending", (
            f"turn {conv.turn}: expected confirmation pending after '{label}', "
            f"got {confirmation!r}"
        )
        assert conv.plan.get("status") == "AWAITING_CONFIRMATION", (
            f"turn {conv.turn}: expected AWAITING_CONFIRMATION after '{label}', "
            f"got {conv.plan.get('status')!r}"
        )
        assert conv.plan.get("action") is None, (
            f"turn {conv.turn}: no execution action expected after bind, "
            f"got {conv.plan.get('action')!r}"
        )
        awaiting = conv.plan.get("awaiting") or conv.outcome.get("awaiting")
        assert awaiting != "TIME_SELECTION", (
            f"turn {conv.turn}: dotted time must not remain in TIME_SELECTION "
            f"clarification, got awaiting={awaiting!r}"
        )

        snapshot = {
            "planner": conv.plan.get("status"),
            "awaiting": awaiting,
            "action": conv.plan.get("action"),
            "confirmation": confirmation,
            "service_id": planning_slots.get("service_id") or slots.get("service_id"),
            "date": str(
                planning_slots.get("date")
                or slots.get("date")
                or _POST_AVAIL_BASELINE.get("date")
                or ""
            ),
            "time": str(time_value),
            "search_count": availability.get_service_availability.call_count,
        }
        _DOTTED_TIME_BIND[label] = snapshot
        counterpart = "1.30pm" if label == "1.30" else "1.30"
        if counterpart in _DOTTED_TIME_BIND:
            assert _DOTTED_TIME_BIND[label] == _DOTTED_TIME_BIND[counterpart], (
                f"'1.30' and '1.30pm' must produce identical booking outcomes; "
                f"got {_DOTTED_TIME_BIND!r}"
            )

    return _after


_register(
    Scenario(
        "Invalid time input explains and re-shows availability",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "xxxxx",
            Expect(
                planner="READY",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                missing_slots=["time"],
                response_text_present=True,
            ),
            after=_assert_invalid_time_explains_and_reshows,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "invalid-time", "recording"],
        id="invalid-time-explains-and-reshows-availability",
    )
)


_register(
    Scenario(
        "Dotted time 1.30 binds presented 1:30 PM",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "1.30",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "13:30", "date": TARGET_DATE},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "dotted-time"],
        id="dotted-time-1-30-binds-presented-130pm",
    )
)


_register(
    Scenario(
        "Dotted time 1.30pm binds presented 1:30 PM",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "1.30pm",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "13:30", "date": TARGET_DATE},
                missing_slots=[],
            ),
            after=_assert_dotted_time_bound("1.30pm"),
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "dotted-time"],
        id="dotted-time-1-30pm-binds-presented-130pm",
    )
)


_register(
    Scenario(
        "Malformed clock 5.xyz is unparseable not unavailable",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "5.xyz",
            Expect(
                planner="READY",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                missing_slots=["time"],
                response_text_present=True,
            ),
            after=_assert_malformed_clock_not_mismatch,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "malformed-clock", "recovery"],
        id="malformed-clock-5-xyz-unparseable-not-unavailable",
    )
)


_register(
    Scenario(
        "Unavailable 5pm uses mismatch wording not unparseable",
        Turn(
            "book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "5pm",
            Expect(
                planner="NEEDS_CLARIFICATION",
                awaiting="TIME_SELECTION",
                action=None,
                confirmation=None,
                time_match=TIME_MATCH_MISMATCH,
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_unavailable_5pm_mismatch_wording,
        ),
        fixture="scripted_dotted_time_selection",
        tags=["booking", "time-selection", "time-mismatch"],
        id="unavailable-5pm-mismatch-not-unparseable",
    )
)


_JULY_24 = "2026-07-24"


def _assert_numeric_hour_binds_unique_offered_time(conv, booking, availability) -> None:
    """Bare \"9\" with a unique offered 9:00 binds that time and awaits confirmation."""
    _assert_no_booking(conv, booking)
    _assert_no_extra_availability_search(conv, availability)
    _assert_booking_context_preserved(conv)

    plan = conv.plan or {}
    outcome = conv.outcome or {}
    status = plan.get("status") or outcome.get("status")
    action = plan.get("action") if "action" in plan else outcome.get("action")

    assert status == "AWAITING_CONFIRMATION", (
        f"turn {conv.turn}: expected AWAITING_CONFIRMATION for unique bare \"9\", "
        f"got status={status!r}"
    )
    assert action is None, (
        f"turn {conv.turn}: confirmation turn must not execute, got action={action!r}"
    )

    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    time_value = planning_slots.get("time") or slots.get("time")
    assert time_value and "09:00" in str(time_value), (
        f"turn {conv.turn}: bare \"9\" must bind unique offered 09:00, got {time_value!r}"
    )
    confirmation = _confirmation_state(sess)
    assert confirmation == "pending", (
        f"turn {conv.turn}: expected confirmation pending after unique \"9\", "
        f"got {confirmation!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    assert text.strip(), (
        f"turn {conv.turn}: expected confirmation prompt, got {text!r}"
    )
    assert "book" in lowered or "confirm" in lowered or "go ahead" in lowered, (
        f"turn {conv.turn}: expected booking confirmation wording, got {text!r}"
    )


_register(
    Scenario(
        "Numeric hour selects unique offered time",
        Turn(
            "book me a premium haircut on July 24th",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                missing_slots=["time"],
                date_proposal=_JULY_24,
                confirmation=None,
                response_text_present=True,
            ),
            after=_capture_post_availability_baseline,
        ),
        Turn(
            "9",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                time_match=TIME_MATCH_EXACT,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09:00"},
                missing_slots=[],
                response_text_present=True,
            ),
            after=_assert_numeric_hour_binds_unique_offered_time,
        ),
        fixture="scripted_confirm",
        tags=["booking", "time-selection", "numeric-hour", "regression"],
        id="numeric-time-selection-requires-clarification",
    )
)


# ---------------------------------------------------------------------------
# Date requests after availability must SEARCH (not date-axis browse)
# ---------------------------------------------------------------------------

_JULY_23 = "2026-07-23"
_JULY_25 = "2026-07-25"
_DATE_AFTER_SEARCH_STATE: Dict[str, Any] = {}


def _presented_search_date(session: Dict[str, Any]) -> Any:
    presented = session.get("presented_availability")
    if isinstance(presented, dict) and presented.get("search_date"):
        return _resolve_search_date(str(presented.get("search_date")))
    cache = session.get("last_execution_result")
    if isinstance(cache, dict) and cache.get("search_date"):
        return _resolve_search_date(str(cache.get("search_date")))
    return None


def _assert_july23_availability_presented(conv, booking, availability) -> None:
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count >= 1
    _DATE_AFTER_SEARCH_STATE["search_count"] = (
        availability.get_service_availability.call_count
    )
    _DATE_AFTER_SEARCH_STATE["fingerprint"] = (conv.session() or {}).get(
        "availability_fingerprint"
    )
    sess = conv.session() or {}
    presented_date = _presented_search_date(sess)
    assert presented_date == _JULY_23, (
        f"turn {conv.turn}: expected July 23 availability window, "
        f"got presented.search_date={presented_date!r}"
    )


def _assert_july25_searches(conv, booking, availability) -> None:
    """Asking for July 25 after browsing/searching July 23 must SEARCH anew."""
    _assert_no_booking(conv, booking)
    baseline = _DATE_AFTER_SEARCH_STATE.get("search_count", 0)
    assert availability.get_service_availability.call_count > baseline, (
        f"turn {conv.turn}: July 25 must run SEARCH_AVAILABILITY "
        f"(baseline={baseline}, got {availability.get_service_availability.call_count})"
    )
    plan = conv.plan or {}
    # After execution, plan.action may already be consumed; prefer call evidence.
    sess = conv.session() or {}
    new_fp = sess.get("availability_fingerprint")
    prior_fp = _DATE_AFTER_SEARCH_STATE.get("fingerprint")
    assert new_fp and new_fp != prior_fp, (
        f"turn {conv.turn}: expected a new fingerprint for July 25, "
        f"prior={prior_fp!r} new={new_fp!r}"
    )
    presented_date = _presented_search_date(sess)
    assert presented_date == _JULY_25, (
        f"turn {conv.turn}: expected July 25 presentation, got {presented_date!r}"
    )
    _ = plan


_register(
    Scenario(
        "Date request after availability creates a new search",
        Turn(
            "Book me a premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_july23_availability_presented,
        ),
        Turn(
            "July 25",
            Expect(
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_july25_searches,
        ),
        fixture="scripted_multi_day_july23",
        tags=["booking", "availability", "search", "date-revision", "regression"],
        id="date-request-after-availability-searches",
    )
)
