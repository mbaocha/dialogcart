"""E2E scenarios: cross-domain OFF_TOPIC (non-booking cold start).

Booking-specific OFF_TOPIC interruptions live under
``core.tests.e2e.booking`` conversation-state modules.
"""

from __future__ import annotations

from typing import Any, List

from core.tests.e2e.framework.conversation import (
    Expect,
    Scenario,
    Turn,
    _response_text,
    assert_no_booking_execution,
)
from core.tests.e2e.framework.turn_understanding import (
    assert_understanding_everywhere,
)

SCENARIOS: List[Scenario] = []

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
