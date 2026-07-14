"""Confirmation gate turn classification (accept / reject / revise / none).

While a booking confirmation is pending, every turn is classified once.
Downstream code should branch on this decision rather than re-checking
raw Luma intents and session flags in multiple places.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ConfirmationGateTurn(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REVISE = "REVISE"
    NONE = "NONE"


def get_confirmation_state(session_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Read confirmation_state from booking (canonical) or top-level (legacy)."""
    if not isinstance(session_state, dict):
        return None
    booking = session_state.get("booking")
    if isinstance(booking, dict) and booking.get("confirmation_state") is not None:
        return booking.get("confirmation_state")
    return session_state.get("confirmation_state")


def set_confirmation_state(
    state: Optional[Dict[str, Any]],
    value: Optional[str],
) -> Dict[str, Any]:
    """Set canonical ``booking.confirmation_state``; mirror top-level for compat.

    ``value`` is ``\"pending\"``, ``\"confirmed\"``, or ``None`` (cleared).
    Mutates ``state`` in place and returns it.
    """
    if not isinstance(state, dict):
        return {}

    booking = state.get("booking")
    if not isinstance(booking, dict):
        booking = {}
    else:
        booking = dict(booking)

    if value is None:
        booking.pop("confirmation_state", None)
        state.pop("confirmation_state", None)
        state["booking"] = booking
        return state

    booking["confirmation_state"] = value
    state["booking"] = booking
    # Deprecated mirror — keep in sync until all readers use get_confirmation_state.
    state["confirmation_state"] = value
    return state


def has_committed_create_appointment(
    slots: Optional[Dict[str, Any]],
) -> bool:
    """True when CREATE_APPOINTMENT slots carry a persisted booking_id (post-commit)."""
    if not isinstance(slots, dict):
        return False
    booking_id = slots.get("booking_id")
    return booking_id is not None and booking_id != ""


def consume_confirmation_state(
    state: Optional[Dict[str, Any]],
    *,
    reason: str = "",
) -> Dict[str, Any]:
    """Clear confirmation_state from session or merged-response dict (booking mirrors too)."""
    if not isinstance(state, dict):
        return {}
    set_confirmation_state(state, None)
    if reason:
        logger.debug(
            "[BOOKING_CONFIRMATION] consume_confirmation_state reason=%s", reason)
    return state


def consume_create_appointment_confirmation(
    session_state: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]] = None,
    *,
    reason: str = "commit_consumed",
) -> None:
    """Successful CREATE_APPOINTMENT commit consumes pre-commit confirmation."""
    consume_confirmation_state(session_state, reason=reason)
    if isinstance(merged_luma_response, dict):
        consume_confirmation_state(merged_luma_response, reason=reason)


def normalize_confirmation_state(
    state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ensure booking and top-level confirmation_state agree (booking wins)."""
    if not isinstance(state, dict):
        return {}

    booking = state.get("booking")
    booking_state = (
        booking.get("confirmation_state") if isinstance(
            booking, dict) else None
    )
    top_state = state.get("confirmation_state")

    if booking_state is not None:
        return set_confirmation_state(state, booking_state)
    if top_state is not None:
        return set_confirmation_state(state, top_state)
    return set_confirmation_state(state, None)


def clear_pending_confirmation(
    state: Optional[Dict[str, Any]],
    *,
    clear_time: bool = True,
    clear_date: bool = False,
    clear_availability: bool = False,
    clear_service: bool = False,
    reason: str = "",
) -> Dict[str, Any]:
    """Clear pending confirmation from a session or merged-response dict.

    Always clears confirmation_state via set_confirmation_state.
    When clear_time/clear_date are set, also drops bound temporal fields so the
    planner does not immediately re-enter the confirmation gate.
    clear_availability drops presented/search artifacts that are no longer valid.
    clear_service drops durable service_id so a new service fact can replace it.

    Mutates ``state`` in place and returns it.
    """
    if not isinstance(state, dict):
        return {}

    set_confirmation_state(state, None)

    if clear_time or clear_date or clear_service:
        slots = state.get("slots")
        if isinstance(slots, dict):
            slots = dict(slots)
            if clear_time:
                slots.pop("time", None)
            if clear_date:
                slots.pop("date", None)
                slots.pop("date_range", None)
            if clear_service:
                slots.pop("service_id", None)
            state["slots"] = slots

        state.pop("resolved_datetime_range", None)

        facts = state.get("facts")
        if isinstance(facts, dict):
            facts = dict(facts)
            facts.pop("resolved_datetime_range", None)
            fact_slots = facts.get("slots")
            if isinstance(fact_slots, dict):
                fact_slots = dict(fact_slots)
                if clear_time:
                    fact_slots.pop("time", None)
                if clear_date:
                    fact_slots.pop("date", None)
                    fact_slots.pop("date_range", None)
                if clear_service:
                    fact_slots.pop("service_id", None)
                facts["slots"] = fact_slots
            state["facts"] = facts

    if clear_availability:
        state.pop("presented_availability", None)
        state.pop("availability_fingerprint", None)
        state.pop("last_execution_result", None)

    if reason:
        logger.debug(
            "[BOOKING_CONFIRMATION] clear_pending reason=%s clear_time=%s "
            "clear_date=%s clear_availability=%s clear_service=%s",
            reason,
            clear_time,
            clear_date,
            clear_availability,
            clear_service,
        )
    return state


@dataclass(frozen=True)
class FieldChange:
    """One field change for revision acknowledgements (raw slot values)."""

    field: str
    from_value: Any = None
    to_value: Any = None


@dataclass(frozen=True)
class BookingRevision:
    """Which booking fields this turn changes relative to the bound session."""

    service: bool = False
    date: bool = False
    time: bool = False
    changes: tuple = ()

    @property
    def any(self) -> bool:
        return bool(self.changes) or self.service or self.date or self.time

    def to_summary(self) -> Optional[Dict[str, Any]]:
        """Turn-only metadata for rendering acknowledgements."""
        if not self.changes:
            return None
        return {
            "changes": [
                {
                    "field": change.field,
                    "from": change.from_value,
                    "to": change.to_value,
                }
                for change in self.changes
            ]
        }


def _normalize_date_value(value: Any) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    return value.split("T")[0].split(" ")[0]


def detect_booking_revision(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> BookingRevision:
    """Detect which bound booking fields this turn revises.

    Captures from/to values before apply_booking_revision mutates session.
    """
    session_slots = (
        session_state.get("slots") if isinstance(session_state, dict) else None
    ) or {}
    changes: list = []

    service_changed = False
    new_service = None
    if isinstance(luma_response, dict):
        facts = luma_response.get("facts")
        if isinstance(facts, dict):
            new_service = facts.get("service_id")
    if new_service:
        current_service = session_slots.get("service_id")
        if not current_service or str(new_service) != str(current_service):
            service_changed = True
            changes.append(
                FieldChange(
                    field="service",
                    from_value=current_service,
                    to_value=new_service,
                )
            )

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
        if new_date:
            current_date = _normalize_date_value(session_slots.get("date"))
            if not current_date or new_date != current_date:
                date_changed = True
                changes.append(
                    FieldChange(
                        field="date",
                        from_value=current_date,
                        to_value=new_date,
                    )
                )

    time_changed = False
    if isinstance(luma_response, dict):
        from core.workflows.availability.fingerprint import (
            _normalize_time_for_fingerprint,
        )
        from core.planning.temporal_proposal import exact_time_proposal_from_luma

        time_proposal = exact_time_proposal_from_luma(luma_response)
        if time_proposal and time_proposal.get("value"):
            new_time = _normalize_time_for_fingerprint(
                time_proposal.get("value"))
            if new_time:
                current_time = _normalize_time_for_fingerprint(
                    session_slots.get("time"))
                if not current_time or new_time != current_time:
                    time_changed = True
                    changes.append(
                        FieldChange(
                            field="time",
                            from_value=current_time,
                            to_value=new_time,
                        )
                    )

    return BookingRevision(
        service=service_changed,
        date=date_changed,
        time=time_changed,
        changes=tuple(changes),
    )


def apply_booking_revision(
    state: Optional[Dict[str, Any]],
    revision: BookingRevision,
    *,
    reason: str = "booking_revision",
) -> Dict[str, Any]:
    """Invalidate pending confirmation and bound fields affected by ``revision``.

    Policy:
      - time only: clear time + pending; keep service/date/presented availability
      - date: clear date/time/presented/fingerprint + pending; keep service
      - service: clear service/date/time/presented/fingerprint + pending
    """
    if not isinstance(state, dict) or not revision.any:
        return state if isinstance(state, dict) else {}

    clear_time = revision.time or revision.date or revision.service
    clear_date = revision.date or revision.service
    clear_availability = revision.date or revision.service
    clear_service = revision.service

    return clear_pending_confirmation(
        state,
        clear_time=clear_time,
        clear_date=clear_date,
        clear_availability=clear_availability,
        clear_service=clear_service,
        reason=reason,
    )


def is_confirmation_gate_open(session_state: Optional[Dict[str, Any]]) -> bool:
    """True when the user is at (or effectively at) the booking confirmation prompt.

    Matches current behaviour: pending confirmation, AWAITING_CONFIRMATION status,
    or a bound datetime on a durable booking session (covers intermittent status).
    """
    if not isinstance(session_state, dict):
        return False

    intent_name = session_state.get(
        "intent_name") or session_state.get("intent")
    if isinstance(intent_name, dict):
        intent_name = intent_name.get("name")
    if not intent_name:
        return False

    try:
        from core.policy.intent_policy import get_intent_durable

        if not get_intent_durable(intent_name):
            return False
    except Exception:
        return False

    if (
        intent_name == "CREATE_APPOINTMENT"
        and has_committed_create_appointment(session_state.get("slots"))
    ):
        return False

    if session_state.get("status") == "AWAITING_CONFIRMATION":
        return True
    if get_confirmation_state(session_state) == "pending":
        return True

    from core.planning.temporal_proposal import has_bound_booking_datetime

    return has_bound_booking_datetime(
        session_state.get("slots"), session_state
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

    proposals = extract_nlu_proposals(luma_response)
    date_proposal = proposals.get("date_proposal")
    if isinstance(date_proposal, dict) and date_proposal.get("start"):
        return True

    facts = luma_response.get("facts")
    if isinstance(facts, dict):
        times = facts.get("times")
        if isinstance(times, list) and times:
            return True
        dates = facts.get("dates")
        if isinstance(dates, list) and dates:
            return True

    time_constraint = luma_response.get("time_constraint")
    if (
        isinstance(time_constraint, dict)
        and time_constraint.get("mode") == "exact"
        and time_constraint.get("start")
    ):
        return True

    return False


def has_actionable_booking_facts(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when this turn has proposals/facts that must reach bind/planning.

    Used to prevent informational early-return from skipping time/date/service
    revisions (which arrive as proposals, not new slot keys).
    """
    if has_revision_facts(luma_response):
        return True
    if _has_service_revision(luma_response, session_state):
        return True
    return False


def _raw_luma_intent_name(luma_response: Optional[Dict[str, Any]]) -> str:
    if not isinstance(luma_response, dict):
        return ""
    intent = luma_response.get("intent")
    if isinstance(intent, dict):
        return intent.get("name") or ""
    if isinstance(intent, str):
        return intent
    return ""


def _session_booking_intent(session_state: Optional[Dict[str, Any]]) -> str:
    if not isinstance(session_state, dict):
        return ""
    intent_name = session_state.get(
        "intent_name") or session_state.get("intent")
    if isinstance(intent_name, dict):
        return intent_name.get("name") or ""
    return intent_name or ""


def _has_service_revision(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> bool:
    if not isinstance(luma_response, dict):
        return False
    facts = luma_response.get("facts")
    if not isinstance(facts, dict):
        return False
    new_service = facts.get("service_id")
    if not new_service:
        return False
    session_slots = (
        session_state.get("slots") if isinstance(session_state, dict) else None
    ) or {}
    current = session_slots.get("service_id")
    return str(new_service) != str(current) if current else True


def classify_confirmation_gate_turn(
    luma_response: Optional[Dict[str, Any]],
    session_state: Optional[Dict[str, Any]],
) -> ConfirmationGateTurn:
    """Classify this turn relative to the booking confirmation gate.

    Priority when gate is open:
      1. REVISE — any revision facts (wins over reject/accept intent)
      2. ACCEPT — raw CONFIRM_ACTION, no revision facts
      3. REJECT — raw REJECT_ACTION, no revision facts
      4. NONE
    """
    if not is_confirmation_gate_open(session_state):
        return ConfirmationGateTurn.NONE

    if not _session_booking_intent(session_state):
        return ConfirmationGateTurn.NONE

    revision = has_revision_facts(luma_response) or _has_service_revision(
        luma_response, session_state
    )
    if revision:
        return ConfirmationGateTurn.REVISE

    raw_intent = _raw_luma_intent_name(luma_response)
    if raw_intent == "CONFIRM_ACTION":
        return ConfirmationGateTurn.ACCEPT
    if raw_intent == "REJECT_ACTION":
        return ConfirmationGateTurn.REJECT

    return ConfirmationGateTurn.NONE
