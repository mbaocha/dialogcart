"""E2E scenarios: turn.understanding must track utterance evidence, not session stickiness."""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.confirmation_interruption import (
    assert_availability_rendered,
    assert_no_search_since,
    assert_not_confirmation_rendered,
)
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
)
from core.tests.e2e.framework.scripted_temporal import (
    exact_time_temporal,
    single_day_temporal,
)
from core.tests.e2e.framework.turn_understanding import (
    assert_understanding_everywhere,
    outcome_understanding,
    session_fingerprint,
    session_service_id,
)

SCENARIOS: List[Scenario] = []
_STATE: Dict[str, Any] = {}

JULY_21 = "2026-07-21"
JULY_22 = "2026-07-22"

_UNDERSTOOD = "UNDERSTOOD"
_UNRECOGNIZED = "UNRECOGNIZED_INPUT"

_CONFIRMATION_PHRASES = (
    "Would you like me to go ahead",
    "You're about to book",
    "Should I go ahead",
)

_INTERNAL_PLACEHOLDERS = (
    "[NEEDS_CLARIFICATION]",
    "no text — try a booking request",
    "(no text — try a booking request)",
)


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _luma(conv) -> Any:
    luma = getattr(conv, "luma_client", None)
    conv._assert(luma is not None, f"turn {conv.turn}: luma_client missing on conversation")
    return luma


def _assert_understanding(expected: str, *, require_result: bool = True):
    def _hook(conv, _booking, _availability) -> None:
        assert_understanding_everywhere(
            conv, _luma(conv), expected, require_result=require_result
        )

    return _hook


def turn_understanding_scripts() -> Dict[str, Any]:
    """Stage-2-shaped scripts; understanding is derived by the aware client."""
    return {
        "book haircut on july 21": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id", "time"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
            "facts": {},
            "service_term": None,
            "temporal": single_day_temporal(JULY_21),
        },
        "premium": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "service_term": "premium",
            "missing_slots": ["time"],
            "temporal": {"mode": "none", "confidence": 1.0},
        },
        # Gibberish: no utterance evidence. Production NLU returns UNKNOWN;
        # in-flow Core recovers durable session intent. Sticky resolved_service_id
        # must not flip understanding to UNDERSTOOD after service ambiguity reuse.
        "aaa": {
            "success": True,
            "intent": {"name": "UNKNOWN"},
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "service_term": None,
            "operation": None,
            "temporal": {"mode": "none", "confidence": 0.0},
            "missing_slots": [],
        },
        "flexi": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": FLEXI_SERVICE,
                "slots": {"service_id": FLEXI_SERVICE},
            },
            "slots": {"service_id": FLEXI_SERVICE},
            "service_term": "flexi",
            "missing_slots": ["time"],
            "needs_clarification": False,
            "temporal": {"mode": "none", "confidence": 1.0},
        },
        "2pm": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "service_term": None,
            "temporal": exact_time_temporal("14:00"),
            "missing_slots": [],
        },
        "july 22": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {},
            "service_term": None,
            "temporal": single_day_temporal(JULY_22),
            "missing_slots": ["time"],
            "needs_clarification": False,
        },
        "show more": {
            "success": True,
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_next",
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "service_term": None,
            "missing_slots": ["time"],
        },
        "cancel booking 12345": {
            "success": True,
            "intent": {"name": "CANCEL_BOOKING"},
            "facts": {
                "booking_id": "12345",
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
            },
            "service_term": None,
            "temporal": {"mode": "none", "confidence": 1.0},
        },
    }


def _capture_availability_baseline(conv, _booking, availability) -> None:
    sess = conv.session() or {}
    _STATE["search_count"] = availability.get_service_availability.call_count
    _STATE["fingerprint"] = session_fingerprint(sess)
    _STATE["presented_times"] = extract_presented_times(conv.last_body or {}, sess)
    _STATE["service_id"] = session_service_id(sess)


def _assert_recovery_asks_for_time(conv, booking, availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNRECOGNIZED)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))

    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: service_id must remain {PREMIUM_SERVICE!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    conv.assert_date_proposal(JULY_21)
    missing = sess.get("missing_slots") or conv.outcome.get("missing_slots") or []
    conv._assert(
        list(missing) == ["time"],
        f"turn {conv.turn}: missing_slots expected ['time'], got {missing!r}",
    )

    fp = session_fingerprint(sess)
    conv._assert(
        fp == _STATE.get("fingerprint"),
        (
            f"turn {conv.turn}: availability fingerprint must be unchanged, "
            f"got {fp!r} vs {_STATE.get('fingerprint')!r}"
        ),
    )
    presented = extract_presented_times(conv.last_body or {}, sess)
    conv._assert(
        presented == _STATE.get("presented_times"),
        (
            f"turn {conv.turn}: presented availability times must be unchanged, "
            f"got {presented!r} vs {_STATE.get('presented_times')!r}"
        ),
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected recovery response text, got {text!r}",
    )
    conv._assert(
        "time" in lowered or "understand" in lowered or "didn't" in lowered
        or "did not" in lowered,
        f"turn {conv.turn}: expected recovery prompt about time, got {text!r}",
    )
    for phrase in _CONFIRMATION_PHRASES:
        conv._assert(
            phrase not in text,
            f"turn {conv.turn}: confirmation phrase {phrase!r} must not appear",
        )


def _assert_premium_repeat_understood(conv, booking, availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        f"turn {conv.turn}: service_id expected {PREMIUM_SERVICE!r}",
    )
    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    # No recovery — UNDERSTOOD with sticky-same service must not apologise.
    conv._assert(
        "didn't understand" not in lowered and "did not understand" not in lowered,
        f"turn {conv.turn}: recovery renderer must not run, got {text!r}",
    )


def _assert_flexi_service_revision(conv, booking, availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == FLEXI_SERVICE,
        (
            f"turn {conv.turn}: service_id must change to {FLEXI_SERVICE!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    baseline = _STATE.get("search_count", 0)
    conv._assert(
        availability.get_service_availability.call_count > baseline,
        (
            f"turn {conv.turn}: expected availability refresh for new service, "
            f"call_count={availability.get_service_availability.call_count} "
            f"baseline={baseline}"
        ),
    )
    assert_availability_rendered(conv)
    assert_not_confirmation_rendered(conv)


def _assert_time_bind_confirmation(conv, booking, _availability) -> None:
    # Confirmation envelope may omit turn on RESULT; NLU understanding is required.
    assert_understanding_everywhere(
        conv, _luma(conv), _UNDERSTOOD, require_result=False
    )
    outcome_value = outcome_understanding(conv)
    if outcome_value is not None:
        conv._assert(
            outcome_value == _UNDERSTOOD,
            f"turn {conv.turn}: RESULT understanding must be UNDERSTOOD when present",
        )
    assert not booking.create_booking.called
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        f"turn {conv.turn}: inherited service_id must remain {PREMIUM_SERVICE!r}",
    )
    text = _response_text(conv.last_body or {})
    conv._assert(
        any(p in text for p in _CONFIRMATION_PHRASES),
        f"turn {conv.turn}: expected confirmation prompt, got {text!r}",
    )


def _assert_date_revision(conv, booking, availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        f"turn {conv.turn}: service must remain {PREMIUM_SERVICE!r}",
    )
    conv.assert_date_proposal(JULY_22)
    baseline = _STATE.get("search_count", 0)
    conv._assert(
        availability.get_service_availability.call_count > baseline,
        (
            f"turn {conv.turn}: expected availability search after date revision, "
            f"call_count={availability.get_service_availability.call_count}"
        ),
    )
    assert_availability_rendered(conv)


def _assert_browse_next(conv, booking, availability) -> None:
    assert_understanding_everywhere(
        conv, _luma(conv), _UNDERSTOOD, require_result=False
    )
    assert_no_booking_execution(conv, booking)
    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        "didn't understand" not in lowered and "did not understand" not in lowered,
        f"turn {conv.turn}: recovery must not run on browse, got {text!r}",
    )
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        f"turn {conv.turn}: browse must preserve service",
    )
    conv.assert_date_proposal(JULY_21)
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected browse response text, got {text!r}",
    )


def _assert_confirmation_unrecognized(conv, booking, availability) -> None:
    """Gibberish during confirmation must be UNRECOGNIZED and must not commit.

    Note: CREATE_APPOINTMENT continuation currently supersedes confirmation via
    ANOTHER_REQUEST (planner READY). Understanding + no-commit are the hard
    contracts for this suite; confirmation stickiness is asserted when present.
    """
    assert_understanding_everywhere(
        conv, _luma(conv), _UNRECOGNIZED, require_result=False
    )
    outcome_value = outcome_understanding(conv)
    if outcome_value is not None:
        conv._assert(
            outcome_value == _UNRECOGNIZED,
            f"turn {conv.turn}: RESULT understanding must be UNRECOGNIZED when present",
        )
    assert_no_booking_execution(conv, booking)
    assert not booking.create_booking.called
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))

    sess = conv.session() or {}
    confirmation = sess.get("confirmation_state")
    status = conv.outcome.get("status")
    text = _response_text(conv.last_body or {})
    lowered = text.lower()

    if confirmation == "pending" or status == "AWAITING_CONFIRMATION":
        conv._assert(
            bool(text.strip()),
            f"turn {conv.turn}: expected recovery text during confirmation, got {text!r}",
        )
        conv._assert(
            "understand" in lowered or "yes" in lowered or "confirm" in lowered
            or "didn't" in lowered or "did not" in lowered,
            f"turn {conv.turn}: expected confirmation recovery guidance, got {text!r}",
        )
        return

    # Confirmation superseded: still must not commit, and must not invent success.
    conv._assert(
        status in ("READY", "AWAITING_CONFIRMATION", "NEEDS_CLARIFICATION"),
        f"turn {conv.turn}: unexpected status after unrecognized confirmation input: {status!r}",
    )
    conv._assert(
        "booked" not in lowered and "confirmed" not in lowered,
        f"turn {conv.turn}: must not claim booking succeeded, got {text!r}",
    )


def _assert_cold_start_unrecognized_recovery(conv, booking, availability) -> None:
    """Random input with no active workflow must recover, not expose placeholders."""
    luma = _luma(conv)
    nlu = getattr(luma, "last_response", None) or {}
    nlu_intent = nlu.get("intent") or {}
    nlu_intent_name = (
        nlu_intent.get("name") if isinstance(nlu_intent, dict) else nlu_intent
    )
    conv._assert(
        nlu_intent_name == "UNKNOWN",
        f"turn {conv.turn}: NLU intent expected UNKNOWN, got {nlu_intent_name!r}",
    )
    assert_understanding_everywhere(conv, luma, _UNRECOGNIZED)

    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, 0)
    conv._assert(
        not booking.create_booking.called and not booking.cancel_booking.called,
        f"turn {conv.turn}: no booking execution should occur on cold-start gibberish",
    )

    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    for key in ("service_id", "date", "time", "date_range"):
        conv._assert(
            slots.get(key) in (None, "", []),
            f"turn {conv.turn}: session slot {key!r} must be empty, got {slots.get(key)!r}",
        )
    session_intent = sess.get("intent_name")
    conv._assert(
        session_intent in (None, "", "UNKNOWN"),
        (
            f"turn {conv.turn}: no active booking intent expected, "
            f"got session.intent_name={session_intent!r}"
        ),
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected general recovery response text, got {text!r}",
    )
    conv._assert(
        "understand" in lowered
        or "didn't" in lowered
        or "did not" in lowered
        or "catch" in lowered,
        f"turn {conv.turn}: expected user-facing recovery apology, got {text!r}",
    )
    for placeholder in _INTERNAL_PLACEHOLDERS:
        conv._assert(
            placeholder not in text,
            f"turn {conv.turn}: internal placeholder {placeholder!r} must not appear",
        )
    # Intent-neutral: no booking/workflow assumptions when nothing is active.
    forbidden = (
        "book",
        "booking",
        "appointment",
        "service",
        "availability",
        "available",
        "date",
        "july",
        "haircut",
        "premium",
        "flexi",
    )
    for term in forbidden:
        conv._assert(
            term not in lowered,
            (
                f"turn {conv.turn}: general recovery must be intent-neutral; "
                f"found {term!r} in {text!r}"
            ),
        )
    conv._assert(
        "how can i help" in lowered
        or "rephrase" in lowered
        or "try again" in lowered
        or "say that again" in lowered
        or "what you need" in lowered
        or "what you'd like" in lowered
        or "what you would like" in lowered,
        f"turn {conv.turn}: expected intent-neutral help/rephrase invite, got {text!r}",
    )


def _assert_cancel_booking_id_evidence(conv, booking, _availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    conv._assert(
        conv.outcome.get("intent_name") == "CANCEL_BOOKING"
        or conv.plan.get("intent") == "CANCEL_BOOKING"
        or (conv.session() or {}).get("intent_name") == "CANCEL_BOOKING",
        (
            f"turn {conv.turn}: expected CANCEL_BOOKING intent, "
            f"outcome={conv.outcome.get('intent_name')!r}"
        ),
    )
    # booking_id evidence must be treated as UNDERSTOOD; cancel may ask confirm
    # or execute depending on policy — require progress beyond unrecognized recovery.
    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        "didn't understand" not in lowered and "did not understand" not in lowered,
        f"turn {conv.turn}: booking_id must not trigger unrecognized recovery, got {text!r}",
    )
    # Prefer cancel execution when the mock accepts the booking id.
    if booking.cancel_booking.called:
        return
    action = conv.plan.get("action")
    conv._assert(
        action in ("CANCEL_BOOKING", "CONFIRM_CANCEL", None)
        or conv.outcome.get("status")
        in ("AWAITING_CONFIRMATION", "READY", "NEEDS_CLARIFICATION", "succeeded"),
        (
            f"turn {conv.turn}: cancel flow should continue with booking_id evidence, "
            f"action={action!r} status={conv.outcome.get('status')!r}"
        ),
    )


def _common_setup_turns(*, after_premium) -> tuple:
    return (
        Turn(
            "book haircut on July 21",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=JULY_21,
            ),
            after=_assert_understanding(_UNDERSTOOD),
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
                date_proposal=JULY_21,
                response_text_present=True,
            ),
            after=after_premium,
        ),
    )


def _after_premium_capture_and_understood(conv, booking, availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    _capture_availability_baseline(conv, booking, availability)


_register(
    Scenario(
        "Unrecognized input with no active workflow",
        Turn(
            "aaa",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="UNKNOWN",
                action=None,
                response_text_present=True,
                slot_absent=["service_id", "date", "time"],
            ),
            after=_assert_cold_start_unrecognized_recovery,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "recovery", "regression", "cold-start"],
        id="understanding-unrecognized-no-active-workflow",
    )
)


_register(
    Scenario(
        "Unrecognized input after service selection",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "aaa",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                missing_slots=["time"],
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=JULY_21,
                action=None,
                response_text_present=True,
            ),
            after=_assert_recovery_asks_for_time,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "recovery", "regression"],
        id="understanding-unrecognized-after-premium",
    )
)


_register(
    Scenario(
        "Service explicitly repeated remains understood",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "premium",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=JULY_21,
            ),
            after=_assert_premium_repeat_understood,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "regression"],
        id="understanding-service-repeated",
    )
)


_register(
    Scenario(
        "User changes service",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "flexi",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                date_proposal=JULY_21,
                response_text_present=True,
            ),
            after=_assert_flexi_service_revision,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "revision", "regression"],
        id="understanding-service-change",
    )
)


_register(
    Scenario(
        "Time after sticky service",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "2pm",
            Expect(
                planner="AWAITING_CONFIRMATION",
                response_status="AWAITING_CONFIRMATION",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                missing_slots=[],
                confirmation="pending",
                response_text_present=True,
            ),
            after=_assert_time_bind_confirmation,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "confirmation", "regression"],
        id="understanding-time-after-sticky-service",
    )
)


_register(
    Scenario(
        "Date revision after sticky service",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "July 22",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=JULY_22,
                response_text_present=True,
            ),
            after=_assert_date_revision,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "revision", "regression"],
        id="understanding-date-revision",
    )
)


_register(
    Scenario(
        "Show more browse remains understood",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "show more",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=JULY_21,
                response_text_present=True,
            ),
            after=_assert_browse_next,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "browse", "regression"],
        id="understanding-show-more",
    )
)


def _after_confirm_capture(conv, booking, availability) -> None:
    _assert_time_bind_confirmation(conv, booking, availability)
    _capture_availability_baseline(conv, booking, availability)


_register(
    Scenario(
        "Random text after confirmation",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "2pm",
            Expect(
                planner="AWAITING_CONFIRMATION",
                response_status="AWAITING_CONFIRMATION",
                intent="CREATE_APPOINTMENT",
                confirmation="pending",
                missing_slots=[],
                response_text_present=True,
            ),
            after=_after_confirm_capture,
        ),
        Turn(
            "aaa",
            Expect(
                intent="CREATE_APPOINTMENT",
                action=None,
            ),
            after=_assert_confirmation_unrecognized,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "confirmation", "recovery", "regression"],
        id="understanding-unrecognized-during-confirmation",
    )
)


_register(
    Scenario(
        "Booking ID counts as utterance evidence",
        Turn(
            "cancel booking 12345",
            Expect(
                intent="CANCEL_BOOKING",
                response_text_present=True,
            ),
            after=_assert_cancel_booking_id_evidence,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "cancel", "regression"],
        id="understanding-booking-id-evidence",
    )
)
