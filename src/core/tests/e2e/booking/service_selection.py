"""Booking E2E scenarios — service selection conversation state."""

# ============================================================
# Covered
#
# ✓ Valid
# ✓ Revision
# ✓ Interruptions
# ✓ Invalid
# ✓ Recovery
#
# TODO
#
# □ References
# ============================================================

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FIRST_AVAILABLE_DATE,
    FROZEN_TIME,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _confirmation_state,
    _normalize_explicit_search_date,
    _plan_view,
    _presentation_page_index,
    _resolve_search_date,
    _response_indicates_no_more_times,
    _response_text,
    assert_no_booking_execution,
    attach_commit_customer_identity,
    extract_presented_times,
)
from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session, save_session
from core.tests.e2e.booking import _helpers as _booking_helpers
from core.workflows.availability.fingerprint import compute_availability_fingerprint
from core.workflows.availability.presentation import (
    availability_fingerprint_from_session,
)

globals().update(
    {
        name: getattr(_booking_helpers, name)
        for name in getattr(_booking_helpers, "__all__", dir(_booking_helpers))
        if not name.startswith("__")
    }
)

SCENARIOS: List[Scenario] = []


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario

# ============================================================
# VALID RESPONSES
# ============================================================
JULY_24 = "2026-07-24"

_INTERNAL_STATUS_MARKERS = (
    "NON_DURABLE_INTENT",
    "[NON_DURABLE_INTENT]",
    "no text — try a booking request",
    "(no text — try a booking request)",
    "[NEEDS_CLARIFICATION]",
)




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
# ============================================================
# REFERENCE EXPRESSIONS
# ============================================================
# (no scenarios in this section yet)
# ============================================================
# REVISIONS
# ============================================================
_register(
    Scenario(
        "Service revision invalidates availability",
        Turn("book haircut"),
        Turn("premium"),
        Turn(
            "10am",
            Expect(
                response_status="AWAITING_CONFIRMATION",
                confirmation="pending",
                session_slots={"service_id": PREMIUM_SERVICE},
            ),
            after=_capture_searches,
        ),
        Turn(
            "rather book flexi haircut",
            Expect(
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                confirmation=None,
            ),
            after=_assert_service_revision,
        ),
        fixture="scripted_service_revision",
        tags=["booking", "invalidation"],
    )
)

_register(
    Scenario(
        "Availability service revision searches Flexi not Premium",
        Turn("book haircut"),
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
            after=_capture_searches_before_flexi,
        ),
        Turn(
            "show availability for flexi",
            Expect(
                response_status="succeeded",
                planner="READY",
                stage="AVAILABILITY",
                action="SEARCH_AVAILABILITY",
                intent="CREATE_APPOINTMENT",
                session_slots={"service_id": FLEXI_SERVICE},
                execution="availability",
                has_availability_slots=True,
                availability_invalidated=True,
            ),
            after=_assert_availability_searched_flexi,
        ),
        fixture="scripted_availability_service_revision",
        tags=["booking", "availability", "service-revision", "bug2"],
        id="availability-service-revision-flexi",
    )
)
# ============================================================
# INTERRUPTIONS
# ============================================================
_register(
    Scenario(
        "Off-topic during service clarification then premium",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
                response_text_present=True,
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "Who is the president of Nigeria?",
            Expect(
                response_status="OFF_TOPIC",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_no_search_yet,
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
                confirmation=None,
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted_off_topic",
        tags=["booking", "service-selection", "off_topic", "interruption"],
        id="off-topic-during-service-then-premium",
    )
)

_register(
    Scenario(
        "FAQ during service clarification then premium",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                planner="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
                response_text_present=True,
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "how much does a haircut cost?",
            Expect(
                response_status="HANDLER_DELEGATED",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_no_search_yet,
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
                confirmation=None,
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted_off_topic",
        tags=["booking", "service-selection", "faq", "interruption"],
        id="faq-during-service-then-premium",
    )
)
# ============================================================
# INVALID INPUT
# ============================================================
_register(
    Scenario(
        "Invalid during service clarification then premium",
        Turn(
            "book haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
            ),
            after=_assert_no_search_yet,
        ),
        Turn(
            "aaa",
            Expect(
                intent="CREATE_APPOINTMENT",
                missing_slots=["service_id", "date", "time"],
                confirmation=None,
                response_text_present=True,
            ),
            after=_assert_no_search_yet,
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
                confirmation=None,
            ),
            after=_assert_no_booking_single_search,
        ),
        fixture="scripted_off_topic",
        tags=["booking", "service-selection", "invalid", "recovery"],
        id="invalid-during-service-then-premium",
    )
)
# ============================================================
# RECOVERY
# ============================================================
JULY_20 = "2026-07-20"
JULY_21 = "2026-07-21"
JULY_23 = "2026-07-23"
TODAY = FROZEN_TIME.strftime("%Y-%m-%d")



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
        expected_item_id = _expected_search_catalog_item_id(PREMIUM_SERVICE)
        conv._assert(
            searched_service == expected_item_id,
            (
                f"turn {conv.turn}: availability provider must receive Premium "
                f"catalog item {expected_item_id!r}, got {searched_service!r}"
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
