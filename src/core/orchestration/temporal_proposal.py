"""NLU temporal proposals vs confirmed booking slots (Phase 2).

- date_proposal / time_proposal: extracted from NLU, constrain availability search
- slots.date / slots.time: confirmed datetime (after availability or explicit commit)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.orchestration.luma_facts_adapter import is_flexible_combined_utterance

logger = logging.getLogger(__name__)

# Durable slot keys that must not persist for CREATE_APPOINTMENT until availability confirms.
_CREATE_APPOINTMENT_TEMPORAL_SLOT_KEYS = frozenset(
    {"date", "time", "date_range", "datetime_range", "start_date", "end_date"}
)


def has_bound_booking_datetime(
    slots: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    merged: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when a confirmed booking datetime is bound (not merely proposed or searched)."""
    for source in (merged, session_state):
        if not isinstance(source, dict):
            continue
        resolved = source.get("resolved_datetime_range")
        if isinstance(resolved, dict) and resolved.get("start"):
            return True
        facts = source.get("facts")
        if isinstance(facts, dict):
            facts_resolved = facts.get("resolved_datetime_range")
            if isinstance(facts_resolved, dict) and facts_resolved.get("start"):
                return True
    slot_sources = [slots]
    if isinstance(session_state, dict):
        slot_sources.append(session_state.get("slots"))
    for slot_map in slot_sources:
        if not isinstance(slot_map, dict):
            continue
        if slot_map.get("date") and slot_map.get("time"):
            return True
        dt_range = slot_map.get("datetime_range")
        if isinstance(dt_range, dict) and dt_range.get("start"):
            return True
    return False


def temporal_slots_confirmed(session_state: Optional[Dict[str, Any]] = None) -> bool:
    """Return True when date/time were confirmed by explicit slot binding."""
    return has_bound_booking_datetime(session_state=session_state)


def _normalize_search_date(date_raw: Any) -> Optional[str]:
    """Normalize a date value to YYYY-MM-DD."""
    if not date_raw or not isinstance(date_raw, str):
        return None
    return date_raw.split("T")[0].split(" ")[0]


def _offer_start_summaries(offers: list) -> list:
    """Compact start timestamps for diagnostic logs."""
    summaries = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        start = offer.get("starts_at") or offer.get("start")
        if start:
            summaries.append(str(start))
    return summaries


def _filter_offers_to_search_date(offers: list, search_date: str) -> list:
    """Keep only offers on the presented search date."""
    filtered = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        parsed = _parse_offer_start_parts(
            offer.get("starts_at") or offer.get("start"))
        if parsed and parsed[0] == search_date:
            filtered.append(offer)
    return filtered


def _derive_presentation_date_from_offers(offers: list) -> Optional[str]:
    """Return the single date when all offers share one day."""
    dates = set()
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        parsed = _parse_offer_start_parts(
            offer.get("starts_at") or offer.get("start"))
        if parsed:
            dates.add(parsed[0])
    if len(dates) == 1:
        return next(iter(dates))
    return None


def _resolve_presentation_search_date(
    last_result: Dict[str, Any], offers: list
) -> Optional[str]:
    """Resolve the date of the availability list most recently shown to the user."""
    search_date = _normalize_search_date(last_result.get("search_date"))
    if search_date:
        return search_date
    single_date = _derive_presentation_date_from_offers(offers)
    if single_date:
        return single_date
    # Legacy accumulated offers: assume the last offer is from the latest search.
    for offer in reversed(offers):
        if not isinstance(offer, dict):
            continue
        parsed = _parse_offer_start_parts(
            offer.get("starts_at") or offer.get("start"))
        if parsed:
            return parsed[0]
    return None


def enrich_last_execution_result(
    exec_result: Dict[str, Any],
    *,
    search_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a replaceable last_execution_result payload for one availability search.

    Keeps the full latest search slots for diagnostics. Binding uses
    ``presented_availability`` (what was shown), not this full list.
    """
    slots_list = list(exec_result.get("slots") or [])
    payload: Dict[str, Any] = {
        "type": exec_result.get("type"),
        "status": exec_result.get("status"),
        "slots": slots_list,
    }
    if exec_result.get("availability_fingerprint"):
        payload["availability_fingerprint"] = exec_result["availability_fingerprint"]

    offer_date = _derive_presentation_date_from_offers(slots_list)
    arg_date = _normalize_search_date(search_date) or _normalize_search_date(
        exec_result.get("search_date")
    )
    resolved_search_date = offer_date
    if arg_date:
        matching = _filter_offers_to_search_date(slots_list, arg_date)
        if matching:
            resolved_search_date = arg_date
        elif not offer_date:
            # Prefer last offer date over a plan date that matches nothing.
            for offer in reversed(slots_list):
                if not isinstance(offer, dict):
                    continue
                parsed = _parse_offer_start_parts(
                    offer.get("starts_at") or offer.get("start")
                )
                if parsed:
                    resolved_search_date = parsed[0]
                    break

    if resolved_search_date:
        payload["search_date"] = resolved_search_date

    logger.info(
        "[AVAILABILITY_PERSIST] search_date_arg=%s resolved_search_date=%s "
        "slots=%s offer_starts=%s",
        search_date,
        resolved_search_date,
        len(slots_list),
        _offer_start_summaries(slots_list),
    )
    return payload


def get_presented_availability_offers(
    session_state: Optional[Dict[str, Any]],
) -> list:
    """Return slots the user was shown (selectable set for time binding)."""
    if not isinstance(session_state, dict):
        logger.debug("[TIME_SELECTION_OFFERS] no session_state")
        return []

    presented = session_state.get("presented_availability")
    if isinstance(presented, dict):
        offers = presented.get("slots", [])
        if isinstance(offers, list) and offers:
            logger.debug(
                "[TIME_SELECTION_OFFERS] source=presented_availability "
                "search_date=%s offers=%s offer_starts=%s",
                presented.get("search_date"),
                len(offers),
                _offer_start_summaries(offers),
            )
            return offers

    # Legacy sessions: approximate presentation from latest search (same cap as UI).
    last_result = session_state.get("last_execution_result")
    if not isinstance(last_result, dict):
        logger.debug(
            "[TIME_SELECTION_OFFERS] no presented_availability or last_execution_result")
        return []
    if last_result.get("type") != "availability" or last_result.get("status") != "success":
        logger.debug(
            "[TIME_SELECTION_OFFERS] last_execution_result not usable type=%s status=%s",
            last_result.get("type"),
            last_result.get("status"),
        )
        return []
    offers = last_result.get("slots", [])
    if not isinstance(offers, list) or not offers:
        logger.debug(
            "[TIME_SELECTION_OFFERS] empty slots in last_execution_result "
            "search_date=%s",
            last_result.get("search_date"),
        )
        return []

    presentation_date = _resolve_presentation_search_date(last_result, offers)
    candidate = offers
    if presentation_date:
        candidate = _filter_offers_to_search_date(offers, presentation_date)
        if offers and not candidate:
            # Never bind against an emptied filter; fall back to offer-derived day.
            fallback_date = _derive_presentation_date_from_offers(offers)
            if fallback_date:
                candidate = _filter_offers_to_search_date(
                    offers, fallback_date)
            else:
                candidate = offers
            logger.warning(
                "[TIME_SELECTION_OFFERS] search_date filter emptied offers; "
                "fallback_date=%s offers=%s",
                fallback_date,
                _offer_start_summaries(candidate),
            )

    from core.rendering.availability_renderer import build_presented_availability

    presented_payload = build_presented_availability(
        candidate, search_date=presentation_date
    )
    presented_offers = presented_payload.get("slots") or []
    logger.debug(
        "[TIME_SELECTION_OFFERS] source=legacy_last_execution_result "
        "presentation_date=%s presented=%s offer_starts=%s",
        presented_payload.get("search_date"),
        len(presented_offers),
        _offer_start_summaries(presented_offers),
    )
    return presented_offers if isinstance(presented_offers, list) else []


def get_cached_availability_offers(
    session_state: Optional[Dict[str, Any]],
) -> list:
    """Return selectable offers for time binding (presented availability)."""
    return get_presented_availability_offers(session_state)


def _parse_offer_start_parts(start_raw: Any) -> Optional[tuple[str, str]]:
    """Parse an ISO offer start into (YYYY-MM-DD, HH:MM)."""
    if not start_raw:
        return None
    from datetime import datetime as _dt

    raw = str(start_raw).replace("Z", "+00:00")
    try:
        parsed = _dt.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return parsed.date().isoformat(), f"{parsed.hour:02d}:{parsed.minute:02d}"


def try_bind_offered_time_selection(
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    *,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Bind durable slots when the user picks a time from presented availability."""
    presented = (
        session_state.get("presented_availability")
        if isinstance(session_state, dict)
        else None
    )
    last_result = (
        session_state.get("last_execution_result")
        if isinstance(session_state, dict)
        else None
    )

    user_time_raw = None
    if isinstance(time_proposal, dict) and time_proposal.get("mode") == "exact":
        user_time_raw = time_proposal.get("value")
    if not user_time_raw and isinstance(time_constraint, dict):
        if time_constraint.get("mode") == "exact" and time_constraint.get("start"):
            user_time_raw = time_constraint.get("start")

    offers = get_presented_availability_offers(session_state)
    slots_before = dict(slots or {})
    logger.debug(
        "[TIME_SELECTION_BIND] attempt user_time_raw=%r time_proposal=%s "
        "time_constraint=%s date_proposal=%s slots.date=%s "
        "presented_search_date=%s presented_slots=%s "
        "last_execution_result.search_date=%s",
        user_time_raw,
        time_proposal,
        time_constraint,
        date_proposal,
        (slots or {}).get("date") if isinstance(slots, dict) else None,
        presented.get("search_date") if isinstance(presented, dict) else None,
        _offer_start_summaries(offers),
        last_result.get("search_date") if isinstance(
            last_result, dict) else None,
    )

    if not offers:
        logger.debug(
            "[TIME_SELECTION_BIND] no presented offers to search; bind skipped")
        _emit_bind_trace(
            slots_before=slots_before,
            bind_result=None,
            skip_reason="no_offers",
            time_proposal=time_proposal,
            time_constraint=time_constraint,
            offers=offers,
            user_time_raw=user_time_raw,
        )
        return None

    if not user_time_raw:
        logger.debug(
            "[TIME_SELECTION_BIND] no exact user time from time_proposal/time_constraint"
        )
        _emit_bind_trace(
            slots_before=slots_before,
            bind_result=None,
            skip_reason="no_user_time",
            time_proposal=time_proposal,
            time_constraint=time_constraint,
            offers=offers,
            user_time_raw=user_time_raw,
        )
        return None

    from core.orchestration.availability_fingerprint import _normalize_time_for_fingerprint

    user_time_norm = _normalize_time_for_fingerprint(user_time_raw)
    if not user_time_norm:
        logger.debug(
            "[TIME_SELECTION_BIND] user time failed normalization user_time_raw=%r",
            user_time_raw,
        )
        _emit_bind_trace(
            slots_before=slots_before,
            bind_result=None,
            skip_reason="normalize_failed",
            time_proposal=time_proposal,
            time_constraint=time_constraint,
            offers=offers,
            user_time_raw=user_time_raw,
            user_time_norm=user_time_norm,
        )
        return None

    # Bind only against the availability list that was just presented to the user.
    # Do not use stale date_proposal or durable slots.date — those may reflect an
    # earlier search (e.g. July 6) while the user is picking from July 3 offers.
    expected_date = None
    if isinstance(presented, dict):
        expected_date = _normalize_search_date(presented.get("search_date"))
    if not expected_date:
        expected_date = _derive_presentation_date_from_offers(offers)

    logger.debug(
        "[TIME_SELECTION_BIND] searching user_time_norm=%s expected_date=%s "
        "offers=%s offer_starts=%s",
        user_time_norm,
        expected_date,
        len(offers),
        _offer_start_summaries(offers),
    )

    for offer in offers:
        if not isinstance(offer, dict):
            logger.debug(
                "[TIME_SELECTION_BIND] reject reason=not_dict offer=%r", offer)
            continue
        start_raw = offer.get("starts_at") or offer.get("start")
        end_raw = offer.get("ends_at") or offer.get("end")
        parsed = _parse_offer_start_parts(start_raw)
        if not parsed:
            logger.debug(
                "[TIME_SELECTION_BIND] reject reason=parse_failed start_raw=%r",
                start_raw,
            )
            continue
        offer_date, offer_time = parsed
        if offer_time != user_time_norm:
            logger.debug(
                "[TIME_SELECTION_BIND] reject reason=time_mismatch "
                "offer_date=%s offer_time=%s user_time_norm=%s start_raw=%s",
                offer_date,
                offer_time,
                user_time_norm,
                start_raw,
            )
            continue
        if expected_date and offer_date != expected_date:
            logger.debug(
                "[TIME_SELECTION_BIND] reject reason=date_mismatch "
                "offer_date=%s expected_date=%s offer_time=%s start_raw=%s",
                offer_date,
                expected_date,
                offer_time,
                start_raw,
            )
            continue

        bound_slots = dict(slots or {})
        bound_slots["date"] = offer_date
        bound_slots["time"] = user_time_norm
        resolved_datetime_range = None
        if start_raw and end_raw:
            resolved_datetime_range = {
                "start": str(start_raw), "end": str(end_raw)}
        else:
            resolved_datetime_range = build_datetime_range_from_slots(
                bound_slots, session_state.get("last_execution_result")
                if isinstance(session_state, dict)
                else None
            )
        if not resolved_datetime_range:
            logger.debug(
                "[TIME_SELECTION_BIND] reject reason=no_datetime_range "
                "offer_date=%s offer_time=%s start_raw=%r end_raw=%r",
                offer_date,
                offer_time,
                start_raw,
                end_raw,
            )
            return None
        logger.debug(
            "[TIME_SELECTION_BIND] bound date=%s time=%s from offered availability "
            "start=%s end=%s",
            offer_date,
            user_time_norm,
            start_raw,
            end_raw,
        )
        result = {
            "slots": bound_slots,
            "resolved_datetime_range": resolved_datetime_range,
        }
        _emit_bind_trace(
            slots_before=slots_before,
            bind_result=result,
            time_proposal=time_proposal,
            time_constraint=time_constraint,
            offers=offers,
            user_time_raw=user_time_raw,
            user_time_norm=user_time_norm,
            expected_date=expected_date,
            matched_offer_start=str(start_raw),
        )
        return result
    logger.debug(
        "[TIME_SELECTION_BIND] no match user_time_raw=%r user_time_norm=%s "
        "expected_date=%s offers=%s",
        user_time_raw,
        user_time_norm,
        expected_date,
        _offer_start_summaries(offers),
    )
    _emit_bind_trace(
        slots_before=slots_before,
        bind_result=None,
        skip_reason="time_mismatch",
        time_proposal=time_proposal,
        time_constraint=time_constraint,
        offers=offers,
        user_time_raw=user_time_raw,
        user_time_norm=user_time_norm,
        expected_date=expected_date,
    )
    return None


def _emit_bind_trace(**kwargs: Any) -> None:
    try:
        from core.tracing.binding import emit_bind_time_trace

        emit_bind_time_trace(**kwargs)
    except ImportError:
        pass


def strip_unconfirmed_temporal_slots(
    slots: Dict[str, Any],
    intent_name: Optional[str],
    session_state: Optional[Dict[str, Any]] = None,
    *,
    confirmed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Remove date/time from durable slots until availability confirms them."""
    if intent_name != "CREATE_APPOINTMENT":
        return dict(slots or {})
    if confirmed is None:
        confirmed = temporal_slots_confirmed(session_state)
    if confirmed:
        return dict(slots or {})
    cleaned = dict(slots or {})
    for key in _CREATE_APPOINTMENT_TEMPORAL_SLOT_KEYS:
        cleaned.pop(key, None)
    return cleaned


def build_date_proposal(
    facts: Optional[Dict[str, Any]],
    date_constraint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build date_proposal from NLU facts + date_constraint."""
    facts = facts or {}
    dates = facts.get("dates")
    if not isinstance(dates, list):
        dates = []
    if not dates:
        single_date = facts.get("date")
        if isinstance(single_date, str) and single_date:
            dates = [single_date]
        elif isinstance(single_date, list):
            dates = single_date

    mode = None
    if isinstance(date_constraint, dict):
        mode = date_constraint.get("mode")

    if not mode:
        if len(dates) >= 2:
            mode = "range"
        elif len(dates) == 1:
            mode = "single_day"
        else:
            raw_dr = facts.get("date_range")
            if isinstance(raw_dr, dict) and raw_dr.get("start"):
                mode = "range"
            else:
                return None

    proposal: Dict[str, Any] = {"mode": mode}

    raw_dr = facts.get("date_range")
    if isinstance(raw_dr, dict):
        start = raw_dr.get("start") or raw_dr.get("start_date")
        end = raw_dr.get("end") or raw_dr.get("end_date")
        if start:
            proposal["start"] = start
        if end:
            proposal["end"] = end
    elif len(dates) == 1:
        proposal["start"] = dates[0]
    elif len(dates) >= 2:
        proposal["start"] = dates[0]
        proposal["end"] = dates[-1]

    if not proposal.get("start"):
        return None
    return proposal


def build_time_proposal(
    facts: Optional[Dict[str, Any]],
    time_constraint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build time_proposal from NLU facts + time_constraint."""
    facts = facts or {}
    times = facts.get("times")
    if isinstance(times, list) and times:
        return {"mode": "exact", "value": times[0]}

    single_time = facts.get("time")
    if single_time:
        return {"mode": "exact", "value": single_time}

    if isinstance(time_constraint, dict):
        mode = time_constraint.get("mode")
        if mode == "exact" and time_constraint.get("start"):
            return {"mode": "exact", "value": time_constraint["start"]}
        if mode == "fuzzy":
            label = time_constraint.get("label")
            if label:
                return {
                    "mode": "fuzzy",
                    "label": label,
                    "start": time_constraint.get("start"),
                    "end": time_constraint.get("end"),
                }
    return None


def extract_nlu_proposals(
    luma_response: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Extract date/time proposals from a Luma/NLU response."""
    if not isinstance(luma_response, dict):
        return {"date_proposal": None, "time_proposal": None}
    facts = luma_response.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}

    explicit_time_proposal = luma_response.get("time_proposal")
    if isinstance(explicit_time_proposal, dict):
        time_proposal = explicit_time_proposal
    else:
        time_proposal = build_time_proposal(
            facts, luma_response.get("time_constraint")
        )
        if not time_proposal:
            slots = luma_response.get("slots")
            if isinstance(slots, dict) and slots.get("time"):
                time_proposal = {"mode": "exact", "value": slots["time"]}

    return {
        "date_proposal": build_date_proposal(
            facts, luma_response.get("date_constraint")
        ),
        "time_proposal": time_proposal,
    }


def exact_time_proposal_from_luma(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return an exact time_proposal from a Luma response, if present."""
    proposals = extract_nlu_proposals(luma_response)
    time_proposal = proposals.get("time_proposal")
    if (
        isinstance(time_proposal, dict)
        and time_proposal.get("mode") == "exact"
        and time_proposal.get("value")
    ):
        return time_proposal
    if not isinstance(luma_response, dict):
        return None
    time_constraint = luma_response.get("time_constraint")
    if (
        isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
        and time_constraint.get("start")
    ):
        return {"mode": "exact", "value": time_constraint["start"]}
    return None


def merge_session_proposals(
    session_state: Optional[Dict[str, Any]],
    date_proposal: Optional[Dict[str, Any]],
    time_proposal: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Merge new NLU proposals with session (new turn overwrites when present)."""
    session_state = session_state or {}
    facts = session_state.get("facts")
    if not isinstance(facts, dict):
        facts = {}
    merged_date = session_state.get(
        "date_proposal") or facts.get("date_proposal")
    merged_time = session_state.get(
        "time_proposal") or facts.get("time_proposal")
    if date_proposal:
        merged_date = date_proposal
    if time_proposal:
        merged_time = time_proposal
    return {"date_proposal": merged_date, "time_proposal": merged_time}


def resolve_session_proposals(
    *,
    merged_luma_response: Optional[Dict[str, Any]] = None,
    outcome: Optional[Dict[str, Any]] = None,
    previous_session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals from response, outcome facts, or prior session."""
    date_proposal = None
    time_proposal = None
    for source in (
        merged_luma_response,
        (outcome or {}).get("facts") if isinstance(outcome, dict) else None,
        previous_session_state,
        (previous_session_state or {}).get("facts")
        if isinstance(previous_session_state, dict)
        else None,
    ):
        if not isinstance(source, dict):
            continue
        if date_proposal is None:
            date_proposal = source.get("date_proposal")
        if time_proposal is None:
            time_proposal = source.get("time_proposal")
    return {"date_proposal": date_proposal, "time_proposal": time_proposal}


def expand_slots_for_planning(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    date_constraint: Optional[Dict[str, Any]] = None,
    nlu_facts: Optional[Dict[str, Any]] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Virtual slot view for missing_slots — proposals satisfy planning, not confirmed slots."""
    expanded = dict(slots or {})

    if is_flexible_combined_utterance(date_constraint, nlu_facts):
        return expanded

    if date_proposal:
        mode = date_proposal.get("mode")
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if mode == "single_day" and start:
            # New date_proposal overrides stale session date (e.g. friday → saturday).
            expanded["date"] = start
        elif start and end:
            expanded["date_range"] = {"start": start, "end": end}
            expanded["date"] = start
        elif start and not expanded.get("date") and not expanded.get("date_range"):
            expanded["date"] = start

    if time_proposal:
        if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
            # Exact time from this turn overrides stale session time (e.g. 2pm → 3pm,
            # or fuzzy label "afternoon" → 14:00 after clarification).
            expanded["time"] = time_proposal["value"]
        elif not expanded.get("time"):
            # Fuzzy time (mode=fuzzy, window, etc.) does NOT satisfy the time planning
            # requirement — consistent with apply_time_constraint_to_missing_slots which
            # also requires mode=exact before removing "time" from missing_slots.
            pass

    # CREATE_APPOINTMENT exact time_constraint (legacy path)
    if (
        intent_name == "CREATE_APPOINTMENT"
        and not expanded.get("time")
        and isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
        and time_constraint.get("start")
    ):
        expanded["time"] = time_constraint["start"]

    # Infer date from date_range.start when date is absent.
    # Ensures date_range in slots satisfies a "date" planning requirement (e.g. MODIFY_BOOKING).
    if not expanded.get("date") and isinstance(expanded.get("date_range"), dict):
        start = expanded["date_range"].get("start")
        if start:
            expanded["date"] = start

    return expanded


def proposal_satisfies_planning_time(
    time_proposal: Optional[Dict[str, Any]],
) -> bool:
    """True when time_proposal satisfies the planning-time requirement for missing_slots.

    Exact proposals always satisfy.  Bounded fuzzy windows (start + end) satisfy
    planning without promoting the fuzzy label into confirmed slots.time.
    """
    if not isinstance(time_proposal, dict):
        return False
    if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
        return True
    if time_proposal.get("mode") == "fuzzy":
        start = time_proposal.get("start")
        end = time_proposal.get("end")
        return bool(start and end)
    return False


def apply_confirmed_datetime(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy proposals into confirmed slots after availability is resolved."""
    confirmed = dict(slots or {})
    if date_proposal:
        mode = date_proposal.get("mode")
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if mode == "single_day" and start:
            confirmed["date"] = start
        elif start and end:
            confirmed["date_range"] = {"start": start, "end": end}
            confirmed["date"] = start
        elif start:
            confirmed["date"] = start
    if time_proposal:
        # Only exact times become confirmed slots; fuzzy windows stay proposals until
        # the user supplies an exact time (matches expand_slots_for_planning).
        if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
            confirmed["time"] = time_proposal["value"]
        elif time_proposal.get("mode") == "fuzzy":
            # slots_for_availability_search may inject fuzzy labels for the API call;
            # they must not survive as confirmed slots.time in session.
            confirmed.pop("time", None)
    return confirmed


def apply_time_constraint_to_missing_slots(
    intent_name: str,
    missing_slots: list,
    time_constraint: Optional[Dict[str, Any]],
) -> list:
    """Remove 'time' from missing_slots when an exact time_constraint satisfies it.

    Only mode=exact satisfies the time requirement.  Fuzzy/window constraints do
    NOT — the user stated a preference window, not a committed time.
    """
    if intent_name != "CREATE_APPOINTMENT":
        return missing_slots
    if not isinstance(time_constraint, dict):
        return missing_slots
    if time_constraint.get("mode") == "exact" and "time" in missing_slots:
        return [s for s in missing_slots if s != "time"]
    return missing_slots


def resolve_execution_proposals(
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals for execution (availability search / confirmation).

    Priority:
      1. plan.date_proposal / time_proposal  (set by plan_message from merged response)
      2. session_state.date_proposal / time_proposal  (prior turn, persisted)
      3. session_state.facts.date_proposal / time_proposal  (legacy fallback)
    """
    plan = plan or {}
    session_state = session_state or {}
    session_facts = session_state.get("facts") or {}

    date_proposal = (
        plan.get("date_proposal")
        or session_state.get("date_proposal")
        or (session_facts.get("date_proposal") if isinstance(session_facts, dict) else None)
    )
    time_proposal = (
        plan.get("time_proposal")
        or session_state.get("time_proposal")
        or (session_facts.get("time_proposal") if isinstance(session_facts, dict) else None)
    )
    return {"date_proposal": date_proposal, "time_proposal": time_proposal}


def build_datetime_range_from_slots(
    slots: Dict[str, Any],
    execution_result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Construct a datetime_range from slots.date + slots.time.

    Returns the existing datetime_range if already present.
    Duration is read from the first availability slot when execution_result is provided;
    falls back to 60 minutes.
    Returns None when date/time are missing or unparseable.
    """
    if isinstance(slots.get("datetime_range"), dict):
        return slots["datetime_range"]

    date_str = slots.get("date")
    time_str = slots.get("time")
    if not date_str or not time_str:
        return None

    from datetime import datetime as _dt, timedelta as _td

    date_obj = None
    if isinstance(date_str, str):
        date_only = date_str.split("T")[0].split(" ")[0]
        try:
            date_obj = _dt.strptime(date_only, "%Y-%m-%d")
        except ValueError:
            try:
                date_obj = _dt.fromisoformat(date_only)
            except (ValueError, AttributeError):
                pass
    if not date_obj:
        return None

    time_lower = str(time_str).lower()
    is_pm = "pm" in time_lower
    is_am = "am" in time_lower
    time_clean = time_lower.replace("am", "").replace("pm", "").strip()
    parts = time_clean.split(":") if ":" in time_clean else [time_clean, "00"]
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        start_dt = date_obj.replace(
            hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, IndexError, TypeError):
        return None

    duration_minutes = 60
    avail_slots = (execution_result or {}).get("slots", [])
    if isinstance(avail_slots, list) and avail_slots:
        first = avail_slots[0]
        if isinstance(first, dict):
            s_raw = first.get("starts_at") or first.get("start")
            e_raw = first.get("ends_at") or first.get("end")
            if s_raw and e_raw:
                try:
                    s = _dt.fromisoformat(str(s_raw).replace("Z", "+00:00"))
                    e = _dt.fromisoformat(str(e_raw).replace("Z", "+00:00"))
                    duration_minutes = int((e - s).total_seconds() / 60)
                except (ValueError, AttributeError):
                    pass

    end_dt = start_dt + _td(minutes=duration_minutes)
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat()}


def datetime_range_from_availability_result(
    execution_result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """Build resolved_datetime_range from normalized availability execution slots."""
    if not isinstance(execution_result, dict):
        return None
    slots_list = execution_result.get("slots", [])
    if not isinstance(slots_list, list) or not slots_list:
        return None
    first = slots_list[0]
    if not isinstance(first, dict):
        return None
    start = first.get("starts_at") or first.get("start")
    end = first.get("ends_at") or first.get("end")
    if start and end:
        return {"start": str(start), "end": str(end)}
    return None


def slots_for_availability_search(
    slots: Dict[str, Any],
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execution view: proposals constrain search when confirmed slots absent."""
    search_slots = dict(slots or {})
    if not search_slots.get("date") and date_proposal:
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if start:
            search_slots["date"] = start
        if end:
            search_slots["date_range"] = {"start": start, "end": end}
    if not search_slots.get("time") and time_proposal:
        if time_proposal.get("value"):
            search_slots["time"] = time_proposal["value"]
        elif time_proposal.get("label"):
            search_slots["time"] = time_proposal["label"]
    return search_slots
