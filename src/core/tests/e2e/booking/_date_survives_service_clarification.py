"""E2E regression: explicit date must survive service clarification.

Protects against losing a pre-collected date when the user only answers the
service clarification, so SEARCH_AVAILABILITY / discovery still run for the
stated day — not a different default such as "today".
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.tests.e2e.framework.conversation import (
    Expect,
    FROZEN_TIME,
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
JULY_23 = "2026-07-23"
TODAY = FROZEN_TIME.strftime("%Y-%m-%d")


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario

def _slot_dates(starts: List[str]) -> List[str]:
    return [s[:10] for s in starts if isinstance(s, str) and len(s) >= 10]


def _session_fingerprint(session: Dict[str, Any]) -> Any:
    availability = session.get("availability")
    if isinstance(availability, dict) and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    from core.workflows.availability.presentation import availability_fingerprint_from_session
    return availability_fingerprint_from_session(session)


def _session_temporal(session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    temporal = session.get("temporal")
    if isinstance(temporal, dict):
        return temporal
    planning = session.get("planning")
    if isinstance(planning, dict):
        nested = planning.get("temporal")
        if isinstance(nested, dict):
            return nested
    return None


def _assert_temporal_start_date(conv, expected_date: str) -> None:
    sess = conv.session() or {}
    temporal = _session_temporal(sess)
    conv._assert(
        isinstance(temporal, dict),
        f"turn {conv.turn}: expected persisted Temporal, got {temporal!r}",
    )
    start_date = _resolve_search_date(str((temporal or {}).get("start_date") or ""))
    conv._assert(
        start_date == expected_date,
        (
            f"turn {conv.turn}: Temporal.start_date must be {expected_date!r}, "
            f"got {start_date!r} from {temporal!r}"
        ),
    )


def _assert_date_retained_awaiting_service(
    expected_date: str,
    *,
    wrong_defaults: Optional[List[str]] = None,
) -> Callable:
    wrong = list(wrong_defaults or [])

    def _assert(conv, booking, availability) -> None:
        """Turn 1: stated date kept; service still missing; no availability search yet."""
        assert_no_booking_execution(conv, booking)
        conv._assert(
            availability.get_service_availability.call_count == 0,
            (
                f"turn {conv.turn}: clarification must not SEARCH_AVAILABILITY yet, "
                f"got call_count={availability.get_service_availability.call_count}"
            ),
        )
        conv.assert_date_proposal(expected_date)
        _assert_temporal_start_date(conv, expected_date)

        sess = conv.session() or {}
        facts = sess.get("facts") if isinstance(sess.get("facts"), dict) else {}
        dates = facts.get("dates") if isinstance(facts, dict) else None
        if isinstance(dates, list) and dates:
            retained = _resolve_search_date(str(dates[0]))
            conv._assert(
                retained == expected_date,
                (
                    f"turn {conv.turn}: facts.dates must retain {expected_date}, "
                    f"got {dates!r}"
                ),
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
        for wrong_date in wrong:
            conv._assert(
                expected_date != wrong_date,
                f"turn {conv.turn}: expected date must not equal {wrong_date!r}",
            )

    return _assert


def _assert_search_after_premium(
    expected_date: str,
    *,
    wrong_defaults: Optional[List[str]] = None,
    date_phrases: Optional[List[str]] = None,
) -> Callable:
    wrong = list(wrong_defaults or [])
    phrases = list(date_phrases or [])

    def _assert(conv, booking, availability) -> None:
        """Turn 2: Premium fills service only; search/discovery/render use stated date."""
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
            searched_date == expected_date,
            (
                f"turn {conv.turn}: availability provider must receive {expected_date}, "
                f"got {searched_date!r}"
            ),
        )
        for wrong_date in wrong:
            conv._assert(
                searched_date != wrong_date,
                (
                    f"turn {conv.turn}: availability must not default to "
                    f"{wrong_date}, got {searched_date!r}"
                ),
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
        conv.assert_date_proposal(expected_date)
        _assert_temporal_start_date(conv, expected_date)

        from core.workflows.availability.presentation import presented_availability_from_session
        presented_payload = presented_availability_from_session(sess) or {}
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
                f"turn {conv.turn}: presented search_date must be {expected_date}, "
                f"got {search_date!r}"
            ),
        )

        presented = extract_presented_times(conv.last_body, sess)
        conv._assert(
            bool(presented),
            f"turn {conv.turn}: expected discovery-presented {expected_date} window",
        )
        for date in _slot_dates(presented):
            conv._assert(
                date == expected_date,
                f"turn {conv.turn}: presented offer must be {expected_date}, got {date!r}",
            )
        for wrong_date in wrong:
            conv._assert(
                wrong_date not in "".join(presented),
                (
                    f"turn {conv.turn}: presented window must not include "
                    f"{wrong_date}: {presented!r}"
                ),
            )

        from core.workflows.availability.presentation import availability_cache_from_session
        cache = availability_cache_from_session(sess) or {}
        if isinstance(cache, dict):
            cache_slots = cache.get("slots") or []
            for slot in cache_slots:
                if not isinstance(slot, dict):
                    continue
                start = str(slot.get("starts_at") or slot.get("start") or "")
                if start:
                    conv._assert(
                        start.startswith(expected_date),
                        (
                            f"turn {conv.turn}: discovery cache slot must be "
                            f"{expected_date}, got {start!r}"
                        ),
                    )

        fp = _session_fingerprint(sess)
        expected_fp = compute_availability_fingerprint(
            {
                "organization_id": ORG_ID,
                "service_id": PREMIUM_SERVICE,
                "date": expected_date,
            }
        )
        conv._assert(bool(fp), f"turn {conv.turn}: expected availability fingerprint")
        conv._assert(
            fp == expected_fp,
            (
                f"turn {conv.turn}: fingerprint mismatch for {expected_date}: "
                f"{fp!r} != {expected_fp!r}"
            ),
        )

        text = _response_text(conv.last_body or {})
        lowered = text.lower()
        conv._assert(
            isinstance(text, str) and bool(text.strip()),
            f"turn {conv.turn}: expected rendered availability text, got {text!r}",
        )
        phrase_ok = expected_date in text or any(p.lower() in lowered for p in phrases)
        conv._assert(
            phrase_ok,
            (
                f"turn {conv.turn}: rendered response must state {expected_date} "
                f"(phrases={phrases!r}), got {text!r}"
            ),
        )
        for wrong_date in wrong:
            wrong_phrase = wrong_date  # ISO always checked
            conv._assert(
                wrong_phrase not in text,
                (
                    f"turn {conv.turn}: rendered response must not mention "
                    f"{wrong_date}, got {text!r}"
                ),
            )

    return _assert


_assert_date_retained_awaiting_service_july_21 = _assert_date_retained_awaiting_service(
    JULY_21,
    wrong_defaults=[JULY_20],
)
_assert_search_july_21_after_premium = _assert_search_after_premium(
    JULY_21,
    wrong_defaults=[JULY_20],
    date_phrases=["July 21"],
)
_assert_date_retained_awaiting_service_july_23 = _assert_date_retained_awaiting_service(
    JULY_23,
    wrong_defaults=[TODAY, JULY_20, JULY_21],
)
_assert_search_july_23_after_premium = _assert_search_after_premium(
    JULY_23,
    wrong_defaults=[TODAY, JULY_20, JULY_21],
    date_phrases=["July 23", "23rd july", "23rd July"],
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
            after=_assert_date_retained_awaiting_service_july_21,
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


_register(
    Scenario(
        "July 23 survives service clarification",
        Turn(
            "book me haircut on 23rd july",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "time"],
                date_proposal=JULY_23,
                confirmation=None,
            ),
            after=_assert_date_retained_awaiting_service_july_23,
        ),
        Turn(
            "premium haircut",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution="availability",
                has_availability_slots=True,
                date_proposal=JULY_23,
                response_text_present=True,
                confirmation=None,
            ),
            after=_assert_search_july_23_after_premium,
        ),
        fixture="scripted",
        tags=[
            "booking",
            "clarification",
            "availability",
            "regression",
            "discovery",
            "temporal",
        ],
        id="july-23-survives-service-clarification",
    )
)
