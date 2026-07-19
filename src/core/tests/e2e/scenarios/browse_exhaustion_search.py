"""E2E scenario: browse exhaustion must not poison a subsequent date search.

Regression: after pagination was exhausted for one date, a request for a
different date incorrectly redisplayed the prior window instead of running
SEARCH_AVAILABILITY.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.tests.e2e.framework.confirmation_interruption import (
    availability_date_change_script,
)
from core.tests.e2e.framework.conversation import (
    Expect,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _presentation_page_index,
    _resolve_search_date,
    _response_indicates_no_more_times,
    _response_text,
    assert_no_booking_execution,
    extract_presented_times,
)
from core.workflows.availability.fingerprint import compute_availability_fingerprint

SCENARIOS: List[Scenario] = []
_STATE: Dict[str, Any] = {}

JULY_20 = "2026-07-20"
JULY_21 = "2026-07-21"


def _register(scenario: Scenario) -> Scenario:
    SCENARIOS.append(scenario)
    return scenario


def browse_exhaustion_search_scripts() -> Dict[str, Any]:
    return {
        "book me a haircut": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "needs_clarification": True,
            "missing_slots": ["service_id"],
            "service_candidates": [
                {"text": PREMIUM_SERVICE},
                {"text": "flexi haircut + prunning"},
            ],
        },
        "premium": {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "dates": [JULY_20],
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "date_proposal": {"mode": "single_day", "start": JULY_20},
            "missing_slots": ["time"],
        },
        "are there more times for july 20?": {
            "success": True,
            "intent": {"name": "AVAILABILITY"},
            "operation": "browse_next",
            "facts": {
                "service_id": PREMIUM_SERVICE,
                "slots": {"service_id": PREMIUM_SERVICE},
            },
            "slots": {"service_id": PREMIUM_SERVICE},
            "missing_slots": ["time"],
        },
        "show dates for july 21": availability_date_change_script(JULY_21),
    }


def _slot_dates(starts: List[str]) -> List[str]:
    return [s[:10] for s in starts if isinstance(s, str) and len(s) >= 10]


def _session_fingerprint(session: Dict[str, Any]) -> Any:
    availability = session.get("availability")
    if isinstance(availability, dict) and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    return session.get("availability_fingerprint")


def _assert_first_search_july_20(conv, booking, availability) -> None:
    assert_no_booking_execution(conv, booking)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count >= 1,
        f"turn {conv.turn}: expected first SEARCH_AVAILABILITY, "
        f"got call_count={call_count}",
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = _resolve_search_date(kwargs.get("date"))
    conv._assert(
        searched == JULY_20,
        f"turn {conv.turn}: expected search date {JULY_20!r}, got {searched!r}",
    )

    sess = conv.session() or {}
    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(bool(presented), f"turn {conv.turn}: expected presented July 20 window")
    for date in _slot_dates(presented):
        conv._assert(
            date == JULY_20,
            f"turn {conv.turn}: presented slot date {date!r} must be {JULY_20}",
        )
    conv._assert(
        _presentation_page_index(sess) == 0,
        f"turn {conv.turn}: expected page_index 0 after first search",
    )

    fp = _session_fingerprint(sess)
    expected_fp = compute_availability_fingerprint(
        {
            "organization_id": ORG_ID,
            "service_id": PREMIUM_SERVICE,
            "date": JULY_20,
        }
    )
    conv._assert(bool(fp), f"turn {conv.turn}: expected availability fingerprint")
    conv._assert(
        fp == expected_fp,
        f"turn {conv.turn}: fingerprint mismatch for July 20: {fp!r} != {expected_fp!r}",
    )

    _STATE.clear()
    _STATE["search_count"] = call_count
    _STATE["fingerprint"] = fp
    _STATE["presented"] = list(presented)


def _assert_browse_exhausted_no_search(conv, booking, availability) -> None:
    assert_no_booking_execution(conv, booking)
    baseline = _STATE.get("search_count", 0)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == baseline,
        (
            f"turn {conv.turn}: browse exhaustion must not search again "
            f"(baseline={baseline}, got={call_count})"
        ),
    )

    pagination = (conv.outcome or {}).get("availability_pagination") or (
        conv.last_body or {}
    ).get("availability_pagination") or {}
    conv._assert(
        isinstance(pagination, dict) and pagination.get("exhausted") is True,
        f"turn {conv.turn}: expected exhausted browse, got {pagination!r}",
    )
    conv._assert(
        pagination.get("direction") == "next",
        f"turn {conv.turn}: expected browse direction next, got {pagination!r}",
    )
    text = _response_text(conv.last_body or {})
    conv._assert(
        _response_indicates_no_more_times(text),
        f"turn {conv.turn}: expected no-more-times response, got {text!r}",
    )

    sess = conv.session() or {}
    presented = extract_presented_times(conv.last_body, sess)
    for date in _slot_dates(presented):
        conv._assert(
            date == JULY_20,
            f"turn {conv.turn}: exhausted browse must keep July 20 window, got {date!r}",
        )
    conv._assert(
        _session_fingerprint(sess) == _STATE.get("fingerprint"),
        f"turn {conv.turn}: fingerprint must not change on exhausted browse",
    )


def _assert_july_21_search_not_poisoned(conv, booking, availability) -> None:
    assert_no_booking_execution(conv, booking)
    baseline = _STATE.get("search_count", 0)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == baseline + 1,
        (
            f"turn {conv.turn}: expected exactly one new SEARCH_AVAILABILITY "
            f"(baseline={baseline}, got={call_count})"
        ),
    )

    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = _resolve_search_date(kwargs.get("date"))
    conv._assert(
        searched == JULY_21,
        f"turn {conv.turn}: expected search date {JULY_21!r}, got {searched!r}",
    )

    sess = conv.session() or {}
    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(bool(presented), f"turn {conv.turn}: expected new July 21 presented window")
    for date in _slot_dates(presented):
        conv._assert(
            date == JULY_21,
            f"turn {conv.turn}: presented offer must be July 21, got {date!r}",
        )
    conv._assert(
        JULY_20 not in "".join(presented),
        f"turn {conv.turn}: presented window must not retain July 20 starts: {presented!r}",
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    conv._assert(
        "2026-07-20" not in text and "july 20" not in lowered,
        f"turn {conv.turn}: response must not contain July 20 availability, got {text!r}",
    )

    presented_payload = sess.get("presented_availability") or {}
    search_date = presented_payload.get("search_date")
    if search_date:
        conv._assert(
            _resolve_search_date(str(search_date)) == JULY_21,
            f"turn {conv.turn}: presented search_date must be {JULY_21}, got {search_date!r}",
        )

    cache = sess.get("last_execution_result") or {}
    cache_slots = cache.get("slots") or []
    for slot in cache_slots:
        if not isinstance(slot, dict):
            continue
        start = str(slot.get("starts_at") or slot.get("start") or "")
        if start:
            conv._assert(
                start.startswith(JULY_21),
                f"turn {conv.turn}: cache slot must be July 21, got {start!r}",
            )

    new_fp = _session_fingerprint(sess)
    expected_fp = compute_availability_fingerprint(
        {
            "organization_id": ORG_ID,
            "service_id": PREMIUM_SERVICE,
            "date": JULY_21,
        }
    )
    conv._assert(bool(new_fp), f"turn {conv.turn}: expected new fingerprint after July 21 search")
    conv._assert(
        new_fp != _STATE.get("fingerprint"),
        f"turn {conv.turn}: fingerprint must change after new-date search",
    )
    conv._assert(
        new_fp == expected_fp,
        f"turn {conv.turn}: fingerprint mismatch for July 21: {new_fp!r} != {expected_fp!r}",
    )
    conv._assert(
        _presentation_page_index(sess) == 0,
        f"turn {conv.turn}: pagination must reset after new search, "
        f"page_index={_presentation_page_index(sess)}",
    )

    pagination = (conv.outcome or {}).get("availability_pagination") or (
        conv.last_body or {}
    ).get("availability_pagination") or {}
    if isinstance(pagination, dict) and pagination:
        conv._assert(
            pagination.get("exhausted") is not True,
            f"turn {conv.turn}: new search must discard exhausted browse state, "
            f"got {pagination!r}",
        )


_register(
    Scenario(
        "browse exhaustion then July 21 search",
        Turn(
            "Book me a haircut",
            Expect(
                response_status="NEEDS_CLARIFICATION",
                intent="CREATE_APPOINTMENT",
            ),
        ),
        Turn(
            "Premium",
            Expect(
                action="SEARCH_AVAILABILITY",
                session_slots={"service_id": PREMIUM_SERVICE},
                execution_type="availability",
                has_availability_slots=True,
                date_proposal_start=JULY_20,
            ),
            after=_assert_first_search_july_20,
        ),
        Turn(
            "Are there more times for July 20?",
            Expect(
                action=None,
                intent="CREATE_APPOINTMENT",
            ),
            after=_assert_browse_exhausted_no_search,
        ),
        Turn(
            "Show dates for July 21",
            Expect(
                action="SEARCH_AVAILABILITY",
                execution_type="availability",
                has_availability_slots=True,
                date_proposal_start=JULY_21,
            ),
            after=_assert_july_21_search_not_poisoned,
        ),
        fixture="scripted_browse_exhaustion_search",
        tags=["browse", "availability", "regression", "discovery"],
        id="browse-exhaustion-must-not-poison-subsequent-search",
    )
)
