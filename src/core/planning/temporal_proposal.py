"""NLU temporal proposals vs confirmed booking slots (Phase 2).

- date_proposal / time_proposal: extracted from NLU, constrain availability search
- slots.date / slots.time: confirmed datetime (after availability or explicit commit)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TypedDict

from core.planning.temporal_contract import is_flexible_combined_utterance

logger = logging.getLogger(__name__)


class ExecutionProposalResolutionContext(TypedDict):
    """Typed planning evidence consumed by execution proposal resolution."""

    current_turn_time_proposal: Optional[Dict[str, Any]]
    current_turn_temporal: Optional[Dict[str, Any]]
    current_turn_has_explicit_time: bool
    session_time_proposal_reuse_allowed: bool
    confirmation_continuation: bool
    availability_invalidated: bool
    bound_datetime_cleared: bool


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
    ignore_session = bool(
        isinstance(merged, dict)
        and (
            merged.get("_revision_invalidated_availability")
            or merged.get("_bound_datetime_cleared")
        )
    )
    sources = (merged,)
    if not ignore_session:
        sources = (merged, session_state)
    for source in sources:
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
    if not ignore_session and isinstance(session_state, dict):
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

    Keeps the full latest search slots as the trusted AvailabilityCache. Ambiguous
    selection uses ``presented_availability``; explicit complete selection may use
    this full list via the availability selection resolver.
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
    """Return slots in the current discovery/disambiguation window."""
    from core.workflows.availability.presentation import (
        ensure_presented_availability,
        presented_availability_from_session,
    )

    if not isinstance(session_state, dict):
        logger.debug("[TIME_SELECTION_OFFERS] no session_state")
        return []

    presented = presented_availability_from_session(session_state)
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

    # Legacy sessions: approximate presentation from trusted cache (same cap as UI).
    presented_payload = ensure_presented_availability(session_state=session_state)
    if not isinstance(presented_payload, dict):
        logger.debug("[TIME_SELECTION_OFFERS] no presented_availability or cache")
        return []
    presented_offers = presented_payload.get("slots") or []
    logger.debug(
        "[TIME_SELECTION_OFFERS] source=cache_derived_presentation "
        "presentation_date=%s presented=%s offer_starts=%s",
        presented_payload.get("search_date"),
        len(presented_offers),
        _offer_start_summaries(presented_offers),
    )
    return presented_offers if isinstance(presented_offers, list) else []


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


def create_bound_datetime_from_offer(
    *,
    slots: Dict[str, Any],
    offer: Dict[str, Any],
    offer_date: str,
    user_time_norm: str,
    execution_result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Canonical creator for a successful presented-offer time bind.

    Returns normalized durable slots plus ``resolved_datetime_range``, or None when
    the offer cannot be converted into a bound datetime.
    """
    start_raw = offer.get("starts_at") or offer.get("start")
    end_raw = offer.get("ends_at") or offer.get("end")
    bound_slots = dict(slots or {})
    bound_slots["date"] = offer_date
    bound_slots["time"] = user_time_norm
    if start_raw and end_raw:
        resolved_datetime_range = {"start": str(start_raw), "end": str(end_raw)}
    else:
        resolved_datetime_range = build_datetime_range_from_slots(
            bound_slots, execution_result
        )
    if not resolved_datetime_range:
        return None
    return {
        "slots": bound_slots,
        "resolved_datetime_range": resolved_datetime_range,
    }


def build_selection_user_facts(
    turn_payload: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build SelectionResolver user_facts from current-turn provenance flags.

    Carried session date/time alone must not set ``*_from_current_turn``.
    """
    facts: Dict[str, Any] = {}
    if not isinstance(turn_payload, dict):
        return facts

    if turn_payload.get("_current_turn_has_date") and turn_payload.get("_current_turn_date"):
        facts["date"] = turn_payload.get("_current_turn_date")
        facts["date_from_current_turn"] = True

    if turn_payload.get("_current_turn_has_time") and turn_payload.get("_current_turn_time"):
        facts["time"] = turn_payload.get("_current_turn_time")
        facts["time_from_current_turn"] = True
    elif turn_payload.get("_current_turn_has_time"):
        tp = turn_payload.get("time_proposal")
        if isinstance(tp, dict) and tp.get("mode") == "exact" and tp.get("value"):
            facts["time"] = tp.get("value")
            facts["time_from_current_turn"] = True

    from core.adapters.nlu.entity_schema_builder import (
        SEARCH_CRITERIA_KEY_ALIASES,
        canonicalize_search_criteria_key,
        search_criteria_slot_keys_from_entity_schema,
    )

    schema = turn_payload.get("_entity_schema")
    if not isinstance(schema, dict):
        schema = None
    criteria_keys = set(search_criteria_slot_keys_from_entity_schema(schema))
    criteria_keys.update(SEARCH_CRITERIA_KEY_ALIASES.keys())
    for key in sorted(criteria_keys):
        canon = canonicalize_search_criteria_key(key)
        turn_key = f"_current_turn_{canon}"
        if turn_payload.get(turn_key) is not None:
            facts[canon] = turn_payload.get(turn_key)
            facts[f"{canon}_from_current_turn"] = True
        # Legacy payload key support
        legacy_key = f"_current_turn_{key}"
        if key != canon and turn_payload.get(legacy_key) is not None:
            facts[canon] = turn_payload.get(legacy_key)
            facts[f"{canon}_from_current_turn"] = True

    if isinstance(schema, dict):
        facts["_entity_schema"] = schema

    return facts


def try_bind_offered_time_selection(
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    *,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    user_facts: Optional[Dict[str, Any]] = None,
    turn_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Bind durable slots when the user picks a time from availability.

    Selection is routed through Discovery Selector via
    ``resolve_via_discovery`` (AvailabilitySelectionPolicy).
    Ambiguous choices resolve against presented availability; explicit complete
    choices may resolve against the trusted cache when uniquely matched.
    """
    from core.workflows.availability.discovery.bridge import resolve_via_discovery
    from core.workflows.availability.presentation import (
        availability_cache_from_session,
        presented_availability_from_session,
    )

    slots_before = dict(slots or {})
    resolved_facts = dict(user_facts or {})
    if not resolved_facts and turn_payload is not None:
        resolved_facts = build_selection_user_facts(turn_payload)

    if not resolved_facts.get("time_from_current_turn"):
        if isinstance(time_proposal, dict) and time_proposal.get("mode") == "exact":
            if time_proposal.get("value"):
                resolved_facts.setdefault("time", time_proposal.get("value"))
                if turn_payload is None or turn_payload.get("_current_turn_has_time"):
                    resolved_facts["time_from_current_turn"] = True
        elif isinstance(temporal, dict) and temporal.get("start_time"):
            resolved_facts.setdefault("time", temporal.get("start_time"))
            if turn_payload is None or turn_payload.get("_current_turn_has_time"):
                resolved_facts["time_from_current_turn"] = True

    user_time_raw = resolved_facts.get("time")
    if (
        not user_time_raw
        and isinstance(time_proposal, dict)
        and time_proposal.get("mode") == "exact"
    ):
        user_time_raw = time_proposal.get("value")
    if not user_time_raw and isinstance(temporal, dict) and temporal.get("start_time"):
        user_time_raw = temporal.get("start_time")

    presented = presented_availability_from_session(session_state)
    cache = availability_cache_from_session(session_state)
    offers = get_presented_availability_offers(session_state)
    logger.debug(
        "[TIME_SELECTION_BIND] attempt user_time_raw=%r time_proposal=%s "
        "temporal=%s date_proposal=%s slots.date=%s "
        "presented_search_date=%s presented_slots=%s "
        "cache_search_date=%s user_facts=%s",
        user_time_raw,
        time_proposal,
        temporal,
        date_proposal,
        (slots or {}).get("date") if isinstance(slots, dict) else None,
        presented.get("search_date") if isinstance(presented, dict) else None,
        _offer_start_summaries(offers),
        cache.get("search_date") if isinstance(cache, dict) else None,
        {
            k: resolved_facts.get(k)
            for k in (
                "date",
                "time",
                "date_from_current_turn",
                "time_from_current_turn",
            )
        },
    )

    resolution = resolve_via_discovery(
        slots=slots,
        presented_availability=presented,
        availability_cache=cache,
        user_facts=resolved_facts,
        date_proposal=date_proposal,
        time_proposal=time_proposal,
        temporal=temporal,
        session_state=session_state,
    )
    if isinstance(turn_payload, dict):
        turn_payload["_selection_resolution"] = {
            "status": resolution.get("status"),
            "source": resolution.get("source"),
            "reason_code": resolution.get("reason_code"),
        }

    bind_result = resolution.get("bind_result")
    reason_code = resolution.get("reason_code") or "not_found"
    matched = resolution.get("status") == "matched" and isinstance(bind_result, dict)

    if matched:
        bound_slots = bind_result.get("slots") if isinstance(bind_result, dict) else {}
        resolved = (
            bind_result.get("resolved_datetime_range")
            if isinstance(bind_result, dict)
            else None
        )
        logger.debug(
            "[TIME_SELECTION_BIND] bound date=%s time=%s source=%s start=%s",
            (bound_slots or {}).get("date"),
            (bound_slots or {}).get("time"),
            resolution.get("source"),
            (resolved or {}).get("start") if isinstance(resolved, dict) else None,
        )
        _emit_bind_trace(
            slots_before=slots_before,
            bind_result=bind_result,
            time_proposal=time_proposal,
            temporal=temporal,
            offers=offers,
            user_time_raw=user_time_raw,
            user_time_norm=(bound_slots or {}).get("time"),
            expected_date=(bound_slots or {}).get("date"),
            matched_offer_start=(
                (resolved or {}).get("start") if isinstance(resolved, dict) else None
            ),
        )
        return bind_result

    skip_reason = {
        "no_offers": "no_offers",
        "no_user_time": "no_user_time",
        "normalize_failed": "normalize_failed",
        "no_presented_match": "time_mismatch",
        "multiple_presented_matches": "time_mismatch",
        "explicit_offer_not_in_cache": "time_mismatch",
        "multiple_cache_matches": "time_mismatch",
        "explicit_selection_incomplete": "no_user_time",
        "search_criteria_changed": "time_mismatch",
        "time_mismatch": "time_mismatch",
        "presentation_ambiguous": "time_mismatch",
        "explicit_cache_miss": "time_mismatch",
        "explicit_cache_ambiguous": "time_mismatch",
        "parse_failed": "time_mismatch",
        "no_datetime_range": "time_mismatch",
    }.get(str(reason_code), "time_mismatch")
    logger.debug(
        "[TIME_SELECTION_BIND] no match reason=%s status=%s source=%s",
        reason_code,
        resolution.get("status"),
        resolution.get("source"),
    )
    _emit_bind_trace(
        slots_before=slots_before,
        bind_result=None,
        skip_reason=skip_reason,
        time_proposal=time_proposal,
        temporal=temporal,
        offers=offers,
        user_time_raw=user_time_raw,
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
    facts: Optional[Dict[str, Any]] = None,
    *,
    temporal: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build date_proposal from Temporal only."""
    del facts
    from core.planning.temporal_contract import date_proposal_from_temporal

    return date_proposal_from_temporal(temporal)


def build_time_proposal(
    facts: Optional[Dict[str, Any]] = None,
    *,
    temporal: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Build time_proposal from Temporal only."""
    del facts
    from core.planning.temporal_contract import time_proposal_from_temporal

    return time_proposal_from_temporal(temporal)


def extract_nlu_proposals(
    luma_response: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Extract date/time proposals from Temporal on a Luma/NLU response."""
    from core.planning.temporal_contract import get_temporal

    if not isinstance(luma_response, dict):
        return {"date_proposal": None, "time_proposal": None}

    # Prefer explicit proposals when already present (tests / upstream).
    explicit_date_proposal = luma_response.get("date_proposal")
    explicit_time_proposal = luma_response.get("time_proposal")

    temporal = get_temporal(luma_response)
    facts = luma_response.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}
    slots = luma_response.get("slots")
    if not isinstance(slots, dict):
        slots = {}

    if isinstance(explicit_date_proposal, dict) and explicit_date_proposal.get("start"):
        date_proposal = explicit_date_proposal
    else:
        date_proposal = build_date_proposal(facts, temporal=temporal)
        if not date_proposal and slots.get("date"):
            date_proposal = {
                "mode": "single_day",
                "start": slots["date"],
            }

    if isinstance(explicit_time_proposal, dict):
        time_proposal = explicit_time_proposal
    else:
        time_proposal = build_time_proposal(facts, temporal=temporal)
        if not time_proposal and slots.get("time"):
            time_proposal = {"mode": "exact", "value": slots["time"]}

    return {
        "date_proposal": date_proposal,
        "time_proposal": time_proposal,
    }


def exact_time_proposal_from_luma(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Return an exact time_proposal from Temporal / Luma response, if present."""
    from core.planning.temporal_contract import get_temporal, time_proposal_from_temporal

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
    tp = time_proposal_from_temporal(get_temporal(luma_response))
    if (
        isinstance(tp, dict)
        and tp.get("mode") == "exact"
        and tp.get("value")
    ):
        return tp
    return None


def merge_session_proposals(
    session_state: Optional[Dict[str, Any]],
    date_proposal: Optional[Dict[str, Any]],
    time_proposal: Optional[Dict[str, Any]],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Merge new NLU proposals with session (new turn overwrites when present)."""
    session_state = session_state or {}
    merged_date = _session_date_proposal(session_state)
    merged_time = _session_time_proposal(session_state)
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
    nlu_facts: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None,
    temporal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Virtual slot view for missing_slots — proposals satisfy planning, not confirmed slots."""
    expanded = dict(slots or {})

    if is_flexible_combined_utterance(temporal, nlu_facts):
        return expanded

    if date_proposal:
        mode = date_proposal.get("mode")
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if mode == "single_day" and start:
            expanded["date"] = start
        elif start and end:
            expanded["date_range"] = {"start": start, "end": end}
            expanded["date"] = start
        elif start and not expanded.get("date") and not expanded.get("date_range"):
            expanded["date"] = start

    # Session-carried Temporal date satisfies planning when proposal mirrors are absent.
    if (
        not expanded.get("date")
        and not expanded.get("date_range")
        and isinstance(temporal, dict)
        and temporal.get("start_date")
    ):
        expanded["date"] = temporal["start_date"]

    if time_proposal:
        if time_proposal.get("mode") == "exact" and time_proposal.get("value"):
            expanded["time"] = time_proposal["value"]
        elif not expanded.get("time"):
            pass

    # CREATE_APPOINTMENT exact time from Temporal
    if (
        intent_name == "CREATE_APPOINTMENT"
        and not expanded.get("time")
        and isinstance(temporal, dict)
        and temporal.get("start_time")
    ):
        expanded["time"] = temporal["start_time"]

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
    *_ignored: Any,
    temporal: Optional[Dict[str, Any]] = None,
) -> list:
    """Remove 'time' from missing_slots when Temporal has an exact start_time."""
    from core.planning.temporal_contract import exact_time_from_temporal

    if intent_name != "CREATE_APPOINTMENT":
        return missing_slots
    if exact_time_from_temporal(temporal) and "time" in missing_slots:
        return [s for s in missing_slots if s != "time"]
    return missing_slots


def _session_v2_proposal(
    session_state: Dict[str, Any],
    *,
    kind: str,
) -> Optional[Dict[str, Any]]:
    """Read a persisted proposal from Session V2 ``planning.proposals``."""
    planning = session_state.get("planning")
    if not isinstance(planning, dict):
        return None
    proposals = planning.get("proposals")
    if not isinstance(proposals, dict):
        return None
    proposal = proposals.get(kind)
    return proposal if isinstance(proposal, dict) else None


def _session_date_proposal(session_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve carried date_proposal from legacy mirrors or V2 nested storage."""
    proposal = session_state.get("date_proposal")
    if isinstance(proposal, dict) and proposal.get("start"):
        return proposal
    if proposal is not None and not isinstance(proposal, dict):
        return proposal
    facts = session_state.get("facts")
    if isinstance(facts, dict):
        fact_proposal = facts.get("date_proposal")
        if isinstance(fact_proposal, dict) and fact_proposal.get("start"):
            return fact_proposal
    nested = _session_v2_proposal(session_state, kind="date")
    if isinstance(nested, dict) and nested.get("start"):
        return nested
    # After availability search the active date may only live on presentation /
    # cache artifacts when proposal mirrors were not rehydrated.
    return _date_proposal_from_availability_artifacts(session_state)


def _date_proposal_from_availability_artifacts(
    session_state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build a single-day date_proposal from presented/cached availability date."""
    presented = session_state.get("presented_availability")
    if isinstance(presented, dict):
        search_date = _normalize_search_date(presented.get("search_date"))
        if search_date:
            return {"mode": "single_day", "start": search_date}
    last = session_state.get("last_execution_result")
    if isinstance(last, dict):
        search_date = _normalize_search_date(last.get("search_date"))
        if search_date:
            return {"mode": "single_day", "start": search_date}
    availability = session_state.get("availability")
    if isinstance(availability, dict):
        presentation = availability.get("presentation") or {}
        if isinstance(presentation, dict):
            presented = presentation.get("presented") or {}
            if isinstance(presented, dict):
                search_date = _normalize_search_date(presented.get("search_date"))
                if search_date:
                    return {"mode": "single_day", "start": search_date}
        cache = availability.get("cache") or {}
        if isinstance(cache, dict):
            search_result = cache.get("search_result") or {}
            if isinstance(search_result, dict):
                search_date = _normalize_search_date(search_result.get("search_date"))
                if search_date:
                    return {"mode": "single_day", "start": search_date}
    return None


def _session_time_proposal(session_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve carried time_proposal from legacy mirrors or V2 nested storage."""
    proposal = session_state.get("time_proposal")
    if proposal is not None:
        return proposal
    facts = session_state.get("facts")
    if isinstance(facts, dict) and facts.get("time_proposal") is not None:
        return facts.get("time_proposal")
    return _session_v2_proposal(session_state, kind="time")


def _has_established_availability_search(session_state: Dict[str, Any]) -> bool:
    """True when session already holds artifacts from a prior availability search."""
    if session_state.get("availability_fingerprint"):
        return True
    last = session_state.get("last_execution_result")
    if isinstance(last, dict) and last:
        return True
    presented = session_state.get("presented_availability")
    if isinstance(presented, dict) and (
        presented.get("slots") or presented.get("times")
    ):
        return True
    availability = session_state.get("availability")
    if not isinstance(availability, dict):
        return False
    if availability.get("fingerprint"):
        return True
    cache = availability.get("cache")
    if isinstance(cache, dict) and cache.get("search_result"):
        return True
    presentation = availability.get("presentation")
    if isinstance(presentation, dict) and presentation.get("presented"):
        return True
    return False


def resolve_execution_proposals(
    plan: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    *,
    context: Optional[ExecutionProposalResolutionContext] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals for execution (availability search / confirmation).

    Time precedence with typed context:
      1. Current-turn explicit time proposal.
      2. Current-turn resolved time constraint.
      3. Session time proposal, only when reuse remains valid.
      4. No time proposal.

    Session date proposals are read from top-level mirrors, facts, and V2
    ``planning.proposals.date``.

    When planning marked ``_revision_invalidated_availability`` *and* the session
    already has an established availability search, the session ``date_proposal``
    belongs to the prior search criteria and must not rebind the new search.
    Session time proposals remain gated by ``revision_invalidated`` alone.

    Initial service clarification (no prior search) still reuses the carried
    ``date_proposal`` so SEARCH keeps the user-stated date.
    """
    plan = plan or {}
    session_state = session_state or {}
    plan_facts = plan.get("facts") if isinstance(plan.get("facts"), dict) else {}
    merged = plan.get("_merged_luma_response")
    if not isinstance(merged, dict):
        merged = {}
    if context is None:
        plan_context = plan.get("execution_proposal_context")
        if isinstance(plan_context, dict):
            context = plan_context
    context_invalidated = bool(
        isinstance(context, dict) and context.get("availability_invalidated")
    )
    revision_invalidated = bool(
        plan.get("_revision_invalidated_availability")
        or merged.get("_revision_invalidated_availability")
        or context_invalidated
    )
    established_availability = _has_established_availability_search(session_state)
    # Mid-flow service/date revision after a prior SEARCH must not reuse the old
    # date. Clarification that only fills service (no prior SEARCH) must keep it.
    suppress_session_date = revision_invalidated and established_availability

    date_proposal = (
        plan.get("date_proposal")
        or plan_facts.get("date_proposal")
        or merged.get("date_proposal")
    )
    if context is not None:
        current_turn_time = context.get("current_turn_time_proposal")
        current_turn_temporal = context.get("current_turn_temporal")
        time_proposal = (
            dict(current_turn_time)
            if isinstance(current_turn_time, dict)
            else build_time_proposal(
                {},
                temporal=(
                    dict(current_turn_temporal)
                    if isinstance(current_turn_temporal, dict)
                    else None
                ),
            )
        )
        reuse_session_time = bool(
            context.get("session_time_proposal_reuse_allowed", True)
        )
    else:
        time_proposal = (
            plan.get("time_proposal")
            or plan_facts.get("time_proposal")
            or merged.get("time_proposal")
        )
        reuse_session_time = True
    if not suppress_session_date:
        date_proposal = date_proposal or _session_date_proposal(session_state)
    if not revision_invalidated and reuse_session_time and not time_proposal:
        time_proposal = _session_time_proposal(session_state)
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
    """Build a datetime range from a canonical availability execution result."""
    if not isinstance(execution_result, dict):
        return None
    availability = execution_result.get("availability")
    if not isinstance(availability, dict):
        return None
    slots_list = availability.get("slots", [])
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
    """Execution view: current-turn date proposals supersede durable slots.date.

    Temporal/proposal dates from the active turn must drive SEARCH, even when a
    prior bind left ``slots.date`` populated (confirmation interruption / revision).
    Time proposals still fill only when a durable time is absent.
    """
    search_slots = dict(slots or {})
    if date_proposal:
        start = date_proposal.get("start")
        end = date_proposal.get("end")
        if start:
            search_slots["date"] = start
        if end:
            search_slots["date_range"] = {"start": start, "end": end}
        elif start and "date_range" in search_slots:
            # Single-day proposal replaces a stale multi-day range.
            search_slots.pop("date_range", None)
    if not search_slots.get("time") and time_proposal:
        if time_proposal.get("value"):
            search_slots["time"] = time_proposal["value"]
        elif time_proposal.get("label"):
            search_slots["time"] = time_proposal["label"]
    return search_slots
