"""
Slot Contract - Required Slots Definition

Defines required slots per intent and computes missing slots.

This is the single source of truth for what slots are required for each intent.
missing_slots are computed fresh from intent + collected slots.
"""

import os
from typing import Dict, List, Set, Any


# Execution-required slots per intent (for execution readiness checks)
# These are checked later, not used for planning missing_slots computation
EXECUTION_REQUIRED_SLOTS_BY_INTENT: Dict[str, List[str]] = {
    "CREATE_APPOINTMENT": ["service_id", "date", "time"],
    "CREATE_RESERVATION": ["service_id", "start_date", "end_date"],
    "MODIFY_BOOKING": ["booking_id"],  # Domain-specific slots added by normalizer
    "CANCEL_BOOKING": ["booking_id"],
}

# Planning-required slots per intent (for missing_slots computation)
# Tests validate PLANNING behavior, not execution readiness
# Planning requires more information than execution for some intents
PLANNING_REQUIRED_SLOTS_BY_INTENT: Dict[str, List[str]] = {
    "CREATE_APPOINTMENT": ["service_id", "date", "time"],
    "CREATE_RESERVATION": ["service_id", "start_date", "end_date"],
    "MODIFY_BOOKING": ["booking_id", "date", "time"],  # Base: ambiguous modify requires both date and time
    "MODIFY_RESERVATION": ["booking_id", "start_date", "end_date"],  # Planning requires both dates (context-aware)
    "CANCEL_BOOKING": ["booking_id"],
}

# Backward compatibility: REQUIRED_SLOTS_BY_INTENT now refers to planning slots
# (since compute_missing_slots uses planning slots for tests)
REQUIRED_SLOTS_BY_INTENT = PLANNING_REQUIRED_SLOTS_BY_INTENT


def get_required_slots_for_intent(intent_name: str) -> List[str]:
    """
    Get required slots for an intent from the intent contract.
    
    This is the authoritative source for what slots are required.
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "CREATE_RESERVATION")
        
    Returns:
        List of required slot names for the intent
    """
    return REQUIRED_SLOTS_BY_INTENT.get(intent_name, [])


def get_planning_required_slots_for_intent(
    intent_name: str,
    collected_slots: Dict[str, Any] = None,
    modification_context: Dict[str, Any] = None
) -> List[str]:
    """
    Get planning-required slots for an intent, context-aware for MODIFY intents.
    
    For MODIFY_BOOKING (service domain):
    - Base: ["booking_id"]
    - Time-only change: ["booking_id"] (date NOT required if only time is being modified)
    - Date-only change: ["booking_id", "date"] (date required)
    - Date+time change: ["booking_id", "date", "time"] (both required)
    
    For MODIFY_RESERVATION:
    - Base: ["booking_id"]
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
    import logging
    logger = logging.getLogger(__name__)
    
    base_planning_slots = PLANNING_REQUIRED_SLOTS_BY_INTENT.get(intent_name, [])
    
    print(f"[REQUIRED_SLOTS_COMPUTE] ENTRY: intent={intent_name}, base_slots={base_planning_slots}")
    print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots type={type(collected_slots)}, value={collected_slots}")
    print(f"[REQUIRED_SLOTS_COMPUTE] modification_context={modification_context}")
    if collected_slots:
        print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots keys={list(collected_slots.keys())}")
        print(f"[REQUIRED_SLOTS_COMPUTE] collected_slots values={collected_slots}")
    
    # MODIFY_BOOKING: Context-aware required slots based on what's being modified
    # CRITICAL: If modification_context is present, it MUST override base planning slots
    # Base slots are fallback only when modification_context is absent
    # modification_context is authoritative and cannot be bypassed
    if intent_name == "MODIFY_BOOKING":
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING path: collected_slots={collected_slots}, modification_context={modification_context}")
        
        # Base: ambiguous modify requires both date and time (fallback only)
        base_required_slots = ["booking_id", "date", "time"]
        
        # CRITICAL: If modification_context is present, it MUST override base planning slots
        # modification_context is authoritative - check it first, before checking collected_slots
        if modification_context:
            # modification_context is present - use it as authoritative source
            has_time = modification_context.get("modifying_time", False)
            has_date = modification_context.get("modifying_date", False)
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: Using authoritative modification_context: modifying_time={has_time}, modifying_date={has_date}")
        else:
            # No modification_context - fall back to checking collected_slots (semantic signals from current turn)
            has_time = False
            has_date = False
            if collected_slots:
                has_time = "time" in collected_slots and collected_slots.get("time") is not None
                has_date = "date" in collected_slots and collected_slots.get("date") is not None
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: No modification_context, checking collected_slots: has_time={has_time}, has_date={has_date}")
        
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING analysis: has_time={has_time}, has_date={has_date}")
        if collected_slots:
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING time value={collected_slots.get('time')}")
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING date value={collected_slots.get('date')}")
        
        # Start with booking_id (always required)
        required_slots = ["booking_id"]
        
        # Narrow based on modification context (authoritative):
        if has_time and not has_date:
            # Time-only change: require only time
            required_slots.append("time")
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: time-only change -> required_slots={required_slots}")
        elif has_date and not has_time:
            # Date-only change: require only date
            required_slots.append("date")
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: date-only change -> required_slots={required_slots}")
        elif has_time and has_date:
            # Date+time change: require both
            required_slots.extend(["date", "time"])
            print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: date+time change -> required_slots={required_slots}")
        else:
            # Both False (unknown/ambiguous): use base (require both date and time)
            # Only fallback to base when modification_context is absent or both flags are False
            if not modification_context:
                required_slots = base_required_slots.copy()
                print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: no modification_context -> using base_required_slots={required_slots}")
            else:
                # modification_context present but both False - still use base (ambiguous)
                required_slots = base_required_slots.copy()
                print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING: modification_context present but ambiguous -> using base_required_slots={required_slots}")
        
        print(f"[REQUIRED_SLOTS_COMPUTE] MODIFY_BOOKING FINAL: required_slots={required_slots}")
        return sorted(list(set(required_slots)))
    
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
    session_state: Dict[str, Any] = None
) -> List[str]:
    """
    Compute missing slots fresh from planning contract and collected slots.
    
    ARCHITECTURAL INVARIANT: missing_slots = PLANNING_REQUIRED_SLOTS(intent) - collected_slots
    This is a pure function with no side effects.
    
    Formula: missing_slots = planning_required_slots - collected_slots
    
    Rules:
    - Uses PLANNING slot contract, not execution contract
    - Tests validate PLANNING behavior, not execution readiness
    - No inference
    - No context-based satisfaction
    - Slot is satisfied ONLY if explicitly present in collected_slots
    
    CRITICAL: collected_slots should be the current-turn effective slot view:
    - merge(session.slots, promoted_current_turn_slots) after domain filtering
    - For MODIFY_* intents, this allows context-aware required slot computation
    
    Special rules:
    - MODIFY_BOOKING: Context-aware based on what's being modified (time-only, date-only, etc.)
    - MODIFY_RESERVATION: Context-aware based on what's being modified
    
    Args:
        intent_name: Intent name (e.g., "CREATE_APPOINTMENT", "MODIFY_BOOKING")
        collected_slots: Dictionary of effective collected slots (current-turn view after domain filtering)
        
    Returns:
        Sorted list of missing slot names (empty list if all slots satisfied)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if not intent_name:
        return []
    
    # CRITICAL: Use current-turn effective slots for required slot computation
    # This allows MODIFY_* intents to be context-aware based on what's being modified
    # collected_slots should be the effective slot view: merge(session.slots, promoted_current_turn_slots)
    # after domain filtering
    # CRITICAL: Pass modification_context to get_planning_required_slots_for_intent
    
    # TRACE 3: Right before required-slot computation
    import json
    print(json.dumps({
        "trace_point": "BEFORE_REQUIRED_SLOTS",
        "intent": intent_name,
        "modification_context": modification_context,
        "slots_used_for_computation": collected_slots,
        "session_slots": session_state.get("slots") if session_state else None,
    }))
    
    required_slots = set(get_planning_required_slots_for_intent(intent_name, collected_slots, modification_context))
    collected_slot_keys = set(collected_slots.keys()) if collected_slots else set()
    
    # LOG: intent, collected_slots, and computed missing_slots
    logger.info(
        f"[MISSING_SLOTS] compute_missing_slots: "
        f"intent={intent_name}, "
        f"collected_slots={list(collected_slot_keys)}, "
        f"planning_required_slots={list(required_slots)}"
    )
    print(f"[MISSING_SLOTS] compute_missing_slots: intent={intent_name}, collected_slots={list(collected_slot_keys)}, planning_required_slots={list(required_slots)}")
    
    # missing_slots = planning_required_slots - collected_slots
    missing = required_slots - collected_slot_keys
    
    # Sort for consistency
    missing_slots = sorted(missing)
    
    # LOG: computed missing_slots
    logger.info(
        f"[MISSING_SLOTS] compute_missing_slots result: {missing_slots}"
    )
    print(f"[MISSING_SLOTS] compute_missing_slots result: {missing_slots}")
    
    # INVARIANT CHECK: missing_slots must be a list
    assert isinstance(missing_slots, list), (
        f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
    )
    
    return missing_slots


def filter_slots_by_domain(
    slots: Dict[str, Any],
    intent_name: str
) -> Dict[str, Any]:
    """
    Filter slots to only include those valid for the intent's domain.
    
    ARCHITECTURAL INVARIANT: Domain slot isolation
    - Service domain (CREATE_APPOINTMENT, MODIFY_BOOKING): date, time, service_id
    - Reservation domain (CREATE_RESERVATION, MODIFY_RESERVATION): start_date, end_date, service_id
    - service_id is valid in both domains
    - date/time from service must NOT leak into reservation
    - start_date/end_date from reservation must NOT leak into service
    - Generic 'date' must NOT satisfy start_date/end_date for CREATE_RESERVATION
    
    This must be called BEFORE computing effective_collected_slots to prevent
    cross-domain slot leakage.
    
    Args:
        slots: Slots dictionary to filter
        intent_name: Intent name to determine domain
        
    Returns:
        Filtered slots dictionary (only slots valid for intent domain)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    print(f"[DOMAIN_FILTER] ========== ENTRY ==========")
    print(f"[DOMAIN_FILTER] intent_name={intent_name}")
    print(f"[DOMAIN_FILTER] slots type={type(slots)}")
    print(f"[DOMAIN_FILTER] slots={slots}")
    
    if not intent_name or not slots:
        result = slots.copy() if slots else {}
        print(f"[DOMAIN_FILTER] EARLY EXIT: intent_name={intent_name}, slots empty -> returning {result}")
        return result
    
    print(f"[DOMAIN_FILTER] Input slots keys={list(slots.keys())}")
    for key, value in slots.items():
        print(f"[DOMAIN_FILTER]   input_slot[{key}] = {value}")
    
    # Define domain-specific valid slots
    service_domain_intents = {"CREATE_APPOINTMENT", "MODIFY_BOOKING"}
    reservation_domain_intents = {"CREATE_RESERVATION", "MODIFY_RESERVATION"}
    
    # Determine domain
    if intent_name in service_domain_intents:
        # Service domain: date, time, service_id, has_datetime, date_range, booking_id
        valid_slots = {"date", "time", "service_id", "has_datetime", "date_range", "booking_id"}
        
        # MODIFY_BOOKING delta slots: preserve all slots valid for EITHER domain OR declared delta slots
        # Valid delta slots for MODIFY_BOOKING: start_date, end_date, date_range, time, service_id, duration
        if intent_name == "MODIFY_BOOKING":
            # Add delta slots to valid_slots for MODIFY_BOOKING
            delta_slots = {"start_date", "end_date", "duration"}
            valid_slots = valid_slots | delta_slots
            print(f"[DOMAIN_FILTER] MODIFY_BOOKING detected: adding delta slots {delta_slots}, valid_slots={valid_slots}")
        
        print(f"[DOMAIN_FILTER] Service domain detected: valid_slots={valid_slots}")
    elif intent_name in reservation_domain_intents:
        # Reservation domain: start_date, end_date, service_id, date_range (NOT date, time)
        valid_slots = {"start_date", "end_date", "service_id", "date_range", "booking_id"}
        print(f"[DOMAIN_FILTER] Reservation domain detected: valid_slots={valid_slots}")
    else:
        # Unknown intent - keep all slots (let other filters handle it)
        result = slots.copy()
        print(f"[DOMAIN_FILTER] Unknown intent -> keeping all slots: {list(result.keys())}")
        return result
    
    # Filter slots to only valid ones for domain (or delta slots for MODIFY_BOOKING)
    filtered = {}
    dropped = []
    
    for slot_name, slot_value in slots.items():
        if slot_name in valid_slots:
            # Slot is valid for domain (or delta slot for MODIFY_BOOKING) - preserve it
            filtered[slot_name] = slot_value
            print(f"[DOMAIN_FILTER] PRESERVED slot[{slot_name}] = {slot_value} (valid for {intent_name})")
        else:
            # Slot is NOT valid for domain - drop it
            dropped.append(slot_name)
            print(f"[DOMAIN_FILTER] DROPPED slot[{slot_name}] = {slot_value} (NOT valid for {intent_name}, valid_slots={valid_slots})")
            logger.debug(
                f"[DOMAIN_FILTER] Dropping slot '{slot_name}' (not valid for {intent_name} domain, "
                f"valid_slots={valid_slots})"
            )
    
    print(f"[DOMAIN_FILTER] Filtered slots keys={list(filtered.keys())}")
    print(f"[DOMAIN_FILTER] Dropped slots={dropped}")
    
    # INVARIANT CHECK (test/debug only): If raw_luma_slots (slots input) is non-empty 
    # AND domain_filtered_slots is empty -> RAISE error
    # This guarantees Luma slots are never silently dropped
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_DOMAIN_FILTER") == "1":
        if slots and not filtered:
            error_msg = (
                f"INVARIANT VIOLATION: Domain filter returned empty dict when input slots were non-empty!\n"
                f"  intent: {intent_name}\n"
                f"  input_slots: {slots}\n"
                f"  filtered_slots: {filtered}\n"
                f"  valid_slots: {valid_slots}\n"
                f"  dropped_slots: {dropped}\n"
                f"This violates the invariant that domain_filter MUST NOT discard valid Luma slots."
            )
            logger.error(f"[DOMAIN_FILTER_INVARIANT] {error_msg}")
            print(f"\n[DOMAIN_FILTER_INVARIANT] {error_msg}")
            # Do NOT swallow this error - let the test crash
            raise Exception(error_msg)
    
    # Log filtering results
    if dropped:
        logger.info(
            f"[DOMAIN_FILTER] filter_slots_by_domain: "
            f"intent={intent_name}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )
        print(f"[DOMAIN_FILTER] LOG: dropped_slots={dropped}, preserved_slots={list(filtered.keys())}")
    
    print(f"[DOMAIN_FILTER] ========== EXIT: filtered={list(filtered.keys())} ==========")
    return filtered


def filter_collected_slots_for_intent(
    collected_slots: Dict[str, Any],
    old_intent: str,
    new_intent: str
) -> Dict[str, Any]:
    """
    Filter collected slots when intent changes.
    
    ARCHITECTURAL INVARIANT: Intent change is a hard boundary
    - On intent change, drop slots that are not valid for the new intent
    - Preserve slots that overlap semantically (e.g., service_id if applicable)
    - Do NOT reuse old slot names (e.g. date/time must NOT leak into reservation)
    - start_date/end_date must NOT satisfy service date implicitly
    - Only keep slots that are in the new intent's slot universe
    
    This function must be STRICT to prevent cross-domain slot leakage.
    
    Args:
        collected_slots: Previously collected slots (effective_slots from session)
        old_intent: Previous intent name
        new_intent: New intent name
        
    Returns:
        Filtered collected slots (only slots valid for new intent)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    if old_intent == new_intent:
        # Same intent - keep all slots
        return collected_slots.copy() if collected_slots else {}
    
    # Intent changed - only keep slots that are valid for new intent
    # CRITICAL: This must be strict to prevent slot leakage
    
    # Define valid slot universe for each intent (STRICT)
    # CREATE_APPOINTMENT: date, time, service_id (and derived has_datetime, date_range)
    # CREATE_RESERVATION: start_date, end_date, service_id, date_range (NOT date, time)
    # MODIFY_BOOKING: booking_id (and domain-specific slots)
    # CANCEL_BOOKING: booking_id
    
    valid_slots_by_intent = {
        "CREATE_APPOINTMENT": {"date", "time", "service_id", "has_datetime", "date_range"},
        "CREATE_RESERVATION": {"start_date", "end_date", "service_id", "date_range"},
        "MODIFY_BOOKING": {"booking_id"},
        "CANCEL_BOOKING": {"booking_id"},
    }
    
    valid_slots_new = valid_slots_by_intent.get(new_intent, set())
    
    # CRITICAL: Strict filtering - only keep slots in valid universe
    # service_id is preserved if it's in the valid slots (it's in both CREATE_APPOINTMENT and CREATE_RESERVATION)
    # date/time from service intent must NOT leak into reservation intent
    # start_date/end_date must NOT satisfy service date implicitly
    
    filtered = {}
    dropped = []
    
    for slot_name, slot_value in (collected_slots or {}).items():
        if slot_name in valid_slots_new:
            # Slot is valid for new intent - preserve it
            filtered[slot_name] = slot_value
        else:
            # Slot is NOT valid for new intent - drop it
            dropped.append(slot_name)
            logger.debug(
                f"[INTENT_CHANGE] Dropping slot '{slot_name}' (not valid for {new_intent}, "
                f"valid_slots={valid_slots_new})"
            )
    
    # Log filtering results
    if dropped:
        logger.info(
            f"[INTENT_CHANGE] filter_collected_slots_for_intent: "
            f"old_intent={old_intent}, new_intent={new_intent}, "
            f"dropped_slots={dropped}, preserved_slots={list(filtered.keys())}"
        )
    
    return filtered


def promote_slots_for_intent(
    raw_slots: Dict[str, Any],
    intent_name: str,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Promote raw slots to intent-specific slots (in-memory, non-persistent).
    
    ARCHITECTURAL INVARIANT: Promotion must be IDEMPOTENT and ADDITIVE.
    - Promotion starts with ALL existing merged slots (session.slots + luma slots)
    - Promotion may ADD derived slots (e.g., date → start_date)
    - Promotion must NEVER remove or overwrite existing slots
    - Promotion must NOT depend on current-turn presence alone
    - If start_date exists in merged slots, it must remain even if no date_roles appear this turn
    - date_roles may ADD meaning but must not be required to PRESERVE slots
    - Promotion logic must be safe to run repeatedly with no side effects
    
    This runs BEFORE computing missing_slots but is NEVER persisted.
    Promotion rules are intent-scoped and role-aware.
    
    Args:
        raw_slots: Merged slots (session slots + luma slots) - durable facts that must be preserved
        intent_name: Intent name for promotion rules
        context: Context from Luma response (for date_roles, etc.)
        
    Returns:
        Promoted slots dict (raw_slots + promoted slots, non-persistent)
        All input slots are preserved - promotion is additive only
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # CRITICAL: Start with copy of ALL existing merged slots - promotion is additive, never destructive
    # This ensures all session slots are preserved, regardless of current-turn conditions
    promoted = raw_slots.copy() if raw_slots else {}
    
    # LOG: promoted_slots BEFORE promotion
    logger.info(
        f"[PROMOTION] BEFORE promotion: intent={intent_name}, "
        f"input_slots={list(raw_slots.keys()) if raw_slots else []}, "
        f"promoted_slots={list(promoted.keys())}"
    )
    print(
        f"[PROMOTION] BEFORE promotion: intent={intent_name}, "
        f"input_slots={list(raw_slots.keys()) if raw_slots else []}, "
        f"promoted_slots={list(promoted.keys())}"
    )
    
    if intent_name == "CREATE_RESERVATION":
        # Promotion rules for reservations
        date_roles = context.get("date_roles", []) if context else []
        
        # date_range → start_date + end_date (ADD only if both present AND slots don't already exist)
        # CRITICAL: Do NOT overwrite existing start_date/end_date
        date_range = raw_slots.get("date_range")
        if isinstance(date_range, dict):
            range_start = date_range.get("start")
            range_end = date_range.get("end")
            if range_start and range_end:
                # Only ADD if slots don't already exist
                if "start_date" not in promoted:
                    promoted["start_date"] = range_start
                    logger.info(f"[PROMOTION] ADDED start_date from date_range: {range_start}")
                    print(f"[PROMOTION] ADDED start_date from date_range: {range_start}")
                else:
                    logger.debug(
                        f"[PROMOTION] SKIPPED start_date promotion (already exists: {promoted.get('start_date')})"
                    )
                
                if "end_date" not in promoted:
                    promoted["end_date"] = range_end
                    logger.info(f"[PROMOTION] ADDED end_date from date_range: {range_end}")
                    print(f"[PROMOTION] ADDED end_date from date_range: {range_end}")
                else:
                    logger.debug(
                        f"[PROMOTION] SKIPPED end_date promotion (already exists: {promoted.get('end_date')})"
                    )
        
        # date → start_date (ADD only if date_roles explicitly indicates START_DATE AND start_date doesn't exist)
        # CRITICAL: Generic 'date' must NOT automatically satisfy start_date/end_date
        # Only route via awaiting_slot, not satisfy required slots
        # date_roles may ADD meaning but must not be required to PRESERVE slots
        # If start_date already exists, preserve it regardless of date_roles
        if "date" in raw_slots and "START_DATE" in date_roles:
            if "start_date" not in promoted:
                promoted["start_date"] = raw_slots["date"]
                logger.info(
                    f"[PROMOTION] ADDED start_date from date with START_DATE role: {raw_slots['date']}"
                )
                print(
                    f"[PROMOTION] ADDED start_date from date with START_DATE role: {raw_slots['date']}"
                )
            else:
                logger.debug(
                    f"[PROMOTION] SKIPPED start_date promotion (already exists: {promoted.get('start_date')}, "
                    f"date_roles={date_roles})"
                )
        elif "start_date" in promoted:
            # start_date exists but no date_roles this turn - PRESERVE it
            logger.debug(
                f"[PROMOTION] PRESERVED existing start_date: {promoted.get('start_date')} "
                f"(no date_roles this turn, but slot persists)"
            )
        # CRITICAL: Do NOT promote generic 'date' to start_date without explicit START_DATE role
        # Generic 'date' should only route via awaiting_slot, not satisfy required slots
        
        # date → end_date (ADD only if date_roles explicitly indicates END_DATE AND end_date doesn't exist)
        if "date" in raw_slots and "END_DATE" in date_roles:
            if "end_date" not in promoted:
                promoted["end_date"] = raw_slots["date"]
                logger.info(
                    f"[PROMOTION] ADDED end_date from date with END_DATE role: {raw_slots['date']}"
                )
                print(
                    f"[PROMOTION] ADDED end_date from date with END_DATE role: {raw_slots['date']}"
                )
            else:
                logger.debug(
                    f"[PROMOTION] SKIPPED end_date promotion (already exists: {promoted.get('end_date')}, "
                    f"date_roles={date_roles})"
                )
        elif "end_date" in promoted:
            # end_date exists but no date_roles this turn - PRESERVE it
            logger.debug(
                f"[PROMOTION] PRESERVED existing end_date: {promoted.get('end_date')} "
                f"(no date_roles this turn, but slot persists)"
            )
        # CRITICAL: Do NOT promote generic 'date' to end_date without explicit END_DATE role
        # Generic 'date' should only route via awaiting_slot, not satisfy required slots
    
    elif intent_name == "CREATE_APPOINTMENT":
        # Promotion rules for service appointments
        # date_range → date (ADD only if date not already present)
        # CRITICAL: Do NOT overwrite existing date slot
        if "date_range" in raw_slots and "date" not in promoted:
            # Promote date_range to date (non-persistent view)
            date_range = raw_slots.get("date_range")
            if isinstance(date_range, dict):
                # If date_range is a dict, extract start date
                promoted["date"] = date_range.get("start") or date_range.get("value") or date_range
            else:
                # If date_range is a string or other type, use directly
                promoted["date"] = date_range
            logger.info(f"[PROMOTION] ADDED date from date_range: {promoted['date']}")
            print(f"[PROMOTION] ADDED date from date_range: {promoted['date']}")
        elif "date" in promoted:
            logger.debug(
                f"[PROMOTION] SKIPPED date promotion (already exists: {promoted.get('date')})"
            )
        
        # date + time → has_datetime (for execution readiness)
        # CRITICAL: Only ADD has_datetime if both date and time exist
        # Do NOT overwrite if already exists
        if "has_datetime" not in promoted:
            if ("date" in promoted and "time" in raw_slots) or ("date" in raw_slots and "time" in raw_slots):
                promoted["has_datetime"] = True
                logger.info("[PROMOTION] ADDED has_datetime (date + time present)")
                print("[PROMOTION] ADDED has_datetime (date + time present)")
    
    # LOG: promoted_slots AFTER promotion
    logger.info(
        f"[PROMOTION] AFTER promotion: intent={intent_name}, "
        f"promoted_slots={list(promoted.keys())}"
    )
    print(
        f"[PROMOTION] AFTER promotion: intent={intent_name}, "
        f"promoted_slots={list(promoted.keys())}"
    )
    
    # CRITICAL: Verify all input slots are preserved
    input_slot_keys = set(raw_slots.keys()) if raw_slots else set()
    promoted_slot_keys = set(promoted.keys())
    if input_slot_keys:
        lost_slots = input_slot_keys - promoted_slot_keys
        if lost_slots:
            logger.error(
                f"[PROMOTION] VIOLATION: Slots lost during promotion! "
                f"Lost slots: {list(lost_slots)}, "
                f"input_slots={list(input_slot_keys)}, "
                f"promoted_slots={list(promoted_slot_keys)}"
            )
            # Restore lost slots (fail-safe)
            for key in lost_slots:
                promoted[key] = raw_slots[key]
                logger.warning(f"[PROMOTION] Restored lost slot: {key} = {raw_slots[key]}")
    
    return promoted

