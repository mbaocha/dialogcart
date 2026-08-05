"""E2E regression: cold-start dated AVAILABILITY must clarify service.

Protects against Stage 01 non-durable early exit exposing ``NON_DURABLE_INTENT``
when the user asks for slots without naming a service.
"""

from __future__ import annotations

from typing import List

from core.tests.e2e.framework.conversation import (
    Expect,
    Scenario,
    Turn,
    _plan_view,
    _response_text,
    assert_no_booking_execution,
)

SCENARIOS: List[Scenario] = []

JULY_24 = "2026-07-24"

_INTERNAL_STATUS_MARKERS = (
    "NON_DURABLE_INTENT",
    "[NON_DURABLE_INTENT]",
    "no text — try a booking request",
    "(no text — try a booking request)",
    "[NEEDS_CLARIFICATION]",
)


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def _assert_cold_start_asks_for_service(conv, booking, availability) -> None:
    """Cold dated availability: clarify service; never leak planner status codes."""
    assert_no_booking_execution(conv, booking)
    conv._assert(
        availability.get_service_availability.call_count == 0,
        (
            f"turn {conv.turn}: clarification must not SEARCH_AVAILABILITY yet, "
            f"got call_count={availability.get_service_availability.call_count}"
        ),
    )

    plan = _plan_view(conv.outcome or {}, conv.last_body)
    planner_status = plan.get("status")
    conv._assert(
        planner_status != "NON_DURABLE_INTENT",
        (
            f"turn {conv.turn}: planner must not return NON_DURABLE_INTENT, "
            f"got {planner_status!r}"
        ),
    )
    conv._assert(
        planner_status == "NEEDS_CLARIFICATION",
        (
            f"turn {conv.turn}: expected planner NEEDS_CLARIFICATION, "
            f"got {planner_status!r}"
        ),
    )

    sess = conv.session()
    conv._assert(
        isinstance(sess, dict) and bool(sess),
        f"turn {conv.turn}: session must remain active after clarification, got {sess!r}",
    )
    missing = sess.get("missing_slots") or conv.outcome.get("missing_slots") or []
    conv._assert(
        "service_id" in list(missing),
        f"turn {conv.turn}: expected service_id still missing, got {missing!r}",
    )
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    conv._assert(
        not slots.get("service_id"),
        (
            f"turn {conv.turn}: service must not be filled before clarification, "
            f"got {slots.get('service_id')!r}"
        ),
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected conversational clarification text, got {text!r}",
    )
    for marker in _INTERNAL_STATUS_MARKERS:
        conv._assert(
            marker not in text,
            (
                f"turn {conv.turn}: internal planner status {marker!r} "
                f"must not be exposed to the user, got {text!r}"
            ),
        )
    conv._assert(
        "service" in lowered,
        (
            f"turn {conv.turn}: clarification must request a service, "
            f"got {text!r}"
        ),
    )


_register(
    Scenario(
        "Cold-start availability asks for service",
        Turn(
            "show slots for july 24",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                date_proposal=JULY_24,
                response_text_present=True,
                confirmation=None,
            ),
            after=_assert_cold_start_asks_for_service,
        ),
        fixture="scripted",
        tags=["availability", "clarification", "regression", "cold-start"],
        id="cold-start-availability-asks-for-service",
    )
)
