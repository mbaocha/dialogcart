"""
Missing Slots Computation

Computes missing slots for intents based on collected slots and planning policy.
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
    Get planning-required slots for an intent, context-aware for MODIFY intents.
    
    GUARD: Base requirements MUST come from intent_planning.yaml via the planner.
    This function preserves context-aware logic for MODIFY intents but delegates
    base requirements to the planner.
    
    For MODIFY_BOOKING (service domain):
    - Base: ["booking_id"] (from intent_planning.yaml)
    - Time-only change: ["booking_id"] (date NOT required if only time is being modified)
    - Date-only change: ["booking_id", "date"] (date required)
    - Date+time change: ["booking_id", "date", "time"] (both required)
    
    For MODIFY_RESERVATION:
    - Base: ["booking_id"] (from intent_planning.yaml)
    - Date-only change: ["booking_id", "start_date", "end_date"] (both dates required)
    - Single date provided: ["booking_id"] + whichever date(s) are missing
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        collected_slots: Optional collected slots to check for conditional requirements
        modification_context: Optional modification context (e.g., {"modifying_time": True}) from session
                             Used for MODIFY_* intents when current turn slots are empty
        
    Returns:
        List of planning-required slot names for the intent
    """
    # PREFER unified policy for required_slots, fallback to legacy planning config
    base_planning_slots = []
    try:
        from core.policy.intent_policy import get_planning_required_slots
        base_planning_slots = get_planning_required_slots(intent_name)
    except (ImportError, Exception):
        # Fallback to legacy planning config
        pass
    
    if not base_planning_slots:
        # Fallback to legacy planning config
        from core.planning.policy.action_policy import load_planning_policy
        policy = load_planning_policy()
        intent_policy = policy.get(intent_name, {})
        base_planning_slots = intent_policy.get("required_slots", [])
    if not isinstance(base_planning_slots, list):
        base_planning_slots = []
    
    print(f"[REQUIRED_SLOTS_COMPUTE] ENTRY: intent={intent_name}, base_slots={base_planning_slots}")
    print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots type={type(collected_slots)}, value={collected_slots}")
    print(f"[REQUIRED_SLOTS_COMPUTE] modification_context={modification_context}")
    if collected_slots:
        print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots keys={list(collected_slots.keys())}")
        print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots values={collected_slots}")
    
    # MODIFY_BOOKING: Use policy-defined required_slots (booking_id only)
    # Policy: required_slots = ['booking_id'], optional_slots = ['date', 'time', 'date_range']
    # 
    # TODO: All intent-specific slot logic MUST come from intent_policy.yaml, not inline lists.
    # This ensures policy is the single source of truth for intent behavior.
    # Any hard-coded slot requirements (like the old ["booking_id", "date", "time"]) violate this principle.
    if intent_name == "MODIFY_BOOKING":
        # Use policy-defined required_slots (only booking_id)
        # date/time/date_range are optional at planning time
        required_slots = base_planning_slots if base_planning_slots else ["booking_id"]
        logger.debug(
            f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: Using policy-defined required_slots: {required_slots}"
        )
        return sorted(required_slots)
    
    # MODIFY_RESERVATION: Context-aware required slots based on what's being modified
    # CRITICAL: If modification_context is present, it MUST override base planning slots
    # modification_context is authoritative and cannot be bypassed
    if intent_name == "MODIFY_RESERVATION":
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION path: collected_slots={collected_slots}, modification_context={modification_context}")
        
        # Start with booking_id (always required)
        required_slots = ["booking_id"]
        base_required_slots = ["booking_id", "start_date", "end_date"]
        
        # CRITICAL: If modification_context is present, it MUST override base planning slots
        # modification_context is authoritative - check it first, before checking collected_slots
        if modification_context:
            # modification_context is present - use it as authoritative source
            has_start_date = modification_context.get("modifying_start_date", False)
            has_end_date = modification_context.get("modifying_end_date", False)
            has_date = modification_context.get("modifying_date", False)
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: Using authoritative modification_context: modifying_start_date={has_start_date}, modifying_end_date={has_end_date}, modifying_date={has_date}")
        else:
            # No modification_context - fall back to checking collected_slots (semantic signals from current turn)
            has_start_date = False
            has_end_date = False
            has_date = False
            if collected_slots:
                has_start_date = "start_date" in collected_slots and collected_slots.get("start_date") is not None
                has_end_date = "end_date" in collected_slots and collected_slots.get("end_date") is not None
                has_date = "date" in collected_slots and collected_slots.get("date") is not None
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: No modification_context, checking collected_slots: has_start_date={has_start_date}, has_end_date={has_end_date}, has_date={has_date}")
        
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION analysis: has_start_date={has_start_date}, has_end_date={has_end_date}, has_date={has_date}")
        
        if has_start_date or has_end_date:
            # At least one reservation date is provided - require both for range
            if not has_start_date:
                required_slots.append("start_date")
            if not has_end_date:
                required_slots.append("end_date")
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: reservation dates provided -> required_slots={required_slots}")
        elif has_date:
            # Generic date provided - this should NOT satisfy start_date/end_date
            # Keep base requirements (both dates)
            required_slots.extend(["start_date", "end_date"])
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: generic date provided -> required_slots={required_slots}")
        else:
            # No date slots provided yet - use base planning slots
            # Only fallback to base when modification_context is absent or all flags are False
            if not modification_context:
                required_slots = base_required_slots.copy()
                print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: no modification_context -> using base_required_slots={required_slots}")
            else:
                # modification_context present but all False - still use base (ambiguous)
                required_slots = base_required_slots.copy()
                print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION: modification_context present but ambiguous -> using base_required_slots={required_slots}")
        
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_RESERVATION FINAL: required_slots={required_slots}")
        return required_slots
    
    print(f"[REQUIRED_SLOTS_COMPUTE] DEFAULT path: intent={intent_name} -> base_planning_slots={base_planning_slots}")
    return base_planning_slots


def compute_missing_slots(
    intent_name: str,
    collected_slots: Dict[str, Any],
    modification_context: Dict[str, Any] = None,
    session_state: Dict[str, Any] = None,
    time_constraint: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Compute missing slots for an intent based on collected slots and planning policy.
    
    GUARD: This function MUST use the planner for base requirements.
    It preserves context-aware logic for MODIFY intents but delegates to planner.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        collected_slots: Dictionary of effective collected slots
        modification_context: Optional modification context for MODIFY intents
        session_state: Optional session state (for logging)
        time_constraint: Optional time_constraint from Luma (for CREATE_APPOINTMENT)
        
    Returns:
        Sorted list of missing slot names (empty list if all slots satisfied)
    """
    if not intent_name:
        return []
    
    # PREFER unified policy for base requirements, fallback to legacy planning config
    # For MODIFY intents, apply context-aware logic after getting base requirements
    from core.planning.policy.action_policy import plan_intent, load_planning_policy
    
    # Get base required slots from unified policy (intent_policy.yaml) with fallback to legacy (intent_planning.yaml)
    base_required_slots = []
    try:
        from core.policy.intent_policy import get_planning_required_slots
        base_required_slots = get_planning_required_slots(intent_name)
    except (ImportError, Exception):
        # Fallback to legacy planning config
        pass
    
    if not base_required_slots:
        # Fallback to legacy planning config
        policy = load_planning_policy()
        intent_policy = policy.get(intent_name, {})
        base_required_slots = intent_policy.get("required_slots", [])
    
    if not isinstance(base_required_slots, list):
        base_required_slots = []
    
    # For MODIFY intents, apply context-aware logic
    # This preserves existing behavior for MODIFY_BOOKING and MODIFY_RESERVATION
    required_slots_list = get_planning_required_slots_for_intent(
        intent_name, collected_slots, modification_context
    )
    
    # Compute missing slots: required_slots - collected_slots
    required_slots = set(required_slots_list)
    collected_slot_keys = set(collected_slots.keys()) if collected_slots else set()
    
    missing = required_slots - collected_slot_keys
    missing_slots = sorted(missing)

    # APPOINTMENT INTENT RULE: Only exact time_constraint satisfies the time requirement
    # mode=exact → satisfies time, mode=fuzzy/window → does NOT satisfy time
    if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
        # Check if time_constraint mode is exact (only exact satisfies time requirement)
        time_constraint_mode = None
        if isinstance(time_constraint, dict):
            time_constraint_mode = time_constraint.get("mode")
        
        # Only remove "time" from missing_slots if mode is exact
        if time_constraint_mode == "exact":
            if "time" in missing_slots:
                missing_slots = [s for s in missing_slots if s != "time"]
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode=exact) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots"
                )
        else:
            # Fuzzy/window time_constraint does NOT satisfy time requirement
            logger.debug(
                f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots"
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

