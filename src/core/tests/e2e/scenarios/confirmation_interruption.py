"""E2E scenarios for confirmation interruption (ANOTHER_REQUEST) architecture."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from core.planning.time_resolution import TIME_MATCH_EXACT
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
    availability_date_change_script,
    availability_reshow_script,
    availability_service_change_script,
    capture_pre_interruption_state,
    premium_booking_start_script,
)
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FROZEN_TIME,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _resolve_search_date,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
)
from core.tests.e2e.framework.fixtures import TARGET_DATE
from core.tests.e2e.framework.scripted_temporal import (
    exact_time_temporal,
    single_day_temporal,
)

SCENARIOS: List[Scenario] = []
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


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


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


def confirmation_interruption_scripts() -> Dict[str, Any]:
    return {
        "book me a premium haircut": premium_booking_start_script(),
        "book me premium haircut": premium_booking_start_script(),
        "book me a haircut": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
        },
        "book haircut": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
        },
        "book me premium haircut on 23rd july": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": PREMIUM_SERVICE,
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "temporal": single_day_temporal(_JULY_23),
            "missing_slots": ["time"],
        },
        # Live regression: Premium turn established July 20 (observed out.out flow).
        # Service-only — date must come from the booking turn's Temporal (e.g. July 20).
        "premium": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "missing_slots": ["time"],
        },
        "book me a haircut on july 20": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
            "facts": {},
            "temporal": single_day_temporal(_JULY_20),
        },
        "book me haircut on 21st july": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
            "facts": {},
            "temporal": single_day_temporal(_JULY_21),
        },
        "show availability for 22nd july": availability_date_change_script(_JULY_22),
        "check availability for tomorrow": availability_date_change_script(_TOMORROW),
        "switch to flexi haircut": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": FLEXI_SERVICE,
                "slots": {"service_id": FLEXI_SERVICE},
            },
            "slots": {"service_id": FLEXI_SERVICE},
            "missing_slots": [],
            "needs_clarification": False,
        },
        "are there more times for july 20?": {
            "success": True,
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_next",
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "missing_slots": ["time"],
        },
        "show dates for july 21": availability_date_change_script(_JULY_21),
        "show me availability": availability_reshow_script(),
        "show availability": availability_reshow_script(),
        "show availability for 21st july": availability_date_change_script(_JULY_21),
        "show availability for July 21": availability_date_change_script(_JULY_21),
        "no. search availability for 24th july": availability_date_change_script(
            _JULY_24
        ),
        "show me availability for flexi haircut": availability_service_change_script(
            FLEXI_SERVICE
        ),
        "show availability for flexi haircut": availability_service_change_script(
            FLEXI_SERVICE
        ),
        "show availability for flexi": availability_service_change_script(FLEXI_SERVICE),
        "9am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "temporal": exact_time_temporal("09:00"),
        },
        "9:30": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "temporal": exact_time_temporal("09:30"),
        },
        "9:30am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "temporal": exact_time_temporal("09:30"),
        },
        "10am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "temporal": exact_time_temporal("10:00"),
        },
        "11am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "temporal": exact_time_temporal("11:00"),
        },
        "yes": {
            "success": True,
            "intent": {"name": "CONFIRM_ACTION"},
            "facts": {},
        },
        "switch to 10am": {
            "success": True,
            "intent": {"name": "CORRECTION"},
            "facts": {},
            "temporal": exact_time_temporal("10:00"),
        },
    }


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
    presented_payload = sess.get("presented_availability") or {}
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

    presented_payload = sess.get("presented_availability") or {}
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

        presented_payload = sess.get("presented_availability") or {}
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
                response_status="succeeded",
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

