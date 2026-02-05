"""
Missing Slots Computation

Computes missing slots for intents based on collected slots and planning policy.
All intent-specific logic comes from intent_policy.yaml - no hard-coded intent checks.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def get_planning_required_slots_for_intent(
    intent_name: str,
    collected_slots: Dict[str, Any] = None,
    modification_context: Dict[str, Any] = None
) -> List[str]:
    """
    Get planning-required slots for an intent from intent_policy.yaml.
    
    POLICY AS SINGLE SOURCE OF TRUTH:
    - All required_slots MUST come from intent_policy.yaml via get_planning_required_slots()
    - No intent-specific branching or hard-coded slot lists
    - Context-aware logic (e.g., modification_context) is preserved but delegates to policy
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        collected_slots: Optional collected slots (for logging/debugging)
        modification_context: Optional modification context (for MODIFY intents)
        
    Returns:
        List of planning-required slot names from intent_policy.yaml
    """
    # Get required slots from unified policy (intent_policy.yaml)
    try:
        from core.policy.intent_policy import get_planning_required_slots
        required_slots = get_planning_required_slots(intent_name)
        logger.debug(
            f"[REQUIRED_SLOTS_COMPUTE] intent={intent_name}, "
            f"required_slots={required_slots} (from intent_policy.yaml)"
        )
        return sorted(required_slots)
    except (ImportError, KeyError, Exception) as e:
        # Fallback to legacy planning config if policy not available
        logger.warning(
            f"[REQUIRED_SLOTS_COMPUTE] Failed to get required_slots from intent_policy.yaml for {intent_name}: {e}. "
            f"Falling back to legacy planning config."
        )
        from core.planning.policy.action_policy import load_planning_policy
        policy = load_planning_policy()
        intent_policy = policy.get(intent_name, {})
        required_slots = intent_policy.get("required_slots", [])
        if not isinstance(required_slots, list):
            required_slots = []
        return sorted(required_slots)


def compute_missing_slots(
    intent_name: str,
    collected_slots: Dict[str, Any],
    modification_context: Dict[str, Any] = None,
    session_state: Dict[str, Any] = None,
    time_constraint: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Compute missing slots for an intent based on collected slots and planning policy.
    
    POLICY AS SINGLE SOURCE OF TRUTH:
    - All required_slots come from intent_policy.yaml
    - No intent-specific branching
    - Special handling (e.g., time_constraint) is preserved but minimal
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        collected_slots: Dictionary of effective collected slots
        modification_context: Optional modification context for MODIFY intents (preserved for backward compatibility)
        session_state: Optional session state (for logging)
        time_constraint: Optional time_constraint from Luma (for CREATE_APPOINTMENT time satisfaction)
        
    Returns:
        Sorted list of missing slot names (empty list if all slots satisfied)
    """
    if not intent_name:
        return []
    
    # Get required slots from policy (single source of truth)
    required_slots_list = get_planning_required_slots_for_intent(
        intent_name, collected_slots, modification_context
    )
    
    # Compute missing slots: required_slots - collected_slots
    required_slots = set(required_slots_list)
    collected_slot_keys = set(collected_slots.keys()) if collected_slots else set()
    
    missing = required_slots - collected_slot_keys
    missing_slots = sorted(missing)

    # SPECIAL HANDLING: time_constraint satisfaction for CREATE_APPOINTMENT
    # This is a semantic rule: exact time_constraint satisfies time requirement
    # TODO: Consider moving this to policy via a "satisfies" field
    if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
        time_constraint_mode = None
        if isinstance(time_constraint, dict):
            time_constraint_mode = time_constraint.get("mode")
        
        # Only remove "time" from missing_slots if mode is exact
        if time_constraint_mode == "exact":
            if "time" in missing_slots:
                missing_slots = [s for s in missing_slots if s != "time"]
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - "
                    f"removed 'time' from missing_slots"
                )
    
    logger.info(
        f"[MISSING_SLOTS] compute_missing_slots: "
        f"intent={intent_name}, "
        f"collected_slots={list(collected_slot_keys)}, "
        f"required_slots={list(required_slots)}, "
        f"missing_slots={missing_slots}, "
        f"time_constraint={time_constraint is not None}"
    )
    
    assert isinstance(missing_slots, list), (
        f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
    )
    
    return missing_slots
