"""E2E scenarios for confirmation interruption (ANOTHER_REQUEST) architecture."""

from __future__ import annotations

from datetime import datetime
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
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _resolve_search_date,
    assert_no_booking_execution,
)
from core.tests.e2e.framework.fixtures import TARGET_DATE

SCENARIOS: List[Scenario] = []
_INTERRUPTION_STATE: Dict[str, Any] = {}
_JULY_21 = "2026-07-21"
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
        "show me availability": availability_reshow_script(),
        "show availability": availability_reshow_script(),
        "show availability for 21st july": availability_date_change_script(_JULY_21),
        "show availability for July 21": availability_date_change_script(_JULY_21),
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
            "facts": {"times": ["09:00"]},
            "time_constraint": {
                "mode": "exact",
                "start": "09:00",
                "end": "09:00",
            },
        },
        "9:30": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"times": ["09:30"]},
            "time_constraint": {
                "mode": "exact",
                "start": "09:30",
                "end": "09:30",
            },
        },
        "10am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"times": ["10:00"]},
            "time_constraint": {
                "mode": "exact",
                "start": "10:00",
                "end": "10:00",
            },
        },
        "11am": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"times": ["11:00"]},
            "time_constraint": {
                "mode": "exact",
                "start": "11:00",
                "end": "11:00",
            },
        },
        "yes": {
            "success": True,
            "intent": {"name": "CONFIRM_ACTION"},
            "facts": {},
        },
        "switch to 10am": {
            "success": True,
            "intent": {"name": "CORRECTION"},
            "facts": {"times": ["10:00"]},
            "time_constraint": {
                "mode": "exact",
                "start": "10:00",
                "end": "10:00",
            },
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
