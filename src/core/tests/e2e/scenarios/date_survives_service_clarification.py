"""E2E regression: explicit date must survive service clarification.

Protects against losing a pre-collected date (July 21) when the user only
answers the service clarification, so SEARCH_AVAILABILITY / discovery still
run for July 21 — not a different default date such as July 20.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.conversation import (
    Expect,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _resolve_search_date,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
)
from core.workflows.availability.fingerprint import compute_availability_fingerprint

SCENARIOS: List[Scenario] = []

JULY_20 = "2026-07-20"
JULY_21 = "2026-07-21"


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def date_survives_service_clarification_scripts() -> Dict[str, Any]:
    return {
        "book me for a haircut on july 21": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id", "time"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
            "facts": {"dates": [JULY_21]},
            "date_proposal": {"mode": "single_day", "start": JULY_21},
        },
        # Service only — date must come from session, not this turn's NLU payload.
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
    }


def _slot_dates(starts: List[str]) -> List[str]:
    return [s[:10] for s in starts if isinstance(s, str) and len(s) >= 10]


def _session_fingerprint(session: Dict[str, Any]) -> Any:
    availability = session.get("availability")
    if isinstance(availability, dict) and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    return session.get("availability_fingerprint")


def _assert_date_retained_awaiting_service(conv, booking, availability) -> None:
    """Turn 1: July 21 kept; service still missing; no availability search yet."""
    assert_no_booking_execution(conv, booking)
    conv._assert(
        availability.get_service_availability.call_count == 0,
        (
            f"turn {conv.turn}: clarification must not SEARCH_AVAILABILITY yet, "
            f"got call_count={availability.get_service_availability.call_count}"
        ),
    )
    conv.assert_date_proposal(JULY_21)

    sess = conv.session() or {}
    facts = sess.get("facts") if isinstance(sess.get("facts"), dict) else {}
    dates = facts.get("dates") if isinstance(facts, dict) else None
    if isinstance(dates, list) and dates:
        retained = _resolve_search_date(str(dates[0]))
        conv._assert(
            retained == JULY_21,
            f"turn {conv.turn}: facts.dates must retain July 21, got {dates!r}",
        )

    missing = sess.get("missing_slots") or conv.plan.get("missing_slots") or []
    conv._assert(
        "service_id" in list(missing),
        f"turn {conv.turn}: expected service_id still missing, got {missing!r}",
    )
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    conv._assert(
        not slots.get("service_id"),
        f"turn {conv.turn}: service must not be filled before clarification, "
        f"got {slots.get('service_id')!r}",
    )


def _assert_search_july_21_after_premium(conv, booking, availability) -> None:
    """Turn 2: Premium fills service only; search/discovery/render use July 21."""
    assert_no_booking_execution(conv, booking)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == 1,
        (
            f"turn {conv.turn}: expected exactly one SEARCH_AVAILABILITY, "
            f"got call_count={call_count}"
        ),
    )

    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched_service = kwargs.get("service_id")
    searched_date = _resolve_search_date(kwargs.get("date"))
    conv._assert(
        searched_service == PREMIUM_SERVICE,
        (
            f"turn {conv.turn}: availability provider must receive Premium, "
            f"got {searched_service!r}"
        ),
    )
    conv._assert(
        searched_date == JULY_21,
        (
            f"turn {conv.turn}: availability provider must receive July 21, "
            f"got {searched_date!r}"
        ),
    )
    conv._assert(
        searched_date != JULY_20,
        f"turn {conv.turn}: availability must not default to July 20",
    )

    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    effective_service = planning_slots.get("service_id") or slots.get("service_id")
    conv._assert(
        effective_service == PREMIUM_SERVICE,
        f"turn {conv.turn}: session service must be Premium, got {effective_service!r}",
    )
    conv.assert_date_proposal(JULY_21)

    presented_payload = sess.get("presented_availability") or {}
    if not isinstance(presented_payload, dict):
        presented_payload = {}
    search_date = presented_payload.get("search_date")
    conv._assert(
        bool(search_date),
        f"turn {conv.turn}: expected presented_availability.search_date",
    )
    conv._assert(
        _resolve_search_date(str(search_date)) == JULY_21,
        (
            f"turn {conv.turn}: presented search_date must be {JULY_21}, "
            f"got {search_date!r}"
        ),
    )

    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(
        bool(presented),
        f"turn {conv.turn}: expected discovery-presented July 21 window",
    )
    for date in _slot_dates(presented):
        conv._assert(
            date == JULY_21,
            f"turn {conv.turn}: presented offer must be July 21, got {date!r}",
        )
    conv._assert(
        JULY_20 not in "".join(presented),
        f"turn {conv.turn}: presented window must not include July 20: {presented!r}",
    )

    cache = sess.get("last_execution_result") or {}
    if isinstance(cache, dict):
        cache_slots = cache.get("slots") or []
        for slot in cache_slots:
            if not isinstance(slot, dict):
                continue
            start = str(slot.get("starts_at") or slot.get("start") or "")
            if start:
                conv._assert(
                    start.startswith(JULY_21),
                    f"turn {conv.turn}: discovery cache slot must be July 21, got {start!r}",
                )

    fp = _session_fingerprint(sess)
    expected_fp = compute_availability_fingerprint(
        {
            "organization_id": ORG_ID,
            "service_id": PREMIUM_SERVICE,
            "date": JULY_21,
        }
    )
    conv._assert(bool(fp), f"turn {conv.turn}: expected availability fingerprint")
    conv._assert(
        fp == expected_fp,
        f"turn {conv.turn}: fingerprint mismatch for July 21: {fp!r} != {expected_fp!r}",
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        isinstance(text, str) and bool(text.strip()),
        f"turn {conv.turn}: expected rendered availability text, got {text!r}",
    )
    conv._assert(
        "july 21" in lowered or JULY_21 in text,
        f"turn {conv.turn}: rendered response must state July 21, got {text!r}",
    )
    conv._assert(
        "july 20" not in lowered and JULY_20 not in text,
        f"turn {conv.turn}: rendered response must not mention July 20, got {text!r}",
    )


_register(
    Scenario(
        "Explicit date survives service clarification",
        Turn(
            "Book me for a haircut on July 21",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=JULY_21,
                confirmation=None,
            ),
            after=_assert_date_retained_awaiting_service,
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
                date_proposal=JULY_21,
                response_text_present=True,
                confirmation=None,
            ),
            after=_assert_search_july_21_after_premium,
        ),
        fixture="scripted",
        tags=["booking", "clarification", "availability", "regression", "discovery"],
        id="explicit-date-survives-service-clarification",
    )
)
