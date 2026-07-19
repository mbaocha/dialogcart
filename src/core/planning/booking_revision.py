"""Planning-owned detection of booking facts and field revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


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
    changes: tuple = ()

    @property
    def any(self) -> bool:
        return bool(self.changes) or self.service or self.date or self.time


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


def detect_booking_revision(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> BookingRevision:
    """Detect mid-flow service/date/time replacements against durable session slots.

    First acquisition (None → value), same-value restatement, and absent current-turn
    values are not revisions. Only a prior durable value replaced by a different
    meaningful value is classified as a revision (and may invalidate availability).
    """
    session_slots = (
        session_state.get("slots") if isinstance(session_state, dict) else None
    ) or {}
    changes = []

    service_changed = False
    new_service = None
    if isinstance(luma_response, dict):
        facts = luma_response.get("facts")
        if isinstance(facts, dict):
            new_service = facts.get("service_id")
    new_service = _meaningful_text(new_service)
    current_service = _meaningful_text(session_slots.get("service_id"))
    if new_service and current_service and new_service != current_service:
        service_changed = True
        changes.append(FieldChange("service", current_service, new_service))

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
    current_date = _normalize_date_value(session_slots.get("date"))
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
        changes=tuple(changes),
    )


def has_revision_facts(luma_response: Optional[Dict[str, Any]]) -> bool:
    """True when this turn carries actionable booking revision facts."""
    if not isinstance(luma_response, dict):
        return False

    from core.planning.temporal_proposal import (
        exact_time_proposal_from_luma,
        extract_nlu_proposals,
    )

    if exact_time_proposal_from_luma(luma_response):
        return True
    date_proposal = extract_nlu_proposals(luma_response).get("date_proposal")
    if isinstance(date_proposal, dict) and date_proposal.get("start"):
        return True

    facts = luma_response.get("facts")
    if isinstance(facts, dict):
        if isinstance(facts.get("times"), list) and facts["times"]:
            return True
        if isinstance(facts.get("dates"), list) and facts["dates"]:
            return True

    time_constraint = luma_response.get("time_constraint")
    return bool(
        isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
        and time_constraint.get("start")
    )


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
