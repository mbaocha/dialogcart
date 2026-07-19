"""Resolve missing_slots for session persistence from planner outcome only."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _missing_slots_from_outcome(outcome: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Extract canonical missing_slots from planner / execution outcome shapes."""
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

    plan = outcome.get("plan")
    if isinstance(plan, dict):
        plan_missing = plan.get("missing_slots")
        if isinstance(plan_missing, list):
            return plan_missing

    return None


def resolve_missing_slots_for_persist(
    outcome: Dict[str, Any],
    intent_name: str,
    slots: Dict[str, Any],
    merged_luma_response: Optional[Dict[str, Any]],
    previous_session_state: Optional[Dict[str, Any]],
) -> List[str]:
    """
    Persist the planner's canonical missing_slots without independent recomputation.

    Priority:
    1. outcome.missing_slots
    2. outcome.facts.missing_slots
    3. outcome.plan.missing_slots (execution-path attachment)
    """
    _ = slots, previous_session_state

    from_outcome = _missing_slots_from_outcome(outcome)
    if from_outcome is not None:
        logger.info(
            "[MISSING_SLOTS] Using outcome missing_slots: intent=%s missing=%s",
            intent_name,
            from_outcome,
        )
        missing_slots = from_outcome
    elif not intent_name:
        logger.warning("[MISSING_SLOTS] Cannot resolve missing_slots: intent_name is empty")
        return []
    else:
        logger.error(
            "[MISSING_SLOTS] Canonical missing_slots absent from outcome "
            "(intent=%s). Refusing to recompute at persist time.",
            intent_name,
        )
        raise ValueError(
            "Canonical missing_slots missing from planner outcome; "
            "persistence must not independently recompute them."
        )

    if "facts" in outcome and isinstance(outcome["facts"], dict):
        outcome["facts"]["missing_slots"] = missing_slots
    if merged_luma_response and isinstance(merged_luma_response, dict):
        merged_luma_response["missing_slots"] = missing_slots

    return missing_slots
