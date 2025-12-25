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


def is_active_booking(memory_state: Optional[Dict[str, Any]]) -> bool:
    """
    Check if memory contains an active booking (PARTIAL or RESOLVED).

    An active booking is one that:
    - Has intent == "CREATE_BOOKING"
    - Has booking_state == "PARTIAL" or "RESOLVED" OR has clarification (PARTIAL)
    """
    if not memory_state:
        return False

    if memory_state.get("intent") != "CREATE_BOOKING":
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
    - Has intent == "CREATE_BOOKING"
    - Has booking_state == "PARTIAL" OR has clarification
    """
    if not memory_state:
        return False

    if memory_state.get("intent") != "CREATE_BOOKING":
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
    Persist service-only and service+date drafts as active booking drafts.
    This ensures multi-turn slot filling works (service → date → time).

    Args:
        resolved_booking: The resolved booking dictionary to check
        memory_state: Current memory state (may be None)
        intent: Current intent
        memory_store: Memory store instance (may be None)
        user_id: User ID
        domain: Domain (e.g., "service")
        memory_ttl: TTL for memory storage
        execution_trace: Execution trace dictionary for timing
        request_id: Request ID for logging
        logger: Logger instance

    Returns:
        Updated memory_state dictionary (mutated in place, returned for convenience)
    """
    if not resolved_booking:
        return memory_state or {}

    has_service = bool(resolved_booking.get("services"))
    has_date = bool(
        resolved_booking.get("date_refs") or
        resolved_booking.get("date_range")
    )
    has_time = bool(
        resolved_booking.get("time_refs") or
        resolved_booking.get("time_range") or
        resolved_booking.get("time_constraint")
    )

    # Case 1: Service-only draft (persist for continuation)
    if has_service and not has_date and not has_time:
        # Ensure memory_state exists and is properly structured for persistence
        if not memory_state:
            memory_state = {}

        # Set intent if not already set
        if "intent" not in memory_state:
            memory_state["intent"] = "CREATE_BOOKING"

        # Store the semantic draft for proper merging on next turn
        memory_state["resolved_booking_semantics"] = resolved_booking
        memory_state["booking_state"] = {
            "booking_state": "PARTIAL",
            "reason": "MISSING_DATE_AND_TIME"
        }

        # Debug log #2: After memory mutation
        logger.debug(
            "MEMORY_WRITE",
            extra={
                'request_id': request_id,
                'user_id': user_id,
                'keys': list(memory_state.keys()),
                'booking_state': memory_state.get("booking_state"),
                'has_resolved_booking': "resolved_booking_semantics" in memory_state,
            }
        )

        # Persist immediately to memory store if available
        if memory_store and intent == "CREATE_BOOKING":
            try:
                # Debug log #1: Right before memory write
                logger.debug(
                    "SERVICE_ONLY_CHECK",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'has_service': bool(resolved_booking.get("services")),
                        'has_date': bool(resolved_booking.get("date_refs") or resolved_booking.get("date_range")),
                        'has_time': bool(resolved_booking.get("time_refs") or resolved_booking.get("time_range")),
                        'decision': None,  # Decision not yet made at this point
                    }
                )
                memory_state["last_updated"] = datetime.now(
                    dt_timezone.utc).isoformat()
                # Time memory write operation
                from luma.perf import StageTimer
                with StageTimer(execution_trace, "memory", request_id=request_id):
                    memory_store.set(
                        user_id=user_id,
                        domain=domain,
                        state=memory_state,
                        ttl=memory_ttl
                    )
                logger.debug(
                    "Persisted service-only booking draft to memory store",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'services': [s.get("text", "") for s in resolved_booking.get("services", []) if isinstance(s, dict)]
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Failed to persist service-only draft: {e}",
                    extra={'request_id': request_id}
                )
        else:
            # Debug log #1: Right before memory write (when persistence will happen later)
            logger.debug(
                "SERVICE_ONLY_CHECK",
                extra={
                    'request_id': request_id,
                    'user_id': user_id,
                    'has_service': bool(resolved_booking.get("services")),
                    'has_date': bool(resolved_booking.get("date_refs") or resolved_booking.get("date_range")),
                    'has_time': bool(resolved_booking.get("time_refs") or resolved_booking.get("time_range")),
                    'decision': None,  # Decision not yet made at this point
                }
            )
            logger.debug(
                "Prepared service-only semantic draft (will be persisted later)",
                extra={
                    'request_id': request_id,
                    'user_id': user_id,
                    'services': [s.get("text", "") for s in resolved_booking.get("services", []) if isinstance(s, dict)]
                }
            )

    # Case 2: Service + date draft (persist for continuation)
    elif has_service and has_date and not has_time:
        # Ensure memory_state exists and is properly structured for persistence
        if not memory_state:
            memory_state = {}

        # Set intent if not already set
        if "intent" not in memory_state:
            memory_state["intent"] = "CREATE_BOOKING"

        # Store the semantic draft for proper merging on next turn
        memory_state["resolved_booking_semantics"] = resolved_booking
        memory_state["booking_state"] = {
            "booking_state": "PARTIAL",
            "reason": "MISSING_TIME"
        }

        # Debug log #2: After memory mutation
        logger.debug(
            "MEMORY_WRITE",
            extra={
                'request_id': request_id,
                'user_id': user_id,
                'keys': list(memory_state.keys()),
                'booking_state': memory_state.get("booking_state"),
                'has_resolved_booking': "resolved_booking_semantics" in memory_state,
            }
        )

        # Persist immediately to memory store if available
        if memory_store and intent == "CREATE_BOOKING":
            try:
                # Debug log #1: Right before memory write
                logger.debug(
                    "SERVICE_ONLY_CHECK",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'has_service': bool(resolved_booking.get("services")),
                        'has_date': bool(resolved_booking.get("date_refs") or resolved_booking.get("date_range")),
                        'has_time': bool(resolved_booking.get("time_refs") or resolved_booking.get("time_range")),
                        'decision': None,  # Decision not yet made at this point
                    }
                )
                memory_state["last_updated"] = datetime.now(
                    dt_timezone.utc).isoformat()
                # Time memory write operation
                from luma.perf import StageTimer
                with StageTimer(execution_trace, "memory", request_id=request_id):
                    memory_store.set(
                        user_id=user_id,
                        domain=domain,
                        state=memory_state,
                        ttl=memory_ttl
                    )
                logger.debug(
                    "Persisted service+date booking draft to memory store",
                    extra={
                        'request_id': request_id,
                        'user_id': user_id,
                        'services': len(resolved_booking.get("services", [])),
                        'date_refs': resolved_booking.get("date_refs", []),
                        'date_mode': resolved_booking.get("date_mode", "none")
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Failed to persist service+date draft: {e}",
                    extra={'request_id': request_id}
                )
        else:
            # Debug log #1: Right before memory write (when persistence will happen later)
            logger.debug(
                "SERVICE_ONLY_CHECK",
                extra={
                    'request_id': request_id,
                    'user_id': user_id,
                    'has_service': bool(resolved_booking.get("services")),
                    'has_date': bool(resolved_booking.get("date_refs") or resolved_booking.get("date_range")),
                    'has_time': bool(resolved_booking.get("time_refs") or resolved_booking.get("time_range")),
                    'decision': None,  # Decision not yet made at this point
                }
            )
            logger.debug(
                "Prepared service+date semantic draft (will be persisted later)",
                extra={
                    'request_id': request_id,
                    'user_id': user_id,
                    'services': len(resolved_booking.get("services", [])),
                    'date_refs': resolved_booking.get("date_refs", []),
                    'date_mode': resolved_booking.get("date_mode", "none")
                }
            )

    return memory_state or {}


def normalize_intent_for_continuation(
    intent: str,
    memory_state: Optional[Dict[str, Any]]
) -> Tuple[str, Optional[str]]:
    """
    Normalize intent for active booking continuations.
    
    If an active booking exists, force intent to CREATE_BOOKING regardless of raw classification.
    This ensures merge logic always runs for continuations like "at 10" or "make it 10".
    
    Args:
        intent: Original intent from intent resolver
        memory_state: Current memory state (may be None)
    
    Returns:
        Tuple of (normalized_intent, original_intent)
        - normalized_intent: "CREATE_BOOKING" if continuation, otherwise original intent
        - original_intent: Original intent if normalized, None otherwise
    """
    if is_active_booking(memory_state):
        return "CREATE_BOOKING", intent
    return intent, None


def is_continuation_applicable(
    memory_state: Optional[Dict[str, Any]],
    intent: str
) -> bool:
    """
    Determine if continuation logic should be applied.
    
    Continuation applies when:
    - intent == "CREATE_BOOKING"
    - memory_state contains an active booking (PARTIAL or RESOLVED)
    
    Args:
        memory_state: Current memory state (may be None)
        intent: Current intent
    
    Returns:
        True if continuation should be applied, False otherwise
    """
    return intent == "CREATE_BOOKING" and is_active_booking(memory_state)


def detect_continuation(
    memory_state: Optional[Dict[str, Any]],
    semantic_result: Any  # SemanticResolutionResult
) -> Tuple[bool, Dict[str, Any], bool]:
    """
    Detect if this is a continuation and extract memory booking data.
    
    Args:
        memory_state: Current memory state (may be None)
        semantic_result: Current semantic resolution result
    
    Returns:
        Tuple of (is_continuation, memory_resolved_booking, is_resolved_continuation)
        - is_continuation: True if this is a continuation
        - memory_resolved_booking: Resolved booking dict from memory (empty if not continuation)
        - is_resolved_continuation: True if memory contains RESOLVED booking
    """
    if not memory_state:
        return False, {}, False
    
    memory_booking_state = memory_state.get("booking_state", {})
    booking_state_value = memory_booking_state.get(
        "booking_state") if isinstance(memory_booking_state, dict) else None
    is_resolved_continuation = booking_state_value == "RESOLVED"
    
    # Extract resolved_booking from memory (if stored) or reconstruct from booking_state
    memory_resolved_booking = memory_state.get("resolved_booking_semantics", {})
    
    # If not stored, reconstruct from booking_state
    if not memory_resolved_booking:
        memory_resolved_booking = {}
        # Services from memory
        memory_services = memory_booking_state.get("services", [])
        if memory_services:
            memory_resolved_booking["services"] = memory_services
        # Note: date/time refs not available from booking_state alone
    
    return True, memory_resolved_booking, is_resolved_continuation


def merge_continuation_semantics(
    memory_resolved_booking: Dict[str, Any],
    current_semantic_result: Any  # SemanticResolutionResult
) -> Dict[str, Any]:
    """
    Merge semantic results from memory and current input for continuation.
    
    Args:
        memory_resolved_booking: Resolved booking from memory
        current_semantic_result: Current semantic resolution result
    
    Returns:
        Dict with keys: resolved_booking, needs_clarification, clarification
    """
    # Merge semantic results: preserve memory, fill with current
    merged_resolved_booking = _merge_semantic_results(
        memory_resolved_booking,
        current_semantic_result.resolved_booking
    )
    
    # CRITICAL: Preserve semantic clarifications (e.g., SERVICE_VARIANT) from original semantic_result
    merged_needs_clarification = current_semantic_result.needs_clarification
    merged_clarification = current_semantic_result.clarification
    
    # Only override if the original didn't have clarification
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
    Detect if intent should be normalized to CONTEXTUAL_UPDATE.
    
    CONTEXTUAL_UPDATE is detected when:
    - memory_state exists with intent == "CREATE_BOOKING"
    - current intent is "MODIFY_BOOKING" or "UNKNOWN"
    - at least one mutable slot is modified (date, time, duration)
    - no service or booking verb present
    
    Args:
        memory_state: Current memory state (may be None)
        intent: Current intent
        text: Original user text
        merged_semantic_result: Merged semantic result
        extraction_result: Extraction result
        logger: Logger instance
        user_id: User ID for logging
        request_id: Request ID for logging
    
    Returns:
        Effective intent (CONTEXTUAL_UPDATE if detected, otherwise original intent)
    """
    effective_intent = intent
    
    if memory_state and memory_state.get("intent") == "CREATE_BOOKING":
        # Check if this is a contextual update to existing CREATE_BOOKING draft
        if intent == "MODIFY_BOOKING" or intent == "UNKNOWN":
            # Check if at least one mutable slot is modified (date, time, or duration)
            mutable_slots_modified = _count_mutable_slots_modified(
                merged_semantic_result, extraction_result
            )
            # Check if no service or booking verb is present
            has_service = len(_get_business_categories(extraction_result)) > 0
            has_booking_verb = _has_booking_verb(text)
            
            # Safety: No booking_id, no service, no booking verb, and at least one mutable slot
            if mutable_slots_modified >= 1 and not has_service and not has_booking_verb:
                effective_intent = CONTEXTUAL_UPDATE
                logger.debug(
                    f"Detected CONTEXTUAL_UPDATE for user {user_id}",
                    extra={
                        'request_id': request_id,
                        'original_intent': intent,
                        'slots_modified': mutable_slots_modified
                    }
                )
        elif intent == "CREATE_BOOKING" and is_partial_booking(memory_state):
            # CREATE_BOOKING with PARTIAL memory is already handled as continuation
            # This is just for logging
            logger.debug(
                f"CREATE_BOOKING continuation of PARTIAL booking for user {user_id}",
                extra={'request_id': request_id}
            )
    
    return effective_intent


def should_clear_memory(intent: str) -> bool:
    """
    Determine if memory should be cleared based on intent.
    
    Memory is cleared for:
    - CANCEL_BOOKING
    - CONFIRM_BOOKING
    - COMMIT_BOOKING
    
    Args:
        intent: Current intent
    
    Returns:
        True if memory should be cleared, False otherwise
    """
    return intent in {"CANCEL_BOOKING", "CONFIRM_BOOKING", "COMMIT_BOOKING"}


def should_persist_memory(
    effective_intent: str,
    data: Dict[str, Any],
    should_clear: bool
) -> Tuple[bool, bool, bool, str]:
    """
    Determine if memory should be persisted and how.
    
    Args:
        effective_intent: Effective intent (may be CONTEXTUAL_UPDATE)
        data: Request data (contains booking_id for MODIFY_BOOKING check)
        should_clear: Whether memory should be cleared
    
    Returns:
        Tuple of (should_persist, is_booking_intent, is_modify_with_booking_id, persist_intent)
    """
    if should_clear:
        return False, False, False, effective_intent
    
    is_booking_intent = effective_intent == "CREATE_BOOKING" or effective_intent == CONTEXTUAL_UPDATE
    is_modify_with_booking_id = effective_intent == "MODIFY_BOOKING" and data.get("booking_id") is not None
    
    should_persist = is_booking_intent or is_modify_with_booking_id
    
    # CONTEXTUAL_UPDATE merges into CREATE_BOOKING draft
    # Persist with intent = CREATE_BOOKING (never persist CONTEXTUAL_UPDATE)
    persist_intent = "CREATE_BOOKING" if effective_intent == CONTEXTUAL_UPDATE else effective_intent
    
    return should_persist, is_booking_intent, is_modify_with_booking_id, persist_intent


def prepare_memory_for_persistence(
    memory_state: Optional[Dict[str, Any]],
    decision_result: Any,  # DecisionResult
    persist_intent: str,
    current_booking: Dict[str, Any],
    current_clarification: Optional[Dict[str, Any]],
    merged_semantic_result: Any,  # SemanticResolutionResult
    logger: Any,
    user_id: str,
    request_id: str
) -> Dict[str, Any]:
    """
    Prepare memory state for persistence based on decision result.
    
    This handles:
    - Clearing PARTIAL state for RESOLVED bookings
    - Ensuring RESOLVED bookings don't have clarifications
    - Storing resolved_booking_semantics for RESOLVED bookings
    
    Args:
        memory_state: Current memory state (may be None)
        decision_result: Decision result
        persist_intent: Intent to use for persistence
        current_booking: Current booking state
        current_clarification: Current clarification (may be None)
        merged_semantic_result: Merged semantic result
        logger: Logger instance
        user_id: User ID for logging
        request_id: Request ID for logging
    
    Returns:
        Prepared merged memory state
    """
    from luma.memory.merger import merge_booking_state
    
    prepared_memory_state = memory_state
    prepared_clarification = current_clarification
    
    # CRITICAL: Branch strictly on decision result for persistence
    # RESOLVED: Clear PARTIAL state and persist RESOLVED booking
    if decision_result and decision_result.status == "RESOLVED":
        # Decision is RESOLVED - clear any existing PARTIAL state
        if prepared_memory_state and is_partial_booking(prepared_memory_state):
            prepared_memory_state = None
            logger.info(
                f"Clearing PARTIAL booking state for RESOLVED booking: user {user_id}",
                extra={'request_id': request_id}
            )
        
        # Ensure current_clarification is None for RESOLVED
        if prepared_clarification is not None:
            logger.warning(
                f"Unexpected clarification for RESOLVED booking, clearing: user {user_id}",
                extra={'request_id': request_id}
            )
            prepared_clarification = None
    
    # Merge booking state (memory_state may be None if RESOLVED and PARTIAL was cleared)
    merged_memory = merge_booking_state(
        memory_state=prepared_memory_state,
        current_intent=persist_intent,
        current_booking=current_booking,
        current_clarification=prepared_clarification
    )
    
    # CRITICAL: Verify merged state has no clarification if decision is RESOLVED
    if decision_result and decision_result.status == "RESOLVED":
        if merged_memory.get("clarification") is not None:
            merged_memory["clarification"] = None
            logger.warning(
                f"Force-cleared clarification for RESOLVED booking: user {user_id}",
                extra={'request_id': request_id}
            )
        
        # Store booking_state = "RESOLVED" inside the booking_state dict
        if "booking_state" in merged_memory:
            merged_memory["booking_state"]["booking_state"] = "RESOLVED"
        
        # Store resolved_booking_semantics for RESOLVED bookings
        resolved_booking = merged_semantic_result.resolved_booking
        if resolved_booking:
            merged_memory["resolved_booking_semantics"] = resolved_booking
            logger.info(
                f"Storing resolved_booking_semantics for RESOLVED: user {user_id}",
                extra={
                    'request_id': request_id,
                    'stored_date_refs': resolved_booking.get('date_refs', []),
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
    is_booking_intent: bool,
    is_modify_with_booking_id: bool,
    effective_intent: str,
    memory_state: Optional[Dict[str, Any]],
    current_booking: Dict[str, Any],
    current_clarification: Optional[Dict[str, Any]],
    merged_memory: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Get final memory state based on clearing and persistence decisions.
    
    Args:
        should_clear: Whether memory should be cleared
        should_persist: Whether memory should be persisted
        is_booking_intent: Whether this is a booking intent
        is_modify_with_booking_id: Whether this is MODIFY_BOOKING with booking_id
        effective_intent: Effective intent
        memory_state: Current memory state
        current_booking: Current booking state
        current_clarification: Current clarification
        merged_memory: Merged memory state (if persistence happened)
    
    Returns:
        Final memory state dict
    """
    if not should_clear and is_booking_intent and merged_memory:
        # Use merged memory from booking intent persistence
        return merged_memory
    elif not should_clear and is_modify_with_booking_id and merged_memory:
        # Use merged memory from MODIFY_BOOKING persistence
        return merged_memory
    elif not should_clear and memory_state:
        # For non-booking intents, keep existing memory but don't update it
        return memory_state
    else:
        # If clearing or no memory, use current state only (no merge)
        return {
            "intent": effective_intent if effective_intent != CONTEXTUAL_UPDATE else "CREATE_BOOKING",
            "booking_state": current_booking if (is_booking_intent or is_modify_with_booking_id) else {},
            "clarification": current_clarification,
            "last_updated": datetime.now(dt_timezone.utc).isoformat()
        }

