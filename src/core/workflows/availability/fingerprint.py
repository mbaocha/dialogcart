"""
Slot fingerprint utilities for availability resolution.

Fingerprints represent **search criteria only** — the parameters that
triggered or would trigger SEARCH_AVAILABILITY. Selection and presentation
state (time, time_proposal, page_index, presented_availability) must never
affect the fingerprint hash itself.

Readiness comparison may treat hydrating the already-presented search day
into ``date_proposal`` as equivalent to an undated exploratory fingerprint
(see ``slots_match_availability_fingerprint_for_readiness``). That is a
readiness equivalence check — not a fingerprint field.

Availability criteria come from ``search_criteria_slot_keys_from_entity_schema``
(single source of truth). When possible the hashed identity aligns with the
availability request adapter output.
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict, Mapping, Optional

from core.adapters.nlu.entity_schema_builder import (
    canonicalize_search_criteria_key,
    search_criteria_slot_keys_from_entity_schema,
)

logger = logging.getLogger(__name__)


def _canonicalize_criteria_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy aliases (e.g. staff → staff_id) for search criteria."""
    if not isinstance(slots, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in slots.items():
        if value is None:
            continue
        canonical = canonicalize_search_criteria_key(str(key))
        # Prefer an already-canonical value over a legacy alias.
        if canonical in out and key != canonical:
            continue
        out[canonical] = value
    return out


def _normalize_time_for_fingerprint(time_value: Any) -> Optional[str]:
    """Normalize clock strings to canonical ``HH:MM`` (24-hour).

    Contract:
    - Valid clock input → ``HH:MM`` string (zero-padded).
    - Absent / empty / unrecognized / out-of-range → ``None`` (explicit failure).

    Callers must treat only a non-``None`` return as successful normalization.
    Never returns the raw input string for unrecognized forms.

    Supported forms (optional spaces): ``1:30``, ``1.30``, ``13:30``, ``1.30pm``,
    ``5pm``, ``9``, ``9am``.
    """
    if time_value is None:
        return None
    raw = str(time_value).lower().strip()
    if not raw:
        return None

    compact = re.sub(r"\s+", "", raw)

    # Colon or dot minute separator without meridiem: 1:30 / 1.30 / 13:30
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", compact)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None

    # Hour with optional minutes (colon or dot) and required meridiem: 1.30pm / 5pm
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(am|pm)", compact)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3)
        if hour < 1 or hour > 12 or minute < 0 or minute > 59:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    # Bare hour without meridiem: "9" → 09:00
    m = re.fullmatch(r"(\d{1,2})", compact)
    if m:
        hour = int(m.group(1))
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"
        return None

    return None


def _criteria_keys(
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> frozenset:
    return search_criteria_slot_keys_from_entity_schema(entity_schema)


def _extract_search_criteria_slots(
    slots: Dict[str, Any],
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep only durable availability-criteria slots; drop selection fields."""
    canonical = _canonicalize_criteria_slots(slots if isinstance(slots, dict) else {})
    allowed = _criteria_keys(entity_schema)
    return {
        key: value
        for key, value in canonical.items()
        if key in allowed and value is not None
    }


def _extract_normalized_slots(
    slots: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract and normalize slots for fingerprint computation.

    Returns:
        Tuple of (organization_id, service_id, date, time) as normalized strings.
        Time is extracted for diagnostics only — not used in fingerprint hashing.
    """
    organization_id = slots.get("organization_id")
    normalized_org = str(organization_id).lower().strip() if organization_id else None

    service_id = slots.get("service_id")
    if not service_id:
        return (normalized_org, None, None, None)
    normalized_service = str(service_id).lower().strip()

    date = slots.get("date") or slots.get("start_date")
    if not date:
        date_range = slots.get("date_range")
        if isinstance(date_range, dict):
            date = date_range.get("start") or date_range.get("start_date")

    normalized_date = str(date).lower().strip() if date else None
    normalized_time = _normalize_time_for_fingerprint(slots.get("time"))

    return (normalized_org, normalized_service, normalized_date, normalized_time)


def _resolve_fingerprint_proposals(
    *,
    luma_response: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Resolve date/time proposals for fingerprint expansion (session fallbacks included)."""
    luma_response = luma_response if isinstance(luma_response, dict) else {}
    session_state = session_state if isinstance(session_state, dict) else {}
    session_facts = session_state.get("facts") or {}

    resolved_date = (
        date_proposal
        or luma_response.get("date_proposal")
        or session_state.get("date_proposal")
        or (session_facts.get("date_proposal") if isinstance(session_facts, dict) else None)
    )
    resolved_time = (
        time_proposal
        or luma_response.get("time_proposal")
        or session_state.get("time_proposal")
        or (session_facts.get("time_proposal") if isinstance(session_facts, dict) else None)
    )
    return {"date_proposal": resolved_date, "time_proposal": resolved_time}


def _resolve_organization_id_for_fingerprint(
    organization_id: int,
) -> int:
    """Return request-scoped organization_id for fingerprint (never derived elsewhere)."""
    return organization_id


def _entity_schema_from_sources(
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
    luma_response: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    slots: Optional[Dict[str, Any]] = None,
) -> Optional[Mapping[str, Any]]:
    if isinstance(entity_schema, Mapping):
        return entity_schema
    for container in (luma_response, session_state, slots):
        if not isinstance(container, dict):
            continue
        schema = container.get("_entity_schema")
        if isinstance(schema, Mapping):
            return schema
        facts = container.get("facts")
        if isinstance(facts, dict):
            nested = facts.get("_entity_schema")
            if isinstance(nested, Mapping):
                return nested
    return None


def build_availability_fingerprint_slots(
    slots: Dict[str, Any],
    *,
    intent_name: Optional[str] = None,
    organization_id: int,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    nlu_facts: Optional[Dict[str, Any]] = None,
    luma_response: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble canonical **search-criteria** slot inputs for fingerprint store/compare.

    Planning/durable slots intentionally omit organization_id; this helper injects
    request-scoped organization_id without persisting it to session slots.

    Selection and presentation fields are excluded: time, time_proposal,
    page_index, presented_availability, selected_time, datetime_range, etc.
    """
    from core.planning.temporal_proposal import expand_slots_for_planning

    luma_response = luma_response if isinstance(luma_response, dict) else {}
    session_state = session_state if isinstance(session_state, dict) else {}
    schema = _entity_schema_from_sources(
        entity_schema=entity_schema,
        luma_response=luma_response,
        session_state=session_state,
        slots=slots if isinstance(slots, dict) else None,
    )
    proposals = _resolve_fingerprint_proposals(
        luma_response=luma_response,
        session_state=session_state,
        date_proposal=date_proposal,
        time_proposal=time_proposal,
    )

    presentation = session_state.get("availability_presentation") or {}
    presented = session_state.get("presented_availability") or {}
    logger.debug(
        "[FINGERPRINT_SLOTS_INPUT] raw_slots=%s intent=%s organization_id=%s "
        "date_proposal=%s time_proposal=%s temporal=%s "
        "nlu_facts_keys=%s session_page_index=%s presented_slot_count=%s "
        "session_date_proposal=%s session_time_proposal=%s "
        "luma_date_proposal=%s luma_time_proposal=%s luma_operation=%s",
        slots,
        intent_name,
        organization_id,
        date_proposal,
        time_proposal,
        temporal or luma_response.get("temporal"),
        list(nlu_facts.keys()) if isinstance(nlu_facts, dict) else None,
        presentation.get("page_index"),
        len(presented.get("slots") or []) if isinstance(presented, dict) else None,
        session_state.get("date_proposal"),
        session_state.get("time_proposal"),
        luma_response.get("date_proposal"),
        luma_response.get("time_proposal"),
        (luma_response.get("facts") or {}).get("operation")
        if isinstance(luma_response.get("facts"), dict)
        else luma_response.get("operation"),
    )

    criteria_slots = _extract_search_criteria_slots(
        slots or {}, entity_schema=schema
    )

    expanded = expand_slots_for_planning(
        criteria_slots,
        date_proposal=proposals["date_proposal"],
        time_proposal=None,
        nlu_facts=nlu_facts,
        intent_name=intent_name,
        temporal=temporal or luma_response.get("temporal"),
    )
    expanded.pop("time", None)
    expanded.pop("datetime_range", None)
    expanded["organization_id"] = _resolve_organization_id_for_fingerprint(
        organization_id
    )
    if schema is not None:
        expanded["_entity_schema"] = schema

    logger.debug(
        "[FINGERPRINT_SLOTS_OUTPUT] criteria_slots=%s",
        {k: v for k, v in expanded.items() if k != "_entity_schema"},
    )
    return expanded


def compute_availability_fingerprint(
    slots: Dict[str, Any],
    intent_name: Optional[str] = None,
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """
    Compute a deterministic fingerprint from **availability criteria** slots.

    Includes organization_id, service_id, date, and any other keys from
    ``search_criteria_slot_keys_from_entity_schema``. Never includes time or
    presentation/selection fields.
    """
    logger.debug(
        "[FINGERPRINT_COMPUTE_INPUT] slots=%s intent=%s",
        slots,
        intent_name,
    )

    schema = _entity_schema_from_sources(
        entity_schema=entity_schema,
        slots=slots if isinstance(slots, dict) else None,
    )
    criteria = _canonicalize_criteria_slots(slots if isinstance(slots, dict) else {})
    service = criteria.get("service_id")
    if not service:
        logger.debug("[FINGERPRINT_COMPUTE_OUTPUT] skipped: missing service_id")
        return None

    # Prefer identity aligned with the availability request adapter.
    from core.workflows.availability.request_adapter import (
        build_service_availability_request,
    )

    org = criteria.get("organization_id")
    try:
        org_id: Any = int(org) if org is not None else None
    except (TypeError, ValueError):
        org_id = org
    request = build_service_availability_request(
        criteria,
        organization_id=org_id,
        api_service_id=service,
        entity_schema=schema,
    )
    identity = dict(request.get("identity") or {})
    # Preserve legacy fingerprint shape when organization_id was omitted.
    if org is None:
        identity["organization_id"] = None
    return _hash_fingerprint_identity(identity, ignored_time=criteria.get("time"))


def _hash_fingerprint_identity(
    identity: Mapping[str, Any],
    *,
    ignored_time: Any = None,
) -> Optional[str]:
    if not identity.get("service_id"):
        return None
    normalized: Dict[str, Any] = {}
    for key, value in identity.items():
        if value is None or value == "" or key == "_entity_schema":
            continue
        if key == "organization_id":
            normalized[key] = str(value).lower().strip()
        elif key in ("service_id", "date"):
            normalized[key] = str(value).lower().strip()
        else:
            normalized[key] = str(value).lower().strip()
    if "service_id" not in normalized:
        return None
    # Ensure date key present (may be None) for stable salon/hotel hashes.
    if "date" not in normalized:
        normalized["date"] = None
    fingerprint_json = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    fingerprint_hash = hashlib.sha256(fingerprint_json.encode("utf-8")).hexdigest()
    logger.debug(
        "[FINGERPRINT_COMPUTE_OUTPUT] fingerprint_dict=%s ignored_time=%s hash=%s",
        normalized,
        ignored_time,
        fingerprint_hash[:16],
    )
    return fingerprint_hash


def slots_match_availability_fingerprint(
    slots: Dict[str, Any],
    stored_fingerprint: Optional[str],
    intent_name: Optional[str] = None,
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> bool:
    """
    Check if current search criteria match a stored availability fingerprint.

    Args:
        slots: Current slot values (search criteria only — time is ignored)
        stored_fingerprint: Previously stored fingerprint (from session)
        intent_name: Optional intent name (kept for backward compatibility)

    Returns:
        True if fingerprints match (availability is resolved for these search params)
    """
    if not stored_fingerprint:
        return False

    current_fingerprint = compute_availability_fingerprint(
        slots, intent_name, entity_schema=entity_schema
    )
    if not current_fingerprint:
        return False

    return current_fingerprint == stored_fingerprint


def _active_presented_search_date(
    session_state: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Concrete day already shown from the trusted availability presentation/cache."""
    if not isinstance(session_state, dict):
        return None
    from core.workflows.availability.presentation import normalize_search_date

    presented = session_state.get("presented_availability")
    if isinstance(presented, dict):
        search_date = normalize_search_date(presented.get("search_date"))
        if search_date:
            return search_date
    last = session_state.get("last_execution_result")
    if isinstance(last, dict):
        search_date = normalize_search_date(last.get("search_date"))
        if search_date:
            return search_date
    availability = session_state.get("availability")
    if isinstance(availability, dict):
        presentation = availability.get("presentation") or {}
        if isinstance(presentation, dict):
            nested = presentation.get("presented") or {}
            if isinstance(nested, dict):
                search_date = normalize_search_date(nested.get("search_date"))
                if search_date:
                    return search_date
        cache = availability.get("cache") or {}
        if isinstance(cache, dict):
            search_result = cache.get("search_result") or {}
            if isinstance(search_result, dict):
                search_date = normalize_search_date(search_result.get("search_date"))
                if search_date:
                    return search_date
    return None


def slots_match_availability_fingerprint_for_readiness(
    slots: Dict[str, Any],
    stored_fingerprint: Optional[str],
    *,
    intent_name: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Fingerprint match for availability readiness, including presentation-date hydration.

    Undated exploratory SEARCH stores a service-only fingerprint while the
    concrete day lives on presentation. Hydrating that same day into
    ``date_proposal`` must not look like a criteria change.

    Equivalence holds only when the current date equals the already-presented
    search day and the undated criteria still match the stored fingerprint.
    A different explicit date remains a real criteria change.
    """
    schema = entity_schema or _entity_schema_from_sources(
        slots=slots if isinstance(slots, dict) else None,
        session_state=session_state,
    )
    if slots_match_availability_fingerprint(
        slots, stored_fingerprint, intent_name, entity_schema=schema
    ):
        return True
    if not stored_fingerprint:
        return False

    presented_date = _active_presented_search_date(session_state)
    if not presented_date:
        return False

    _, _, current_date, _ = _extract_normalized_slots(slots)
    presented_norm = str(presented_date).lower().strip()
    if not current_date or current_date != presented_norm:
        return False

    undated_slots = {
        key: value
        for key, value in slots.items()
        if key not in ("date", "start_date", "date_range") and value is not None
    }
    return slots_match_availability_fingerprint(
        undated_slots, stored_fingerprint, intent_name, entity_schema=schema
    )
