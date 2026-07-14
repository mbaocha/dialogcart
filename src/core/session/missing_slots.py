"""Resolve missing_slots for session persistence (single source of truth)."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _missing_slots_from_outcome(outcome: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    if not outcome or not isinstance(outcome, dict):
        return None
    top_level = outcome.get("missing_slots")
    if isinstance(top_level, list):
        return top_level
    facts = outcome.get("facts")
    if isinstance(facts, dict):
        facts_missing = facts.get("missing_slots")
        if isinstance(facts_missing, list):
            return facts_missing
    return None


def recompute_missing_slots_from_slots(
    intent_name: str,
    slots: Dict[str, Any],
    outcome: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
) -> List[str]:
    """Recompute missing_slots from persisted slots via planner."""
    from core.planning.policy.action_policy import load_planning_policy, plan_intent
    from core.adapters.nlu.luma_response_processor import (
        _normalize_modify_booking_missing_slots,
    )
    from core.planning.temporal_proposal import (
        apply_time_constraint_to_missing_slots,
        expand_slots_for_planning,
        resolve_session_proposals,
    )

    modification_context = None
    if merged_luma_response:
        modification_context = merged_luma_response.get("_modification_context")
    if not modification_context and previous_session_state:
        modification_context = previous_session_state.get("_modification_context")

    policy = load_planning_policy()
    proposals = resolve_session_proposals(
        merged_luma_response=merged_luma_response,
        outcome=outcome,
        previous_session_state=previous_session_state,
    )
    facts_for_planning = outcome.get("facts", {}) if isinstance(outcome, dict) else {}
    if not isinstance(facts_for_planning, dict):
        facts_for_planning = {}
    merged_response = merged_luma_response or {}
    planning_slots = expand_slots_for_planning(
        slots,
        date_proposal=proposals["date_proposal"],
        time_proposal=proposals["time_proposal"],
        date_constraint=merged_response.get("date_constraint")
        or (
            previous_session_state.get("date_constraint")
            if isinstance(previous_session_state, dict)
            else None
        ),
        nlu_facts=facts_for_planning,
        time_constraint=merged_response.get("time_constraint")
        or (
            previous_session_state.get("time_constraint")
            if isinstance(previous_session_state, dict)
            else None
        ),
        intent_name=intent_name,
    )
    plan = plan_intent(intent_name, planning_slots, policy)
    missing_slots = plan["missing_slots"]

    missing_slots = apply_time_constraint_to_missing_slots(
        intent_name,
        missing_slots,
        merged_response.get("time_constraint"),
    )
    missing_slots = _normalize_modify_booking_missing_slots(missing_slots, merged_response)

    assert isinstance(missing_slots, list), (
        f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
    )

    if "time" in slots and "time" in missing_slots:
        logger.error(
            "[SLOT_SATISFACTION] VIOLATION: time is in session.slots but also in missing_slots! "
            "session.slots=%s, missing_slots=%s",
            list(slots.keys()),
            missing_slots,
        )
        missing_slots = [slot for slot in missing_slots if slot != "time"]
        logger.warning(
            "[SLOT_SATISFACTION] Fixed: removed 'time' from missing_slots because it's in session.slots"
        )

    return missing_slots


def resolve_missing_slots_for_persist(
    outcome: Dict[str, Any],
    intent_name: str,
    slots: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
) -> List[str]:
    """
    Single source of truth for missing_slots at persist time.

    Priority:
    1. outcome.missing_slots or outcome.facts.missing_slots (from turn planner)
    2. Recompute from persisted slots via planner
    """
    from_outcome = _missing_slots_from_outcome(outcome)
    if from_outcome is not None and intent_name:
        logger.info(
            "[MISSING_SLOTS] Using outcome missing_slots: intent=%s missing=%s",
            intent_name,
            from_outcome,
        )
        missing_slots = from_outcome
    elif intent_name:
        missing_slots = recompute_missing_slots_from_slots(
            intent_name,
            slots,
            outcome,
            merged_luma_response,
            previous_session_state,
        )
        logger.info(
            "[MISSING_SLOTS] Recomputed missing_slots: intent=%s missing=%s",
            intent_name,
            missing_slots,
        )
    else:
        logger.warning("[MISSING_SLOTS] Cannot resolve missing_slots: intent_name is empty")
        return []

    if "facts" in outcome and isinstance(outcome["facts"], dict):
        outcome["facts"]["missing_slots"] = missing_slots
    if merged_luma_response and isinstance(merged_luma_response, dict):
        merged_luma_response["missing_slots"] = missing_slots

    return missing_slots
