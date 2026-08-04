"""Planning-owned detection of booking facts and field revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Set

from core.adapters.nlu.entity_schema_builder import (
    canonicalize_search_criteria_key,
    search_criteria_slot_keys_from_entity_schema,
)


def has_committed_create_appointment(
    slots: Optional[Dict[str, Any]],
) -> bool:
    """True when CREATE_APPOINTMENT slots contain a persisted booking identifier."""
    if not isinstance(slots, dict):
        return False
    booking_id = slots.get("booking_id")
    return booking_id is not None and booking_id != ""


@dataclass(frozen=True)
class FieldChange:
    field: str
    from_value: Any = None
    to_value: Any = None


@dataclass(frozen=True)
class BookingRevision:
    """Booking fields changed by current-turn facts relative to durable state."""

    service: bool = False
    date: bool = False
    time: bool = False
    # Non-service search-criteria keys that changed (e.g. staff_id).
    criteria: bool = False
    changes: tuple = ()

    @property
    def any(self) -> bool:
        return (
            bool(self.changes)
            or self.service
            or self.date
            or self.time
            or self.criteria
        )

    @property
    def invalidates_availability(self) -> bool:
        """True when trusted availability search identity must be cleared."""
        return self.service or self.date or self.criteria


def _normalize_date_value(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    return value.split("T")[0].split(" ")[0]


def _meaningful_text(value: Any) -> Optional[str]:
    """Return a non-empty string value, or None when absent/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _active_search_date(session_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Canonical active search date for revision comparison.

    Prefer durable ``slots.date``. After exploratory availability search the
    date often lives only in the carried ``date_proposal`` (fingerprint /
    search criteria), not in durable slots — that proposal is still the
    active search date and must participate in revision detection.
    """
    if not isinstance(session_state, dict):
        return None
    session_slots = session_state.get("slots")
    if isinstance(session_slots, dict):
        slotted = _normalize_date_value(session_slots.get("date"))
        if slotted:
            return slotted

    from core.planning.temporal_proposal import _session_date_proposal

    proposal = _session_date_proposal(session_state)
    if isinstance(proposal, dict):
        return _normalize_date_value(proposal.get("start"))
    return None


def _entity_schema_from_payload(
    luma_response: Optional[Dict[str, Any]],
) -> Optional[Mapping[str, Any]]:
    if not isinstance(luma_response, dict):
        return None
    schema = luma_response.get("_entity_schema")
    return schema if isinstance(schema, dict) else None


def _current_turn_value(
    luma_response: Dict[str, Any],
    key: str,
) -> Optional[str]:
    """Prefer facts, then promoted slots, for a search-criteria key."""
    facts = luma_response.get("facts")
    if isinstance(facts, dict):
        value = _meaningful_text(facts.get(key))
        if value:
            return value
        # Legacy alias (staff → staff_id already canonicalized by callers).
        for raw_key, raw_val in facts.items():
            if canonicalize_search_criteria_key(str(raw_key)) == key:
                value = _meaningful_text(raw_val)
                if value:
                    return value
    slots = luma_response.get("slots")
    if isinstance(slots, dict):
        return _meaningful_text(slots.get(key))
    return None


def detect_booking_revision(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
    *,
    entity_schema: Optional[Mapping[str, Any]] = None,
) -> BookingRevision:
    """Detect mid-flow search-criteria / temporal replacements vs durable state.

    First acquisition (None → value), same-value restatement, and absent current-turn
    values are not revisions. Only a prior durable value replaced by a different
    meaningful value is classified as a revision (and may invalidate availability).

    Availability-criteria business keys come from
    ``search_criteria_slot_keys_from_entity_schema`` (effective
    ``availability_criteria``, with role defaults when absent).
    """
    session_slots = (
        session_state.get("slots") if isinstance(session_state, dict) else None
    ) or {}
    changes = []
    schema = entity_schema or _entity_schema_from_payload(luma_response)
    criteria_keys: Set[str] = set(search_criteria_slot_keys_from_entity_schema(schema))
    # Temporal keys are handled separately below.
    criteria_keys -= {"date", "start_date", "date_range"}

    service_changed = False
    new_service = None
    if isinstance(luma_response, dict):
        new_service = _current_turn_value(luma_response, "service_id")
    current_service = _meaningful_text(session_slots.get("service_id"))
    if new_service and current_service and new_service != current_service:
        service_changed = True
        changes.append(FieldChange("service", current_service, new_service))

    criteria_changed = False
    if isinstance(luma_response, dict):
        for key in sorted(criteria_keys):
            if key == "service_id":
                continue  # tracked via service flag for restore compatibility
            new_val = _current_turn_value(luma_response, key)
            current_val = _meaningful_text(session_slots.get(key))
            if new_val and current_val and new_val != current_val:
                criteria_changed = True
                changes.append(FieldChange(key, current_val, new_val))

    date_changed = False
    new_date = None
    if isinstance(luma_response, dict):
        from core.planning.temporal_proposal import extract_nlu_proposals

        proposals = extract_nlu_proposals(luma_response)
        date_proposal = proposals.get("date_proposal")
        if isinstance(date_proposal, dict) and date_proposal.get("start"):
            new_date = _normalize_date_value(date_proposal.get("start"))
        facts = luma_response.get("facts")
        if not new_date and isinstance(facts, dict):
            dates = facts.get("dates")
            if isinstance(dates, list) and dates:
                new_date = _normalize_date_value(dates[0])
    current_date = _active_search_date(session_state)
    if new_date and current_date and new_date != current_date:
        date_changed = True
        changes.append(FieldChange("date", current_date, new_date))

    time_changed = False
    if isinstance(luma_response, dict):
        from core.planning.temporal_proposal import exact_time_proposal_from_luma
        from core.workflows.availability.fingerprint import (
            _normalize_time_for_fingerprint,
        )

        time_proposal = exact_time_proposal_from_luma(luma_response)
        if time_proposal and time_proposal.get("value"):
            new_time = _normalize_time_for_fingerprint(time_proposal.get("value"))
            current_time = _normalize_time_for_fingerprint(session_slots.get("time"))
            if new_time and current_time and new_time != current_time:
                time_changed = True
                changes.append(FieldChange("time", current_time, new_time))

    return BookingRevision(
        service=service_changed,
        date=date_changed,
        time=time_changed,
        criteria=criteria_changed,
        changes=tuple(changes),
    )


def has_revision_facts(luma_response: Optional[Dict[str, Any]]) -> bool:
    """True when this turn carries actionable booking revision facts.

    Must ignore session-carried date/time that Stage 02 / merge already projected
    onto the working payload (``date_proposal``, merged Temporal). Prefer
    ``_current_turn_has_date`` / ``_current_turn_has_time`` provenance when set.
    """
    if not isinstance(luma_response, dict):
        return False

    # Stage 02 stamps these from NLU-only proposals before session merge.
    if "_current_turn_has_date" in luma_response or "_current_turn_has_time" in luma_response:
        return bool(
            luma_response.get("_current_turn_has_date")
            or luma_response.get("_current_turn_has_time")
        )

    from core.planning.temporal_contract import (
        get_temporal,
        temporal_has_date_material,
        temporal_has_time_material,
    )
    from core.planning.temporal_proposal import exact_time_proposal_from_luma

    if exact_time_proposal_from_luma(luma_response):
        return True

    temporal = get_temporal(luma_response)
    if temporal_has_date_material(temporal) or temporal_has_time_material(temporal):
        return True

    facts = luma_response.get("facts")
    if isinstance(facts, dict):
        # Inbound compat for fixtures that still only populate legacy bags.
        if isinstance(facts.get("times"), list) and facts["times"]:
            return True
        if isinstance(facts.get("dates"), list) and facts["dates"]:
            return True

    return False


def has_actionable_booking_facts(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when current-turn facts must reach normal booking planning."""
    if has_revision_facts(luma_response):
        return True
    if not isinstance(luma_response, dict):
        return False
    facts = luma_response.get("facts")
    if not isinstance(facts, dict) or not facts.get("service_id"):
        return False
    current = (
        (session_state.get("slots") or {}).get("service_id")
        if isinstance(session_state, dict)
        else None
    )
    return str(facts["service_id"]) != str(current) if current else True
