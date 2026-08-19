"""Availability selection algorithms: mode classification and offer matching.

Matching is invoked via Discovery ``Selector`` + ``AvailabilitySelectionPolicy``.
This module does not browse, render, search, or select planner actions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.adapters.nlu.entity_schema_builder import (
    search_criteria_slot_keys_from_entity_schema,
)

# Stable reason codes for planner/clarification mapping.
REASON_MULTIPLE_PRESENTED = "multiple_presented_matches"
REASON_NO_PRESENTED = "no_presented_match"
REASON_MULTIPLE_CACHE = "multiple_cache_matches"
REASON_NOT_IN_CACHE = "explicit_offer_not_in_cache"
REASON_INCOMPLETE = "explicit_selection_incomplete"
REASON_CRITERIA_CHANGED = "search_criteria_changed"
REASON_PRESENTATION_MATCH = "presentation_match"
REASON_CACHE_MATCH = "explicit_cache_match"
REASON_NO_USER_TIME = "no_user_time"
REASON_NO_OFFERS = "no_offers"

_TEMPORAL_CRITERIA_KEYS = frozenset({"date", "start_date", "date_range"})


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


def _normalize_user_time(user_time_raw: Any) -> Optional[str]:
    from core.workflows.availability.fingerprint import _normalize_time_for_fingerprint

    return _normalize_time_for_fingerprint(user_time_raw)


def _current_turn_time(user_facts: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(user_facts, dict):
        return None
    if user_facts.get("time_from_current_turn") and user_facts.get("time"):
        return str(user_facts.get("time"))
    return None


def _extract_user_time(
    *,
    time_proposal: Optional[Dict[str, Any]],
    temporal: Optional[Dict[str, Any]] = None,
    user_facts: Optional[Dict[str, Any]],
) -> Optional[str]:
    current = _current_turn_time(user_facts)
    if current:
        return current
    # Presentation-anchored path may use exact proposals without the provenance flag
    # when callers only supply time_proposal (legacy adapter).
    if isinstance(user_facts, dict) and user_facts.get("time"):
        return str(user_facts.get("time"))
    if isinstance(time_proposal, dict) and time_proposal.get("mode") == "exact":
        value = time_proposal.get("value")
        if value:
            return str(value)
    if isinstance(temporal, dict) and temporal.get("start_time"):
        return str(temporal.get("start_time"))
    return None


def _offer_staff_or_resource(offer: Dict[str, Any]) -> Optional[str]:
    for key in ("staff", "staff_name", "resource", "resource_id", "practitioner"):
        value = offer.get(key)
        if value:
            return str(value).strip().lower()
    return None


def _offer_location(offer: Dict[str, Any]) -> Optional[str]:
    for key in ("location", "location_id", "site"):
        value = offer.get(key)
        if value:
            return str(value).strip().lower()
    return None


def _offer_passes_filters(
    offer: Dict[str, Any],
    *,
    expected_date: Optional[str],
    staff_norm: Optional[str],
    location_norm: Optional[str],
) -> Optional[tuple[str, str]]:
    """Return (offer_date, offer_time) when filters pass; else None."""
    start_raw = offer.get("starts_at") or offer.get("start")
    parsed = _parse_offer_start_parts(start_raw)
    if not parsed:
        return None
    offer_date, offer_time = parsed
    if expected_date and offer_date != expected_date:
        return None
    if staff_norm:
        offer_staff = _offer_staff_or_resource(offer)
        if offer_staff and staff_norm not in offer_staff and offer_staff not in staff_norm:
            return None
        if not offer_staff:
            return None
    if location_norm:
        offer_loc = _offer_location(offer)
        if offer_loc and location_norm not in offer_loc and offer_loc not in location_norm:
            return None
        if not offer_loc:
            return None
    return offer_date, offer_time


def _match_offers(
    offers: List[Dict[str, Any]],
    *,
    user_time_norm: Optional[str],
    expected_date: Optional[str],
    staff: Optional[str] = None,
    location: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Match offers by exact canonical HH:MM only."""
    staff_norm = staff.strip().lower() if staff else None
    location_norm = location.strip().lower() if location else None

    exact: List[Dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        parsed = _offer_passes_filters(
            offer,
            expected_date=expected_date,
            staff_norm=staff_norm,
            location_norm=location_norm,
        )
        if not parsed:
            continue
        _offer_date, offer_time = parsed
        if user_time_norm and offer_time != user_time_norm:
            continue
        exact.append(offer)
    # Semantic meridiem resolution belongs to NLU. Core exact-matches only.
    return exact


def _create_bind_result(
    *,
    slots: Dict[str, Any],
    offer: Dict[str, Any],
    offer_date: str,
    user_time_norm: str,
    execution_result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    from core.planning.temporal_proposal import create_bound_datetime_from_offer

    return create_bound_datetime_from_offer(
        slots=slots,
        offer=offer,
        offer_date=offer_date,
        user_time_norm=user_time_norm,
        execution_result=execution_result,
    )


def search_criteria_changed(
    *,
    user_facts: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when current-turn facts change fingerprint search identity dimensions."""
    return _search_criteria_changed(
        user_facts=user_facts,
        session_state=session_state,
        entity_schema=entity_schema,
    )


def _entity_schema_for_selection(
    *,
    entity_schema: Optional[Mapping[str, Any]],
    user_facts: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> Optional[Mapping[str, Any]]:
    if isinstance(entity_schema, Mapping):
        return entity_schema
    for container in (user_facts, session_state):
        if not isinstance(container, dict):
            continue
        schema = container.get("_entity_schema")
        if isinstance(schema, Mapping):
            return schema
    return None


def _search_criteria_changed(
    *,
    user_facts: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when current-turn facts change fingerprint search identity dimensions."""
    if not isinstance(user_facts, dict) or not isinstance(session_state, dict):
        return False
    session_slots = session_state.get("slots")
    session_slots = session_slots if isinstance(session_slots, dict) else {}
    schema = _entity_schema_for_selection(
        entity_schema=entity_schema,
        user_facts=user_facts,
        session_state=session_state,
    )
    criteria_keys = search_criteria_slot_keys_from_entity_schema(schema)
    for key in sorted(criteria_keys - _TEMPORAL_CRITERIA_KEYS):
        if not user_facts.get(f"{key}_from_current_turn"):
            continue
        new_val = user_facts.get(key)
        if new_val is None or new_val == "":
            continue
        old_val = session_slots.get(key)
        if old_val is None or old_val == "":
            # First acquisition is not a mid-flow criteria change for selection.
            continue
        if str(new_val).strip().lower() != str(old_val).strip().lower():
            return True
    return False


def classify_selection_mode(
    *,
    user_facts: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> str:
    """Classify selection mode without performing matching.

    Returns one of:
    ``ambiguous``, ``explicit_complete``, ``explicit_incomplete``, ``criteria_changed``.
    """
    _ = time_proposal, temporal
    if _search_criteria_changed(
        user_facts=user_facts,
        session_state=session_state,
        entity_schema=entity_schema,
    ):
        return "criteria_changed"

    facts = user_facts if isinstance(user_facts, dict) else {}

    # Ordinal / vague / demonstrative → always presentation-anchored when flagged.
    if facts.get("ordinal") or facts.get("vague_period") or facts.get("demonstrative"):
        return "ambiguous"

    has_explicit_date = bool(facts.get("date_from_current_turn") and facts.get("date"))
    has_explicit_time = bool(facts.get("time_from_current_turn") and facts.get("time"))

    if has_explicit_date and has_explicit_time:
        return "explicit_complete"
    if has_explicit_date and not has_explicit_time:
        return "explicit_incomplete"
    return "ambiguous"
