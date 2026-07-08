"""
Slot fingerprint utilities for availability resolution.

Fingerprints represent **search criteria only** — the parameters that
triggered or would trigger SEARCH_AVAILABILITY. Selection and presentation
state (time, time_proposal, page_index, presented_availability) must never
affect the fingerprint.

Search criteria: organization_id, service_id, date, and optional
location/staff/resource when applicable.
"""

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SEARCH_CRITERIA_SLOT_KEYS = frozenset(
    {
        "service_id",
        "date",
        "start_date",
        "date_range",
        "location",
        "staff",
        "resource",
        "resource_id",
    }
)


def _normalize_time_for_fingerprint(time_value: Any) -> Optional[str]:
    """Normalize time strings to HH:MM (24h) for stable comparison outside fingerprints."""
    if time_value is None:
        return None
    raw = str(time_value).lower().strip()
    if not raw:
        return None

    m = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"

    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridiem = m.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    return raw


def _extract_search_criteria_slots(slots: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only durable search-parameter slots; drop selection/presentation fields."""
    if not isinstance(slots, dict):
        return {}
    return {
        key: value
        for key, value in slots.items()
        if key in _SEARCH_CRITERIA_SLOT_KEYS and value is not None
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


def _get_default_organization_id() -> int:
    """Return organization_id from ORG_ID env var with safe default."""
    value = os.getenv("ORG_ID", "1")
    try:
        org_id = int(value)
        if org_id <= 0:
            raise ValueError("ORG_ID must be positive")
        return org_id
    except Exception:  # noqa: BLE001
        logger.warning("Invalid ORG_ID env value '%s', defaulting to 1", value)
        return 1


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
    slots: Dict[str, Any],
    organization_id: Optional[int] = None,
) -> Any:
    """Resolve request-scoped organization_id for fingerprint (never from durable session)."""
    if organization_id is not None:
        return organization_id
    slot_org = (slots or {}).get("organization_id")
    if slot_org is not None and slot_org != "":
        return slot_org
    return _get_default_organization_id()


def build_availability_fingerprint_slots(
    slots: Dict[str, Any],
    *,
    intent_name: Optional[str] = None,
    organization_id: Optional[int] = None,
    date_proposal: Optional[Dict[str, Any]] = None,
    time_proposal: Optional[Dict[str, Any]] = None,
    date_constraint: Optional[Dict[str, Any]] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
    nlu_facts: Optional[Dict[str, Any]] = None,
    luma_response: Optional[Dict[str, Any]] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble canonical **search-criteria** slot inputs for fingerprint store/compare.

    Planning/durable slots intentionally omit organization_id; this helper injects
    request-scoped organization_id without persisting it to session slots.

    Selection and presentation fields are excluded: time, time_proposal,
    page_index, presented_availability, selected_time, datetime_range, etc.
    """
    from core.orchestration.temporal_proposal import expand_slots_for_planning

    luma_response = luma_response if isinstance(luma_response, dict) else {}
    session_state = session_state if isinstance(session_state, dict) else {}
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
        "date_proposal=%s time_proposal=%s date_constraint=%s time_constraint=%s "
        "nlu_facts_keys=%s session_page_index=%s presented_slot_count=%s "
        "session_date_proposal=%s session_time_proposal=%s "
        "luma_date_proposal=%s luma_time_proposal=%s luma_operation=%s",
        slots,
        intent_name,
        organization_id,
        date_proposal,
        time_proposal,
        date_constraint,
        time_constraint,
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

    criteria_slots = _extract_search_criteria_slots(slots or {})

    expanded = expand_slots_for_planning(
        criteria_slots,
        date_proposal=proposals["date_proposal"],
        time_proposal=None,
        date_constraint=(
            date_constraint
            if date_constraint is not None
            else luma_response.get("date_constraint")
        ),
        time_constraint=None,
        nlu_facts=nlu_facts,
        intent_name=intent_name,
    )
    expanded.pop("time", None)
    expanded.pop("datetime_range", None)
    expanded["organization_id"] = _resolve_organization_id_for_fingerprint(
        slots, organization_id
    )

    logger.debug(
        "[FINGERPRINT_SLOTS_OUTPUT] criteria_slots=%s",
        expanded,
    )
    return expanded


def compute_availability_fingerprint(
    slots: Dict[str, Any], intent_name: Optional[str] = None
) -> Optional[str]:
    """
    Compute a deterministic fingerprint from **search criteria** slots.

    Includes: organization_id, service_id, date, and optional location/staff.
    Never includes time or presentation/selection fields.
    """
    logger.debug(
        "[FINGERPRINT_COMPUTE_INPUT] slots=%s intent=%s",
        slots,
        intent_name,
    )

    normalized_org, normalized_service, normalized_date, ignored_time = (
        _extract_normalized_slots(slots)
    )

    if not normalized_service:
        logger.debug("[FINGERPRINT_COMPUTE_OUTPUT] skipped: missing service_id")
        return None

    fingerprint_dict: Dict[str, Any] = {
        "organization_id": normalized_org,
        "service_id": normalized_service,
        "date": normalized_date,
    }

    for optional_key in ("location", "staff", "resource", "resource_id"):
        raw = slots.get(optional_key)
        if raw is not None and raw != "":
            fingerprint_dict[optional_key] = str(raw).lower().strip()

    fingerprint_json = json.dumps(fingerprint_dict, sort_keys=True, ensure_ascii=False)
    fingerprint_hash = hashlib.sha256(fingerprint_json.encode("utf-8")).hexdigest()

    logger.debug(
        "[FINGERPRINT_COMPUTE_OUTPUT] fingerprint_dict=%s ignored_time=%s hash=%s",
        fingerprint_dict,
        ignored_time,
        fingerprint_hash[:16],
    )
    return fingerprint_hash


def slots_match_availability_fingerprint(
    slots: Dict[str, Any],
    stored_fingerprint: Optional[str],
    intent_name: Optional[str] = None,
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

    current_fingerprint = compute_availability_fingerprint(slots, intent_name)
    if not current_fingerprint:
        return False

    return current_fingerprint == stored_fingerprint
