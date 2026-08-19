"""Derived booking lifecycle and projector-owned post-commit transition."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class BookingLifecycle(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    COMMITTED = "COMMITTED"


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def derive_booking_lifecycle(
    session_or_state: Optional[Mapping[str, Any]],
) -> BookingLifecycle:
    """Derive lifecycle from canonical Session V2 fields.

    Historical documents must first pass through the existing normalization or
    load boundary. This function intentionally does not inspect legacy mirrors.
    """
    if not isinstance(session_or_state, Mapping):
        return BookingLifecycle.IDLE
    booking = session_or_state.get("booking")
    if isinstance(booking, Mapping) and (
        _present(booking.get("booking_id"))
        or _present(booking.get("booking_code"))
    ):
        return BookingLifecycle.COMMITTED
    if session_or_state.get("confirmation_state") == "pending":
        return BookingLifecycle.AWAITING_CONFIRMATION

    planning = session_or_state.get("planning")
    if not isinstance(planning, Mapping):
        return BookingLifecycle.IDLE
    has_continuation = bool(
        planning.get("status")
        or planning.get("slots")
        or planning.get("bound_datetime")
        or planning.get("missing_slots")
        or planning.get("ask_next")
        or planning.get("pending_profile_request")
    )
    if _present(planning.get("intent_name")) and has_continuation:
        return BookingLifecycle.ACTIVE
    return BookingLifecycle.IDLE


def build_post_commit_transition(
    *, booking_id: Any, booking_code: Any, completed_slot_keys: Any = None
) -> Dict[str, Any]:
    """Build the explicit workflow artifact consumed by SessionProjectorV2."""
    return {
        "kind": "CREATE_APPOINTMENT_COMMITTED",
        "booking_id": booking_id,
        "booking_code": booking_code,
        "completed_slot_keys": sorted(
            str(key) for key in (completed_slot_keys or []) if str(key)
        ),
    }


def apply_post_commit_transition_v2(
    session: Dict[str, Any], transition: Mapping[str, Any]
) -> None:
    """Apply lifecycle closure to a projected Session V2 document in place."""
    if transition.get("kind") != "CREATE_APPOINTMENT_COMMITTED":
        return
    booking = session.setdefault("booking", {})
    if _present(transition.get("booking_id")):
        booking["booking_id"] = transition.get("booking_id")
    if _present(transition.get("booking_code")):
        booking["booking_code"] = transition.get("booking_code")
    _close_committed_authorization(
        session,
        completed_slot_keys=set(transition.get("completed_slot_keys") or []),
    )


def sanitize_committed_booking_v2(session: Dict[str, Any]) -> bool:
    """Make committed canonical state win over contradictory authorization."""
    if derive_booking_lifecycle(session) != BookingLifecycle.COMMITTED:
        return False
    planning = session.get("planning")
    planning_intent = (
        planning.get("intent_name") if isinstance(planning, Mapping) else None
    )
    # A committed booking can legitimately be the target of a separate modify,
    # cancel, or view workflow. Phase 1 closes only stale create authorization.
    if planning_intent not in (None, "CREATE_APPOINTMENT"):
        return False
    # Load sanitation cannot reliably identify which generic assistant proposal
    # belonged to the old draft. Closing planning/confirmation authorization is
    # sufficient; the explicit success artifact expires slot-bound proposals.
    _close_committed_authorization(session, completed_slot_keys=set())
    return True


def _close_committed_authorization(
    session: Dict[str, Any], *, completed_slot_keys: Optional[set[str]]
) -> None:
    session["confirmation_state"] = None
    planning = session.setdefault("planning", {})
    planning.update(
        {
            "intent_name": None,
            "status": None,
            "slots": {},
            "bound_datetime": None,
            "missing_slots": [],
            "ask_next": None,
            "declined_slots": [],
            "retry": {"slot_attempts": {}, "last_filled_slot": None},
            "proposals": {"date": None, "time": None},
            "constraints": {"date": None, "time": None},
            "temporal": None,
            "pending_entity_resolutions": [],
            "pending_profile_request": None,
            "modification_context": None,
        }
    )
    # The projector returns hydrated compatibility mirrors in memory. Clear the
    # mirrors at the same boundary so save-time compatibility sync cannot
    # overwrite the closed canonical planning section.
    for mirror in (
        "intent_name",
        "intent",
        "status",
        "slots",
        "missing_slots",
        "ask_next",
        "declined_slots",
        "slot_attempts",
        "last_filled_slot",
        "awaiting_slot",
        "resolved_datetime_range",
        "date_proposal",
        "time_proposal",
        "temporal",
    ):
        session.pop(mirror, None)
    availability = session.setdefault("availability", {})
    availability["fingerprint"] = None
    availability.setdefault("cache", {})["search_result"] = None
    session.pop("availability_fingerprint", None)
    session.pop("last_execution_result", None)
    # Historical presentation is retained; it is not availability trust.

    conversation = session.setdefault("conversation", {})
    proposals = conversation.get("pending_proposals")
    if not isinstance(proposals, list):
        return
    closed = []
    for raw in proposals:
        proposal = deepcopy(raw) if isinstance(raw, dict) else raw
        if isinstance(proposal, dict) and proposal.get("status") == "PENDING":
            slot_key = str(proposal.get("slot_key") or "")
            if completed_slot_keys is None or slot_key in completed_slot_keys:
                proposal["status"] = "EXPIRED"
        closed.append(proposal)
    conversation["pending_proposals"] = closed


__all__ = [
    "BookingLifecycle",
    "apply_post_commit_transition_v2",
    "build_post_commit_transition",
    "derive_booking_lifecycle",
    "sanitize_committed_booking_v2",
]
