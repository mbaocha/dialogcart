"""E2E scenarios: OFF_TOPIC as a first-class non-durable intent."""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.confirmation_interruption import (
    assert_availability_rendered,
    assert_no_search_since,
)
from core.tests.e2e.framework.conversation import (
    Expect,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
)
from core.tests.e2e.framework.turn_understanding import (
    assert_understanding_everywhere,
    session_fingerprint,
    session_service_id,
)

SCENARIOS: List[Scenario] = []
_STATE: Dict[str, Any] = {}

_UNDERSTOOD = "UNDERSTOOD"
_UNRECOGNIZED = "UNRECOGNIZED_INPUT"
_JULY_21 = "2026-07-21"

_RECOVERY_PHRASES = (
    "didn't understand",
    "did not understand",
    "i didn't understand",
    "sorry, i didn't understand",
)

_SHOW_MORE_MARKER = "show more"


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _luma(conv) -> Any:
    luma = getattr(conv, "luma_client", None)
    conv._assert(luma is not None, f"turn {conv.turn}: luma_client missing on conversation")
    return luma

def _assert_off_topic_digression(conv, booking, _availability) -> None:
    assert_understanding_everywhere(conv, _luma(conv), _UNDERSTOOD)
    assert_no_booking_execution(conv, booking)

    outcome = conv.outcome or {}
    conv._assert(
        outcome.get("status") == "OFF_TOPIC",
        f"turn {conv.turn}: expected OFF_TOPIC, got {outcome.get('status')!r}",
    )
    conv._assert(
        outcome.get("active_handler") in (None, "", False),
        (
            f"turn {conv.turn}: expected no active_handler for Core OFF_TOPIC, "
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


def _assert_cold_start_off_topic(conv, booking, availability) -> None:
    _assert_off_topic_digression(conv, booking, availability)
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
    _assert_off_topic_digression(conv, booking, availability)

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
                response_status="OFF_TOPIC",
                response_text_present=True,
            ),
            after=_assert_cold_start_off_topic,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "regression"],
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
                response_status="OFF_TOPIC",
                # Session intent stays durable booking; outcome is OFF_TOPIC.
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_mid_booking_off_topic_preserves,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "booking", "regression"],
        id="off-topic-mid-booking-preserves",
    )
)


def _mentions_show_more(text: str) -> bool:
    return _SHOW_MORE_MARKER in (text or "").lower()


def _capture_availability_after_premium(conv, booking, availability) -> None:
    """Baseline after times are shown: search, presentation, show-more guidance."""
    _capture_booking_baseline(conv, booking, availability)
    assert_availability_rendered(conv)
    sess = conv.session() or {}
    text = _response_text(conv.last_body or {})
    _STATE["search_count"] = availability.get_service_availability.call_count
    _STATE["fingerprint"] = session_fingerprint(sess)
    _STATE["presented_times"] = extract_presented_times(conv.last_body or {}, sess)
    _STATE["availability_text"] = text
    _STATE["availability_mentions_show_more"] = _mentions_show_more(text)


def _assert_off_topic_resume_after_times(conv, booking, availability) -> None:
    """OFF_TOPIC with factual answer must resume time selection without re-search."""
    _assert_mid_booking_off_topic_preserves(conv, booking, availability)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        "lion" in lowered or "egg" in lowered or "no" in lowered or "mammal" in lowered,
        (
            f"turn {conv.turn}: expected a factual answer about the lion question, "
            f"got {text!r}"
        ),
    )
    conv._assert(
        "time" in lowered or "continue" in lowered or "booking" in lowered,
        f"turn {conv.turn}: OFF_TOPIC reply must resume time selection, got {text!r}",
    )


def _assert_resume_show_more_consistency(conv, booking, availability) -> None:
    """Resumed OFF_TOPIC prompt must match original availability show-more guidance."""
    _assert_off_topic_resume_after_times(conv, booking, availability)
    text = _response_text(conv.last_body or {})
    resume_mentions = _mentions_show_more(text)
    original_mentions = bool(_STATE.get("availability_mentions_show_more"))
    conv._assert(
        resume_mentions == original_mentions,
        (
            f"turn {conv.turn}: resume show-more guidance must match original "
            f"availability prompt "
            f"(original={original_mentions}, resume={resume_mentions}). "
            f"availability={_STATE.get('availability_text')!r} resume={text!r}"
        ),
    )


def _assert_unrecognized_after_off_topic(conv, booking, availability) -> None:
    """Unrecognized input after OFF_TOPIC must acknowledge, then resume — not discard."""
    assert_understanding_everywhere(conv, _luma(conv), _UNRECOGNIZED)
    assert_no_booking_execution(conv, booking)
    assert_no_search_since(conv, availability, _STATE.get("search_count", 0))

    sess = conv.session() or {}
    conv._assert(
        sess.get("intent_name") == _STATE.get("intent"),
        (
            f"turn {conv.turn}: booking must not restart "
            f"(intent {_STATE.get('intent')!r} -> {sess.get('intent_name')!r})"
        ),
    )
    expected_service = _STATE.get("service_id") or PREMIUM_SERVICE
    conv._assert(
        session_service_id(sess) == expected_service,
        (
            f"turn {conv.turn}: service_id must remain {expected_service!r}, "
            f"got {session_service_id(sess)!r}"
        ),
    )
    conv.assert_date_proposal(_JULY_21)
    fp = session_fingerprint(sess)
    conv._assert(
        fp == _STATE.get("fingerprint"),
        (
            f"turn {conv.turn}: availability fingerprint must be unchanged "
            f"({_STATE.get('fingerprint')!r} -> {fp!r})"
        ),
    )
    presented = extract_presented_times(conv.last_body or {}, sess)
    baseline_times = _STATE.get("presented_times")
    if baseline_times:
        conv._assert(
            presented == baseline_times,
            (
                f"turn {conv.turn}: presented times must not be regenerated "
                f"({baseline_times!r} -> {presented!r})"
            ),
        )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: unrecognized interruption must not be silent, got {text!r}",
    )
    conv._assert(
        any(phrase in lowered for phrase in _RECOVERY_PHRASES)
        or "understand" in lowered,
        (
            f"turn {conv.turn}: must acknowledge unrecognized input first, "
            f"got {text!r}"
        ),
    )
    conv._assert(
        "time" in lowered
        or "booking" in lowered
        or "continue" in lowered
        or "returning" in lowered
        or "works best" in lowered,
        (
            f"turn {conv.turn}: after acknowledgment must resume pending time "
            f"selection, got {text!r}"
        ),
    )


_register(
    Scenario(
        "OFF_TOPIC then unrecognized resumes booking",
        Turn(
            "book me a haircut on July 21",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=_JULY_21,
                response_text_present=True,
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
                response_text_present=True,
            ),
            after=_capture_availability_after_premium,
        ),
        Turn(
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_off_topic_resume_after_times,
        ),
        Turn(
            "aaaa",
            Expect(
                planner="READY",
                intent="CREATE_APPOINTMENT",
                missing_slots=["time"],
                session_slots={"service_id": PREMIUM_SERVICE},
                date_proposal=_JULY_21,
                action=None,
                response_text_present=True,
            ),
            after=_assert_unrecognized_after_off_topic,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "recovery", "interruption", "regression"],
        id="off-topic-then-unrecognized-resumes",
    )
)


_register(
    Scenario(
        "OFF_TOPIC resume matches availability show-more guidance",
        Turn(
            "book haircut on July 21",
            Expect(
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=_JULY_21,
                response_text_present=True,
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
                response_text_present=True,
            ),
            after=_capture_availability_after_premium,
        ),
        Turn(
            "Does a lion lay eggs?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                response_text_present=True,
            ),
            after=_assert_resume_show_more_consistency,
        ),
        fixture="scripted_off_topic",
        tags=["off_topic", "resume", "availability", "regression"],
        id="off-topic-resume-show-more-consistency",
    )
)
