"""Shared helpers for booking E2E state scenarios."""


from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from core.adapters.errors import UpstreamError
from core.session.session_manager import get_session
from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.tests.e2e.framework.conversation import (
    Expect,
    FLEXI_SERVICE,
    FLEXI_SERVICE_ITEM_ID,
    FROZEN_TIME,
    PREMIUM_SERVICE,
    PREMIUM_SERVICE_ITEM_ID,
    Scenario,
    Turn,
    _confirmation_state,
    _resolve_search_date,
    _response_text,
    attach_commit_customer_identity,
)

def _sess_fp(sess):
    from core.workflows.availability.presentation import (
        availability_fingerprint_from_session,
    )

    return availability_fingerprint_from_session(sess)


def _sess_presented(sess):
    from core.workflows.availability.presentation import (
        presented_availability_from_session,
    )

    return presented_availability_from_session(sess)


def _sess_cache(sess):
    from core.workflows.availability.presentation import availability_cache_from_session

    cache = availability_cache_from_session(sess)
    if isinstance(cache, dict):
        return cache
    if isinstance(sess, dict):
        availability = sess.get("availability")
        if isinstance(availability, dict):
            nested = availability.get("cache")
            if isinstance(nested, dict) and isinstance(
                nested.get("search_result"), dict
            ):
                return nested["search_result"]
        legacy = sess.get("last_execution_result")
        if isinstance(legacy, dict):
            return legacy
    return {}


TARGET_DATE = _resolve_search_date(None)
# Relative "tomorrow" against the shared E2E clock (not TARGET_DATE = frozen+2).
_TOMORROW = (FROZEN_TIME + timedelta(days=1)).strftime("%Y-%m-%d")




def _assert_booking_created(conv, booking_client, _availability=None) -> None:
    assert booking_client.create_booking.called, (
        f"turn {conv.turn}: expected create_booking after confirmation"
    )
    assert booking_client.create_booking.call_count == 1, (
        f"turn {conv.turn}: expected exactly one CONFIRM_APPOINTMENT/"
        f"create_booking, got {booking_client.create_booking.call_count}"
    )
    call = booking_client.create_booking.call_args
    kwargs = call.kwargs if call else {}
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    session_customer_id = sess.get("customer_id")
    assert payload_customer_id and int(payload_customer_id) > 0, (
        f"turn {conv.turn}: booking payload must use resolved commerce "
        f"customer_id, got {payload_customer_id!r}"
    )
    assert session_customer_id == payload_customer_id, (
        f"turn {conv.turn}: session customer_id {session_customer_id!r} "
        f"must match booking payload {payload_customer_id!r}"
    )
    assert str(payload_customer_id) != str(conv.user_id), (
        f"turn {conv.turn}: customer_id must not be chat user_id "
        f"({conv.user_id!r})"
    )
    slots = sess.get("slots") or {}
    booking = sess.get("booking") or {}
    booking_id = booking.get("booking_id") or slots.get("booking_id")
    booking_code = booking.get("booking_code") or slots.get("booking_code")
    assert booking_id, (
        f"turn {conv.turn}: expected booking_id in session "
        f"(booking={booking!r}, slots_keys={list(slots.keys())})"
    )
    assert booking_code, (
        f"turn {conv.turn}: expected booking_code in session "
        f"(booking={booking!r}, slots_keys={list(slots.keys())})"
    )
    planning = sess.get("planning") or {}
    assert sess.get("confirmation_state") is None, (
        f"turn {conv.turn}: successful commit must consume confirmation"
    )
    assert planning.get("intent_name") is None, (
        f"turn {conv.turn}: successful commit must close CREATE_APPOINTMENT intent"
    )
    assert planning.get("slots") == {}, (
        f"turn {conv.turn}: successful commit must clear planning slots, "
        f"got {planning.get('slots')!r}"
    )
    assert planning.get("missing_slots") == [], (
        f"turn {conv.turn}: successful commit must clear missing slots, "
        f"got {planning.get('missing_slots')!r}"
    )
    assert _sess_fp(sess) is None, (
        f"turn {conv.turn}: successful commit must clear availability fingerprint"
    )
    availability = sess.get("availability") or {}
    cache = availability.get("cache") or {}
    assert cache.get("search_result") is None, (
        f"turn {conv.turn}: successful commit must clear cached availability"
    )


def _assert_no_booking(conv, booking_client, _availability=None) -> None:
    assert not booking_client.create_booking.called, (
        f"turn {conv.turn}: booking should not have been created"
    )


def _assert_booking_created_with_item_id(expected_item_id: int):
    """Return a successful-booking assertion that also checks the API item ID."""

    def assert_booking(conv, booking_client, availability=None) -> None:
        _assert_booking_created(conv, booking_client, availability)
        call = booking_client.create_booking.call_args
        kwargs = call.kwargs if call else {}
        assert kwargs.get("item_id") == expected_item_id, (
            f"turn {conv.turn}: expected booking item_id {expected_item_id}, "
            f"got {kwargs.get('item_id')!r}"
        )

    return assert_booking


def _assert_booking_created_with_exact_payload(
    *,
    expected_item_id: int,
    expected_service_id: Any,
    expected_date: str,
    expected_time: str,
    abandoned_values=(),
):
    """Assert the final booking request uses only the revised booking values."""

    def assert_booking(conv, booking_client, availability=None) -> None:
        _assert_booking_created(conv, booking_client, availability)
        call = booking_client.create_booking.call_args
        kwargs = call.kwargs if call else {}
        assert kwargs.get("item_id") == expected_item_id, (
            f"turn {conv.turn}: expected service {expected_service_id!r} "
            f"as item_id {expected_item_id}, "
            f"got {kwargs.get('item_id')!r}"
        )
        start_time = str(kwargs.get("start_time") or "")
        expected_prefix = f"{expected_date}T{expected_time}"
        assert start_time.startswith(expected_prefix), (
            f"turn {conv.turn}: expected booking start {expected_prefix!r}, "
            f"got {start_time!r}"
        )
        for abandoned in abandoned_values:
            assert str(abandoned) not in start_time, (
                f"turn {conv.turn}: abandoned value {abandoned!r} reached "
                f"create_booking start_time={start_time!r}"
            )
    return assert_booking


def _assert_no_booking_and_date_kept(conv, booking_client, _availability=None) -> None:
    _assert_no_booking(conv, booking_client)
    sess = conv.session() or {}
    assert (sess.get("slots") or {}).get("date"), (
        f"turn {conv.turn}: expected date retained after rejection"
    )


# Per-scenario scratch pads (runner is single-threaded / sequential per test).
_SEARCH_STATE: Dict[str, Any] = {}


def _capture_searches(_conv, _booking, availability) -> None:
    _SEARCH_STATE["count"] = availability.get_service_availability.call_count


def _assert_no_extra_search(conv, booking, availability) -> None:
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count == _SEARCH_STATE.get(
        "count")
    last = _sess_cache(conv.session() or {})
    assert last.get("type") == "availability"
    assert len(last.get("slots") or []) >= 1
    missing = (conv.session() or {}).get("missing_slots") or []
    assert "service_id" not in missing


def _assert_unavailable_time_mismatch(conv, booking, availability) -> None:
    """12pm did not bind against presented offers; post-bind mismatch ran instead."""
    _assert_no_extra_search(conv, booking, availability)

    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("time") in (None, ""), (
        f"turn {conv.turn}: try_bind must not persist time for unavailable 12pm, "
        f"got {slots.get('time')!r}"
    )
    assert slots.get("date") in (None, ""), (
        f"turn {conv.turn}: date must not bind on mismatch, got {slots.get('date')!r}"
    )

    time_match = (
        conv.plan.get("time_match_outcome")
        or conv.outcome.get("time_match_outcome")
        or (conv.outcome.get("facts") or {}).get("time_match_outcome")
        or (sess.get("time_match_outcome"))
    )
    assert time_match == TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: expected TIME_MATCH_MISMATCH, got {time_match!r}"
    )

    time_resolution = (
        sess.get("time_resolution")
        or conv.outcome.get("time_resolution")
        or (conv.outcome.get("facts") or {}).get("time_resolution")
    )
    assert isinstance(time_resolution, dict), (
        f"turn {conv.turn}: expected time_resolution from apply_post_bind_time_resolution, "
        f"got {time_resolution!r}"
    )
    assert time_resolution.get("outcome") == TIME_MATCH_MISMATCH
    alternatives = time_resolution.get("alternatives")
    assert isinstance(alternatives, list) and len(alternatives) >= 1, (
        f"turn {conv.turn}: expected mismatch alternatives, got {alternatives!r}"
    )

    text = conv.last_body.get("text")
    assert isinstance(text, str) and text.strip(), (
        f"turn {conv.turn}: expected conversational mismatch text, got {text!r}"
    )


def _assert_authoritative_time_absent(conv, *superseded_times: str) -> None:
    """Assert a revision left no authoritative projection of the old time."""
    sess = conv.session() or {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    slot_maps = [sess.get("slots"), planning.get("slots")]
    for slots in slot_maps:
        if isinstance(slots, dict):
            assert slots.get("time") in (None, ""), (
                f"turn {conv.turn}: stale slot time survived: {slots.get('time')!r}"
            )
            assert not slots.get("datetime_range"), (
                f"turn {conv.turn}: stale slot datetime survived: "
                f"{slots.get('datetime_range')!r}"
            )
    assert not sess.get("resolved_datetime_range")
    assert sess.get("time_proposal") in (None, {})
    assert sess.get("time_constraint") in (None, {})
    proposals = planning.get("proposals") if isinstance(planning.get("proposals"), dict) else {}
    assert proposals.get("time") in (None, {}), (
        f"turn {conv.turn}: stale planning.proposals.time survived: "
        f"{proposals.get('time')!r}"
    )
    assert _confirmation_state(sess) is None, (
        f"turn {conv.turn}: revision must invalidate pending confirmation"
    )
    facts = sess.get("facts") if isinstance(sess.get("facts"), dict) else {}
    for key in ("times", "time_proposal", "time_constraint", "resolved_datetime_range"):
        assert facts.get(key) in (None, [], {}), (
            f"turn {conv.turn}: stale facts.{key} survived: {facts.get(key)!r}"
        )
    temporal = sess.get("temporal") if isinstance(sess.get("temporal"), dict) else {}
    executable_temporal_keys = (
        "start_time",
        "end_time",
        "start_time_expression",
        "end_time_expression",
    )
    for key in executable_temporal_keys:
        assert temporal.get(key) in (None, ""), (
            f"turn {conv.turn}: stale temporal.{key} survived: {temporal.get(key)!r}"
        )
    # temporal.expression / *_date_expression are raw NLU metadata, not
    # executable booking criteria (search fingerprint, confirm text, payload).
    executable_temporal = {key: temporal.get(key) for key in executable_temporal_keys}
    authoritative = repr(
        {
            "slots": slot_maps,
            "time_proposal": sess.get("time_proposal"),
            "time_constraint": sess.get("time_constraint"),
            "planning_time_proposal": proposals.get("time"),
            "facts": {key: facts.get(key) for key in ("times", "time_proposal", "time_constraint")},
            "temporal": executable_temporal,
        }
    )
    for stale in superseded_times:
        assert stale not in authoritative, (
            f"turn {conv.turn}: superseded time {stale!r} survived in "
            f"authoritative temporal state: {authoritative}"
        )


def _assert_authoritative_time_replaced(
    conv, expected_time: str, *superseded_times: str
) -> None:
    """Assert all current booking projections agree on the replacement time."""
    sess = conv.session() or {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    slots = planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    if not slots:
        slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    facts = sess.get("facts") if isinstance(sess.get("facts"), dict) else {}
    temporal = sess.get("temporal") if isinstance(sess.get("temporal"), dict) else {}
    proposal = sess.get("time_proposal")
    projections = {
        "slot": slots.get("time"),
        "proposal": proposal,
        "facts_times": facts.get("times"),
        "facts_proposal": facts.get("time_proposal"),
        "temporal_start": temporal.get("start_time"),
    }
    assert expected_time in repr(projections), (
        f"turn {conv.turn}: replacement {expected_time!r} missing from "
        f"authoritative projections: {projections!r}"
    )
    for stale in superseded_times:
        assert stale not in repr(projections), (
            f"turn {conv.turn}: superseded time {stale!r} survived replacement: "
            f"{projections!r}"
        )


def _assert_service_revision(conv, booking, availability) -> None:
    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("time") in (None, ""), (
        f"expected prior time discarded, got {slots.get('time')!r}"
    )
    assert slots.get("date") in (None, ""), (
        f"expected prior date discarded on service revision, got {slots.get('date')!r}"
    )
    # This scenario owns invalidation only. Whether a new search is immediately
    # eligible is covered by availability-service-revision-flexi.
    assert not sess.get("resolved_datetime_range"), (
        f"expected resolved_datetime_range cleared, "
        f"got {sess.get('resolved_datetime_range')!r}"
    )
    _assert_authoritative_time_absent(conv, "10:00")
    _assert_no_booking(conv, booking)


def _assert_date_revision(_conv, booking, availability) -> None:
    assert availability.get_service_availability.call_count > _SEARCH_STATE.get(
        "count", 0)
    _assert_no_booking(_conv, booking)


def _assert_date_revision_without_stale_time(conv, booking, availability) -> None:
    _assert_date_revision(conv, booking, availability)
    _assert_authoritative_time_absent(conv, "10:00", "11:00")


def _assert_booking_called(conv, booking_client, _availability=None) -> None:
    assert booking_client.create_booking.called, (
        f"turn {conv.turn}: expected booking after yes"
    )
    call = booking_client.create_booking.call_args
    kwargs = call.kwargs if call else {}
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    assert payload_customer_id and int(payload_customer_id) > 0, (
        f"turn {conv.turn}: booking payload must use resolved customer_id, "
        f"got {payload_customer_id!r}"
    )
    assert sess.get("customer_id") == payload_customer_id, (
        f"turn {conv.turn}: session/payload customer_id mismatch "
        f"{sess.get('customer_id')!r} vs {payload_customer_id!r}"
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reject then revise time
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unavailable time â†’ TIME_MATCH_MISMATCH
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Service revision invalidates availability
# ---------------------------------------------------------------------------

_SEARCH_FLEXI_STATE: Dict[str, Any] = {}


def _capture_searches_before_flexi(_conv, _booking, availability) -> None:
    _SEARCH_FLEXI_STATE["count"] = availability.get_service_availability.call_count


def _assert_availability_searched_flexi(conv, booking, availability) -> None:
    """Bug 2: AvailabilityClient must search Flexi, not the prior Premium session service."""
    baseline = _SEARCH_FLEXI_STATE.get("count", 0)
    assert availability.get_service_availability.call_count == baseline + 1, (
        f"turn {conv.turn}: expected exactly one SEARCH_AVAILABILITY for flexi, "
        f"got {availability.get_service_availability.call_count - baseline}"
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    searched = kwargs.get("service_id")
    expected_item_id = _expected_search_catalog_item_id(FLEXI_SERVICE)
    assert searched == expected_item_id, (
        f"turn {conv.turn}: AvailabilityClient must receive Flexi catalog item "
        f"{expected_item_id!r}, got {searched!r} (Premium overwrite is Bug 2)"
    )
    assert searched != PREMIUM_SERVICE_ITEM_ID
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    effective = planning_slots.get("service_id") or slots.get("service_id")
    assert effective == FLEXI_SERVICE, (
        f"turn {conv.turn}: session service_id expected Flexi, got {effective!r}"
    )
    assert not booking.create_booking.called


# ---------------------------------------------------------------------------
# Date revision invalidates availability
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scripted time-resolution scenarios
# ---------------------------------------------------------------------------

_TIME_STATE: Dict[str, Any] = {}


def _assert_no_search_yet(_conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 0


def _assert_exact_search_side_effects(conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 1
    call = availability.get_service_availability.call_args
    assert call.kwargs.get("date") == TARGET_DATE
    conv.assert_availability_search_without_time_constraint(availability)
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert sess.get("resolved_datetime_range")
    last = _sess_cache(sess)
    assert last.get("type") == "availability"


def _assert_exact_search_recorded_tomorrow(conv, booking, availability) -> None:
    """Exact-match side effects with search date = shared-clock 'tomorrow'."""
    assert availability.get_service_availability.call_count == 1
    call = availability.get_service_availability.call_args
    assert call.kwargs.get("date") == _TOMORROW, (
        f"expected shared-clock tomorrow {_TOMORROW!r}, "
        f"got {call.kwargs.get('date')!r}"
    )
    conv.assert_availability_search_without_time_constraint(availability)
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert sess.get("resolved_datetime_range")
    last = _sess_cache(sess)
    assert last.get("type") == "availability"


def _assert_no_booking_single_search(conv, booking, availability) -> None:
    assert not booking.create_booking.called
    assert availability.get_service_availability.call_count == 1


def _capture_search_count(_c, _b, availability) -> None:
    _TIME_STATE["searches"] = availability.get_service_availability.call_count


def _assert_mismatch_side_effects(conv, booking, availability) -> None:
    assert availability.get_service_availability.call_count == _TIME_STATE.get(
        "searches", 0) + 1
    assert not booking.create_booking.called
    last = _sess_cache(conv.session() or {})
    assert last.get("type") == "availability"
    assert conv.plan.get("status") != "READY" or conv.plan.get(
        "action") is not None


def _assert_empty_slots(conv, _booking, availability) -> None:
    assert availability.get_service_availability.call_count == 1
    last = _sess_cache(conv.session() or {})
    assert last.get("slots") == []


def _assert_proposals_persisted(conv, _booking, availability) -> None:
    sess = conv.session() or {}
    assert isinstance(sess.get("time_proposal"), dict)
    assert isinstance(sess.get("date_proposal"), dict)
    assert isinstance(_sess_cache(sess), dict) and bool(_sess_cache(sess))
    assert availability.get_service_availability.call_count == 1


# ---------------------------------------------------------------------------
# Post-availability time selection regressions
# ---------------------------------------------------------------------------
# Post-availability time selection (RecordingLumaClient /resolve replay)
# ---------------------------------------------------------------------------
#
# Shared start: book premium â†’ availability presented (includes 1:30 PM).
# NLU bodies come from recorded production /resolve â€” not handwritten scripts.
#
_POST_AVAIL_SEARCH: Dict[str, Any] = {}
_POST_AVAIL_BASELINE: Dict[str, Any] = {}
_DOTTED_TIME_BIND: Dict[str, Any] = {}


def _capture_post_availability_baseline(conv, _booking, availability) -> None:
    """After SEARCH_AVAILABILITY: remember search count, service, date, offers."""
    _POST_AVAIL_SEARCH["count"] = availability.get_service_availability.call_count
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    _POST_AVAIL_BASELINE["service_id"] = (
        planning_slots.get("service_id") or slots.get("service_id")
    )
    _POST_AVAIL_BASELINE["date"] = planning_slots.get("date") or slots.get("date")
    if not _POST_AVAIL_BASELINE["date"]:
        proposal = sess.get("date_proposal") or planning.get("date_proposal")
        if isinstance(proposal, dict):
            _POST_AVAIL_BASELINE["date"] = proposal.get("start") or proposal.get("value")
        elif proposal:
            _POST_AVAIL_BASELINE["date"] = proposal
    presented = _sess_presented(sess)
    times: List[str] = []
    if isinstance(presented, dict):
        raw_times = presented.get("times") or []
        if isinstance(raw_times, list):
            times = [str(t) for t in raw_times if t]
        if not times:
            for slot in presented.get("slots") or []:
                if isinstance(slot, dict):
                    start = slot.get("starts_at") or slot.get("start")
                    if isinstance(start, str) and "T" in start:
                        times.append(start.split("T", 1)[1][:5])
    _POST_AVAIL_BASELINE["presented_times"] = times
    text = _response_text(conv.last_body or {})
    _POST_AVAIL_BASELINE["availability_text"] = text


def _assert_no_extra_availability_search(conv, availability) -> None:
    baseline = _POST_AVAIL_SEARCH.get("count", 1)
    assert availability.get_service_availability.call_count == baseline, (
        f"turn {conv.turn}: must not trigger another availability search "
        f"(baseline={baseline}, "
        f"got={availability.get_service_availability.call_count})"
    )


def _assert_booking_context_preserved(conv) -> None:
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    service_id = planning_slots.get("service_id") or slots.get("service_id")
    date_value = planning_slots.get("date") or slots.get("date")
    if not date_value:
        proposal = sess.get("date_proposal") or planning.get("date_proposal")
        if isinstance(proposal, dict):
            date_value = proposal.get("start") or proposal.get("value")
        elif proposal:
            date_value = proposal

    expected_service = _POST_AVAIL_BASELINE.get("service_id") or PREMIUM_SERVICE
    assert service_id == expected_service, (
        f"turn {conv.turn}: service_id must remain {expected_service!r}, "
        f"got {service_id!r}"
    )
    expected_date = _POST_AVAIL_BASELINE.get("date")
    if expected_date:
        assert date_value and str(expected_date)[:10] in str(date_value), (
            f"turn {conv.turn}: date must remain {expected_date!r}, got {date_value!r}"
        )
    else:
        assert date_value, (
            f"turn {conv.turn}: date must remain selected, got {date_value!r}"
        )


def _assert_invalid_time_explains_and_reshows(conv, booking, availability) -> None:
    """xxxxx after availability â€” clarify without redundant SEARCH."""
    _assert_production_xxxxx_after_availability(conv, booking, availability)
    _assert_no_extra_availability_search(conv, availability)


def _turn_understanding(conv) -> Any:
    for source in (conv.outcome or {}, conv.plan or {}, conv.last_body or {}):
        if not isinstance(source, dict):
            continue
        turn = source.get("turn")
        if isinstance(turn, dict) and turn.get("understanding"):
            return turn.get("understanding")
        nested = source.get("plan")
        if isinstance(nested, dict):
            turn = nested.get("turn")
            if isinstance(turn, dict) and turn.get("understanding"):
                return turn.get("understanding")
        outcome = source.get("outcome")
        if isinstance(outcome, dict):
            turn = outcome.get("turn")
            if isinstance(turn, dict) and turn.get("understanding"):
                return turn.get("understanding")
    return None


def _assert_production_xxxxx_after_availability(conv, booking, availability) -> None:
    """Unrecognized / unusable time after offers â†’ recovery presentation, no SEARCH."""
    _assert_no_booking(conv, booking)
    _assert_booking_context_preserved(conv)

    understanding = _turn_understanding(conv)
    assert understanding == "UNRECOGNIZED_INPUT", (
        f"turn {conv.turn}: expected turn.understanding=UNRECOGNIZED_INPUT "
        f"from recorded /resolve, got {understanding!r}"
    )

    sess = conv.session() or {}
    confirmation = _confirmation_state(sess)
    assert confirmation in (None, "", False), (
        f"turn {conv.turn}: must not enter confirmation, got {confirmation!r}"
    )

    plan = conv.plan or {}
    outcome = conv.outcome or {}
    action = plan.get("action") if "action" in plan else outcome.get("action")
    assert action in (None, "", False), (
        f"turn {conv.turn}: cached offers are authoritative â€” must not SEARCH, "
        f"got action={action!r}"
    )
    status = plan.get("status") or outcome.get("status")
    assert status == "READY", (
        f"turn {conv.turn}: expected READY recovery presentation for unrecognized "
        f"reply, got status={status!r}"
    )
    # Observable recovery vs reshow discriminator (action_branch not on HTTP plan).
    assert plan.get("availability_reshow") not in (True,), (
        f"turn {conv.turn}: recovery presentation must not set availability_reshow, "
        f"got plan.availability_reshow={plan.get('availability_reshow')!r}"
    )
    missing = (
        plan.get("missing_slots")
        or outcome.get("missing_slots")
        or sess.get("missing_slots")
        or []
    )
    assert "time" in (missing if isinstance(missing, list) else []), (
        f"turn {conv.turn}: expected missing time, got {missing!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    assert text.strip(), (
        f"turn {conv.turn}: expected recovery text, got {text!r}"
    )
    assert (
        "understand" in lowered
        or "didn't" in lowered
        or "did not" in lowered
        or "catch" in lowered
    ), (
        f"turn {conv.turn}: must acknowledge unrecognized input, got {text!r}"
    )
    ask_ok = (
        "time" in lowered
        or "available" in lowered
        or "which" in lowered
        or "choose" in lowered
        or "continue" in lowered
    )
    assert ask_ok, (
        f"turn {conv.turn}: expected guidance back to time selection, got {text!r}"
    )
    # Must not be a bare availability reshow that skips acknowledgment.
    # Repeating offered times after the acknowledgement is allowed.
    assert not (
        lowered.strip().startswith("here are the available times")
        and "understand" not in lowered
        and "didn't" not in lowered
        and "did not" not in lowered
        and "catch" not in lowered
    ), (
        f"turn {conv.turn}: availability reshow must not suppress recovery, got {text!r}"
    )
    sess_presented = _sess_presented(sess)
    assert isinstance(sess_presented, dict) and (
        sess_presented.get("times") or sess_presented.get("slots")
    ), (
        f"turn {conv.turn}: presented_availability must remain without re-search"
    )


_MISMATCH_UNAVAILABLE_PHRASES = (
    "isn't available",
    "is not available",
    "not available",
    "that time isn't",
    "that time is not",
    "requested time",
)


def _time_match_from_conv(conv) -> Any:
    sess = conv.session() or {}
    return (
        conv.plan.get("time_match_outcome")
        or conv.outcome.get("time_match_outcome")
        or (conv.outcome.get("facts") or {}).get("time_match_outcome")
        or sess.get("time_match_outcome")
    )


def _assert_malformed_clock_not_mismatch(conv, booking, availability) -> None:
    """5.xyz after availability: clarify unusable input, not TIME_MATCH_MISMATCH.

    Recorded /resolve marks UNRECOGNIZED_INPUT with no clock facts. Cached offers
    remain authoritative â€” planner clarifies rather than re-SEARCH or mismatch.
    """
    _assert_production_xxxxx_after_availability(conv, booking, availability)
    _assert_no_extra_availability_search(conv, availability)
    time_match = _time_match_from_conv(conv)
    assert time_match != TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: malformed '5.xyz' must not be classified as "
        f"TIME_MATCH_MISMATCH (unavailable time), got {time_match!r}"
    )
    text = _response_text(conv.last_body or {}).lower()
    flat = text.replace("\u2019", "'").replace("\u2018", "'")
    for phrase in _MISMATCH_UNAVAILABLE_PHRASES:
        assert phrase not in flat, (
            f"turn {conv.turn}: must not use unavailable-time mismatch wording "
            f"{phrase!r}, got {_response_text(conv.last_body or {})!r}"
        )


def _assert_unavailable_5pm_mismatch_wording(conv, booking, availability) -> None:
    """5pm after afternoon offers: unavailable-time mismatch, not 'couldn't understand'."""
    _assert_no_booking(conv, booking)
    _assert_no_extra_availability_search(conv, availability)
    _assert_booking_context_preserved(conv)

    sess = conv.session() or {}
    confirmation = _confirmation_state(sess)
    assert confirmation in (None, "", False), (
        f"turn {conv.turn}: unavailable 5pm must not confirm, "
        f"got confirmation_state={confirmation!r}"
    )

    time_match = _time_match_from_conv(conv)
    assert time_match == TIME_MATCH_MISMATCH, (
        f"turn {conv.turn}: expected TIME_MATCH_MISMATCH for unavailable 5pm, "
        f"got {time_match!r}"
    )
    awaiting = (
        conv.plan.get("awaiting")
        or conv.outcome.get("awaiting")
        or sess.get("awaiting")
    )
    assert awaiting == "TIME_SELECTION", (
        f"turn {conv.turn}: expected awaiting TIME_SELECTION, got {awaiting!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    assert text.strip(), (
        f"turn {conv.turn}: expected mismatch clarification text, got {text!r}"
    )
    unavailable_ok = (
        "not available" in lowered
        or "isn't available" in lowered
        or "5:00" in text
        or "5pm" in lowered
        or "17:00" in text
    )
    assert unavailable_ok, (
        f"turn {conv.turn}: expected unavailable-time wording for 5pm, got {text!r}"
    )
    for phrase in (
        "couldn't understand",
        "could not understand",
        "didn't understand",
        "did not understand",
        "as a time",
        "as a valid time",
    ):
        assert phrase not in lowered, (
            f"turn {conv.turn}: unavailable 5pm must not use unparseable-time "
            f"wording {phrase!r}, got {text!r}"
        )


def _assert_dotted_time_bound(label: str):
    """Assert confirmation bind for a dotted clock form; prove parity across labels."""

    def _after(conv, booking, availability) -> None:
        _assert_no_booking(conv, booking)
        _assert_no_extra_availability_search(conv, availability)
        _assert_booking_context_preserved(conv)

        sess = conv.session() or {}
        slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
        planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
        planning_slots = (
            planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
        )
        time_value = planning_slots.get("time") or slots.get("time")
        confirmation = _confirmation_state(sess)

        assert time_value and "13:30" in str(time_value), (
            f"turn {conv.turn}: expected bound time 13:30 from presented 1:30 PM "
            f"for '{label}', got {time_value!r}"
        )
        assert confirmation == "pending", (
            f"turn {conv.turn}: expected confirmation pending after '{label}', "
            f"got {confirmation!r}"
        )
        assert conv.plan.get("status") == "AWAITING_CONFIRMATION", (
            f"turn {conv.turn}: expected AWAITING_CONFIRMATION after '{label}', "
            f"got {conv.plan.get('status')!r}"
        )
        assert conv.plan.get("action") is None, (
            f"turn {conv.turn}: no execution action expected after bind, "
            f"got {conv.plan.get('action')!r}"
        )
        awaiting = conv.plan.get("awaiting") or conv.outcome.get("awaiting")
        assert awaiting != "TIME_SELECTION", (
            f"turn {conv.turn}: dotted time must not remain in TIME_SELECTION "
            f"clarification, got awaiting={awaiting!r}"
        )

        snapshot = {
            "planner": conv.plan.get("status"),
            "awaiting": awaiting,
            "action": conv.plan.get("action"),
            "confirmation": confirmation,
            "service_id": planning_slots.get("service_id") or slots.get("service_id"),
            "date": str(
                planning_slots.get("date")
                or slots.get("date")
                or _POST_AVAIL_BASELINE.get("date")
                or ""
            ),
            "time": str(time_value),
            "search_count": availability.get_service_availability.call_count,
        }
        _DOTTED_TIME_BIND[label] = snapshot
        counterpart = "1.30pm" if label == "1.30" else "1.30"
        if counterpart in _DOTTED_TIME_BIND:
            assert _DOTTED_TIME_BIND[label] == _DOTTED_TIME_BIND[counterpart], (
                f"'1.30' and '1.30pm' must produce identical booking outcomes; "
                f"got {_DOTTED_TIME_BIND!r}"
            )

    return _after


_JULY_24 = "2026-07-24"


def _assert_numeric_hour_binds_unique_offered_time(conv, booking, availability) -> None:
    """Bare \"9\" with a unique offered 9:00 binds that time and awaits confirmation."""
    _assert_no_booking(conv, booking)
    _assert_no_extra_availability_search(conv, availability)
    _assert_booking_context_preserved(conv)

    plan = conv.plan or {}
    outcome = conv.outcome or {}
    status = plan.get("status") or outcome.get("status")
    action = plan.get("action") if "action" in plan else outcome.get("action")

    assert status == "AWAITING_CONFIRMATION", (
        f"turn {conv.turn}: expected AWAITING_CONFIRMATION for unique bare \"9\", "
        f"got status={status!r}"
    )
    assert action is None, (
        f"turn {conv.turn}: confirmation turn must not execute, got action={action!r}"
    )

    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    time_value = planning_slots.get("time") or slots.get("time")
    assert time_value and "09:00" in str(time_value), (
        f"turn {conv.turn}: bare \"9\" must bind unique offered 09:00, got {time_value!r}"
    )
    confirmation = _confirmation_state(sess)
    assert confirmation == "pending", (
        f"turn {conv.turn}: expected confirmation pending after unique \"9\", "
        f"got {confirmation!r}"
    )

    text = _response_text(conv.last_body or {})
    lowered = text.lower()
    assert text.strip(), (
        f"turn {conv.turn}: expected confirmation prompt, got {text!r}"
    )
    assert "book" in lowered or "confirm" in lowered or "go ahead" in lowered, (
        f"turn {conv.turn}: expected booking confirmation wording, got {text!r}"
    )


# ---------------------------------------------------------------------------
# Date requests after availability must SEARCH (not date-axis browse)
# ---------------------------------------------------------------------------

_JULY_23 = "2026-07-23"
_JULY_25 = "2026-07-25"
_DATE_AFTER_SEARCH_STATE: Dict[str, Any] = {}


def _presented_search_date(session: Dict[str, Any]) -> Any:
    presented = _sess_presented(session)
    if isinstance(presented, dict) and presented.get("search_date"):
        return _resolve_search_date(str(presented.get("search_date")))
    cache = _sess_cache(session)
    if isinstance(cache, dict) and cache.get("search_date"):
        return _resolve_search_date(str(cache.get("search_date")))
    return None


def _assert_july23_availability_presented(conv, booking, availability) -> None:
    _assert_no_booking(conv, booking)
    assert availability.get_service_availability.call_count >= 1
    _DATE_AFTER_SEARCH_STATE["search_count"] = (
        availability.get_service_availability.call_count
    )
    _DATE_AFTER_SEARCH_STATE["fingerprint"] = _sess_fp(conv.session() or {})
    sess = conv.session() or {}
    presented_date = _presented_search_date(sess)
    assert presented_date == _JULY_23, (
        f"turn {conv.turn}: expected July 23 availability window, "
        f"got presented.search_date={presented_date!r}"
    )


def _assert_july25_searches(conv, booking, availability) -> None:
    """Asking for July 25 after browsing/searching July 23 must SEARCH anew."""
    _assert_no_booking(conv, booking)
    baseline = _DATE_AFTER_SEARCH_STATE.get("search_count", 0)
    assert availability.get_service_availability.call_count > baseline, (
        f"turn {conv.turn}: July 25 must run SEARCH_AVAILABILITY "
        f"(baseline={baseline}, got {availability.get_service_availability.call_count})"
    )
    plan = conv.plan or {}
    # After execution, plan.action may already be consumed; prefer call evidence.
    sess = conv.session() or {}
    new_fp = _sess_fp(sess)
    prior_fp = _DATE_AFTER_SEARCH_STATE.get("fingerprint")
    assert new_fp and new_fp != prior_fp, (
        f"turn {conv.turn}: expected a new fingerprint for July 25, "
        f"prior={prior_fp!r} new={new_fp!r}"
    )
    presented_date = _presented_search_date(sess)
    assert presented_date == _JULY_25, (
        f"turn {conv.turn}: expected July 25 presentation, got {presented_date!r}"
    )
    _ = plan


# ---------------------------------------------------------------------------
# Highest-value audit gaps (identity / failure / idempotency / reload / multi-revision)
# ---------------------------------------------------------------------------

_AUDIT_STATE: Dict[str, Any] = {}
_JULY_12 = "2026-07-12"


def _session_booking_ids(sess: Dict[str, Any]) -> tuple:
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    booking = sess.get("booking") if isinstance(sess.get("booking"), dict) else {}
    booking_id = booking.get("booking_id") or slots.get("booking_id")
    booking_code = booking.get("booking_code") or slots.get("booking_code")
    return booking_id, booking_code


def _assert_criteria_intact(conv, *, service_id=PREMIUM_SERVICE, time_fragment="10") -> None:
    sess = conv.session() or {}
    slots = sess.get("slots") if isinstance(sess.get("slots"), dict) else {}
    planning = sess.get("planning") if isinstance(sess.get("planning"), dict) else {}
    planning_slots = (
        planning.get("slots") if isinstance(planning.get("slots"), dict) else {}
    )
    effective_service = planning_slots.get("service_id") or slots.get("service_id")
    effective_time = planning_slots.get("time") or slots.get("time")
    assert effective_service == service_id, (
        f"turn {conv.turn}: service_id expected {service_id!r}, got {effective_service!r}"
    )
    assert effective_time and time_fragment in str(effective_time), (
        f"turn {conv.turn}: time expected to contain {time_fragment!r}, got {effective_time!r}"
    )


def _assert_identity_blocked_yes(conv, booking, _availability=None) -> None:
    """First yes without ingress identity must not dispatch booking."""
    assert not booking.create_booking.called, (
        f"turn {conv.turn}: identity-blocked yes must not call create_booking"
    )
    sess = conv.session() or {}
    assert _confirmation_state(sess) == "pending", (
        f"turn {conv.turn}: confirmation must remain pending after identity block, "
        f"got {_confirmation_state(sess)!r}"
    )
    assert _confirmation_state(sess) != "confirmed"
    booking_id, booking_code = _session_booking_ids(sess)
    assert not booking_id and not booking_code, (
        f"turn {conv.turn}: no durable booking ids after identity block "
        f"(booking_id={booking_id!r}, booking_code={booking_code!r})"
    )
    assert sess.get("customer_id") in (None, "", 0, False), (
        f"turn {conv.turn}: customer_id must not be chat user_id stand-in, "
        f"got {sess.get('customer_id')!r}"
    )
    assert str(sess.get("customer_id") or "") != str(conv.user_id)
    _assert_criteria_intact(conv)
    text = _response_text(conv.last_body or {}).lower()
    assert "phone" in text or "email" in text, (
        f"turn {conv.turn}: identity block must ask for phone/email, got "
        f"{_response_text(conv.last_body or {})!r}"
    )


def _attach_identity_before_resume(conv, _booking=None, _availability=None) -> None:
    attach_commit_customer_identity(conv)
    # Supply an addressable commerce identity while leaving the requested name
    # to current-turn NLU evidence.
    conv.customer_name = None


def _assert_identity_resolved_pending(conv, booking, _availability=None) -> None:
    """After ingress identity, tenant customer_id is persisted and confirm re-presented."""
    _assert_no_booking(conv, booking)
    sess = conv.session() or {}
    customer_id = sess.get("customer_id")
    assert customer_id and int(customer_id) > 0, (
        f"turn {conv.turn}: expected resolved tenant customer_id, got {customer_id!r}"
    )
    assert str(customer_id) != str(conv.user_id), (
        f"turn {conv.turn}: customer_id must not equal chat user_id {conv.user_id!r}"
    )
    assert _confirmation_state(sess) == "pending", (
        f"turn {conv.turn}: confirmation must be re-presented as pending, "
        f"got {_confirmation_state(sess)!r}"
    )
    _assert_criteria_intact(conv)
    _AUDIT_STATE["resolved_customer_id"] = customer_id


def _fail_booking_once(conv, booking, _availability=None) -> None:
    original = booking.create_booking.side_effect

    def _failing(*args, **kwargs):
        booking.create_booking.side_effect = original
        raise UpstreamError("API returned error 500: booking creation failed")

    booking.create_booking.side_effect = _failing
    _AUDIT_STATE["fail_calls_before"] = booking.create_booking.call_count


def _assert_execution_failed_resumable(conv, booking, _availability=None) -> None:
    """Failed commit must not leave durable booking or false confirmed state."""
    assert booking.create_booking.call_count == _AUDIT_STATE.get("fail_calls_before", 0) + 1, (
        f"turn {conv.turn}: failed attempt must invoke create_booking once"
    )
    # Failed turns may return success=False; do not require assert_http_ok here.
    assert conv.last_http is not None and conv.last_http.status_code == 200, (
        f"turn {conv.turn}: expected HTTP 200 envelope on controlled execution failure, "
        f"got {getattr(conv.last_http, 'status_code', None)}"
    )
    body = conv.last_body or {}
    assert body.get("success") is False or (body.get("outcome") or {}).get("status") in (
        "failed",
        None,
    ) or body.get("error") in ("execution_failed", "upstream_error", None), (
        f"turn {conv.turn}: expected failed execution envelope, got {body!r}"
    )

    sess = conv.session() or {}
    booking_id, booking_code = _session_booking_ids(sess)
    assert not booking_id and not booking_code, (
        f"turn {conv.turn}: failed attempt must not persist booking identifiers "
        f"(booking_id={booking_id!r}, booking_code={booking_code!r})"
    )
    confirmation = _confirmation_state(sess)
    assert confirmation != "confirmed", (
        f"turn {conv.turn}: must not leave durable confirmed after failure, "
        f"got {confirmation!r}"
    )
    # Prefer pending for safe retry; expose defect if confirmation was consumed.
    assert confirmation == "pending", (
        f"turn {conv.turn}: booking must remain resumable with pending confirmation, "
        f"got {confirmation!r}"
    )
    _assert_criteria_intact(conv)
    _AUDIT_STATE["fail_call_count"] = booking.create_booking.call_count


def _assert_retry_single_success(conv, booking, _availability=None) -> None:
    """One failed attempt + one successful commit — not two successful bookings."""
    assert booking.create_booking.called, (
        f"turn {conv.turn}: expected create_booking after successful retry"
    )
    fail_count = _AUDIT_STATE.get("fail_call_count", 1)
    assert booking.create_booking.call_count == fail_count + 1, (
        f"turn {conv.turn}: expected exactly one successful retry after the failed attempt, "
        f"call_count={booking.create_booking.call_count} (fail_baseline={fail_count})"
    )
    call = booking.create_booking.call_args
    kwargs = call.kwargs if call else {}
    payload_customer_id = kwargs.get("customer_id")
    sess = conv.session() or {}
    session_customer_id = sess.get("customer_id")
    assert payload_customer_id and int(payload_customer_id) > 0, (
        f"turn {conv.turn}: booking payload must use resolved commerce "
        f"customer_id, got {payload_customer_id!r}"
    )
    assert session_customer_id == payload_customer_id, (
        f"turn {conv.turn}: session customer_id {session_customer_id!r} "
        f"must match booking payload {payload_customer_id!r}"
    )
    assert str(payload_customer_id) != str(conv.user_id), (
        f"turn {conv.turn}: customer_id must not be chat user_id "
        f"({conv.user_id!r})"
    )
    booking_id, booking_code = _session_booking_ids(sess)
    assert booking_id, (
        f"turn {conv.turn}: expected booking_id after successful retry"
    )
    assert booking_code, (
        f"turn {conv.turn}: expected booking_code after successful retry"
    )
    assert _confirmation_state(sess) is None, (
        f"turn {conv.turn}: confirmation must be consumed after successful commit, "
        f"got {_confirmation_state(sess)!r}"
    )
    planning = sess.get("planning") or {}
    assert planning.get("intent_name") is None, (
        f"turn {conv.turn}: successful retry must close CREATE_APPOINTMENT intent"
    )
    assert planning.get("slots") == {}, (
        f"turn {conv.turn}: successful retry must clear planning slots, "
        f"got {planning.get('slots')!r}"
    )
    assert planning.get("missing_slots") == [], (
        f"turn {conv.turn}: successful retry must clear missing slots, "
        f"got {planning.get('missing_slots')!r}"
    )
    assert _sess_fp(sess) is None, (
        f"turn {conv.turn}: successful retry must clear availability fingerprint"
    )
    availability = sess.get("availability") or {}
    cache = availability.get("cache") or {}
    assert cache.get("search_result") is None, (
        f"turn {conv.turn}: successful retry must clear cached availability"
    )


def _capture_committed_booking(conv, booking, _availability=None) -> None:
    _assert_booking_created(conv, booking)
    sess = conv.session() or {}
    booking_id, booking_code = _session_booking_ids(sess)
    _AUDIT_STATE["committed_booking_id"] = booking_id
    _AUDIT_STATE["committed_booking_code"] = booking_code
    _AUDIT_STATE["commit_call_count"] = booking.create_booking.call_count


def _assert_duplicate_yes_idempotent(conv, booking, _availability=None) -> None:
    assert booking.create_booking.call_count == _AUDIT_STATE.get("commit_call_count", 1), (
        f"turn {conv.turn}: duplicate yes must not call create_booking again "
        f"(baseline={_AUDIT_STATE.get('commit_call_count')}, "
        f"got={booking.create_booking.call_count})"
    )
    sess = conv.session() or {}
    booking_id, booking_code = _session_booking_ids(sess)
    assert booking_id == _AUDIT_STATE.get("committed_booking_id"), (
        f"turn {conv.turn}: booking_id must remain stable "
        f"({_AUDIT_STATE.get('committed_booking_id')!r} -> {booking_id!r})"
    )
    assert booking_code == _AUDIT_STATE.get("committed_booking_code"), (
        f"turn {conv.turn}: booking_code must remain stable "
        f"({_AUDIT_STATE.get('committed_booking_code')!r} -> {booking_code!r})"
    )


def _capture_revision_search(conv, _booking, availability) -> None:
    _AUDIT_STATE["search_count"] = availability.get_service_availability.call_count
    _AUDIT_STATE["fingerprint"] = _sess_fp(conv.session() or {})


_SEARCH_CATALOG_ITEM_IDS = {
    PREMIUM_SERVICE: PREMIUM_SERVICE_ITEM_ID,
    FLEXI_SERVICE: FLEXI_SERVICE_ITEM_ID,
    "haircut": PREMIUM_SERVICE_ITEM_ID,
}


def _expected_search_catalog_item_id(service_id):
    """Availability-client boundary uses numeric catalog ids, not SKU text."""
    if isinstance(service_id, int):
        return service_id
    mapped = _SEARCH_CATALOG_ITEM_IDS.get(service_id)
    if mapped is not None:
        return mapped
    return service_id


def _assert_revision_searched_once(conv, booking, availability, *, service_id) -> None:
    baseline = _AUDIT_STATE.get("search_count", 0)
    assert availability.get_service_availability.call_count == baseline + 1, (
        f"turn {conv.turn}: expected exactly one new SEARCH after revision "
        f"(baseline={baseline}, got={availability.get_service_availability.call_count})"
    )
    call = availability.get_service_availability.call_args
    kwargs = call.kwargs if call else {}
    expected_item_id = _expected_search_catalog_item_id(service_id)
    assert kwargs.get("service_id") == expected_item_id, (
        f"turn {conv.turn}: search must use catalog item {expected_item_id!r}, "
        f"got {kwargs.get('service_id')!r}"
    )
    sess = conv.session() or {}
    assert not sess.get("resolved_datetime_range"), (
        f"turn {conv.turn}: stale bound datetime must be cleared, "
        f"got {sess.get('resolved_datetime_range')!r}"
    )
    new_fp = _sess_fp(sess)
    prior_fp = _AUDIT_STATE.get("fingerprint")
    if prior_fp:
        assert new_fp != prior_fp, (
            f"turn {conv.turn}: stale availability fingerprint must not be reused "
            f"(prior={prior_fp!r}, new={new_fp!r})"
        )
    _assert_no_booking(conv, booking)
    _AUDIT_STATE["search_count"] = availability.get_service_availability.call_count
    _AUDIT_STATE["fingerprint"] = new_fp


def _assert_flexi_revision(conv, booking, availability) -> None:
    _assert_revision_searched_once(conv, booking, availability, service_id=FLEXI_SERVICE)
    sess = conv.session() or {}
    slots = sess.get("slots") or {}
    assert slots.get("time") in (None, ""), (
        f"turn {conv.turn}: stale time must clear on service revision, got {slots.get('time')!r}"
    )
    _assert_authoritative_time_absent(conv, "10:00", "11:00")


def _assert_date_revision_july12(conv, booking, availability) -> None:
    _assert_revision_searched_once(conv, booking, availability, service_id=FLEXI_SERVICE)
    conv.assert_date_proposal(_JULY_12)
    _assert_authoritative_time_absent(conv, "10:00", "11:00")


def _assert_premium_rerevision(conv, booking, availability) -> None:
    _assert_revision_searched_once(conv, booking, availability, service_id=PREMIUM_SERVICE)
    _assert_authoritative_time_absent(conv, "11:00")


def _assert_final_multi_revision_booking(conv, booking, _availability=None) -> None:
    _assert_booking_created_with_exact_payload(
        expected_item_id=PREMIUM_SERVICE_ITEM_ID,
        expected_service_id=PREMIUM_SERVICE,
        expected_date=_JULY_12,
        expected_time="10:00",
        abandoned_values=("11:00",),
    )(conv, booking, _availability)


def _assert_hard_reload_state_survives(conv, booking, _availability=None) -> None:
    """Canonical booking criteria survive forced Session V2 reloads."""
    _assert_no_booking(conv, booking)
    sess = conv.session() or {}
    assert sess.get("schema_version") == 2 or isinstance(sess.get("planning"), dict), (
        f"turn {conv.turn}: expected Session V2 shape after hard reload, keys={list(sess.keys())}"
    )
    assert _confirmation_state(sess) == "pending"
    _assert_criteria_intact(conv)


def _assert_digression_preserves_pending(conv, booking, availability) -> None:
    _assert_no_booking(conv, booking)
    sess = conv.session() or {}
    assert _confirmation_state(sess) == "pending", (
        f"turn {conv.turn}: digression must preserve pending confirmation, "
        f"got {_confirmation_state(sess)!r}"
    )
    _assert_criteria_intact(conv)
    # Digression must not SEARCH.
    if "reload_search_count" in _AUDIT_STATE:
        assert availability.get_service_availability.call_count == _AUDIT_STATE[
            "reload_search_count"
        ], (
            f"turn {conv.turn}: digression must not trigger SEARCH_AVAILABILITY"
        )


def _capture_reload_search(conv, booking, availability) -> None:
    _AUDIT_STATE["reload_search_count"] = availability.get_service_availability.call_count
    _assert_hard_reload_state_survives(conv, booking)

__all__ = [
    '_sess_fp',
    '_sess_presented',
    '_sess_cache',
    '_assert_booking_created',
    '_assert_booking_created_with_item_id',
    '_assert_booking_created_with_exact_payload',
    '_assert_no_booking',
    '_assert_no_booking_and_date_kept',
    '_capture_searches',
    '_assert_no_extra_search',
    '_assert_unavailable_time_mismatch',
    '_assert_authoritative_time_absent',
    '_assert_authoritative_time_replaced',
    '_assert_service_revision',
    '_assert_date_revision',
    '_assert_date_revision_without_stale_time',
    '_assert_booking_called',
    '_capture_searches_before_flexi',
    '_assert_availability_searched_flexi',
    '_assert_no_search_yet',
    '_assert_exact_search_side_effects',
    '_assert_exact_search_recorded_tomorrow',
    '_assert_no_booking_single_search',
    '_capture_search_count',
    '_assert_mismatch_side_effects',
    '_assert_empty_slots',
    '_assert_proposals_persisted',
    '_capture_post_availability_baseline',
    '_assert_no_extra_availability_search',
    '_assert_booking_context_preserved',
    '_assert_invalid_time_explains_and_reshows',
    '_turn_understanding',
    '_assert_production_xxxxx_after_availability',
    '_time_match_from_conv',
    '_assert_malformed_clock_not_mismatch',
    '_assert_unavailable_5pm_mismatch_wording',
    '_assert_dotted_time_bound',
    '_assert_numeric_hour_binds_unique_offered_time',
    '_presented_search_date',
    '_assert_july23_availability_presented',
    '_assert_july25_searches',
    '_session_booking_ids',
    '_assert_criteria_intact',
    '_assert_identity_blocked_yes',
    '_attach_identity_before_resume',
    '_assert_identity_resolved_pending',
    '_fail_booking_once',
    '_assert_execution_failed_resumable',
    '_assert_retry_single_success',
    '_capture_committed_booking',
    '_assert_duplicate_yes_idempotent',
    '_capture_revision_search',
    '_expected_search_catalog_item_id',
    '_assert_revision_searched_once',
    '_assert_flexi_revision',
    '_assert_date_revision_july12',
    '_assert_premium_rerevision',
    '_assert_final_multi_revision_booking',
    '_assert_hard_reload_state_survives',
    '_assert_digression_preserves_pending',
    '_capture_reload_search',
    'TARGET_DATE',
    '_TOMORROW',
    '_MISMATCH_UNAVAILABLE_PHRASES',
    '_JULY_24',
    '_JULY_23',
    '_JULY_25',
    '_JULY_12',
]
