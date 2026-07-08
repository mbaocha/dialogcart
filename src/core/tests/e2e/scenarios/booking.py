"""Declarative booking conversation scenarios (real Luma + scripted time resolution)."""

from __future__ import annotations

from typing import Any, Dict, List

from core.orchestration.session import get_session, save_session
from core.orchestration.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _resolve_search_date,
)

TARGET_DATE = _resolve_search_date(None)
SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _assert_booking_created(conv, booking_client, _availability=None) -> None:
    assert booking_client.create_booking.called, (
        f"turn {conv.turn}: expected create_booking after confirmation"
    )
    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("booking_id") or slots.get("booking_code"), (
        f"turn {conv.turn}: expected booking_id or booking_code in session slots"
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
    save_session(conv.user_id, sess)


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
                missing_slots=["date", "service_id", "time"],
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="success",
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
                response_status="success",
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
                response_status="success",
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
                response_status="success",
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
                date_proposal=TARGET_DATE,
                time_proposal="09",
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "premium",
            Expect(
                response_status="success",
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
            after=_assert_exact_search_side_effects,
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
                date_proposal=TARGET_DATE,
                time_proposal="12",
            ),
        ),
        Turn(
            "premium",
            Expect(
                response_status="success",
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
                response_status="success",
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
        fixture="scripted_confirm",
        tags=["booking", "happy-path", "confirm"],
        id="book-haircut-premium-10am-yes",
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
                response_status="success",
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
                response_status="success",
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
                response_status="success",
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
