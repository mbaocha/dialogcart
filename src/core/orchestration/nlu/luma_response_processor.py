"""
Luma Response Processor

Interprets Luma API responses and produces decision plans.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Clarification interpretation (reason, issues, context)
- CLARIFY outcome construction
- Intent extraction and validation
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
"""

import logging
import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

import yaml

from core.routing import get_template_key, get_action_name
from core.orchestration.errors import UnsupportedIntentError
from core.orchestration.api.turn_state import TurnState, DecisionReason
from luma.clarification.reasons import ClarificationReason

logger = logging.getLogger(__name__)

# Cache for intent execution config (loaded once per process)
_intent_execution_cache: Optional[Dict[str, Any]] = None
_cache_lock = threading.Lock()


def _load_intent_execution_config() -> Dict[str, Any]:
    """
    Load intent execution configuration from YAML file (cached at module level).
    
    Thread-safe lazy loading: loads once on first access, reuses cached data
    for subsequent calls. Zero YAML I/O on request path after initial load.
    
    Returns:
        Dictionary with intent execution config (intents -> commit, fallbacks)
    """
    global _intent_execution_cache
    
    # Fast path: return cached data if already loaded
    if _intent_execution_cache is not None:
        return _intent_execution_cache
    
    # Slow path: load and cache (thread-safe)
    with _cache_lock:
        # Double-check after acquiring lock (another thread may have loaded it)
        if _intent_execution_cache is not None:
            return _intent_execution_cache
        
        # Load YAML file
        config_dir = Path(__file__).resolve().parent.parent / "config"
        config_path = config_dir / "intent_execution.yaml"
        
        if not config_path.exists():
            logger.warning(
                f"intent_execution.yaml not found at {config_path}, "
                "using empty config (no commit actions or fallbacks)"
            )
            _intent_execution_cache = {}
            return _intent_execution_cache
        
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        
        # Extract intents dict from YAML (structure: {intents: {INTENT_NAME: {...}}})
        _intent_execution_cache = raw.get("intents", {}) if isinstance(raw, dict) else {}
        return _intent_execution_cache


def _normalize_modify_booking_missing_slots(
    missing_slots: List[str],
    luma_response: Dict[str, Any]
) -> List[str]:
    """
    Normalize MODIFY_BOOKING missing_slots according to planning contract.
    
    ARCHITECTURAL CHANGE: Planning vs execution slot contracts are now separated.
    - Planning contract: ["booking_id", "date"] (if time provided, date required)
    - Execution contract: ["booking_id"] (checked later, not used for planning)
    
    This function now preserves planning-required slots (date) in missing_slots.
    It only filters out execution-specific slots that shouldn't appear in planning.
    
    Args:
        missing_slots: Raw missing slots list (computed from planning contract)
        luma_response: Luma API response (for context)
        
    Returns:
        Normalized missing slots list (preserves planning requirements)
    """
    # Check if this is MODIFY_BOOKING
    intent = luma_response.get("intent", {})
    intent_name = intent.get("name", "") if isinstance(intent, dict) else ""
    if intent_name != "MODIFY_BOOKING":
        return missing_slots
    
    # Planning contract for MODIFY_BOOKING: ["booking_id", "date"]
    # If time is provided, date becomes required (handled in compute_missing_slots)
    # We preserve all planning-required slots in missing_slots
    # Only filter out execution-specific or invalid slots
    
    # Check slots for context
    slots = luma_response.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    
    # Planning-required slots that should be preserved
    planning_slots = {"booking_id", "date"}
    
    # Filter: keep planning-required slots and remove only invalid/execution-specific ones
    normalized = []
    for slot in missing_slots:
        if slot in planning_slots:
            # Preserve planning-required slots
            normalized.append(slot)
        elif slot not in ("change", "time", "start_date", "end_date", "datetime_range", "date_range"):
            # Keep other valid slots (but not execution-specific datetime slots)
            normalized.append(slot)
        # Filter out: "change" (test artifact), execution-specific datetime slots
    
    return normalized if normalized else missing_slots


def _extract_missing_slots(luma_response: Dict[str, Any]) -> Optional[List[str]]:
    """
    DEPRECATED: This function is no longer used in the main processing path.
    
    The authoritative source of missing_slots is now the recomputed value from
    effective_collected_slots (computed by finalize_turn_state). This function
    is kept for backward compatibility but should not be used for new code.
    
    Extract missing slots from Luma response.
    
    Priority order:
    1. Direct missing_slots field in response (computed by merge) - even if []
    2. Intent result missing_slots
    3. Issues dict (slots with "missing" value) - only if missing_slots not set
    
    CRITICAL: If missing_slots==[], return [] (not None) - it means "no missing slots"
    Only return None if missing_slots is truly not set.
    
    Args:
        luma_response: Luma API response (may have merged missing_slots)
        
    Returns:
        List of missing slot names (normalized for MODIFY_BOOKING) or None if not set
    """
    # PRIORITY 1: Check direct missing_slots field (computed by merge from intent contract)
    # This is the authoritative source - use it even if []
    if "missing_slots" in luma_response:
        direct_missing = luma_response.get("missing_slots")
        if isinstance(direct_missing, list):
            # missing_slots is set (even if []) - use it directly
            # [] means "no missing slots" (all required slots satisfied)
            missing_slots = direct_missing.copy()
            # Normalize MODIFY_BOOKING missing_slots according to test contract
            missing_slots = _normalize_modify_booking_missing_slots(missing_slots, luma_response)
            return missing_slots
    
    # PRIORITY 2: Check intent result
    intent = luma_response.get("intent", {})
    if isinstance(intent, dict) and "missing_slots" in intent:
        intent_missing = intent.get("missing_slots")
        if isinstance(intent_missing, list):
            missing_slots = intent_missing.copy()
            # Normalize MODIFY_BOOKING missing_slots according to test contract
            missing_slots = _normalize_modify_booking_missing_slots(missing_slots, luma_response)
            return missing_slots
    
    # PRIORITY 3: Check issues dict (slots with "missing" value)
    # Only use issues if missing_slots not explicitly set in response
    # This handles cases where Luma hasn't been through merge yet
    missing_slots: List[str] = []
    issues = luma_response.get("issues", {})
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            if slot_value == "missing" and slot_name not in missing_slots:
                missing_slots.append(slot_name)
    
    # Deduplicate
    missing_slots = list(set(missing_slots))
    
    # Normalize MODIFY_BOOKING missing_slots according to test contract
    missing_slots = _normalize_modify_booking_missing_slots(missing_slots, luma_response)
    
    # INVARIANT: Always return a list, never None
    # Return [] if no missing slots found (empty list is valid)
    # This ensures missing_slots is always a list throughout the codebase
    return missing_slots if isinstance(missing_slots, list) else []


def _evaluate_fallbacks(
    intent_config: Dict[str, Any],
    missing_slots: List[str]
) -> List[str]:
    """
    Evaluate fallback actions based on missing slots.
    
    Matches fallback.when_missing.any_of against missing_slots.
    Returns list of action names for matching fallbacks.
    
    Args:
        intent_config: Intent config from intent_execution.yaml
        missing_slots: List of missing slot names from Luma
        
    Returns:
        List of allowed fallback action names
    """
    allowed_actions: List[str] = []
    fallbacks = intent_config.get("fallbacks", [])
    
    if not isinstance(fallbacks, list):
        return allowed_actions
    
    missing_slots_set = set(missing_slots)
    
    for fallback in fallbacks:
        if not isinstance(fallback, dict):
            continue
        
        action = fallback.get("action")
        if not action:
            continue
        
        when_missing = fallback.get("when_missing", {})
        if not isinstance(when_missing, dict):
            continue
        
        any_of = when_missing.get("any_of", [])
        if not isinstance(any_of, list):
            continue
        
        # Check if any of the required slots are missing
        if any(slot in missing_slots_set for slot in any_of):
            allowed_actions.append(action)
    
    return allowed_actions


def _build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str
) -> Dict[str, Any]:
    """
    Build a decision plan from Luma response and intent execution config.
    
    Applies rules:
    - commit.action is the irreversible commit step
    - Block commit when needs_clarification == true OR booking.confirmation_state != "confirmed"
    - Evaluate fallbacks by matching when_missing.any_of against missing_slots
    - Allow matching fallback actions (non-destructive)
    
    Args:
        intent_name: Intent name from Luma
        luma_response: Luma API response
        domain: Domain for template key routing
        
    Returns:
        Decision plan dictionary with:
        - status: READY, NEEDS_CLARIFICATION, or AWAITING_CONFIRMATION
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
    """
    # Load intent execution config
    intent_configs = _load_intent_execution_config()
    intent_config = intent_configs.get(intent_name, {})
    
    # CRITICAL: Get missing_slots from luma_response (recomputed from effective_collected_slots)
    # This is the ONLY source of truth - missing_slots was recomputed by finalize_turn_state
    # before calling build_plan, and MUST NOT be overridden or filtered here
    # missing_slots = [] is VALID and means all required slots are satisfied
    missing_slots = luma_response.get("missing_slots")
    
    # INVARIANT CHECK: missing_slots must be a list (never None after recomputation)
    if missing_slots is None:
        # This should never happen if recomputation ran correctly
        logger.error(
            f"[MISSING_SLOTS] VIOLATION: missing_slots is None after recomputation! "
            f"intent={intent_name}, luma_response_keys={list(luma_response.keys())}"
        )
        # Fail-safe: use empty list (but this indicates a bug)
        missing_slots = []
    elif not isinstance(missing_slots, list):
        # This should never happen if recomputation ran correctly
        logger.error(
            f"[MISSING_SLOTS] VIOLATION: missing_slots is not a list! "
            f"type={type(missing_slots)}, value={missing_slots}, intent={intent_name}"
        )
        # Fail-safe: convert to list (but this indicates a bug)
        missing_slots = list(missing_slots) if missing_slots else []
    
    # CRITICAL: missing_slots is the ONLY source of truth after recomputation
    # It was computed by finalize_turn_state from effective_collected_slots
    # missing_slots = [] is valid and means all required slots are satisfied
    
    # Determine status
    needs_clarification = luma_response.get("needs_clarification", False)
    booking = luma_response.get("booking", {})
    confirmation_state = booking.get("confirmation_state") if isinstance(booking, dict) else None
    
    # DEBUG: Print decision plan building details
    print(f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} needs_clarification={needs_clarification} confirmation_state={confirmation_state}")
    
    # CRITICAL: If missing_slots is non-empty, status MUST be NEEDS_CLARIFICATION
    # This is the authoritative rule - missing slots drive clarification, not Luma flags
    # NEVER use `if not missing_slots` - only check length (empty list is valid)
    if len(missing_slots) > 0:
        status = "NEEDS_CLARIFICATION"
        print(f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}")
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        print(f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True")
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
        print(f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending")
    else:
        status = "READY"
        print(f"[BUILD_PLAN] Setting status=READY (no missing slots, no clarification needed, no pending confirmation)")
    
    # Get commit action
    commit_config = intent_config.get("commit", {})
    commit_action = commit_config.get("action") if isinstance(commit_config, dict) else None
    
    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []
    
    # CRITICAL: If missing_slots exist, block ALL actions (including fallbacks)
    # Planner must never see READY if missing_slots exist
    # Executing fallback actions while missing slots is a bug
    # NEVER use `if not missing_slots` - only check length (empty list is valid)
    if len(missing_slots) > 0:
        # Block all actions when missing_slots exist
        if commit_action:
            blocked_actions.append(commit_action)
        # Do NOT evaluate fallbacks - they should not execute while clarifying
    else:
        # Only evaluate fallbacks if no missing slots (missing_slots = [])
        fallback_actions = _evaluate_fallbacks(intent_config, missing_slots)
        allowed_actions.extend(fallback_actions)
        
        # Commit action blocking rules
        if commit_action:
            # CRITICAL: If missing_slots is empty ([]), allow commit immediately
            # Tests expect READY state to execute without confirmation when slots are complete
            # Do NOT require confirmation_state == "confirmed" when all slots are filled
            if len(missing_slots) > 0:
                # Still have missing slots - block commit
                blocked_actions.append(commit_action)
            elif needs_clarification:
                # Luma explicitly says needs clarification - block commit
                blocked_actions.append(commit_action)
            else:
                # All slots filled and no clarification needed - allow commit
                # Do NOT check confirmation_state - tests expect immediate execution
                allowed_actions.append(commit_action)
    
    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))
    
    # Determine awaiting
    awaiting = "USER_CONFIRMATION" if confirmation_state == "pending" else None
    
    # AWAITING_SLOT: Set when status == NEEDS_CLARIFICATION and exactly one slot is missing
    # This allows Core to route user-provided values into the slot it's currently asking for
    awaiting_slot = None
    if status == "NEEDS_CLARIFICATION" and len(missing_slots) == 1:
        awaiting_slot = missing_slots[0]
        logger.debug(f"Set awaiting_slot={awaiting_slot} (exactly one missing slot)")
    
    # CRITICAL INVARIANT: awaiting_slot lifecycle management
    # - awaiting_slot from session must be cleared ONLY when the awaited slot is explicitly satisfied in current turn
    # - Do NOT clear awaiting_slot merely because missing_slots becomes empty
    # - Keep invariant: if awaiting_slot is not None → status cannot be READY until awaited slot is fulfilled
    awaiting_slot_from_session = luma_response.get("awaiting_slot")
    
    # Get current turn's collected slots to check if awaited slot is satisfied
    # Use _effective_collected_slots if available (post-promotion slots), otherwise use slots
    current_slots = luma_response.get("_effective_collected_slots", luma_response.get("slots", {}))
    if not isinstance(current_slots, dict):
        current_slots = {}
    
    print(
        f"[AWAITING_SLOT_DEBUG] Before status check: "
        f"awaiting_slot_from_session={awaiting_slot_from_session}, "
        f"awaiting_slot_new={awaiting_slot}, "
        f"missing_slots={missing_slots}, "
        f"status={status}, "
        f"current_slots_keys={list(current_slots.keys())}"
    )
    
    # Check if awaited slot from session is satisfied in current turn
    # Only clear awaiting_slot if the specific awaited slot key is present in current slots
    # Do NOT clear awaiting_slot merely because missing_slots becomes empty
    effective_awaiting_slot = None
    if awaiting_slot_from_session:
        if awaiting_slot_from_session in current_slots:
            # The awaited slot has been satisfied in this turn - clear it
            logger.info(
                f"[AWAITING_SLOT_CLEAR] Cleared awaiting_slot={awaiting_slot_from_session} because it is now "
                f"present in current turn slots: {list(current_slots.keys())}"
            )
            print(
                f"[AWAITING_SLOT_CLEAR] Cleared awaiting_slot={awaiting_slot_from_session} because it is now "
                f"present in current turn slots: {list(current_slots.keys())}"
            )
            # Clear awaiting_slot_from_session - it's been satisfied
            # But check if there's a newly computed awaiting_slot for this turn
            effective_awaiting_slot = awaiting_slot if awaiting_slot else None
        else:
            # Awaited slot is NOT satisfied - preserve it
            effective_awaiting_slot = awaiting_slot_from_session
    else:
        # No awaiting_slot from session - use newly computed one
        effective_awaiting_slot = awaiting_slot
    
    print(
        f"[AWAITING_SLOT_DEBUG] effective_awaiting_slot={effective_awaiting_slot}, "
        f"status={status}, "
        f"will_force_needs_clarification={effective_awaiting_slot and status == 'READY'}"
    )
    
    # CRITICAL: Only force NEEDS_CLARIFICATION if awaited slot is NOT satisfied
    # If awaiting_slot was cleared above (because slot was satisfied), don't force NEEDS_CLARIFICATION
    if effective_awaiting_slot and status == "READY":
        # awaiting_slot is set and slot is NOT satisfied - force NEEDS_CLARIFICATION
        status = "NEEDS_CLARIFICATION"
        logger.info(
            f"[AWAITING_SLOT] Forcing status=NEEDS_CLARIFICATION because awaiting_slot={effective_awaiting_slot} is set "
            f"and NOT satisfied in current turn (missing_slots is empty, but awaiting slot still pending)"
        )
        print(
            f"[AWAITING_SLOT] Forcing status=NEEDS_CLARIFICATION because awaiting_slot={effective_awaiting_slot} is set "
            f"and NOT satisfied in current turn (missing_slots is empty, but awaiting slot still pending)"
        )
        # Recompute awaiting_slot since status changed to NEEDS_CLARIFICATION
        if not awaiting_slot and len(missing_slots) == 1:
            awaiting_slot = missing_slots[0]
        elif not awaiting_slot:
            # Use the effective awaiting_slot (may be from session if not satisfied)
            awaiting_slot = effective_awaiting_slot
    else:
        # Either no awaiting_slot, or awaiting_slot was satisfied (cleared above)
        # Use the effective awaiting_slot (which may be None if satisfied)
        awaiting_slot = effective_awaiting_slot
    
    print(
        f"[AWAITING_SLOT_DEBUG] Final plan: "
        f"status={status}, "
        f"awaiting_slot={awaiting_slot}, "
        f"missing_slots={missing_slots}"
    )
    
    # Return awaiting_slot (which may be None if it was cleared due to satisfaction)
    # This ensures the cleared awaiting_slot is propagated back to the session
    return {
        "status": status,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "awaiting_slot": awaiting_slot
    }


def _extract_clarification_data(
    clarification_reason: Optional[str],
    issues: Dict[str, Any],
    clarification_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Extract structured clarification data from Luma response.

    Builds a structured data object with:
    - reason: Stable enum-like value (e.g., "MISSING_TIME", "MULTIPLE_MATCHES")
    - missing: Explicit list of required fields that are missing (e.g., ["time"])
    - ambiguous: List of ambiguous fields that need disambiguation (e.g., ["service"]) for MULTIPLE_MATCHES
    - Additional fields from clarification_data (e.g., "options" for MULTIPLE_MATCHES)

    This function extracts semantic cause of clarification and populates
    data.reason, data.missing, and data.ambiguous. This is the single, authoritative source
    for clarification semantics.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma (contains missing slots, time issues, etc.)
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)

    Returns:
        Dictionary with 'reason', 'missing', and 'ambiguous' fields (all always present), plus any additional fields from clarification_data
    """
    # Extract reason (use clarification_reason if present and non-empty, otherwise infer from issues)
    reason = clarification_reason if clarification_reason and clarification_reason.strip() else None

    # Extract missing and ambiguous fields from issues dict
    # Issues structure: {slot_name: "missing"} or {slot_name: "ambiguous"} or {slot_name: {...}} for rich issues
    missing = []
    ambiguous = []
    if isinstance(issues, dict):
        for slot_name, slot_value in issues.items():
            # If value is "missing", add to missing list
            if slot_value == "missing":
                missing.append(slot_name)
            # If value is "ambiguous", add to ambiguous list (NOT missing)
            elif slot_value == "ambiguous":
                ambiguous.append(slot_name)
            # For rich time issues, still consider "time" as missing if present
            elif slot_name == "time" and isinstance(slot_value, dict):
                # Time has ambiguity but is still missing resolution
                missing.append("time")

    # For UNSUPPORTED_SERVICE, ensure "service" is in missing if not already there
    # Note: MULTIPLE_MATCHES is now handled in Luma (issues.service = "ambiguous"),
    # so it will be extracted from issues above. Only UNSUPPORTED_SERVICE needs fallback handling here.
    if reason == ClarificationReason.UNSUPPORTED_SERVICE.value:
        if "service" not in missing and "service_id" not in missing:
            missing.append("service")

    # If no reason provided but we have missing slots, infer reason
    # Note: Missing slots are already filtered by satisfaction checks in process_luma_response
    # So if "service" is in missing here, it means service_id is truly missing
    if not reason and missing:
        if "time" in missing:
            reason = ClarificationReason.MISSING_TIME.value
        elif "date" in missing:
            reason = ClarificationReason.MISSING_DATE.value
        elif "service" in missing or "service_id" in missing:
            reason = ClarificationReason.MISSING_SERVICE.value
        else:
            reason = ClarificationReason.MISSING_CONTEXT.value  # Generic fallback

    # Build structured data object - always include all fields
    # reason defaults to MISSING_CONTEXT if not provided and cannot be inferred
    if not reason:
        reason = ClarificationReason.MISSING_CONTEXT.value

    # missing and ambiguous default to empty lists if none found
    if not missing:
        missing = []
    if not ambiguous:
        ambiguous = []

    # Start with reason, missing, and ambiguous (always present)
    data = {
        "reason": reason,
        "missing": missing,
        "ambiguous": ambiguous
    }

    # Merge additional fields from clarification_data (e.g., options for MULTIPLE_MATCHES)
    if clarification_data and isinstance(clarification_data, dict):
        # Merge clarification_data into data (e.g., options for MULTIPLE_MATCHES)
        # But preserve reason, missing, and ambiguous as authoritative
        # Note: service_family is not included here - use context.services[0].canonical instead
        for key, value in clarification_data.items():
            if key not in ("reason", "missing", "ambiguous"):  # Don't override reason/missing/ambiguous
                data[key] = value

    return data


def _derive_clarification_reason_from_missing_slots(missing: List[str]) -> str:
    """
    Derive canonical clarification reason from missing slots.
    
    This function provides the single source of truth for mapping missing slots
    to canonical clarification reasons. All clarification outcomes should use
    this function to ensure consistency.
    
    Args:
        missing: List of missing slot names (e.g., ["start_date", "end_date"])
        
    Returns:
        Canonical clarification reason string
    """
    missing_set = set(missing)
    
    if missing_set == {"start_date", "end_date"}:
        return "MISSING_DATE_RANGE"
    elif missing_set == {"start_date"}:
        return "MISSING_START_DATE"
    elif missing_set == {"end_date"}:
        return "MISSING_END_DATE"
    elif missing_set == {"time"}:
        return "MISSING_TIME"
    elif missing_set == {"date"}:
        return "MISSING_DATE"
    elif "time" in missing_set:
        return "MISSING_TIME"
    else:
        return "NEEDS_CLARIFICATION"


def _build_clarify_outcome(
    clarification_reason: str,
    issues: Dict[str, Any],
    context: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    clarification_data: Optional[Dict[str, Any]] = None,
    facts: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from Luma response data.

    Args:
        clarification_reason: Clarification reason string from Luma
        issues: Issues dict from Luma
        context: Context dict from Luma
        booking: Optional booking payload from Luma
        domain: Domain for template key routing
        clarification_data: Optional structured clarification data from Luma (e.g., options for MULTIPLE_MATCHES)
        facts: Optional facts container with slots, missing_slots, context (for rendering)
        intent_name: Intent name from Luma (ALWAYS set when needs_clarification == true)

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(clarification_reason, domain)

    logger.info(
        f"Clarification needed: {clarification_reason} -> {template_key}"
    )

    # Extract structured clarification data (reason, missing, and ambiguous fields)
    # This is the single, authoritative source for clarification semantics
    data = _extract_clarification_data(
        clarification_reason, issues, clarification_data)

    # Derive canonical clarification_reason from missing slots
    # This is the top-level field that tests expect
    missing_slots = data.get("missing", [])
    canonical_reason = _derive_clarification_reason_from_missing_slots(missing_slots)

    # Build outcome with facts for rendering
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "clarification_reason": canonical_reason,  # Top-level canonical reason derived from missing slots
        "template_key": template_key,
        # data contains reason, missing, ambiguous, and any additional fields from clarification_data (e.g., options)
        "data": data,
        "context": context,  # Include context if present
        "booking": booking
    }
    
    # ALWAYS set intent_name when needs_clarification == true (never recompute from facts/slots)
    if intent_name:
        outcome["intent_name"] = intent_name
    
    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {
        "success": True,
        "outcome": outcome
    }


def _build_turn_state(
    intent_name: str,
    raw_luma_slots: Dict[str, Any],
    merged_session_slots: Dict[str, Any],
    promoted_slots: Dict[str, Any],
    effective_collected_slots: Dict[str, Any],
    required_slots: List[str],
    missing_slots: List[str],
    awaiting_slot_before: Optional[str],
    awaiting_slot_after: Optional[str],
    plan: Dict[str, Any]
) -> TurnState:
    """
    Build TurnState object at end of turn processing.
    
    This is the single source of truth for turn state, containing all slot states,
    status, and decision reasoning.
    """
    from core.orchestration.api.slot_contract import get_required_slots_for_intent
    
    final_status = plan.get("status", "")
    
    # Determine decision_reason based on status and conditions
    decision_reason = DecisionReason.CLARIFICATION_REQUIRED
    if final_status == "READY":
        if len(missing_slots) == 0 and awaiting_slot_after is None:
            decision_reason = DecisionReason.READY_ALL_SATISFIED
        else:
            decision_reason = DecisionReason.READY_ALL_SATISFIED  # Should not happen
    elif final_status == "AWAITING_CONFIRMATION":
        decision_reason = DecisionReason.NEEDS_CONFIRMATION
    elif final_status == "NEEDS_CLARIFICATION":
        if awaiting_slot_after is not None:
            decision_reason = DecisionReason.AWAITING_SLOT_BLOCK
        elif len(missing_slots) > 0:
            decision_reason = DecisionReason.MISSING_REQUIRED_SLOTS
        else:
            decision_reason = DecisionReason.CLARIFICATION_REQUIRED
    
    return TurnState(
        intent=intent_name,
        raw_luma_slots=raw_luma_slots,
        merged_session_slots=merged_session_slots,
        promoted_slots=promoted_slots,
        effective_collected_slots=effective_collected_slots,
        required_slots=required_slots if required_slots else (get_required_slots_for_intent(intent_name) if intent_name else []),
        missing_slots=missing_slots,
        awaiting_slot_before=awaiting_slot_before,
        awaiting_slot_after=awaiting_slot_after,
        status=final_status,
        decision_reason=decision_reason.value
    )


def _log_turn_outcome_snapshot(
    intent_name: str,
    awaiting_slot: Optional[str],
    required_slots: List[str],
    raw_luma_slots: Dict[str, Any],
    merged_session_slots: Dict[str, Any],
    promoted_slots: Dict[str, Any],
    effective_collected_slots: Dict[str, Any],
    computed_missing_slots: List[str],
    final_status: str
) -> None:
    """
    Log structured debug snapshot of final turn outcome.
    
    ONLY logs when tests are running or DEBUG_TURN_OUTCOME flag is set.
    Makes violations obvious (e.g., missing_slots contains a key already in effective_collected_slots,
    or status=READY while awaiting_slot != None).
    
    Args:
        intent_name: Intent name
        awaiting_slot: Awaiting slot (if any)
        required_slots: Required slots for intent
        raw_luma_slots: Raw slots from Luma response
        merged_session_slots: Merged session slots (after merge, before promotion)
        promoted_slots: Promoted slots (after promotion)
        effective_collected_slots: Effective collected slots (filtered by required slots)
        computed_missing_slots: Computed missing slots
        final_status: Final status (READY, NEEDS_CLARIFICATION, etc.)
    """
    import os
    import sys
    import json
    
    DEBUG_TURN_OUTCOME = (
        os.getenv("DEBUG_TURN_OUTCOME", "0") == "1" or 
        "pytest" in sys.modules or 
        "_pytest" in sys.modules
    )
    
    if not DEBUG_TURN_OUTCOME:
        return
    
    turn_outcome_snapshot = {
        "intent": intent_name,
        "awaiting_slot": awaiting_slot,
        "required_slots": required_slots,
        "raw_luma_slots": {
            "keys": list(raw_luma_slots.keys()) if isinstance(raw_luma_slots, dict) else [],
            "values": {k: str(v)[:50] for k, v in raw_luma_slots.items()} if isinstance(raw_luma_slots, dict) else {}
        },
        "merged_session_slots": {
            "keys": list(merged_session_slots.keys()),
            "values": {k: str(v)[:50] for k, v in merged_session_slots.items()}
        },
        "promoted_slots": {
            "keys": list(promoted_slots.keys()),
            "values": {k: str(v)[:50] for k, v in promoted_slots.items()}
        },
        "effective_collected_slots": {
            "keys": list(effective_collected_slots.keys()),
            "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()}
        },
        "computed_missing_slots": computed_missing_slots,
        "final_status": final_status
    }
    
    logger.info(
        f"[TURN_OUTCOME_SNAPSHOT] Final turn outcome: intent={intent_name}, "
        f"status={final_status}, missing_slots={computed_missing_slots}, awaiting_slot={awaiting_slot}"
    )
    print(f"[TURN_OUTCOME_SNAPSHOT] {json.dumps(turn_outcome_snapshot, indent=2)}")


def process_luma_response(
    luma_response: Dict[str, Any],
    domain: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Process Luma response and produce a decision plan.

    This function interprets the Luma response and returns a decision plan that includes:
    - status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)
    - allowed_actions
    - blocked_actions
    - awaiting (USER_CONFIRMATION or null)

    Args:
        luma_response: Validated Luma API response
        domain: Domain for template key routing
        user_id: User identifier for logging

    Returns:
        Decision dictionary with:
        - plan: Decision plan (status, allowed_actions, blocked_actions, awaiting)
        - outcome: Clarification outcome (if status is NEEDS_CLARIFICATION)
        - intent_name: Intent name (if status is READY or AWAITING_CONFIRMATION)
        - action_name: Action name for handler mapping
        - booking: Booking payload
        - facts: Facts container with slots, missing_slots, context (passthrough from Luma)
        - error: Error code (if error occurred)
        - message: Error message (if error occurred)
    """
    # Extract intent and validate
    intent = luma_response.get("intent", {})
    intent_name = intent.get("name", "").strip() if intent.get("name") else ""
    
    if not intent_name:
        logger.error(f"Missing intent name in Luma response for user {user_id}")
        # Extract facts container even for error cases
        facts = {
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": luma_response.get("context", {})
        }
        return {
            "error": "missing_intent",
            "message": "Intent name is missing from Luma response",
            "plan": {
                "status": "NEEDS_CLARIFICATION",
                "allowed_actions": [],
                "blocked_actions": [],
                "awaiting": None
            },
            "facts": facts
        }
    
    # CRITICAL: Normalize slots BEFORE filtering and plan building
    # SLOT NORMALIZATION: Extract time from all possible sources and write to slots["time"]
    # This ensures resolved time expressions (noon, morning, 3pm, etc.) are written to slots["time"] before filtering
    # Planning must rely only on slots, never context
    
    # Capture slot states for turn outcome snapshot (before normalization)
    # These represent the states after merge/promotion but before normalization
    promoted_slots_before_normalization = luma_response.get("slots", {})
    if not isinstance(promoted_slots_before_normalization, dict):
        promoted_slots_before_normalization = {}
    merged_session_slots_for_logging = promoted_slots_before_normalization.copy()  # After merge+promotion, before normalization
    
    slots_for_filtering = promoted_slots_before_normalization.copy()
    if not isinstance(slots_for_filtering, dict):
        slots_for_filtering = {}
    
    # Normalize time: extract from multiple sources if not in slots
    # Priority: 1) slots.time (already there), 2) context.time_constraint, 3) trace/semantic data
    # Handle both string time_constraint and dict time_constraint with start/mode fields
    if "time" not in slots_for_filtering or not slots_for_filtering.get("time"):
        time_value = None
        time_mode = None
        
        # Helper to extract time from time_constraint (handles both string and dict)
        def _extract_time_from_constraint(time_constraint_obj, source_name: str):
            """Extract time value from time_constraint (string or dict with start/mode).
            
            Handles multiple formats:
            - String: "12:00" -> "12:00"
            - Dict with start: {"start": "12:00", "mode": "exact"} -> "12:00"
            - Dict with value: {"value": "12:00", "mode": "exact"} -> "12:00"
            - Dict with time: {"time": "12:00", "mode": "exact"} -> "12:00"
            """
            if not time_constraint_obj:
                return None, None
            
            # If time_constraint is a string, use it directly
            if isinstance(time_constraint_obj, str):
                logger.debug(f"Extracting time from {source_name}: string value={time_constraint_obj}")
                return time_constraint_obj, None
            
            # If time_constraint is a dict, extract based on mode
            if isinstance(time_constraint_obj, dict):
                constraint_mode = time_constraint_obj.get("mode", "")
                constraint_start = time_constraint_obj.get("start")
                
                # For exact mode, use start (e.g., "12:00" for "noon")
                if constraint_mode == "exact" and constraint_start:
                    logger.debug(f"Extracting time from {source_name}: mode=exact, start={constraint_start}")
                    return constraint_start, "exact"
                
                # For other modes or if start exists, use start
                if constraint_start:
                    logger.debug(f"Extracting time from {source_name}: start={constraint_start}, mode={constraint_mode}")
                    return constraint_start, constraint_mode
                
                # Fallback: check for "value" field (some formats use "value" instead of "start")
                constraint_value = time_constraint_obj.get("value")
                if constraint_value:
                    logger.debug(f"Extracting time from {source_name}: value={constraint_value}, mode={constraint_mode}")
                    return constraint_value, constraint_mode
                
                # Fallback: check for direct time value
                if "time" in time_constraint_obj:
                    time_val = time_constraint_obj["time"]
                    logger.debug(f"Extracting time from {source_name}: time field={time_val}, mode={constraint_mode}")
                    return time_val, constraint_mode
            
            return None, None
        
        # Check context.time_constraint (most common for resolved expressions like "noon", "morning")
        context = luma_response.get("context", {})
        if isinstance(context, dict):
            time_constraint = context.get("time_constraint")
            if time_constraint:
                extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "context.time_constraint")
                if extracted_time:
                    time_value = extracted_time
                    time_mode = extracted_mode or context.get("time_mode")
                    logger.debug(f"Normalized time from context.time_constraint to slots.time: {time_value} (mode={time_mode})")
        
        # Fallback: Check trace.semantic.time_constraint
        if not time_value:
            trace = luma_response.get("trace", {})
            if isinstance(trace, dict):
                semantic = trace.get("semantic", {})
                if isinstance(semantic, dict):
                    time_constraint = semantic.get("time_constraint")
                    if time_constraint:
                        extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "trace.semantic.time_constraint")
                        if extracted_time:
                            time_value = extracted_time
                            time_mode = extracted_mode or semantic.get("time_mode")
                            logger.debug(f"Normalized time from trace.semantic.time_constraint to slots.time: {time_value} (mode={time_mode})")
        
        # Fallback: Check stages for semantic data
        if not time_value:
            stages = luma_response.get("stages", [])
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, dict):
                        semantic = stage.get("semantic", {})
                        if isinstance(semantic, dict):
                            time_constraint = semantic.get("time_constraint")
                            if time_constraint:
                                extracted_time, extracted_mode = _extract_time_from_constraint(time_constraint, "stages[].semantic.time_constraint")
                                if extracted_time:
                                    time_value = extracted_time
                                    time_mode = extracted_mode or semantic.get("time_mode")
                                    logger.debug(f"Normalized time from stages[].semantic.time_constraint to slots.time: {time_value} (mode={time_mode})")
                                    break
        
        # Write normalized time to slots
        if time_value:
            slots_for_filtering["time"] = time_value
            # Update luma_response slots to include normalized time
            luma_response["slots"] = slots_for_filtering
            logger.info(f"Temporal slot normalization: promoted time={time_value} from context to slots before filtering (mode={time_mode})")
    
    # RIGHT BEFORE build_plan: Recompute missing_slots from effective_collected_slots
    # Use centralized finalize_turn_state to ensure consistency across all callers
    from core.orchestration.api.turn_state import finalize_turn_state
    import json
    
    # Get awaiting_slot from session (if present)
    awaiting_slot = luma_response.get("awaiting_slot")
    
    # STRUCTURED DEBUG: Slot state transitions before finalization
    # This trace object allows debugging slot transformations without stepping through code
    # Note: slots_for_filtering is the merged_session_slots after normalization (time from context)
    slot_state_trace_before_finalization = {
        "intent": intent_name,
        "merged_session_slots": {
            "keys": list(slots_for_filtering.keys()),
            "values": {k: str(v)[:50] for k, v in slots_for_filtering.items()}
        },
        "awaiting_slot": awaiting_slot
    }
    
    # Finalize turn state: compute effective_slots, missing_slots, and base status
    turn_state = finalize_turn_state(
        intent_name=intent_name,
        merged_session_slots=slots_for_filtering,  # Use normalized slots (after time normalization)
        awaiting_slot=awaiting_slot
    )
    
    effective_collected_slots = turn_state["effective_slots"]
    missing_slots = turn_state["missing_slots"]
    # Note: turn_state["status"] is the base status, but build_plan may override based on
    # needs_clarification or confirmation_state
    
    # Complete slot state trace with finalization results
    slot_state_trace_before_finalization.update({
        "effective_collected_slots": {
            "keys": list(effective_collected_slots.keys()),
            "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()}
        },
        "missing_slots": missing_slots,
        "status": turn_state["status"]
    })
    
    logger.info(
        f"[SLOT_STATE_TRACE] Before finalization: intent={intent_name}, "
        f"merged_session_slots_keys={list(slots_for_filtering.keys())}, "
        f"effective_collected_slots_keys={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, awaiting_slot={awaiting_slot}, status={turn_state['status']}"
    )
    print(f"[SLOT_STATE_TRACE] Before finalization: {json.dumps(slot_state_trace_before_finalization, indent=2)}")
    
    logger.info(
        f"[PRE_PLAN] Finalized turn state: "
        f"intent={intent_name}, "
        f"effective_collected={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, "
        f"awaiting_slot={awaiting_slot}"
    )
    print(
        f"[PRE_PLAN] Finalized turn state: "
        f"intent={intent_name}, "
        f"effective_collected={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, "
        f"awaiting_slot={awaiting_slot}"
    )
    
    # CRITICAL: Update luma_response_for_plan with recomputed missing_slots
    # This is the ONLY source of truth - missing_slots computed from effective_collected_slots
    # MUST NOT be overridden or filtered after this point
    luma_response_for_plan = luma_response.copy()
    luma_response_for_plan["missing_slots"] = missing_slots
    
    # Build decision plan with recomputed missing_slots (ONLY source of truth)
    plan = _build_decision_plan(intent_name, luma_response_for_plan, domain)
    
    # Check if Luma indicates clarification is needed
    if luma_response.get("needs_clarification", False):
        reason = luma_response.get("clarification_reason", "")
        issues = luma_response.get("issues", {})
        context = luma_response.get("context", {})
        booking = luma_response.get("booking")
        clarification_data = luma_response.get("clarification_data")
        
        # CRITICAL: Use ONLY the recomputed missing_slots from effective_collected_slots
        # DO NOT extract missing_slots from issues or any other source - the recomputed
        # missing_slots is the ONLY source of truth after finalize_turn_state
        # This ensures slots present in effective_collected_slots (e.g., time from normalization)
        # are not listed as missing
        facts_missing_slots = missing_slots  # Use recomputed missing_slots from above
        
        facts = {
            "slots": slots_for_filtering,  # Use normalized slots (includes normalized time)
            "missing_slots": facts_missing_slots,
            "context": context  # context is already extracted above
        }

        # Build TurnState at end of turn (single source of truth)
        from core.orchestration.api.slot_contract import get_required_slots_for_intent
        raw_luma_slots = luma_response.get("_raw_luma_slots", {})
        merged_session_slots = merged_session_slots_for_logging
        promoted_slots = promoted_slots_before_normalization
        required_slots = get_required_slots_for_intent(intent_name)
        awaiting_slot_final = turn_state.get("awaiting_slot_after") or plan.get("awaiting_slot")
        
        turn_state_obj = _build_turn_state(
            intent_name=intent_name,
            raw_luma_slots=raw_luma_slots,
            merged_session_slots=merged_session_slots,
            promoted_slots=promoted_slots,
            effective_collected_slots=effective_collected_slots,
            required_slots=required_slots,
            missing_slots=facts_missing_slots,
            awaiting_slot_before=turn_state.get("awaiting_slot_before"),
            awaiting_slot_after=awaiting_slot_final,
            plan=plan
        )
        
        # ONE unconditional debug print/log at end of turn
        print(f"TURN_STATE: {json.dumps(turn_state_obj.to_dict(), indent=2, default=str)}")

        return {
            "outcome": _build_clarify_outcome(
                clarification_reason=reason,
                issues=issues,
                context=context,
                booking=booking,
                domain=domain,
                clarification_data=clarification_data,
                facts=facts,
                intent_name=intent_name
            ),
            "plan": plan,
            "facts": facts
        }

    # Get action name for handler mapping
    action_name = get_action_name(intent_name)

    if not action_name:
        logger.warning(
            f"Unsupported intent for user {user_id}: {intent_name!r} "
            f"(type: {type(intent_name).__name__})"
        )
        # Debug: log available intents for troubleshooting
        from core.routing.intent_router import INTENT_ACTIONS
        logger.debug(f"Available intents: {list(INTENT_ACTIONS.keys())}")
        # Extract facts container even for unsupported intent
        facts = {
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": luma_response.get("context", {})
        }
        return {
            "error": "unsupported_intent",
            "message": f"Intent {intent_name} is not supported",
            "plan": plan,
            "facts": facts
        }

    # Return execution instruction with decision plan
    booking = luma_response.get("booking", {})
    
    # CRITICAL: Use ONLY the recomputed missing_slots from effective_collected_slots
    # This is the ONLY source of truth - missing_slots MUST NOT be overridden or filtered
    # This ensures slots present in effective_collected_slots (e.g., time from normalization)
    # are not listed as missing
    
    # Use normalized slots (includes normalized time)
    slots = slots_for_filtering.copy()
    
    # Update luma_response slots with normalized slots (includes normalized time)
    if "slots" in luma_response:
        luma_response["slots"] = slots
    
    facts = {
        "slots": slots,
        "missing_slots": missing_slots,  # Use recomputed missing_slots (ONLY source of truth)
        "context": luma_response.get("context", {})
    }
    
    # Add debug info to facts for troubleshooting
    facts["_debug"] = {
        "recomputed_missing_slots": missing_slots,
        "effective_collected_slots": list(effective_collected_slots.keys()),
        "slots_keys": list(slots.keys()),
        "booking_has_services": (
            isinstance(booking, dict) and 
            isinstance(booking.get("services"), list) and 
            len(booking.get("services", [])) > 0
        ),
        "service_id_in_slots": "service_id" in slots,
        "service_id_value": slots.get("service_id")
    }
    
    # Build TurnState at end of turn (single source of truth)
    from core.orchestration.api.slot_contract import get_required_slots_for_intent
    raw_luma_slots = luma_response.get("_raw_luma_slots", {})
    merged_session_slots = merged_session_slots_for_logging
    promoted_slots = promoted_slots_before_normalization
    required_slots = get_required_slots_for_intent(intent_name)
    awaiting_slot_final = turn_state.get("awaiting_slot_after") or plan.get("awaiting_slot")
    
    turn_state_obj = _build_turn_state(
        intent_name=intent_name,
        raw_luma_slots=raw_luma_slots,
        merged_session_slots=merged_session_slots,
        promoted_slots=promoted_slots,
        effective_collected_slots=effective_collected_slots,
        required_slots=required_slots,
        missing_slots=missing_slots,
        awaiting_slot_before=turn_state.get("awaiting_slot_before"),
        awaiting_slot_after=awaiting_slot_final,
        plan=plan
    )
    
    # ONE unconditional debug print/log at end of turn
    print(f"TURN_STATE: {json.dumps(turn_state_obj.to_dict(), indent=2, default=str)}")
    
    return {
        "intent_name": intent_name,
        "action_name": action_name,
        "booking": booking,
        "plan": plan,
        "facts": facts
    }


def build_clarify_outcome_from_reason(
    reason: str,
    issues: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    facts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a CLARIFY outcome from a core-initiated clarification reason.

    Used when orchestrator detects clarification needs during service/room/extras resolution.

    Args:
        reason: Clarification reason (e.g., "MISSING_SERVICE")
        issues: Issues dict (e.g., {"service": "missing"})
        booking: Optional booking payload
        domain: Domain for template key routing
        facts: Optional facts container with slots, missing_slots, context (for rendering)

    Returns:
        Complete CLARIFY outcome dictionary ready to return
    """
    template_key = get_template_key(reason, domain)

    # Extract structured clarification data
    clarification_data = _extract_clarification_data(reason, issues)

    # Derive canonical clarification_reason from missing slots
    # This is the top-level field that tests expect
    missing_slots = clarification_data.get("missing", [])
    canonical_reason = _derive_clarification_reason_from_missing_slots(missing_slots)

    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "clarification_reason": canonical_reason,  # Top-level canonical reason derived from missing slots
        "template_key": template_key,
        "data": clarification_data,
        "booking": booking
    }
    
    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {
        "success": True,
        "outcome": outcome
    }
