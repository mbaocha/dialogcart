"""
Missing Slots Computation

Policy requirement lookup helpers for Planning.
Canonical effective missing_slots are owned by turn_state.finalize_turn_state().
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_planning_required_slots_for_intent(
    intent_name: str,
    collected_slots: Dict[str, Any] = None,
    modification_context: Dict[str, Any] = None,
) -> List[str]:
    """
    Get planning-required slots for an intent from intent_policy.yaml.

    POLICY AS SINGLE SOURCE OF TRUTH:
    - All required_slots MUST come from intent_policy.yaml via get_planning_required_slots()
    - No intent-specific branching or hard-coded slot lists
    """
    _ = collected_slots, modification_context
    try:
        from core.policy.intent_policy import get_planning_required_slots

        required_slots = get_planning_required_slots(intent_name)
        logger.debug(
            "[REQUIRED_SLOTS_COMPUTE] intent=%s required_slots=%s (from intent_policy.yaml)",
            intent_name,
            required_slots,
        )
        return sorted(required_slots)
    except (ImportError, KeyError, Exception) as e:
        logger.warning(
            "[REQUIRED_SLOTS_COMPUTE] Failed to get required_slots from intent_policy.yaml for %s: %s. "
            "Falling back to legacy planning config.",
            intent_name,
            e,
        )
        from core.planning.policy.action_policy import load_planning_policy

        policy = load_planning_policy()
        intent_policy = policy.get(intent_name, {})
        required_slots = intent_policy.get("required_slots", [])
        if not isinstance(required_slots, list):
            required_slots = []
        return sorted(required_slots)


def normalize_modify_booking_missing_slots(
    missing_slots: List[str],
    *,
    intent_name: str = "",
) -> List[str]:
    """
    Normalize MODIFY_BOOKING missing_slots to the planning contract.

    Preserves planning-required slots (booking_id, date) and filters execution-specific
    temporal names that must not appear in planning missing_slots.
    """
    if intent_name != "MODIFY_BOOKING":
        return missing_slots

    planning_slots = {"booking_id", "date"}
    filtered_execution = {
        "change",
        "time",
        "start_date",
        "end_date",
        "datetime_range",
        "date_range",
    }

    normalized: List[str] = []
    for slot in missing_slots:
        if slot in planning_slots:
            normalized.append(slot)
        elif slot not in filtered_execution:
            normalized.append(slot)

    return normalized if normalized else missing_slots
