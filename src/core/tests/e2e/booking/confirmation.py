"""Booking E2E scenarios — confirmation conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ References
# ✓ Revision
# ✓ Digressions
# ✓ Recovery
#
# TODO
#
# □ Invalid
# ============================================================

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FIRST_AVAILABLE_DATE,
    FROZEN_TIME,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _normalize_explicit_search_date,
    _plan_view,
    _presentation_page_index,
    _resolve_search_date,
    _response_indicates_no_more_times,
    _response_text,
    assert_no_booking_execution,
    attach_commit_customer_identity,
    extract_presented_times,
)
from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session, save_session
from core.tests.e2e.booking import _helpers as _booking_helpers
from core.workflows.availability.fingerprint import compute_availability_fingerprint

from datetime import datetime, timedelta

from core.tests.e2e.framework.confirmation_interruption import (
    assert_availability_rendered,
    assert_cleared_confirmation_binding,
    assert_exactly_one_search_since,
    assert_gate_action,
    assert_no_search_since,
    assert_not_confirmation_rendered,
    assert_planning_intent_preserved,
    assert_returns_to_pending_confirmation,
    assert_service_preserved,
    assert_turn_operation,
    attach_search_count,
    capture_pre_interruption_state,
)
from core.tests.e2e.framework.fixtures import TARGET_DATE

globals().update(
    {
        name: getattr(_booking_helpers, name)
        for name in getattr(_booking_helpers, "__all__", dir(_booking_helpers))
        if not name.startswith("__")
    }
)

SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario

# ============================================================
# VALID RESPONSES
# ============================================================
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
# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
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
# ============================================================
# REVISIONS
# ============================================================
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
# ============================================================
# DIGRESSIONS
# ============================================================
_INTERRUPTION_STATE: Dict[str, Any] = {}
_JULY_20 = "2026-07-20"
_JULY_21 = "2026-07-21"
_JULY_22 = "2026-07-22"
_JULY_23 = "2026-07-23"
_JULY_24 = "2026-07-24"
# Relative "tomorrow" against the e2e frozen clock (not TARGET_DATE = frozen+2).
_TOMORROW = (FROZEN_TIME + timedelta(days=1)).strftime("%Y-%m-%d")
_CONFIRMATION_FORBIDDEN_PHRASES = (
    "Would you like me to go ahead",
    "You're about to book",
    "Should I go ahead",
)




def _capture_post_bind_state(conv, _booking, availability) -> None:
    state = capture_pre_interruption_state(conv)
    attach_search_count(state, availability)
    _INTERRUPTION_STATE["pre"] = state


def _assert_superseding_availability_search(conv, booking, availability) -> None:
    pre = _INTERRUPTION_STATE.get("pre") or {}
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, pre.get("search_count", 0))
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    assert_no_booking_execution(conv, booking)


def _capture_pre_revision_search(conv, _booking, availability) -> None:
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )


def _assert_date_change_during_confirmation(conv, booking, availability) -> None:
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    # Rebind must not search again after this interruption search.
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )
    conv.assert_date_proposal(_JULY_21)
    conv.assert_slot_absent("time")
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    assert_no_booking_execution(conv, booking)


def _assert_service_change_during_confirmation(conv, booking, availability) -> None:
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, FLEXI_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )
    conv.assert_slot_absent("time")
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    assert_no_booking_execution(conv, booking)


def _assert_rebind_after_interruption(conv, booking, availability) -> None:
    assert_returns_to_pending_confirmation(conv)
    assert not booking.create_booking.called, (
        f"turn {conv.turn}: booking should not have been created"
    )
    pre = _INTERRUPTION_STATE.get("pre") or {}
    baseline = _INTERRUPTION_STATE.get("search_baseline")
    if baseline is not None:
        assert_no_search_since(conv, availability, baseline)
    elif pre.get("search_count") is not None:
        assert_no_search_since(conv, availability, pre["search_count"])


def _assert_no_confirmation_prompt_phrases(conv) -> None:
    text = str(conv.last_body.get("text") or "")
    for phrase in _CONFIRMATION_FORBIDDEN_PHRASES:
        conv._assert(
            phrase not in text,
            f"turn {conv.turn}: confirmation phrase {phrase!r} must not appear, got {text!r}",
        )


def _assert_last_search_date(conv, availability, expected_date: str) -> None:
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = _resolve_search_date(kwargs.get("date"))
    conv._assert(
        searched == expected_date,
        (
            f"turn {conv.turn}: expected availability search date {expected_date!r}, "
            f"got {searched!r}"
        ),
    )


def _assert_last_search_service(conv, availability, expected_service: str) -> None:
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = kwargs.get("service_id")
    conv._assert(
        searched == expected_service,
        (
            f"turn {conv.turn}: expected availability search service "
            f"{expected_service!r}, got {searched!r}"
        ),
    )

_register(
    Scenario(
        "Search availability when pending confirmation is superseded",
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
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                missing_slots=[],
            ),
            after=_capture_post_bind_state,
        ),
        Turn(
            "show me availability",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
                execution="availability",
                has_availability_slots=True,
            ),
            trace="1",
            after=_assert_superseding_availability_search,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "11"},
                time_match=TIME_MATCH_EXACT,
            ),
            after=_assert_rebind_after_interruption,
        ),
        fixture="scripted_confirmation_interruption",
        tags=["booking", "confirmation", "interruption", "reshow"],
        id="reshow-availability-during-confirmation",
    )
)


_register(
    Scenario(
        "Change date during pending confirmation",
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
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "show availability for 21st july",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_21,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_date_change_during_confirmation,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "11"},
            ),
            after=_assert_rebind_after_interruption,
        ),
        fixture="scripted_confirmation_interruption",
        tags=["booking", "confirmation", "interruption", "date-revision"],
        id="change-date-during-confirmation",
    )
)


_register(
    Scenario(
        "Switch service during pending confirmation",
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
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "show me availability for flexi haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_service_change_during_confirmation,
        ),
        Turn(
            "11am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "11"},
            ),
            after=_assert_rebind_after_interruption,
        ),
        fixture="scripted_confirmation_interruption",
        tags=["booking", "confirmation", "interruption", "service-revision"],
        id="switch-service-during-confirmation",
    )
)


def _assert_availability_supersedes_confirmation(conv, booking, availability) -> None:
    """Regression: bare AVAILABILITY consumes pending confirmation and re-searches."""
    pre = _INTERRUPTION_STATE.get("pre") or {}
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, pre.get("search_count", 0))
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


def _assert_availability_new_date_supersedes(conv, booking, availability) -> None:
    """Regression: dated AVAILABILITY searches the new day, not the prior cache."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )
    _assert_last_search_date(conv, availability, _JULY_21)
    conv._assert(
        _resolve_search_date(
            (availability.get_service_availability.call_args.kwargs or {}).get("date")
        )
        != TARGET_DATE,
        (
            f"turn {conv.turn}: must not reuse prior search date {TARGET_DATE!r} "
            f"after July 21 supersession"
        ),
    )
    conv.assert_date_proposal(_JULY_21)
    conv.assert_slot_absent("time")
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


def _assert_service_change_supersedes_confirmation(conv, booking, availability) -> None:
    """Regression: AVAILABILITY with a new service invalidates Premium confirmation."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, FLEXI_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _assert_last_search_service(conv, availability, FLEXI_SERVICE)
    call = availability.get_service_availability.call_args
    searched = (call.kwargs if call else {}).get("service_id")
    conv._assert(
        searched != PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: must not reuse Premium availability after Flexi "
            f"supersession, got {searched!r}"
        ),
    )
    conv.assert_slot_absent("time")
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


_register(
    Scenario(
        "Availability supersedes confirmation",
        Turn(
            "book me premium haircut",
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
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
                missing_slots=[],
            ),
            after=_capture_post_bind_state,
        ),
        Turn(
            "show availability",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
                execution="availability",
                has_availability_slots=True,
            ),
            trace="1",
            after=_assert_availability_supersedes_confirmation,
        ),
        fixture="scripted_availability_supersession",
        tags=["booking", "confirmation", "interruption", "availability-supersession"],
        id="availability-supersedes-confirmation",
    )
)


_register(
    Scenario(
        "Availability with new date",
        Turn(
            "book me premium haircut",
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
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "show availability for July 21",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_21,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_availability_new_date_supersedes,
        ),
        fixture="scripted_availability_supersession",
        tags=["booking", "confirmation", "interruption", "date-supersession"],
        id="availability-with-new-date",
    )
)


_register(
    Scenario(
        "Service change during confirmation",
        Turn(
            "book me premium haircut",
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
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "show availability for flexi haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_service_change_supersedes_confirmation,
        ),
        fixture="scripted_availability_supersession",
        tags=["booking", "confirmation", "interruption", "service-supersession"],
        id="service-change-during-confirmation",
    )
)


def _assert_confirmation_time_revision(conv, booking, availability) -> None:
    """Bug 1: CORRECTION time switch must re-enter confirmation with the new slot."""
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "CORRECTION")
    assert_planning_intent_preserved(conv)
    assert_returns_to_pending_confirmation(conv)
    sess = conv.session() or {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    slots = planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    if not slots.get("time"):
        slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    time_value = str(slots.get("time") or "")
    conv._assert(
        time_value.startswith("10"),
        f"turn {conv.turn}: expected selected time 10:00, got {time_value!r}",
    )
    outcome = conv.outcome or {}
    conv._assert(
        outcome.get("status") == "AWAITING_CONFIRMATION",
        (
            f"turn {conv.turn}: expected AWAITING_CONFIRMATION after time correction, "
            f"got {outcome.get('status')!r} (must not be planning-only READY)"
        ),
    )
    text = str(conv.last_body.get("text") or "")
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected confirmation prompt text, got empty response",
    )
    assert not booking.create_booking.called
    baseline = _INTERRUPTION_STATE.get("search_baseline")
    if baseline is not None:
        assert_no_search_since(conv, availability, baseline)


_register(
    Scenario(
        "Correction time revision during pending confirmation",
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
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
            ),
        ),
        Turn(
            "show availability for flexi",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "9:30",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "09:30"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "switch to 10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": FLEXI_SERVICE},
                slot_contains={"time": "10"},
                time_match=TIME_MATCH_EXACT,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_confirmation_time_revision,
        ),
        fixture="scripted_confirmation_time_revision",
        tags=["booking", "confirmation", "interruption", "time-revision", "bug1"],
        id="correction-time-revision-during-confirmation",
    )
)


def _planning_slots(conv) -> Dict[str, Any]:
    sess = conv.session() or {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    slots = planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    if slots:
        return dict(slots)
    return dict(sess.get("slots") or {}) if isinstance(sess.get("slots"), dict) else {}


def _assert_initial_search_uses_target_date(conv, _booking, availability) -> None:
    _assert_last_search_date(conv, availability, TARGET_DATE)
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )


def _assert_reconfirm_july_21_at_10am(conv, booking, availability) -> None:
    """After interruption, 10am must re-enter confirmation on July 21 only."""
    assert_returns_to_pending_confirmation(conv)
    slots = _planning_slots(conv)
    time_value = str(slots.get("time") or "")
    date_value = str(slots.get("date") or "").split("T")[0].split(" ")[0]
    conv._assert(
        time_value.startswith("10"),
        f"turn {conv.turn}: expected selected time 10:00, got {time_value!r}",
    )
    conv._assert(
        not time_value.startswith("09"),
        f"turn {conv.turn}: stale 09:00 selection must be discarded, got {time_value!r}",
    )
    conv._assert(
        date_value == _JULY_21,
        f"turn {conv.turn}: expected confirmation date {_JULY_21!r}, got {date_value!r}",
    )
    conv._assert(
        date_value != TARGET_DATE,
        f"turn {conv.turn}: stale date {TARGET_DATE!r} must not remain after supersession",
    )
    conv.assert_date_proposal(_JULY_21)
    text = str(conv.last_body.get("text") or "")
    conv._assert(
        "July 21" in text,
        f"turn {conv.turn}: confirmation must reference July 21, got {text!r}",
    )
    conv._assert(
        "10:00" in text,
        f"turn {conv.turn}: confirmation must reference 10:00, got {text!r}",
    )
    stale_date_label = (
        f"{datetime.strptime(TARGET_DATE, '%Y-%m-%d').strftime('%B')} "
        f"{int(TARGET_DATE.split('-')[2])}"
    )
    conv._assert(
        stale_date_label not in text,
        f"turn {conv.turn}: confirmation must not reference {stale_date_label!r}, got {text!r}",
    )
    conv._assert(
        "9:00" not in text and "09:00" not in text,
        f"turn {conv.turn}: confirmation must not reference stale 9:00, got {text!r}",
    )
    assert not booking.create_booking.called
    baseline = _INTERRUPTION_STATE.get("search_baseline")
    if baseline is not None:
        assert_no_search_since(conv, availability, baseline)


def _assert_booking_after_interruption(conv, booking, _availability) -> None:
    """CREATE_APPOINTMENT must commit Premium on July 21 at 10:00 only."""
    conv._assert(
        booking.create_booking.called,
        f"turn {conv.turn}: expected create_booking after yes",
    )
    conv._assert(
        booking.create_booking.call_count == 1,
        (
            f"turn {conv.turn}: expected exactly one create_booking, "
            f"got {booking.create_booking.call_count}"
        ),
    )
    call = booking.create_booking.call_args
    kwargs = call.kwargs if call else {}
    start_time = str(kwargs.get("start_time") or "")
    conv._assert(
        start_time.startswith(f"{_JULY_21}T10:00"),
        (
            f"turn {conv.turn}: expected booking start {_JULY_21}T10:00…, "
            f"got {start_time!r}"
        ),
    )
    conv._assert(
        TARGET_DATE not in start_time,
        f"turn {conv.turn}: booking must not use stale date {TARGET_DATE!r}, got {start_time!r}",
    )
    conv._assert(
        "T09:00" not in start_time,
        f"turn {conv.turn}: booking must not use stale 09:00, got {start_time!r}",
    )
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    conv._assert(
        payload_customer_id and int(payload_customer_id) > 0,
        (
            f"turn {conv.turn}: booking payload must use resolved customer_id, "
            f"got {payload_customer_id!r}"
        ),
    )
    conv._assert(
        sess.get("customer_id") == payload_customer_id,
        (
            f"turn {conv.turn}: session/payload customer_id mismatch "
            f"{sess.get('customer_id')!r} vs {payload_customer_id!r}"
        ),
    )
    slots = _planning_slots(conv)
    conv._assert(
        slots.get("service_id") == PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: expected Premium service on booking, "
            f"got {slots.get('service_id')!r}"
        ),
    )
    time_value = str(slots.get("time") or "")
    date_value = str(slots.get("date") or "").split("T")[0].split(" ")[0]
    conv._assert(
        time_value.startswith("10"),
        f"turn {conv.turn}: expected booked time 10:00, got {time_value!r}",
    )
    conv._assert(
        date_value == _JULY_21,
        f"turn {conv.turn}: expected booked date {_JULY_21!r}, got {date_value!r}",
    )
    sess = conv.session() or {}
    booking_state = sess.get("booking") if isinstance(sess.get("booking"), dict) else {}
    booking_id = booking_state.get("booking_id") or slots.get("booking_id")
    conv._assert(
        bool(booking_id),
        f"turn {conv.turn}: expected booking_id after successful commit, got {booking_id!r}",
    )


_register(
    Scenario(
        "Interruption → New Availability → New Booking",
        Turn(
            "book me premium haircut",
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
            after=_assert_initial_search_uses_target_date,
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
                missing_slots=[],
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "show availability for July 21",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_21,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_availability_new_date_supersedes,
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                date_proposal=_JULY_21,
                time_match=TIME_MATCH_EXACT,
                response_text_present=True,
            ),
            after=_assert_reconfirm_july_21_at_10am,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                missing_slots=[],
            ),
            after=_assert_booking_after_interruption,
        ),
        fixture="scripted_availability_supersession",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "date-supersession",
            "full-flow",
        ],
        id="interruption-new-availability-new-booking",
        requires_customer_identity=True,
    )
)


def _session_temporal_start_date(session: Dict[str, Any]) -> Any:
    temporal = session.get("temporal")
    if not isinstance(temporal, dict):
        planning = session.get("planning")
        if isinstance(planning, dict):
            temporal = planning.get("temporal")
    if isinstance(temporal, dict):
        return _resolve_search_date(str(temporal.get("start_date") or ""))
    return None


def _assert_july_23_availability_shown(conv, booking, availability) -> None:
    """Day-1 dated booking: SEARCH_AVAILABILITY for July 23, Temporal persisted."""
    assert_no_booking_execution(conv, booking)
    _assert_last_search_date(conv, availability, _JULY_23)
    conv.assert_date_proposal(_JULY_23)
    sess = conv.session() or {}
    start_date = _session_temporal_start_date(sess)
    conv._assert(
        start_date == _JULY_23,
        f"turn {conv.turn}: Temporal.start_date must be {_JULY_23}, got {start_date!r}",
    )
    from core.workflows.availability.presentation import presented_availability_from_session
    presented_payload = presented_availability_from_session(sess) or {}
    if isinstance(presented_payload, dict) and presented_payload.get("search_date"):
        conv._assert(
            _resolve_search_date(str(presented_payload.get("search_date"))) == _JULY_23,
            (
                f"turn {conv.turn}: presented search_date must be {_JULY_23}, "
                f"got {presented_payload.get('search_date')!r}"
            ),
        )
    _INTERRUPTION_STATE["july23_search_count"] = (
        availability.get_service_availability.call_count
    )


def _assert_july_23_confirmation_pending(conv, booking, availability) -> None:
    """9:30am binds against July 23 offers and enters confirmation."""
    assert not booking.create_booking.called, (
        f"turn {conv.turn}: booking must not create before confirmation"
    )
    conv.assert_date_proposal(_JULY_23)
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    date_value = planning_slots.get("date") or slots.get("date")
    conv._assert(
        _resolve_search_date(str(date_value or "")) == _JULY_23,
        (
            f"turn {conv.turn}: confirmation date slot must remain {_JULY_23}, "
            f"got {date_value!r}"
        ),
    )
    # Fieldwise merge: time-only turn must not erase Temporal.start_date.
    start_date = _session_temporal_start_date(sess)
    conv._assert(
        start_date == _JULY_23,
        (
            f"turn {conv.turn}: Temporal.start_date must survive time bind "
            f"({_JULY_23}), got {start_date!r}"
        ),
    )
    state = capture_pre_interruption_state(conv)
    attach_search_count(state, availability)
    _INTERRUPTION_STATE["pre"] = state
    _INTERRUPTION_STATE["search_baseline"] = (
        availability.get_service_availability.call_count
    )


def _assert_july_24_interrupts_confirmation(conv, booking, availability) -> None:
    """Reject+dated AVAILABILITY leaves confirmation and searches July 24 only."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _assert_last_search_date(conv, availability, _JULY_24)
    conv._assert(
        _resolve_search_date(
            (availability.get_service_availability.call_args.kwargs or {}).get("date")
        )
        != _JULY_23,
        (
            f"turn {conv.turn}: must not reuse prior July 23 search after "
            f"July 24 interruption"
        ),
    )
    conv.assert_date_proposal(_JULY_24)
    conv.assert_slot_absent("time")

    sess = conv.session() or {}
    start_date = _session_temporal_start_date(sess)
    conv._assert(
        start_date == _JULY_24,
        f"turn {conv.turn}: Temporal.start_date must be {_JULY_24}, got {start_date!r}",
    )

    from core.workflows.availability.presentation import presented_availability_from_session
    presented_payload = presented_availability_from_session(sess) or {}
    if not isinstance(presented_payload, dict):
        presented_payload = {}
    search_date = presented_payload.get("search_date")
    conv._assert(
        bool(search_date),
        f"turn {conv.turn}: expected presented_availability.search_date",
    )
    conv._assert(
        _resolve_search_date(str(search_date)) == _JULY_24,
        (
            f"turn {conv.turn}: presented search_date must be {_JULY_24}, "
            f"got {search_date!r}"
        ),
    )

    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(bool(presented), f"turn {conv.turn}: expected July 24 offers")
    for start in presented:
        if isinstance(start, str) and len(start) >= 10:
            conv._assert(
                start.startswith(_JULY_24),
                f"turn {conv.turn}: presented offer must be July 24, got {start!r}",
            )
            conv._assert(
                not start.startswith(_JULY_23),
                f"turn {conv.turn}: must not present July 23 cache, got {start!r}",
            )

    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


_register(
    Scenario(
        "Availability for July 24 interrupts confirmation",
        Turn(
            "book me premium haircut on 23rd july",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_23,
                confirmation=None,
            ),
            after=_assert_july_23_availability_shown,
        ),
        Turn(
            "9:30am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09:30"},
                date_proposal=_JULY_23,
                missing_slots=[],
            ),
            after=_assert_july_23_confirmation_pending,
        ),
        Turn(
            "no. search availability for 24th july",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_24,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_july_24_interrupts_confirmation,
        ),
        fixture="scripted_july_confirm_date_shift",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "date-supersession",
            "temporal",
            "regression",
        ],
        id="availability-july-24-interrupts-confirmation",
    )
)


def _assert_date_surfaces(
    conv,
    availability,
    expected_date: str,
    *,
    wrong_dates: List[str],
    require_search: bool,
    check_render: bool = True,
) -> None:
    """Assert Temporal, date_proposal, slots.date, search, and render agree."""
    conv.assert_date_proposal(expected_date)

    sess = conv.session() or {}
    start_date = _session_temporal_start_date(sess)
    conv._assert(
        start_date == expected_date,
        (
            f"turn {conv.turn}: Temporal.start_date must be {expected_date!r}, "
            f"got {start_date!r}"
        ),
    )

    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    durable_date = planning_slots.get("date") or slots.get("date")
    if durable_date:
        conv._assert(
            _resolve_search_date(str(durable_date)) == expected_date,
            (
                f"turn {conv.turn}: durable slots.date must be {expected_date!r}, "
                f"got {durable_date!r}"
            ),
        )
    for wrong in wrong_dates:
        if durable_date:
            conv._assert(
                _resolve_search_date(str(durable_date)) != wrong,
                (
                    f"turn {conv.turn}: durable slots.date must not revert to "
                    f"{wrong!r}, got {durable_date!r}"
                ),
            )

    if require_search:
        _assert_last_search_date(conv, availability, expected_date)
        for wrong in wrong_dates:
            searched = _resolve_search_date(
                (availability.get_service_availability.call_args.kwargs or {}).get(
                    "date"
                )
            )
            conv._assert(
                searched != wrong,
                (
                    f"turn {conv.turn}: availability search must not use "
                    f"{wrong!r}, got {searched!r}"
                ),
            )

        from core.workflows.availability.presentation import (
            presented_availability_from_session,
        )

        presented_payload = presented_availability_from_session(sess) or {}
        if not isinstance(presented_payload, dict):
            presented_payload = {}
        search_date = presented_payload.get("search_date")
        conv._assert(
            bool(search_date),
            f"turn {conv.turn}: expected presented_availability.search_date",
        )
        conv._assert(
            _resolve_search_date(str(search_date)) == expected_date,
            (
                f"turn {conv.turn}: presented search_date must be {expected_date!r}, "
                f"got {search_date!r}"
            ),
        )

        presented = extract_presented_times(conv.last_body, sess)
        conv._assert(
            bool(presented),
            f"turn {conv.turn}: expected presented offers for {expected_date}",
        )
        for start in presented:
            if isinstance(start, str) and len(start) >= 10:
                conv._assert(
                    start.startswith(expected_date),
                    (
                        f"turn {conv.turn}: presented offer must be {expected_date}, "
                        f"got {start!r}"
                    ),
                )
                for wrong in wrong_dates:
                    conv._assert(
                        not start.startswith(wrong),
                        (
                            f"turn {conv.turn}: presented offer must not be "
                            f"{wrong}, got {start!r}"
                        ),
                    )

    if not check_render:
        return

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    expected_phrase = {
        _JULY_20: "july 20",
        _JULY_21: "july 21",
        _JULY_22: "july 22",
        _TOMORROW: "july 2",
    }.get(expected_date, expected_date)
    conv._assert(
        expected_date in text or expected_phrase in lowered,
        (
            f"turn {conv.turn}: rendered response must show {expected_date}, "
            f"got {text!r}"
        ),
    )
    for wrong in wrong_dates:
        wrong_phrase = {
            _JULY_20: "july 20",
            _JULY_21: "july 21",
            _JULY_22: "july 22",
            TARGET_DATE: "july 3",
            _TOMORROW: "july 2",
        }.get(wrong, wrong)
        conv._assert(
            wrong not in text and wrong_phrase not in lowered,
            (
                f"turn {conv.turn}: rendered response must not show {wrong}, "
                f"got {text!r}"
            ),
        )


def _assert_july20_established_after_premium(conv, booking, availability) -> None:
    """Live turn 2: Premium establishes July 20 Temporal and search."""
    assert_no_booking_execution(conv, booking)
    conv._assert(
        availability.get_service_availability.call_count >= 1,
        (
            f"turn {conv.turn}: expected SEARCH_AVAILABILITY for July 20, "
            f"got call_count={availability.get_service_availability.call_count}"
        ),
    )
    _assert_date_surfaces(
        conv,
        availability,
        _JULY_20,
        wrong_dates=[_JULY_21],
        require_search=True,
    )
    _INTERRUPTION_STATE["july20_search_count"] = (
        availability.get_service_availability.call_count
    )


def _assert_july20_survives_browse_exhaustion(conv, booking, availability) -> None:
    """Live turn 3: browse next keeps July 20; no date fallback."""
    assert_no_booking_execution(conv, booking)
    baseline = _INTERRUPTION_STATE.get("july20_search_count", 0)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == baseline,
        (
            f"turn {conv.turn}: browse exhaustion must not search again "
            f"(baseline={baseline}, got={call_count})"
        ),
    )
    _assert_date_surfaces(
        conv,
        availability,
        _JULY_20,
        wrong_dates=[_JULY_21],
        require_search=False,
        check_render=False,
    )
    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    # Exhaustion / business-closed text is allowed; must not introduce July 21.
    conv._assert(
        _JULY_21 not in text and "july 21" not in lowered,
        (
            f"turn {conv.turn}: browse exhaustion must not introduce July 21, "
            f"got {text!r}"
        ),
    )


def _assert_july21_not_poisoned_by_july20(conv, booking, availability) -> None:
    """Live turn 4: July 21 request must not revert to / render July 20."""
    assert_no_booking_execution(conv, booking)
    baseline = _INTERRUPTION_STATE.get("july20_search_count", 0)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == baseline + 1,
        (
            f"turn {conv.turn}: expected exactly one new SEARCH_AVAILABILITY "
            f"(baseline={baseline}, got={call_count})"
        ),
    )
    _assert_date_surfaces(
        conv,
        availability,
        _JULY_21,
        wrong_dates=[_JULY_20],
        require_search=True,
    )


_register(
    Scenario(
        "July 21 search must keep July 21 after July 20 browse exhaustion",
        Turn(
            "Book me a haircut on july 20",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "Premium",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_20,
                confirmation=None,
            ),
            after=_assert_july20_established_after_premium,
        ),
        Turn(
            "Are there more times for July 20?",
            Expect(
                intent="CREATE_APPOINTMENT",
                action=None,
                date_proposal=_JULY_20,
            ),
            after=_assert_july20_survives_browse_exhaustion,
        ),
        Turn(
            "Show dates for July 21",
            Expect(
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_21,
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_july21_not_poisoned_by_july20,
        ),
        fixture="scripted_browse_exhaustion_search",
        tags=[
            "booking",
            "availability",
            "browse",
            "date-persistence",
            "temporal",
            "regression",
        ],
        id="july21-must-not-revert-after-july20-browse-exhaustion",
    )
)


def _merged_temporal_start_date(conv) -> Any:
    """Resolve Temporal.start_date from merged planning payload or session."""
    body = conv.last_body or {}
    for source in (
        body.get("_merged_luma_response"),
        (body.get("outcome") or {}).get("facts"),
        body.get("outcome"),
        conv.session(),
    ):
        if not isinstance(source, dict):
            continue
        temporal = source.get("temporal")
        if not isinstance(temporal, dict):
            planning = source.get("planning")
            if isinstance(planning, dict):
                temporal = planning.get("temporal")
        if isinstance(temporal, dict) and temporal.get("start_date"):
            return _resolve_search_date(str(temporal.get("start_date")))
    return _session_temporal_start_date(conv.session() or {})


def _assert_flexi_service_switch_preserves_july22(conv, booking, availability) -> None:
    """Service revision must SEARCH Flexi for the current July 22 search date."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, FLEXI_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _assert_last_search_service(conv, availability, FLEXI_SERVICE)
    _assert_last_search_date(conv, availability, _JULY_22)

    # Carried / merged Temporal and date_proposal stay on the active July 22 search.
    merged_start = _merged_temporal_start_date(conv)
    conv._assert(
        merged_start == _JULY_22,
        (
            f"turn {conv.turn}: merged/session Temporal.start_date must be "
            f"{_JULY_22!r}, got {merged_start!r}"
        ),
    )
    conv.assert_date_proposal(_JULY_22)

    plan = conv.plan or {}
    action = plan.get("action")
    if action is None:
        action = (conv.outcome or {}).get("action")
    conv._assert(
        action == "SEARCH_AVAILABILITY",
        f"turn {conv.turn}: expected SEARCH_AVAILABILITY, got {action!r}",
    )

    # Prefer execution context when present; fall back to behavioural invariants
    # (response plan may omit execution_proposal_context after SEARCH).
    ctx = plan.get("execution_proposal_context") or {}
    body = conv.last_body or {}
    if not ctx:
        nested = (body.get("outcome") or {}).get("plan") or {}
        ctx = nested.get("execution_proposal_context") or body.get(
            "execution_proposal_context"
        ) or {}
    if ctx:
        conv._assert(
            ctx.get("availability_invalidated") is True,
            (
                f"turn {conv.turn}: expected execution_proposal_context."
                f"availability_invalidated=True, got {ctx!r}"
            ),
        )
        conv._assert(
            ctx.get("session_time_proposal_reuse_allowed") is False,
            (
                f"turn {conv.turn}: session time-proposal reuse must be disabled after "
                f"service revision, got {ctx!r}"
            ),
        )
    time_match = (
        plan.get("time_match_outcome")
        or (body.get("outcome") or {}).get("time_match_outcome")
    )
    conv._assert(
        time_match != "TIME_MATCH_EXACT",
        (
            f"turn {conv.turn}: stale 09:00 must not TIME_MATCH_EXACT after "
            f"service revision, got {time_match!r}"
        ),
    )
    conv.assert_slot_absent("time")

    _assert_date_surfaces(
        conv,
        availability,
        _JULY_22,
        wrong_dates=[_JULY_21],
        require_search=True,
        check_render=True,
    )
    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        _JULY_21 not in text and "july 21" not in lowered,
        (
            f"turn {conv.turn}: Flexi service switch must not revert to July 21, "
            f"got {text!r}"
        ),
    )
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    assert_no_booking_execution(conv, booking)


def _assert_correction_service_switch_no_rematch(conv, booking, availability) -> None:
    """CORRECTION Flexi switch: SEARCH Flexi, no stale 09:00 confirmation rematch."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, FLEXI_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _assert_last_search_service(conv, availability, FLEXI_SERVICE)
    conv.assert_slot_absent("time")

    plan = conv.plan or {}
    body = conv.last_body or {}
    ctx = plan.get("execution_proposal_context") or {}
    if not ctx:
        nested = (body.get("outcome") or {}).get("plan") or {}
        ctx = nested.get("execution_proposal_context") or body.get(
            "execution_proposal_context"
        ) or {}
    if ctx:
        conv._assert(
            ctx.get("availability_invalidated") is True,
            f"turn {conv.turn}: availability_invalidated must be True, got {ctx!r}",
        )
        conv._assert(
            ctx.get("session_time_proposal_reuse_allowed") is False,
            f"turn {conv.turn}: session time reuse must be False, got {ctx!r}",
        )
    time_match = plan.get("time_match_outcome") or (
        body.get("outcome") or {}
    ).get("time_match_outcome")
    conv._assert(
        time_match != "TIME_MATCH_EXACT",
        f"turn {conv.turn}: must not rematch stale 09:00, got {time_match!r}",
    )
    status = plan.get("status") or (conv.outcome or {}).get("status")
    conv._assert(
        status != "AWAITING_CONFIRMATION",
        f"turn {conv.turn}: must not resume confirmation, got status={status!r}",
    )
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


_register(
    Scenario(
        "CORRECTION service switch after 9am must not rematch confirmation",
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
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "switch to flexi haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_correction_service_switch_no_rematch,
        ),
        fixture="scripted_availability_supersession",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "service-revision",
            "correction",
            "regression",
        ],
        id="correction-service-switch-no-rematch-9am",
    )
)


_register(
    Scenario(
        "Service change must preserve current July 22 search date",

        Turn(
            "book me haircut on 21st july",
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
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_21,
                confirmation=None,
            ),
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
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "11"},
            ),
        ),
        Turn(
            "show availability for 22nd july",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_22,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
                date_proposal=_JULY_22,
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "switch to flexi haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_JULY_22,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_flexi_service_switch_preserves_july22,
        ),
        fixture="scripted_availability_supersession",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "service-supersession",
            "date-persistence",
            "temporal",
            "regression",
        ],
        id="service-change-must-preserve-current-search-date",
    )
)


def _assert_nlu_exposes_resolved_date(conv, expected_date: str) -> None:
    """Scripted / Core-visible NLU Temporal must carry the resolved calendar day."""
    body = conv.last_body or {}
    merged = body.get("_merged_luma_response")
    if not isinstance(merged, dict):
        merged = {}
    raw = merged.get("_raw_luma_response")
    if not isinstance(raw, dict):
        raw = {}
    sources = (raw, merged, body.get("outcome") or {}, conv.session() or {})
    start_date = None
    for source in sources:
        if not isinstance(source, dict):
            continue
        temporal = source.get("temporal")
        if not isinstance(temporal, dict):
            planning = source.get("planning")
            if isinstance(planning, dict):
                temporal = planning.get("temporal")
        if isinstance(temporal, dict) and temporal.get("start_date"):
            start_date = _resolve_search_date(str(temporal.get("start_date")))
            break
    conv._assert(
        start_date == expected_date,
        (
            f"turn {conv.turn}: NLU/Core Temporal.start_date must be "
            f"{expected_date!r} (resolved relative date), got {start_date!r}"
        ),
    )


def _assert_tomorrow_availability_supersedes_confirmation(
    conv, booking, availability
) -> None:
    """Relative 'tomorrow' must survive into SEARCH_AVAILABILITY after pending confirm."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_turn_operation(conv, "AVAILABILITY")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, PREMIUM_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)

    _assert_nlu_exposes_resolved_date(conv, _TOMORROW)
    conv.assert_date_proposal(_TOMORROW)
    _assert_last_search_date(conv, availability, _TOMORROW)

    missing = (conv.outcome or {}).get("missing_slots") or []
    if not isinstance(missing, list):
        missing = []
    conv._assert(
        "date" not in missing,
        (
            f"turn {conv.turn}: must not take a missing-date clarification path, "
            f"got missing_slots={missing!r}"
        ),
    )
    status = (conv.outcome or {}).get("status")
    conv._assert(
        status != "NEEDS_CLARIFICATION",
        (
            f"turn {conv.turn}: tomorrow availability must not clarify for date, "
            f"got status={status!r}"
        ),
    )

    # Must not reuse the prior undated/default search day (TARGET_DATE).
    call = availability.get_service_availability.call_args
    searched = _resolve_search_date((call.kwargs if call else {}).get("date"))
    conv._assert(
        searched != TARGET_DATE,
        (
            f"turn {conv.turn}: must not reuse prior confirmed/default date "
            f"{TARGET_DATE!r}, got {searched!r}"
        ),
    )

    _assert_date_surfaces(
        conv,
        availability,
        _TOMORROW,
        wrong_dates=[TARGET_DATE],
        require_search=True,
        check_render=True,
    )
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    _assert_no_confirmation_prompt_phrases(conv)
    assert_no_booking_execution(conv, booking)


_register(
    Scenario(
        "Relative tomorrow availability must resolve after confirmation",
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
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                confirmation=None,
            ),
        ),
        Turn(
            "9am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "check availability for tomorrow",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=_TOMORROW,
                slot_absent=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_tomorrow_availability_supersedes_confirmation,
        ),
        fixture="scripted_availability_supersession",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "availability",
            "relative-date",
            "temporal",
            "regression",
        ],
        id="relative-tomorrow-availability-must-resolve",
    )
)


def _assert_service_change_after_nine_am_confirmation(
    conv, booking, availability
) -> None:
    """Pending confirmation + service switch → Flexi SEARCH, time selection resumes."""
    baseline = _INTERRUPTION_STATE.get("search_baseline", 0)
    assert_gate_action(conv, "ANOTHER_REQUEST")
    assert_planning_intent_preserved(conv)
    assert_cleared_confirmation_binding(conv)
    assert_service_preserved(conv, FLEXI_SERVICE)
    assert_exactly_one_search_since(conv, availability, baseline)
    _assert_last_search_service(conv, availability, FLEXI_SERVICE)

    plan = conv.plan or {}
    action = plan.get("action")
    if action is None:
        action = (conv.outcome or {}).get("action")
    conv._assert(
        action == "SEARCH_AVAILABILITY",
        f"turn {conv.turn}: expected SEARCH_AVAILABILITY for Flexi, got {action!r}",
    )

    missing = (
        plan.get("missing_slots")
        or (conv.outcome or {}).get("missing_slots")
        or []
    )
    if not isinstance(missing, list):
        missing = []
    conv._assert(
        "time" in missing,
        f"turn {conv.turn}: workflow must resume at time selection, missing={missing!r}",
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected Flexi availability text, got {text!r}",
    )
    conv._assert(
        "flexi" in lowered or "available" in lowered or "time" in lowered,
        f"turn {conv.turn}: expected Flexi availability presentation, got {text!r}",
    )
    assert_not_confirmation_rendered(conv)
    assert_availability_rendered(conv)
    assert_no_booking_execution(conv, booking)
    assert not booking.create_booking.called


_register(
    Scenario(
        "Service change after confirmation researches Flexi",
        Turn(
            "Book premium haircut",
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
        ),
        Turn(
            "9:00",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                planner="AWAITING_CONFIRMATION",
                stage="CONFIRM",
                awaiting="USER_CONFIRMATION",
                action=None,
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "09"},
                response_text_present=True,
            ),
            after=_capture_pre_revision_search,
        ),
        Turn(
            "switch to flexi haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                slot_absent=["time"],
                missing_slots=["time"],
                availability_invalidated=True,
                response_text_present=True,
            ),
            trace="1",
            after=_assert_service_change_after_nine_am_confirmation,
        ),
        fixture="scripted_confirm",
        tags=[
            "booking",
            "confirmation",
            "interruption",
            "service-revision",
            "regression",
        ],
        id="service-change-after-confirmation-to-flexi",
    )
)


# Handler digression (FAQ mid-booking / resume commit).
_STATE: Dict[str, Any] = {}




def _capture_pending_baseline(conv, _booking, availability) -> None:
    sess = conv.session() or {}
    _STATE["confirmation"] = _confirmation_state(sess)
    _STATE["slots"] = dict(sess.get("slots") or {})
    from core.workflows.availability.presentation import (
        availability_fingerprint_from_session,
        presented_availability_from_session,
    )
    _STATE["fingerprint"] = availability_fingerprint_from_session(sess)
    _STATE["presented"] = presented_availability_from_session(sess)
    _STATE["search_count"] = availability.get_service_availability.call_count
    _STATE["intent"] = sess.get("intent_name")


def _assert_handler_delegated_preserves(conv, booking, availability) -> None:
    """FAQ digression is HANDLER_DELEGATED (not Core OFF_TOPIC); booking frozen."""
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count == _STATE.get(
        "search_count", 0
    ), (
        f"turn {conv.turn}: HANDLER_DELEGATED digression must not SEARCH "
        f"(baseline={_STATE.get('search_count')}, "
        f"got={availability.get_service_availability.call_count})"
    )

    outcome = conv.outcome or {}
    status = outcome.get("status")
    assert status == "HANDLER_DELEGATED", (
        f"turn {conv.turn}: expected HANDLER_DELEGATED (not OFF_TOPIC), got {status!r}"
    )
    assert status != "OFF_TOPIC"
    assert outcome.get("active_handler") == "rag", (
        f"turn {conv.turn}: expected active_handler=rag, got {outcome.get('active_handler')!r}"
    )

    sess = conv.session() or {}
    assert sess.get("intent_name") == _STATE.get("intent") or sess.get(
        "intent_name"
    ) == "CREATE_APPOINTMENT", (
        f"turn {conv.turn}: durable booking intent must survive handler digression, "
        f"got {sess.get('intent_name')!r}"
    )
    assert _confirmation_state(sess) == _STATE.get("confirmation") == "pending", (
        f"turn {conv.turn}: confirmation must stay pending, "
        f"got {_confirmation_state(sess)!r}"
    )
    assert dict(sess.get("slots") or {}) == _STATE.get("slots"), (
        f"turn {conv.turn}: booking slots must be unchanged during FAQ digression"
    )
    from core.workflows.availability.presentation import availability_fingerprint_from_session
    assert availability_fingerprint_from_session(sess) == _STATE.get("fingerprint"), (
        f"turn {conv.turn}: availability fingerprint must be unchanged"
    )
    text = _response_text(conv.last_body or {})
    assert text.strip(), f"turn {conv.turn}: expected handler answer text"
    # Resume cue toward pending confirmation.
    lowered = text.lower()
    assert (
        "confirm" in lowered
        or "book" in lowered
        or "continue" in lowered
        or "appointment" in lowered
        or "25" in text
        or "price" in lowered
        or "hour" in lowered
        or "open" in lowered
    ), (
        f"turn {conv.turn}: handler reply should answer FAQ and/or resume booking, "
        f"got {text!r}"
    )


_register(
    Scenario(
        "HANDLER_DELEGATED FAQ then resume and commit",
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
            ),
            after=_capture_pending_baseline,
        ),
        Turn(
            "how much does a haircut cost?",
            Expect(
                response_status="HANDLER_DELEGATED",
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                response_text_present=True,
            ),
            after=_assert_handler_delegated_preserves,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_booking_created,
        ),
        fixture="scripted_off_topic",
        tags=["handler", "digression", "booking", "audit"],
        id="handler-delegated-faq-resume-commit",
        requires_customer_identity=True,
    )
)
# ============================================================
# INVALID INPUT
# ============================================================
# (no scenarios in this section yet)
# ============================================================
# RECOVERY
# ============================================================
_register(
    Scenario(
        "Identity blocked then resolved then freshly confirmed",
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
            ),
            after=_assert_no_booking,
        ),
        Turn(
            "yes",
            Expect(
                planner="NEEDS_CLARIFICATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                response_text_present=True,
            ),
            after=_assert_identity_blocked_yes,
        ),
        Turn(
            "ok",
            Expect(
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
                response_text_present=True,
            ),
            before=_attach_identity_before_resume,
            after=_assert_identity_resolved_pending,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_booking_created,
        ),
        fixture="booking",
        tags=["booking", "identity", "confirmation", "audit"],
        id="identity-blocked-then-resolved-then-confirmed",
        # Identity attached mid-flow — do not set requires_customer_identity.
    )
)

_register(
    Scenario(
        "Execution failure then safe retry",
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
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                has_availability_slots=True,
            ),
        ),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                slot_contains={"time": "10"},
            ),
            after=_assert_no_booking,
        ),
        # No Expect: failed commit returns success=False.
        Turn(
            "yes",
            before=_fail_booking_once,
            after=_assert_execution_failed_resumable,
        ),
        Turn(
            "yes",
            Expect(
                planner="READY",
                stage="CONFIRM",
                action="CONFIRM_APPOINTMENT",
                confirmation=None,
                session_slots={"service_id": PREMIUM_SERVICE},
                slot_contains={"time": "10"},
            ),
            after=_assert_retry_single_success,
        ),
        fixture="booking",
        tags=["booking", "execution", "retry", "audit"],
        id="execution-failure-then-safe-retry",
        requires_customer_identity=True,
    )
)
