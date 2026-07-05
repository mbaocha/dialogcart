"""
Business fact registry for planner sequencing.

Runtime owns implementation details (fingerprints, confirmation_state enums,
slot bindings, hold lifecycle). This module derives forward-looking business
facts that future policy interpreters will consume.

PR1: derivation only.
PR2: SEARCH_AVAILABILITY policy consumes availability_check_required via build_policy_execution_flags.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_APPOINTMENT_INTENTS = frozenset({"CREATE_APPOINTMENT", "MODIFY_BOOKING"})
_RESERVATION_INTENTS = frozenset({"CREATE_RESERVATION", "MODIFY_RESERVATION"})
_BOOKING_REFERENCE_INTENTS = frozenset(
    {"MODIFY_BOOKING", "MODIFY_RESERVATION", "CANCEL_BOOKING", "VIEW_BOOKING"}
)


@dataclass(frozen=True)
class BusinessFacts:
    """Immutable business facts for one planning cycle."""

    availability_check_required: bool
    availability_ready: bool
    booking_identification_required: bool
    booking_identified: bool
    booking_hold_required: bool
    booking_hold_ready: bool
    time_selection_required: bool
    time_selection_ready: bool
    user_confirmation_required: bool
    user_confirmation_satisfied: bool


@dataclass(frozen=True)
class PlanningFactContext:
    """Inputs for a single business-fact derivation pass."""

    intent_name: str
    slots: Dict[str, Any]
    session_state: Optional[Dict[str, Any]] = None
    luma_response: Optional[Dict[str, Any]] = None
    missing_slots: Optional[List[str]] = None
    needs_clarification: bool = False
    confirmation_state: Optional[str] = None


def derive_business_facts(context: PlanningFactContext) -> BusinessFacts:
    """Derive all planner business facts for the given planning context."""
    intent_name = (context.intent_name or "").upper()
    slots = context.slots if isinstance(context.slots, dict) else {}
    session_state = context.session_state
    luma_response = context.luma_response if isinstance(context.luma_response, dict) else {}
    missing_slots = context.missing_slots if isinstance(context.missing_slots, list) else []

    confirmation_state = context.confirmation_state
    if confirmation_state is None:
        confirmation_state = _resolve_confirmation_state(session_state, luma_response)
    user_confirmation_satisfied = confirmation_state == "confirmed"

    availability_ready = _derive_availability_ready(
        intent_name=intent_name,
        slots=slots,
        session_state=session_state,
        luma_response=luma_response,
    )
    availability_check_required = _derive_availability_check_required(
        intent_name=intent_name,
        slots=slots,
        availability_ready=availability_ready,
        user_confirmation_satisfied=user_confirmation_satisfied,
    )

    booking_identified = _derive_booking_identified(slots)
    booking_identification_required = _derive_booking_identification_required(
        intent_name=intent_name,
        booking_identified=booking_identified,
    )

    booking_hold_ready = _derive_booking_hold_ready(slots)
    booking_hold_required = _derive_booking_hold_required(
        intent_name=intent_name,
        slots=slots,
        availability_ready=availability_ready,
        booking_hold_ready=booking_hold_ready,
    )

    time_selection_ready = _derive_time_selection_ready(
        slots=slots,
        session_state=session_state,
        luma_response=luma_response,
    )
    time_selection_required = _derive_time_selection_required(
        intent_name=intent_name,
        time_selection_ready=time_selection_ready,
    )

    user_confirmation_required = _derive_user_confirmation_required(
        intent_name=intent_name,
        confirmation_state=confirmation_state,
        missing_slots=missing_slots,
        needs_clarification=context.needs_clarification,
        availability_ready=availability_ready,
        time_selection_ready=time_selection_ready,
        user_confirmation_satisfied=user_confirmation_satisfied,
    )

    facts = BusinessFacts(
        availability_check_required=availability_check_required,
        availability_ready=availability_ready,
        booking_identification_required=booking_identification_required,
        booking_identified=booking_identified,
        booking_hold_required=booking_hold_required,
        booking_hold_ready=booking_hold_ready,
        time_selection_required=time_selection_required,
        time_selection_ready=time_selection_ready,
        user_confirmation_required=user_confirmation_required,
        user_confirmation_satisfied=user_confirmation_satisfied,
    )
    logger.debug(
        "[BUSINESS_FACTS] intent=%s facts=%s",
        intent_name,
        facts,
    )
    return facts


def build_policy_execution_flags(
    *,
    intent_name: str,
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
    luma_response: Optional[Dict[str, Any]] = None,
    missing_slots: Optional[List[str]] = None,
    needs_clarification: bool = False,
    availability_resolved: bool = False,
    confirmation_state: Optional[str] = None,
    booking_hold_created: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build selector flags including derived business facts for one planning cycle."""
    facts = derive_business_facts(
        PlanningFactContext(
            intent_name=intent_name,
            slots=slots,
            session_state=session_state,
            luma_response=luma_response,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            confirmation_state=confirmation_state,
        )
    )
    if booking_hold_created is None:
        booking_hold_created = facts.booking_hold_ready
    return {
        "availability_resolved": availability_resolved,
        "confirmation_state": confirmation_state,
        "booking_hold_created": booking_hold_created,
        "availability_check_required": facts.availability_check_required,
        "availability_ready": facts.availability_ready,
        "booking_identification_required": facts.booking_identification_required,
        "booking_identified": facts.booking_identified,
        "booking_hold_required": facts.booking_hold_required,
        "booking_hold_ready": facts.booking_hold_ready,
        "time_selection_required": facts.time_selection_required,
        "time_selection_ready": facts.time_selection_ready,
        "user_confirmation_required": facts.user_confirmation_required,
        "user_confirmation_satisfied": facts.user_confirmation_satisfied,
    }


def _derive_availability_ready(
    *,
    intent_name: str,
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    luma_response: Dict[str, Any],
) -> bool:
    """True when availability outcome is trustworthy for current booking parameters.

    Mirrors availability_resolved derivation in luma_response_processor without
    mutating planner state.
    """
    from core.orchestration.availability_fingerprint import (
        slots_match_availability_fingerprint,
    )
    from core.orchestration.temporal_proposal import (
        expand_slots_for_planning,
        has_bound_booking_datetime,
    )

    if has_bound_booking_datetime(slots, session_state, luma_response):
        return True

    stored_fingerprint = None
    if isinstance(session_state, dict):
        stored_fingerprint = session_state.get("availability_fingerprint")

    facts_obj = luma_response.get("facts")
    fingerprint_slots = expand_slots_for_planning(
        slots,
        date_proposal=luma_response.get("date_proposal"),
        time_proposal=luma_response.get("time_proposal"),
        date_constraint=luma_response.get("date_constraint"),
        nlu_facts=facts_obj if isinstance(facts_obj, dict) else None,
        time_constraint=luma_response.get("time_constraint"),
        intent_name=intent_name,
    )

    if slots_match_availability_fingerprint(
        fingerprint_slots, stored_fingerprint, intent_name=intent_name
    ):
        return True

    if luma_response.get("_confirm_booking_continuation"):
        stored_range = (
            session_state.get("resolved_datetime_range")
            if isinstance(session_state, dict)
            else None
        )
        if stored_fingerprint:
            return True
        if isinstance(stored_range, dict) and stored_range.get("start"):
            return True
        if (
            intent_name in _RESERVATION_INTENTS | {"MODIFY_BOOKING"}
            and isinstance(session_state, dict)
            and session_state.get("status") == "READY"
        ):
            return True

    return False


def _intent_has_availability_search(intent_name: str) -> bool:
    from core.policy.intent_policy import get_execution_steps

    if not intent_name:
        return False
    for step in get_execution_steps(intent_name):
        if step.get("action") == "SEARCH_AVAILABILITY":
            return True
    return False


def _search_prerequisites_met(intent_name: str, slots: Dict[str, Any]) -> bool:
    """True when policy allows starting SEARCH_AVAILABILITY with current slots."""
    from core.policy.intent_policy import get_execution_steps

    collected = {
        name for name, value in slots.items() if value is not None
    }
    for step in get_execution_steps(intent_name):
        if step.get("action") != "SEARCH_AVAILABILITY":
            continue
        required = set(step.get("required_slots") or [])
        return required.issubset(collected)
    return False


def _derive_availability_check_required(
    *,
    intent_name: str,
    slots: Dict[str, Any],
    availability_ready: bool,
    user_confirmation_satisfied: bool,
) -> bool:
    if not _intent_has_availability_search(intent_name):
        return False
    if not _search_prerequisites_met(intent_name, slots):
        return False
    if intent_name in ("MODIFY_BOOKING", "MODIFY_RESERVATION"):
        # Preview/search until the user confirms the modification.
        return not user_confirmation_satisfied
    return not availability_ready


def _derive_booking_identified(slots: Dict[str, Any]) -> bool:
    booking_id = slots.get("booking_id")
    return booking_id is not None and booking_id != ""


def _derive_booking_identification_required(
    *,
    intent_name: str,
    booking_identified: bool,
) -> bool:
    if intent_name not in _BOOKING_REFERENCE_INTENTS:
        return False
    return not booking_identified


def _derive_booking_hold_ready(slots: Dict[str, Any]) -> bool:
    return _derive_booking_identified(slots)


def _derive_booking_hold_required(
    *,
    intent_name: str,
    slots: Dict[str, Any],
    availability_ready: bool,
    booking_hold_ready: bool,
) -> bool:
    if intent_name != "CREATE_RESERVATION":
        return False
    if booking_hold_ready:
        return False
    if not availability_ready:
        return False
    if slots.get("service_id") is None:
        return False
    if slots.get("date_range") is None:
        return False
    return True


def _derive_time_selection_ready(
    *,
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    luma_response: Dict[str, Any],
) -> bool:
    from core.orchestration.temporal_proposal import has_bound_booking_datetime

    return has_bound_booking_datetime(slots, session_state, luma_response)


def _derive_time_selection_required(
    *,
    intent_name: str,
    time_selection_ready: bool,
) -> bool:
    if intent_name not in _APPOINTMENT_INTENTS:
        return False
    return not time_selection_ready


def _resolve_confirmation_state(
    session_state: Optional[Dict[str, Any]],
    luma_response: Dict[str, Any],
) -> Optional[str]:
    from core.session.confirmation_gate import get_confirmation_state

    state = get_confirmation_state(luma_response)
    if state is not None:
        return state
    return get_confirmation_state(session_state)


def _derive_user_confirmation_required(
    *,
    intent_name: str,
    confirmation_state: Optional[str],
    missing_slots: List[str],
    needs_clarification: bool,
    availability_ready: bool,
    time_selection_ready: bool,
    user_confirmation_satisfied: bool,
) -> bool:
    if user_confirmation_satisfied:
        return False
    if confirmation_state == "pending":
        return True

    if intent_name == "CREATE_APPOINTMENT":
        return _create_appointment_commit_ready_for_confirmation(
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            availability_ready=availability_ready,
            time_selection_ready=time_selection_ready,
            confirmation_state=confirmation_state,
        )

    if intent_name in _RESERVATION_INTENTS | {"MODIFY_BOOKING"}:
        if confirmation_state is not None:
            return False
        if missing_slots or needs_clarification:
            return False
        if not availability_ready:
            return False
        if intent_name == "MODIFY_BOOKING" and not time_selection_ready:
            return False
        return True

    return False


def _create_appointment_commit_ready_for_confirmation(
    *,
    missing_slots: List[str],
    needs_clarification: bool,
    availability_ready: bool,
    time_selection_ready: bool,
    confirmation_state: Optional[str],
) -> bool:
    """Business view of commit-ready CREATE_APPOINTMENT awaiting explicit confirmation."""
    if confirmation_state is not None:
        return False
    if missing_slots or needs_clarification:
        return False
    if not availability_ready:
        return False
    if not time_selection_ready:
        return False
    return True
