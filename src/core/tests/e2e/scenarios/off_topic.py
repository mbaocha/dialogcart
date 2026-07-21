"""E2E scenarios: OFF_TOPIC as a first-class non-durable intent."""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.conversation import (
    Expect,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _response_text,
    assert_no_booking_execution,
)
from core.tests.e2e.framework.turn_understanding import (
    assert_understanding_everywhere,
    session_service_id,
)

SCENARIOS: List[Scenario] = []
_STATE: Dict[str, Any] = {}

_UNDERSTOOD = "UNDERSTOOD"

_RECOVERY_PHRASES = (
    "didn't understand",
    "did not understand",
    "i didn't understand",
    "sorry, i didn't understand",
)


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _luma(conv) -> Any:
    luma = getattr(conv, "luma_client", None)
    conv._assert(luma is not None, f"turn {conv.turn}: luma_client missing on conversation")
    return luma


def off_topic_scripts() -> Dict[str, Any]:
    """Stage-2-shaped scripts for OFF_TOPIC e2e coverage."""
    return {
        "who is the president of nigeria?": {
            "success": True,
            "intent": {"name": "OFF_TOPIC", "confidence": 0.95},
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
            },
            "search_query": None,
            "off_topic_query": "Who is the president of Nigeria?",
            "service_term": None,
            "temporal": {"mode": "none", "confidence": 1.0},
        },
        "book haircut": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id", "date", "time"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
            "facts": {},
            "service_term": None,
            "temporal": {"mode": "none", "confidence": 1.0},
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
            "missing_slots": ["date", "time"],
            "temporal": {"mode": "none", "confidence": 1.0},
        },
    }


def _assert_off_topic_delegated(conv, booking, _availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)

    outcome = conv.outcome or {}
    conv._assert(
        outcome.get("status") == "HANDLER_DELEGATED",
        f"turn {conv.turn}: expected HANDLER_DELEGATED, got {outcome.get('status')!r}",
    )
    conv._assert(
        outcome.get("active_handler") == "off_topic",
        (
            f"turn {conv.turn}: expected active_handler='off_topic', "
            f"got {outcome.get('active_handler')!r}"
        ),
    )
    conv._assert(
        outcome.get("intent_name") == "OFF_TOPIC",
        f"turn {conv.turn}: outcome intent expected OFF_TOPIC, got {outcome.get('intent_name')!r}",
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(bool(text.strip()), f"turn {conv.turn}: expected OFF_TOPIC response text")
    for phrase in _RECOVERY_PHRASES:
        conv._assert(
            phrase not in lowered,
            f"turn {conv.turn}: recovery renderer must not run for OFF_TOPIC, got {text!r}",
        )
    conv._assert(
        "business" in lowered or "appointment" in lowered or "help" in lowered or "booking" in lowered,
        f"turn {conv.turn}: expected brief business redirect, got {text!r}",
    )


def _assert_cold_start_off_topic(conv, booking, availability) -> None:
    _assert_off_topic_delegated(conv, booking, availability)
    sess = conv.session() or {}
    # Non-durable digression must not invent a booking session intent.
    session_intent = sess.get("intent_name")
    conv._assert(
        session_intent in (None, "", "OFF_TOPIC"),
        f"turn {conv.turn}: cold-start must not create booking intent, got {session_intent!r}",
    )
    slots = sess.get("slots") or {}
    for key in ("service_id", "date", "time", "date_range"):
        conv._assert(
            slots.get(key) in (None, "", []),
            f"turn {conv.turn}: session slot {key!r} must be empty, got {slots.get(key)!r}",
        )


def _capture_booking_baseline(conv, _booking, _availability) -> None:
    sess = conv.session() or {}
    _STATE["intent"] = sess.get("intent_name")
    _STATE["slots"] = dict(sess.get("slots") or {})
    _STATE["confirmation"] = sess.get("confirmation_state")
    _STATE["date_proposal"] = sess.get("date_proposal")
    _STATE["time_proposal"] = sess.get("time_proposal")
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    _STATE["planning_slots"] = dict(planning.get("slots") or {})
    _STATE["service_id"] = session_service_id(sess)


def _assert_mid_booking_off_topic_preserves(conv, booking, availability) -> None:
    _assert_off_topic_delegated(conv, booking, availability)

    sess = conv.session() or {}
    conv._assert(
        sess.get("intent_name") == _STATE.get("intent"),
        (
            f"turn {conv.turn}: session intent must stay {_STATE.get('intent')!r}, "
            f"got {sess.get('intent_name')!r}"
        ),
    )
    conv._assert(
        dict(sess.get("slots") or {}) == _STATE.get("slots"),
        (
            f"turn {conv.turn}: session slots must be unchanged, "
            f"got {sess.get('slots')!r} vs {_STATE.get('slots')!r}"
        ),
    )
    conv._assert(
        sess.get("confirmation_state") == _STATE.get("confirmation"),
        (
            f"turn {conv.turn}: confirmation_state must stay {_STATE.get('confirmation')!r}, "
            f"got {sess.get('confirmation_state')!r}"
        ),
    )
    conv._assert(
        sess.get("date_proposal") == _STATE.get("date_proposal"),
        f"turn {conv.turn}: date_proposal must be unchanged",
    )
    conv._assert(
        sess.get("time_proposal") == _STATE.get("time_proposal"),
        f"turn {conv.turn}: time_proposal must be unchanged",
    )
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    conv._assert(
        dict(planning.get("slots") or {}) == _STATE.get("planning_slots"),
        f"turn {conv.turn}: planning.slots must be unchanged",
    )
    if _STATE.get("service_id"):
        conv._assert(
            session_service_id(sess) == _STATE.get("service_id"),
            (
                f"turn {conv.turn}: service_id must remain {_STATE.get('service_id')!r}, "
                f"got {session_service_id(sess)!r}"
            ),
        )

    text = _response_text(conv.last_body or {}).lower()
    conv._assert(
        "booking" in text or "continue" in text or "time" in text or "appointment" in text
        or "service" in text or "date" in text,
        f"turn {conv.turn}: mid-booking decline should guide back to booking, got {text!r}",
    )


_register(
    Scenario(
        "Cold start OFF_TOPIC",
        Turn(
            "Who is the president of Nigeria?",
            Expect(
                response_status="HANDLER_DELEGATED",
                response_text_present=True,
            ),
            after=_assert_cold_start_off_topic,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "handler", "regression"],
        id="off-topic-cold-start",
    )
)


_register(
    Scenario(
        "Mid-booking OFF_TOPIC preserves booking",
        Turn(
            "Book haircut",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                response_text_present=True,
            ),
        ),
        Turn(
            "Premium",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_capture_booking_baseline,
        ),
        Turn(
            "Who is the president of Nigeria?",
            Expect(
                response_status="HANDLER_DELEGATED",
                # Session intent stays durable booking; outcome is OFF_TOPIC.
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_mid_booking_off_topic_preserves,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "handler", "booking", "regression"],
        id="off-topic-mid-booking-preserves",
    )
)
