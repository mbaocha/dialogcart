"""E2E scenario: browse exhaustion must not poison a subsequent date search.

Regression: after pagination was exhausted for one date, a request for a
different date incorrectly redisplayed the prior window instead of running
SEARCH_AVAILABILITY.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.tests.e2e.framework.conversation import (
    Expect,
    FIRST_AVAILABLE_DATE,
    ORG_ID,
    PREMIUM_SERVICE,
    Scenario,
    Turn,
    _normalize_explicit_search_date,
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

def _slot_dates(starts: List[str]) -> List[str]:
    return [s[:10] for s in starts if isinstance(s, str) and len(s) >= 10]


def _session_fingerprint(session: Dict[str, Any]) -> Any:
    availability = session.get("availability")
    if isinstance(availability, dict) and availability.get("fingerprint") is not None:
        return availability.get("fingerprint")
    from core.workflows.availability.presentation import availability_fingerprint_from_session
    return availability_fingerprint_from_session(session)


def _presented_search_date(session: Dict[str, Any]) -> Optional[str]:
    from core.workflows.availability.presentation import presented_availability_from_session, availability_cache_from_session
    presented = presented_availability_from_session(session)
    if isinstance(presented, dict) and presented.get("search_date"):
        return _resolve_search_date(str(presented.get("search_date")))
    cache = availability_cache_from_session(session)
    if isinstance(cache, dict) and cache.get("search_date"):
        return _resolve_search_date(str(cache.get("search_date")))
    return None


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
    requested = _normalize_explicit_search_date(kwargs.get("date"))
    conv._assert(
        requested == JULY_20,
        f"turn {conv.turn}: expected explicit search date {JULY_20!r}, got {requested!r}",
    )

    sess = conv.session() or {}
    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(bool(presented), f"turn {conv.turn}: expected presented July 20 window")
    for date in _slot_dates(presented):
        conv._assert(
            date == JULY_20,
            f"turn {conv.turn}: presented slot date {date!r} must be {JULY_20}",
        )
    presented_date = _presented_search_date(sess)
    conv._assert(
        presented_date == JULY_20,
        f"turn {conv.turn}: presented.search_date expected {JULY_20}, got {presented_date!r}",
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
    _STATE["search_date"] = JULY_20


def _assert_undated_first_available_search(conv, booking, availability) -> None:
    """Production parity: date=None → backend day → presented.search_date from offers."""
    assert_no_booking_execution(conv, booking)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count >= 1,
        f"turn {conv.turn}: expected SEARCH_AVAILABILITY, got call_count={call_count}",
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    requested = kwargs.get("date")
    conv._assert(
        _normalize_explicit_search_date(requested) is None,
        f"turn {conv.turn}: undated search must omit date (got {requested!r})",
    )

    sess = conv.session() or {}
    date_proposal = sess.get("date_proposal")
    proposal_start = (
        date_proposal.get("start")
        if isinstance(date_proposal, dict)
        else None
    )
    conv._assert(
        not proposal_start,
        f"turn {conv.turn}: undated search must not invent date_proposal, "
        f"got {date_proposal!r}",
    )

    presented_date = _presented_search_date(sess)
    conv._assert(
        presented_date == FIRST_AVAILABLE_DATE,
        (
            f"turn {conv.turn}: presented.search_date must equal backend first-"
            f"available day {FIRST_AVAILABLE_DATE!r}, got {presented_date!r}"
        ),
    )

    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(bool(presented), f"turn {conv.turn}: expected presented offers")
    for date in _slot_dates(presented):
        conv._assert(
            date == FIRST_AVAILABLE_DATE,
            (
                f"turn {conv.turn}: offer date {date!r} must match "
                f"presented.search_date {FIRST_AVAILABLE_DATE!r}"
            ),
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
        }
    )
    conv._assert(bool(fp), f"turn {conv.turn}: expected undated search fingerprint")
    conv._assert(
        fp == expected_fp,
        f"turn {conv.turn}: undated fingerprint mismatch: {fp!r} != {expected_fp!r}",
    )

    _STATE.clear()
    _STATE["search_count"] = call_count
    _STATE["fingerprint"] = fp
    _STATE["presented"] = list(presented)
    _STATE["search_date"] = FIRST_AVAILABLE_DATE


def _assert_browse_more_over_cache(conv, booking, availability) -> None:
    """``Are there more times?`` after undated search is browse — never SEARCH.

    With a small cached result set the first browse_next often exhausts; the last
    successful presentation window must still be shown. Fingerprint and day stay
    on the undated first-available search.
    """
    assert_no_booking_execution(conv, booking)
    baseline = _STATE.get("search_count", 0)
    call_count = availability.get_service_availability.call_count
    conv._assert(
        call_count == baseline,
        (
            f"turn {conv.turn}: browse must not SEARCH_AVAILABILITY "
            f"(baseline={baseline}, got={call_count})"
        ),
    )

    sess = conv.session() or {}
    expected_day = _STATE.get("search_date") or FIRST_AVAILABLE_DATE
    presented = extract_presented_times(conv.last_body, sess)
    conv._assert(
        bool(presented),
        f"turn {conv.turn}: browse must still present availability, got {presented!r}",
    )
    for date in _slot_dates(presented):
        conv._assert(
            date == expected_day,
            (
                f"turn {conv.turn}: presented offer date {date!r} must remain "
                f"{expected_day!r}"
            ),
        )
    presented_date = _presented_search_date(sess)
    conv._assert(
        presented_date == expected_day,
        (
            f"turn {conv.turn}: presented.search_date must remain {expected_day!r}, "
            f"got {presented_date!r}"
        ),
    )
    conv._assert(
        _session_fingerprint(sess) == _STATE.get("fingerprint"),
        f"turn {conv.turn}: fingerprint must not change on browse",
    )

    text = _response_text(conv.last_body or {})
    conv._assert(
        bool(text.strip()),
        f"turn {conv.turn}: expected browse response text, got {text!r}",
    )

    pagination = (conv.outcome or {}).get("availability_pagination") or (
        conv.last_body or {}
    ).get("availability_pagination") or {}
    page_index = _presentation_page_index(sess)
    if isinstance(pagination, dict) and pagination.get("exhausted") is True:
        conv._assert(
            pagination.get("direction") == "next",
            f"turn {conv.turn}: exhausted browse direction must be next, got {pagination!r}",
        )
        conv._assert(
            _response_indicates_no_more_times(text),
            f"turn {conv.turn}: expected no-more-times wording, got {text!r}",
        )
    else:
        prior = _STATE.get("presented") or []
        conv._assert(
            page_index >= 1 or (prior and presented != prior),
            (
                f"turn {conv.turn}: expected browse to advance the presentation "
                f"window (page_index={page_index!r}, prior={prior!r}, "
                f"presented={presented!r}, pagination={pagination!r})"
            ),
        )

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
    expected_day = _STATE.get("search_date") or JULY_20
    presented = extract_presented_times(conv.last_body, sess)
    for date in _slot_dates(presented):
        conv._assert(
            date == expected_day,
            (
                f"turn {conv.turn}: exhausted browse must keep {expected_day} window, "
                f"got {date!r}"
            ),
        )
    presented_date = _presented_search_date(sess)
    conv._assert(
        presented_date == expected_day,
        (
            f"turn {conv.turn}: presented.search_date must remain {expected_day}, "
            f"got {presented_date!r}"
        ),
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
    searched = _normalize_explicit_search_date(kwargs.get("date"))
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

    from core.workflows.availability.presentation import presented_availability_from_session
    presented_payload = presented_availability_from_session(sess) or {}
    search_date = presented_payload.get("search_date")
    if search_date:
        conv._assert(
            _resolve_search_date(str(search_date)) == JULY_21,
            f"turn {conv.turn}: presented search_date must be {JULY_21}, got {search_date!r}",
        )

    from core.workflows.availability.presentation import availability_cache_from_session
    cache = availability_cache_from_session(sess) or {}
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
            # Date must be stated on turn 1 so recorded NLU carries July 20
            # through service clarification (same contract as confirmation_interruption).
            "Book me a haircut on july 20",
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


_register(
    Scenario(
        "undated search uses backend first-available day then browse exhausts",
        Turn(
            "Book me haircut",
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
            ),
            after=_assert_undated_first_available_search,
        ),
        Turn(
            # Browse next over trusted cache — never SEARCH_AVAILABILITY.
            "Are there more times?",
            Expect(
                action=None,
                intent="CREATE_APPOINTMENT",
            ),
            after=_assert_browse_more_over_cache,
        ),
        Turn(
            "Are there more times?",
            Expect(
                action=None,
                intent="CREATE_APPOINTMENT",
            ),
            after=_assert_browse_exhausted_no_search,
        ),
        fixture="scripted_browse_exhaustion_search",
        tags=["browse", "availability", "regression", "discovery", "undated"],
        id="undated-first-available-then-browse-exhaustion",
    )
)
