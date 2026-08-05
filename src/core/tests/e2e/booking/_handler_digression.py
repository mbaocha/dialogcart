"""E2E: HANDLER_DELEGATED FAQ digression preserves booking and resumes commit."""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.conversation import (
    Expect,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _response_text,
)
from core.tests.e2e.booking._helpers import _assert_booking_created, _assert_no_booking

SCENARIOS: List[Scenario] = []
_STATE: Dict[str, Any] = {}


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


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
