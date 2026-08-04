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
    _normalize_explicit_search_date,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
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

def _capture_availability_baseline(conv, _booking, availability) -> None:
    sess = conv.session() or {}
    _STATE["search_count"] = availability.get_service_availability.call_count
    _STATE["fingerprint"] = session_fingerprint(sess)
    _STATE["presented_times"] = extract_presented_times(conv.last_body or {}, sess)
    _STATE["service_id"] = session_service_id(sess)


def _presented_search_date(session: Dict[str, Any]) -> Any:
    from core.workflows.availability.presentation import presented_availability_from_session, availability_cache_from_session
    presented = presented_availability_from_session(session)
    if isinstance(presented, dict) and presented.get("search_date"):
        return str(presented.get("search_date")).split("T")[0].split(" ")[0]
    last = availability_cache_from_session(session)
    if isinstance(last, dict) and last.get("search_date"):
        return str(last.get("search_date")).split("T")[0].split(" ")[0]
    return None


def _assert_undated_first_availability(conv, booking, availability) -> None:
    """Undated exploratory SEARCH presents a concrete backend-selected day."""
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)
    assert_availability_rendered(conv)

    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count >= 1,
        f"turn {conv.turn}: expected SEARCH_AVAILABILITY, got call_count={call_count}",
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    requested = _normalize_explicit_search_date(kwargs.get("date"))
    conv._assert(
        requested is None,
        f"turn {conv.turn}: undated search must omit date, got {requested!r}",
    )

    sess = conv.session() or {}
    presented_date = _presented_search_date(sess)
    conv._assert(
        bool(presented_date),
        f"turn {conv.turn}: expected concrete presented.search_date after undated search",
    )
    presented = extract_presented_times(conv.last_body or {}, sess)
    conv._assert(
        bool(presented),
        f"turn {conv.turn}: expected presented availability times, got {presented!r}",
    )
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: service_id must be {PREMIUM_SERVICE!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    fp = session_fingerprint(sess)
    conv._assert(bool(fp), f"turn {conv.turn}: expected availability fingerprint")

    _STATE.clear()
    _STATE["search_count"] = call_count
    _STATE["fingerprint"] = fp
    _STATE["presented_times"] = presented
    _STATE["search_date"] = presented_date
    _STATE["service_id"] = PREMIUM_SERVICE


def _assert_unrecognized_after_undated_availability(conv, booking, availability) -> None:
    """Unrecognized after undated search must recover — never silently re-SEARCH."""
    assert_understanding_everywhere(conv, _luma(conv), _UNRECOGNIZED)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))

    action = conv.plan.get("action")
    if action is None:
        action = (conv.outcome or {}).get("action")
    conv._assert(
        action in (None, "", False),
        f"turn {conv.turn}: expected action=None (no re-SEARCH), got {action!r}",
    )
    status = (conv.plan or {}).get("status") or (conv.outcome or {}).get("status")
    conv._assert(
        status == "READY",
        (
            f"turn {conv.turn}: expected READY recovery presentation after "
            f"unrecognized reply with open slots, got {status!r}"
        ),
    )
    # Observable recovery vs reshow discriminator (action_branch not on HTTP plan).
    plan = conv.plan or {}
    conv._assert(
        plan.get("availability_reshow") not in (True,),
        (
            f"turn {conv.turn}: recovery presentation must not set "
            f"availability_reshow, got {plan.get('availability_reshow')!r}"
        ),
    )

    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: service_id must remain {PREMIUM_SERVICE!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    presented_date = _presented_search_date(sess)
    conv._assert(
        presented_date == _STATE.get("search_date"),
        (
            f"turn {conv.turn}: effective availability date must stay "
            f"{_STATE.get('search_date')!r}, got {presented_date!r}"
        ),
    )
    fp = session_fingerprint(sess)
    conv._assert(
        fp == _STATE.get("fingerprint"),
        (
            f"turn {conv.turn}: availability fingerprint must remain stable, "
            f"got {fp!r} vs {_STATE.get('fingerprint')!r}"
        ),
    )
    presented = extract_presented_times(conv.last_body or {}, sess)
    baseline_times = _STATE.get("presented_times")
    if baseline_times:
        # Response may be recovery/clarify text — presented times stay in session.
        sess_presented = extract_presented_times({}, sess)
        conv._assert(
            sess_presented == baseline_times or presented == baseline_times,
            (
                f"turn {conv.turn}: presented availability must remain, "
                f"got response={presented!r} session={sess_presented!r} "
                f"vs {baseline_times!r}"
            ),
        )
    else:
        conv._assert(
            bool(extract_presented_times({}, sess) or presented),
            f"turn {conv.turn}: presented availability must remain available",
        )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected recovery response text, got {text!r}",
    )
    conv._assert(
        "understand" in lowered
        or "didn't" in lowered
        or "did not" in lowered
        or "catch" in lowered,
        f"turn {conv.turn}: must acknowledge unrecognized input, got {text!r}",
    )
    # Must not be a bare availability reshow that skips the acknowledgment.
    conv._assert(
        not (
            lowered.strip().startswith("here are the available times")
            and "understand" not in lowered
            and "didn't" not in lowered
            and "did not" not in lowered
            and "catch" not in lowered
        ),
        (
            f"turn {conv.turn}: availability reshow must not suppress recovery "
            f"acknowledgment, got {text!r}"
        ),
    )
    conv._assert(
        "time" in lowered
        or "available" in lowered
        or "booking" in lowered
        or "appointment" in lowered
        or "continue" in lowered
        or "works best" in lowered
        or "show more" in lowered
        or "next" in lowered
        or "previous" in lowered
        or "which" in lowered
        or "choose" in lowered,
        (
            f"turn {conv.turn}: after acknowledgment must guide back to existing "
            f"availability / time selection, got {text!r}"
        ),
    )
    for phrase in _CONFIRMATION_PHRASES:
        conv._assert(
            phrase not in text,
            f"turn {conv.turn}: confirmation phrase {phrase!r} must not appear",
        )


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
    if not presented:
        presented = extract_presented_times({}, sess)
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
    """Sticky same-service restatement after availability: still waiting on time.

    Repeated service is not a booking revision and not an attempt to answer
    ask_next=time. Stage 08 therefore clarifies for the outstanding ask (time)
    rather than READY+action=None (former dead planner state) or recovery.
    """
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))
    sess = conv.session() or {}
    conv._assert(
        session_service_id(sess) == PREMIUM_SERVICE,
        f"turn {conv.turn}: service_id expected {PREMIUM_SERVICE!r}",
    )
    plan = conv.plan or {}
    outcome = conv.outcome or {}
    status = plan.get("status") or outcome.get("status")
    conv._assert(
        status == "NEEDS_CLARIFICATION",
        (
            f"turn {conv.turn}: repeated service leaves ask_next=time open → "
            f"NEEDS_CLARIFICATION (not READY dead state), got {status!r}"
        ),
    )
    action = plan.get("action") if "action" in plan else outcome.get("action")
    conv._assert(
        action in (None, "", False),
        f"turn {conv.turn}: must not SEARCH or execute, got action={action!r}",
    )
    missing = (
        plan.get("missing_slots")
        or outcome.get("missing_slots")
        or sess.get("missing_slots")
        or []
    )
    if not isinstance(missing, list):
        missing = []
    conv._assert(
        "time" in missing,
        (
            f"turn {conv.turn}: outstanding ask remains time after service "
            f"restatement, got missing_slots={missing!r}"
        ),
    )
    awaiting = plan.get("awaiting") or outcome.get("awaiting") or sess.get("awaiting")
    conv._assert(
        awaiting in ("time", None, ""),
        (
            f"turn {conv.turn}: planner should await time (or leave awaiting unset "
            f"with missing time), got awaiting={awaiting!r}"
        ),
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
    """Gibberish under open confirmation: no commit; pending preserved; re-ask."""
    from core.tests.e2e.framework.conversation import assert_confirmation_pending

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

    status = conv.outcome.get("status")
    conv._assert(
        status == "AWAITING_CONFIRMATION",
        f"turn {conv.turn}: expected AWAITING_CONFIRMATION, got {status!r}",
    )
    assert_confirmation_pending(conv)

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected recovery text during confirmation, got {text!r}",
    )
    conv._assert(
        "understand" in lowered
        or "yes" in lowered
        or "confirm" in lowered
        or "go ahead" in lowered
        or "didn't" in lowered
        or "did not" in lowered,
        f"turn {conv.turn}: expected confirmation recovery guidance, got {text!r}",
    )
    conv._assert(
        "booked" not in lowered,
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
        "Unrecognized after undated first availability does not re-search",
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
                response_text_present=True,
            ),
            after=_assert_undated_first_availability,
        ),
        Turn(
            "oooo",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                action=None,
                missing_slots=["time"],
                response_text_present=True,
            ),
            after=_assert_unrecognized_after_undated_availability,
        ),
        fixture="scripted_turn_understanding",
        tags=["understanding", "recovery", "fingerprint", "regression", "undated"],
        id="unrecognized-after-undated-first-availability-does-not-research",
    )
)


_register(
    Scenario(
        "Service explicitly repeated remains understood",
        *_common_setup_turns(after_premium=_after_premium_capture_and_understood),
        Turn(
            "premium",
            Expect(
                # Same-value service restatement is not a revision and does not
                # answer ask_next=time → Stage 08 clarifies (not dead READY).
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                action=None,
                missing_slots=["time"],
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
