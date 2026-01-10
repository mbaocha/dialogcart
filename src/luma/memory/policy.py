"""
Memory policy functions for booking state management.

This module encapsulates ALL memory-related decision logic:
- Determining if a booking is active (PARTIAL or RESOLVED)
- Persisting drafts (service-only, service+date) for continuation
- Intent normalization for continuations
- Continuation detection and merging
- Contextual update detection
- Memory clearing decisions
- Memory persistence decisions
"""
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, timezone as dt_timezone

# Internal intent constant
CONTEXTUAL_UPDATE = "CONTEXTUAL_UPDATE"

# Import helper functions needed for memory policy decisions
try:
    from luma.api import (
        _merge_semantic_results,
        _count_mutable_slots_modified,
        _has_booking_verb,
        _get_business_categories
    )
except ImportError:
    # Fallback if api.py is not importable
    def _merge_semantic_results(*args, **kwargs):
        raise ImportError("_merge_semantic_results not available")
    def _count_mutable_slots_modified(*args, **kwargs):
        raise ImportError("_count_mutable_slots_modified not available")
    def _has_booking_verb(*args, **kwargs):
        raise ImportError("_has_booking_verb not available")
    def _get_business_categories(*args, **kwargs):
        raise ImportError("_get_business_categories not available")


def is_booking_intent(intent: str) -> bool:
    """
    Check if intent is a booking intent (CREATE_APPOINTMENT or CREATE_RESERVATION).
    
    Args:
        intent: Intent string to check
        
    Returns:
        True if intent is CREATE_APPOINTMENT or CREATE_RESERVATION, False otherwise
    """
    return intent in {"CREATE_APPOINTMENT", "CREATE_RESERVATION"}


def is_active_booking(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Check if memory contains an active booking (PARTIAL or RESOLVED).

    An active booking is one that:
    - Has intent == CREATE_APPOINTMENT or CREATE_RESERVATION
    - Has booking_state == "PARTIAL" or "RESOLVED" OR has clarification (PARTIAL)
    """
    if not memory_state:
        return False

    if not is_booking_intent(memory_state.get("intent", "")):
        return False

    # Check for clarification (indicates PARTIAL)
    if memory_state.get("clarification") is not None:
        return True

    # Check booking_state if stored
    booking_state = memory_state.get("booking_state", {})
    if isinstance(booking_state, dict):
        booking_state_value = booking_state.get("booking_state")
        if booking_state_value in ("PARTIAL", "RESOLVED"):
            return True

    return False


def is_partial_booking(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Check if memory contains a PARTIAL booking.
    A partial booking is one that:
    - Has intent == CREATE_APPOINTMENT or CREATE_RESERVATION
    - Has booking_state == "PARTIAL" OR has clarification
    """
    if not memory_state:
        return False

    if not is_booking_intent(memory_state.get("intent", "")):
        return False

    # Check for clarification (indicates PARTIAL)
    if memory_state.get("clarification") is not None:
        return True

    # Check booking_state if stored
    booking_state = memory_state.get("booking_state", {})
    if isinstance(booking_state, dict):
        booking_state_value = booking_state.get("booking_state")
        if booking_state_value == "PARTIAL":
            return True

    return False


def state_exists(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Determine if a non-expired state object exists in memory.
    
    Args:
        memory_state: Memory state dict (None if no state)
    
    Returns:
        True if state exists, False otherwise
    """
    return memory_state is not None and isinstance(memory_state, dict) and len(memory_state) > 0


def is_new_task(
    input_text: str,
    extraction_result: Dict[str, Any],
    intent_result: Dict[str, Any],
    state_exists_flag: bool
) -> bool:
    """
    Determine if the input starts a new task or is a follow-up to existing state.
    
    CRITICAL INVARIANT: Continuation detection is based on state existence, not intent.
    
    Rules:
    - Return True (new task) ONLY if:
        • no state exists (always new task when no state)
        • explicit reset language ("cancel that", "start over", "new booking")
        • VERY strong intent signal (high confidence, non-UNKNOWN, explicit booking intent)
    - Return False (follow-up) if:
        • state exists AND no strong new-task signal detected
        • UNKNOWN intent (always follow-up when state exists)
        • Low confidence intent (always follow-up when state exists)
    
    UNKNOWN intent NEVER forces new-task behavior when state exists.
    
    Args:
        input_text: User input text
        extraction_result: Extraction output with entities
        intent_result: Intent classification result with 'intent' and 'confidence' keys
        state_exists_flag: Whether state exists in memory
    
    Returns:
        True if this starts a new task, False if it's a follow-up
    """
    # No state exists -> always new task
    if not state_exists_flag:
        return True
    
    # CRITICAL INVARIANT: When state exists, default to follow-up unless strong new-task signal
    # UNKNOWN intent must NEVER trigger new-task behavior when state exists
    
    # Check for explicit reset language (strongest new-task signal, overrides intent)
    text_lower = input_text.lower().strip()
    reset_patterns = [
        "cancel that",
        "start over",
        "new booking",
        "forget that",
        "never mind",
        "ignore that",
        "clear that",
        "reset"
    ]
    for pattern in reset_patterns:
        if pattern in text_lower:
            return True
    
    # Get intent - UNKNOWN intent NEVER triggers new-task when state exists
    intent = intent_result.get("intent", "UNKNOWN")
    confidence = intent_result.get("confidence", 0.0)
    
    # UNKNOWN intent (or falsy/unknown intent) NEVER forces new-task (always follow-up when state exists)
    # This check ensures follow-ups like "tomorrow" with UNKNOWN intent continue the existing task
    if intent == "UNKNOWN" or not intent:
        return False
    
    # Check for VERY strong new-task signal: high confidence + explicit booking intent
    # Require both high confidence AND explicit booking intent to start new task
    # This prevents follow-ups from being misclassified as new tasks
    if confidence >= 0.85 and is_booking_intent(intent):
        return True
    
    # Default: treat as follow-up when state exists
    # This ensures follow-ups (date/time only inputs) continue the existing task
    return False


def get_state_intent(memory_state: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Get the intent from existing state.
    
    Args:
        memory_state: Memory state dict
    
    Returns:
        Intent string from state, or None if no state
    """
    if not state_exists(memory_state):
        return None
    return memory_state.get("intent")


def merge_slots_for_followup(
    memory_booking: Dict[str, Any],
    current_booking: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge slots for follow-up: new slot value replaces old, missing slot keeps old.
    
    Args:
        memory_booking: Booking state from memory
        current_booking: Booking state from current input
    
    Returns:
        Merged booking state
    """
    merged = {}
    
    # SERVICES: Replace if mentioned in current, else keep from memory
    current_services = current_booking.get("services", [])
    if current_services:
        merged["services"] = current_services
    else:
        merged["services"] = memory_booking.get("services", [])
    
    # DATETIME: Replace if current has datetime_range, else keep from memory
    current_datetime_range = current_booking.get("datetime_range")
    if current_datetime_range is not None:
        merged["datetime_range"] = current_datetime_range
    else:
        merged["datetime_range"] = memory_booking.get("datetime_range")
    
    # DURATION: Replace if mentioned, else keep from memory
    current_duration = current_booking.get("duration")
    if current_duration is not None:
        merged["duration"] = current_duration
    else:
        merged["duration"] = memory_booking.get("duration")
    
    # DATE_RANGE: Replace if current has date_range, else keep from memory
    current_date_range = current_booking.get("date_range")
    if current_date_range is not None:
        merged["date_range"] = current_date_range
    else:
        merged["date_range"] = memory_booking.get("date_range")
    
    # TIME_RANGE: Replace if current has time_range, else keep from memory
    current_time_range = current_booking.get("time_range")
    if current_time_range is not None:
        merged["time_range"] = current_time_range
    else:
        merged["time_range"] = memory_booking.get("time_range")
    
    
    return merged


def maybe_persist_draft(
    resolved_booking: Dict[str, Any],
    memory_state: Optional[Dict[str, Any]],
    intent: str,
    memory_store: Any,
    user_id: str,
    domain: str,
    memory_ttl: int,
    execution_trace: Dict[str, Any],
    request_id: str,
    logger: Any
) -> Dict[str, Any]:
    """
    Persist service-only or service+date drafts for continuation.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    # Legacy implementation preserved for backward compatibility
    # This may be bypassed in new state-first model
    return memory_state


# ============================================================================
# DEPRECATED FUNCTIONS - DO NOT USE
# ============================================================================
# The following functions are legacy continuation logic that has been replaced
# by the new state-first model (state_exists, is_new_task, merge_slots_for_followup).
# These functions are NOT exported and are kept only for reference.
# DO NOT CALL THESE FUNCTIONS - they may be removed in a future version.
# ============================================================================

def normalize_intent_for_continuation(
    intent: str,
    memory_state: Optional[Dict[str, Any]]
) -> Tuple[str, Optional[str]]:
    """
    DEPRECATED: Normalize intent for active booking continuations.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    if is_active_booking(memory_state):
        # Return the stored intent from memory (already real intent)
        return memory_state.get("intent", intent), intent
    return intent, None


def is_continuation_applicable(
    memory_state: Optional[Dict[str, Any]],
    intent: str
) -> bool:
    """
    DEPRECATED: Determine if continuation logic should be applied.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    return is_booking_intent(intent) and is_active_booking(memory_state)


def detect_continuation(
    memory_state: Optional[Dict[str, Any]],
    semantic_result: Any  # SemanticResolutionResult
) -> Tuple[bool, Dict[str, Any], bool]:
    """
    DEPRECATED: Detect if this is a continuation and extract memory booking data.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    is_continuation = True
    memory_booking_state = memory_state.get("booking_state", {}) if memory_state else {}
    booking_state_value = memory_booking_state.get("booking_state") if isinstance(memory_booking_state, dict) else None
    is_resolved_continuation = booking_state_value == "RESOLVED"

    memory_resolved_booking = memory_state.get("resolved_booking_semantics", {}) if memory_state else {}
    if not memory_resolved_booking:
        memory_resolved_booking = {}
        memory_services = memory_booking_state.get("services", [])
        if memory_services:
            memory_resolved_booking["services"] = memory_services
    
    return is_continuation, memory_resolved_booking, is_resolved_continuation


def merge_continuation_semantics(
    memory_resolved_booking: Dict[str, Any],
    current_semantic_result: Any  # SemanticResolutionResult
) -> Dict[str, Any]:
    """
    DEPRECATED: Merge semantic results from memory and current input for continuation.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    merged_resolved_booking = _merge_semantic_results(
        memory_resolved_booking,
        current_semantic_result.resolved_booking
    )

    merged_needs_clarification = current_semantic_result.needs_clarification
    merged_clarification = current_semantic_result.clarification

    if not merged_needs_clarification or not merged_clarification:
        merged_needs_clarification = False
        merged_clarification = None
    
    return {
        "resolved_booking": merged_resolved_booking,
        "needs_clarification": merged_needs_clarification,
        "clarification": merged_clarification
    }


def detect_contextual_update(
    memory_state: Optional[Dict[str, Any]],
    intent: str,
    text: str,
    merged_semantic_result: Any,  # SemanticResolutionResult
    extraction_result: Dict[str, Any],
    logger: Any,
    user_id: str,
    request_id: str
) -> str:
    """
    DEPRECATED: Detect if intent should be normalized to CONTEXTUAL_UPDATE.
    
    This function is kept for backward compatibility but may be bypassed
    in the new state-first model.
    """
    effective_intent = intent
    if memory_state and is_booking_intent(memory_state.get("intent", "")):
        if intent == "MODIFY_BOOKING" or intent == "UNKNOWN":
            mutable_slots_modified = _count_mutable_slots_modified(
                merged_semantic_result, extraction_result
            )
            has_service = len(_get_business_categories(extraction_result)) > 0
            has_booking_verb = _has_booking_verb(text)

            if mutable_slots_modified >= 1 and not has_service and not has_booking_verb:
                effective_intent = CONTEXTUAL_UPDATE
                logger.debug(
                    f"Detected CONTEXTUAL_UPDATE for user {user_id}",
                    extra={'request_id': request_id,
                           'original_intent': intent,
                           'slots_modified': mutable_slots_modified}
                )
        elif is_booking_intent(intent) and is_partial_booking(memory_state):
            logger.debug(
                f"Booking intent continuation of PARTIAL booking for user {user_id}",
                extra={'request_id': request_id}
            )
    return effective_intent


def should_clear_memory(effective_intent: str) -> bool:
    """Determines if memory should be cleared based on the effective intent."""
    return effective_intent in {"CANCEL_BOOKING", "CONFIRM_BOOKING", "COMMIT_BOOKING"}


def should_persist_memory(
    effective_intent: str,
    data: Dict[str, Any],
    should_clear: bool
) -> Tuple[bool, bool, bool, str]:
    """
    Determines if and how memory should be persisted.
    Returns: (should_persist, is_booking_intent, is_modify_with_booking_id, persist_intent)
    """
    is_booking_intent_flag = is_booking_intent(effective_intent) or effective_intent == CONTEXTUAL_UPDATE
    is_modify_with_booking_id = effective_intent == "MODIFY_BOOKING" and data.get("booking_id") is not None
    
    should_persist = not should_clear and (is_booking_intent_flag or is_modify_with_booking_id)
    # Store real intent (CREATE_APPOINTMENT or CREATE_RESERVATION)
    persist_intent = effective_intent
    
    return should_persist, is_booking_intent_flag, is_modify_with_booking_id, persist_intent


def prepare_memory_for_persistence(
    memory_state: Optional[Dict[str, Any]],
    decision_result: Any, # DecisionResult
    persist_intent: str,
    current_booking: Dict[str, Any],
    current_clarification: Optional[Dict[str, Any]],
    merged_semantic_result: Any,  # SemanticResolutionResult
    logger: Any,
    user_id: str,
    request_id: str
) -> Dict[str, Any]:
    """
    Prepares the memory state for persistence, handling RESOLVED vs PARTIAL logic.
    
    Args:
        persist_intent: Real intent to store (CREATE_APPOINTMENT or CREATE_RESERVATION)
    """
    try:
        from luma.memory.merger import merge_booking_state
    except ImportError:
        # Fallback
        def merge_booking_state(*args, **kwargs):
            return {}
    
    if decision_result and decision_result.status == "RESOLVED":
        if memory_state and is_partial_booking(memory_state):
            memory_state = None
            logger.info(
                f"Clearing PARTIAL booking state for RESOLVED booking: user {user_id}",
                extra={'request_id': request_id}
            )
        if current_clarification is not None:
            logger.warning(
                f"Unexpected clarification for RESOLVED booking, clearing: user {user_id}",
                extra={'request_id': request_id}
            )
            current_clarification = None

    merged_memory = merge_booking_state(
        memory_state=memory_state,
        current_intent=persist_intent,
        current_booking=current_booking,
        current_clarification=current_clarification,
        request_id=request_id
    )

    if decision_result and decision_result.status == "RESOLVED":
        if merged_memory.get("clarification") is not None:
            merged_memory["clarification"] = None
            logger.warning(
                f"Force-cleared clarification for RESOLVED booking: user {user_id}",
                extra={'request_id': request_id}
            )
        if "booking_state" in merged_memory:
            merged_memory["booking_state"]["booking_state"] = "RESOLVED"
    
    # Store resolved_booking_semantics for ALL states (RESOLVED and NEEDS_CLARIFICATION)
    # This ensures follow-ups can merge services/date_refs/time_refs from memory
    resolved_booking = merged_semantic_result.resolved_booking
    if resolved_booking:
        merged_memory["resolved_booking_semantics"] = resolved_booking
        state_label = "RESOLVED" if (decision_result and decision_result.status == "RESOLVED") else "NEEDS_CLARIFICATION"
        logger.info(
            f"Storing resolved_booking_semantics for {state_label}: user {user_id}",
            extra={
                'request_id': request_id,
                'stored_date_refs': resolved_booking.get('date_refs', []),
                'stored_date_roles': resolved_booking.get('date_roles', []),  # ← ADD date_roles to log
                'stored_date_mode': resolved_booking.get('date_mode', 'none'),
                'stored_time_refs': resolved_booking.get('time_refs', []),
                'stored_time_mode': resolved_booking.get('time_mode', 'none'),
                'stored_services': len(resolved_booking.get('services', []))
            }
        )
    return merged_memory


def get_final_memory_state(
    should_clear: bool,
    should_persist: bool,
    memory_state: Optional[Dict[str, Any]],
    merged_memory: Optional[Dict[str, Any]],
    effective_intent: str,
    is_booking_intent: bool,
    is_modify_with_booking_id: bool,
    current_booking: Dict[str, Any],
    current_clarification: Optional[Dict[str, Any]],
    logger: Any,
    request_id: str
) -> Dict[str, Any]:
    """
    Computes the final memory state to be returned in the response or persisted.
    """
    if should_clear:
        return {}
    elif should_persist:
        return merged_memory or {}
    elif memory_state:
        return memory_state
    else:
        # If no memory, use current state only (no merge)
        lifecycle = "CREATING" if is_booking_intent else "NONE"
        return {
            "intent": effective_intent if effective_intent != CONTEXTUAL_UPDATE else (memory_state.get("intent") if memory_state else effective_intent),
            "booking_state": current_booking if (is_booking_intent or is_modify_with_booking_id) else {},
            "booking_lifecycle": lifecycle,
            "clarification": current_clarification,
            "last_updated": datetime.now(dt_timezone.utc).isoformat()
        }
