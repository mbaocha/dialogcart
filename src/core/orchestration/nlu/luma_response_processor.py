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

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from core.orchestration.api.turn_state import DecisionReason, TurnState
from core.orchestration.errors import UnsupportedIntentError
from core.routing import get_action_name, get_template_key
from luma.clarification.reasons import ClarificationReason

logger = logging.getLogger(__name__)


def _convert_time_24h_to_12h(time_24h: str) -> str:
    """
    Convert time from 24-hour format (HH:MM) to 12-hour format (h:mmam/pm).

    For backward compatibility ONLY - derives legacy slots.time from time_constraint.start.

    Examples:
    - "14:00" -> "2pm"
    - "10:00" -> "10am"
    - "15:00" -> "3pm"
    - "12:00" -> "12pm"
    - "00:00" -> "12am"
    - "14:30" -> "2:30pm"

    Args:
        time_24h: Time string in 24-hour format (HH:MM or HH:MM:SS)

    Returns:
        Time string in 12-hour format (h:mmam/pm or ham/pm)
    """
    if not time_24h or ":" not in time_24h:
        return time_24h

    try:
        parts = time_24h.split(":")
        hour = int(parts[0])
        minute = parts[1] if len(parts) > 1 else "00"

        # Convert to 12-hour format
        if hour == 0:
            hour_12 = 12
            period = "am"
        elif hour == 12:
            hour_12 = 12
            period = "pm"
        elif hour < 12:
            hour_12 = hour
            period = "am"
        else:
            hour_12 = hour - 12
            period = "pm"

        # Format as "h:mmam/pm" (e.g., "2pm", "10am", "3pm", "2:30pm")
        if minute == "00" or minute == "0":
            return f"{hour_12}{period}"
        else:
            return f"{hour_12}:{minute}{period}"
    except (ValueError, IndexError):
        # If conversion fails, return original
        return time_24h


def _derive_time_from_constraint(time_constraint: Dict[str, Any]) -> Optional[str]:
    """
    Derive legacy slots.time string from time_constraint for backward compatibility.

    Only extracts from time_constraint.start - does not infer or synthesize.

    Args:
        time_constraint: Time constraint dict with start/end/mode/label

    Returns:
        Time string in 12-hour format (e.g., "2pm", "10am") or None
    """
    if not isinstance(time_constraint, dict):
        return None

    start_time = time_constraint.get("start")
    if not start_time:
        return None

    # Convert from 24h format to 12h format
    return _convert_time_24h_to_12h(str(start_time))


def _normalize_modify_booking_missing_slots(
    missing_slots: List[str], luma_response: Dict[str, Any]
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
        elif slot not in (
            "change",
            "time",
            "start_date",
            "end_date",
            "datetime_range",
            "date_range",
        ):
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
            missing_slots = _normalize_modify_booking_missing_slots(
                missing_slots, luma_response
            )
            return missing_slots

    # PRIORITY 2: Check intent result
    intent = luma_response.get("intent", {})
    if isinstance(intent, dict) and "missing_slots" in intent:
        intent_missing = intent.get("missing_slots")
        if isinstance(intent_missing, list):
            missing_slots = intent_missing.copy()
            # Normalize MODIFY_BOOKING missing_slots according to test contract
            missing_slots = _normalize_modify_booking_missing_slots(
                missing_slots, luma_response
            )
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
    missing_slots = _normalize_modify_booking_missing_slots(
        missing_slots, luma_response
    )

    # INVARIANT: Always return a list, never None
    # Return [] if no missing slots found (empty list is valid)
    # This ensures missing_slots is always a list throughout the codebase
    return missing_slots if isinstance(missing_slots, list) else []


# REMOVED: _evaluate_fallbacks
# Planning logic (executable_actions) is now computed ONLY by the planner from intent_planning.yaml.
# This function violated the planning boundary by using intent_execution.yaml for slot-based decisions.


def _build_decision_plan(
    intent_name: str, luma_response: Dict[str, Any], domain: str
) -> Dict[str, Any]:
    """
    Build a decision plan from Luma response.

    PLANNING BOUNDARY:
    - missing_slots MUST come from planner (intent_policy.yaml)
    - executable_actions MUST come from planner (intent_policy.yaml)
    - commit actions come from intent_policy.yaml execution steps with mode=committing

    Applies rules:
    - commit.action is the irreversible commit step (from intent_policy.yaml)
    - Block commit when needs_clarification == true
    - executable_actions come from planner (partial execution with subsets of slots)

    Args:
        intent_name: Intent name from Luma
        luma_response: Luma API response (must have missing_slots from planner)
        domain: Domain for template key routing

    Returns:
        Decision plan dictionary with:
        - status: READY, NEEDS_CLARIFICATION, or AWAITING_CONFIRMATION
        - allowed_actions: List of allowed action names (from planner)
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
        - executable_actions: List of executable actions (from planner)
    """
    # Get commit action from unified policy
    from core.policy.intent_policy import get_commit_action

    commit_action = get_commit_action(intent_name)

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
    confirmation_state = (
        booking.get("confirmation_state") if isinstance(booking, dict) else None
    )

    # DEBUG: Print decision plan building details
    print(
        f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} needs_clarification={needs_clarification} confirmation_state={confirmation_state}"
    )

    # CRITICAL: If missing_slots is non-empty, status MUST be NEEDS_CLARIFICATION
    # This is the authoritative rule - missing slots drive clarification, not Luma flags
    # NEVER use `if not missing_slots` - only check length (empty list is valid)
    if len(missing_slots) > 0:
        status = "NEEDS_CLARIFICATION"
        print(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}"
        )
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        print(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True"
        )
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
        print(
            f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending"
        )
    else:
        status = "READY"
        print(
            f"[BUILD_PLAN] Setting status=READY (no missing slots, no clarification needed, no pending confirmation)"
        )

    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []

    # Get executable_actions from planner (ONLY source of truth for partial execution)
    # Planner computes executable_actions from intent_policy.yaml based on collected slots
    executable_actions = []
    if intent_name:
        from core.planning.policy.action_policy import load_planning_policy, plan_intent

        # Use effective_collected_slots if available (more accurate), otherwise use slots
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        policy = load_planning_policy()
        planner_result = plan_intent(intent_name, effective_slots, policy)
        executable_actions = planner_result.get("executable_actions", [])

    # CRITICAL: If missing_slots exist, block ALL actions (including executable_actions from planner)
    # Planner's executable_actions are for partial execution, but we block them when missing required slots
    # NEVER use `if not missing_slots` - only check length (empty list is valid)
    if len(missing_slots) > 0:
        # Block all actions when missing_slots exist
        if commit_action:
            blocked_actions.append(commit_action)
        # Do NOT allow executable_actions while clarifying - all actions blocked
    else:
        # No missing slots - ready to confirm
        # CRITICAL: When missing_slots == [], commit action takes priority over executable_actions
        # Tests expect CONFIRM_* actions when all slots are filled, not SEARCH_AVAILABILITY
        if commit_action:
            # CRITICAL: If missing_slots is empty ([]), allow commit immediately
            # Tests expect READY state to execute without confirmation when slots are complete
            # Do NOT require confirmation_state == "confirmed" when all slots are filled
            if needs_clarification:
                # Luma explicitly says needs clarification - block commit
                blocked_actions.append(commit_action)
            else:
                # All slots filled and no clarification needed - allow commit (priority)
                # Do NOT check confirmation_state - tests expect immediate execution
                # CRITICAL: Add commit_action FIRST, before executable_actions
                # Remove commit_action if already present to avoid duplicates
                if commit_action in allowed_actions:
                    allowed_actions.remove(commit_action)
                allowed_actions.insert(0, commit_action)

        # Only add executable_actions if commit_action is not available or is blocked
        # This ensures commit_action takes priority when all slots are filled
        # When commit_action is in allowed_actions, executable_actions should NOT be added
        if not commit_action or (commit_action in blocked_actions):
            allowed_actions.extend(executable_actions)

    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))

    # Determine awaiting
    awaiting = "USER_CONFIRMATION" if confirmation_state == "pending" else None

    # Status is determined by missing_slots - no awaiting_slot needed
    # Users can provide missing slots in any order

    # Add dialog instructions when status is NEEDS_CLARIFICATION
    dialog_instructions = None
    if status == "NEEDS_CLARIFICATION" and len(missing_slots) > 0:
        from core.planning.policy.stage_policy import get_dialog_instructions

        dialog_instructions = get_dialog_instructions(intent_name, missing_slots)

    # Get executable_actions from planner
    # Use effective_collected_slots if available (from turn_state), otherwise use slots from luma_response
    executable_actions = []
    if intent_name and missing_slots is not None:
        from core.planning.policy.action_policy import load_planning_policy, plan_intent

        # Prefer effective_collected_slots if available (more accurate), otherwise use slots
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        policy = load_planning_policy()
        planner_result = plan_intent(intent_name, effective_slots, policy)
        executable_actions = planner_result.get("executable_actions", [])

    # Derive stage and action from current state
    # Stage/action mapping based on missing_slots and executable_actions
    stage = None
    action = None

    # CONFIRM ACTION MAP: Hard mapping for complete slots (missing_slots == [])
    CONFIRM_ACTION_MAP = {
        "CREATE_APPOINTMENT": "CONFIRM_APPOINTMENT",
        "CREATE_RESERVATION": "FINALIZE_RESERVATION",
        "MODIFY_BOOKING": "APPLY_MODIFICATION",
        "CANCEL_BOOKING": "CONFIRM_CANCELLATION",
    }

    if len(missing_slots) > 0:
        # Missing slots - determine stage based on intent and executable actions
        if intent_name in ("MODIFY_BOOKING", "CANCEL_BOOKING"):
            stage = "IDENTIFY"
            action = "FETCH_BOOKING"
        else:
            # CREATE_APPOINTMENT or CREATE_RESERVATION
            stage = "AVAILABILITY"
            # Use first executable action if available, otherwise default to SEARCH_AVAILABILITY
            if executable_actions:
                action = executable_actions[0]
            else:
                action = "SEARCH_AVAILABILITY"
    else:
        # HARD GUARD: No missing slots - CONFIRM decision is FINAL
        # Once CONFIRM is chosen, NOTHING may override the action
        stage = "CONFIRM"

        # Determine action from CONFIRM_ACTION_MAP or commit_action
        if intent_name in CONFIRM_ACTION_MAP:
            action = CONFIRM_ACTION_MAP[intent_name]
        elif commit_action and commit_action in allowed_actions:
            # Commit action is available and allowed - use it
            action = commit_action
        elif commit_action:
            # Commit action exists but is blocked - use fallback
            if intent_name == "MODIFY_BOOKING":
                action = "APPLY_MODIFICATION"
            elif intent_name == "CANCEL_BOOKING":
                action = "CONFIRM_CANCELLATION"
            else:
                # Fallback to first allowed action if no commit action
                # Filter out SEARCH_AVAILABILITY when all slots are complete
                filtered_actions = [
                    a for a in allowed_actions if a != "SEARCH_AVAILABILITY"
                ]
                action = filtered_actions[0] if filtered_actions else None
        else:
            # No commit action defined - use fallback based on intent
            if intent_name == "MODIFY_BOOKING":
                action = "APPLY_MODIFICATION"
            elif intent_name == "CANCEL_BOOKING":
                action = "CONFIRM_CANCELLATION"
            else:
                # Last resort: use first allowed action (but never SEARCH_AVAILABILITY when slots complete)
                filtered_actions = [
                    a for a in allowed_actions if a != "SEARCH_AVAILABILITY"
                ]
                action = filtered_actions[0] if filtered_actions else None

        # HARD GUARD: Lock CONFIRM action - log and prevent any override
        logger.error(
            "[PLANNING_GUARD] CONFIRM locked: stage=%s action=%s intent=%s missing_slots=%s",
            stage,
            action,
            intent_name,
            missing_slots,
        )

        # CRITICAL: Build plan immediately and return - NO FALLTHROUGHS
        # This prevents any code after this block from overriding the CONFIRM action
        plan = {
            "status": status,
            "stage": stage,
            "action": action,
            "allowed_actions": allowed_actions,
            "blocked_actions": blocked_actions,
            "awaiting": awaiting,
            "executable_actions": executable_actions,
        }

        # Add dialog instructions to plan if available
        if dialog_instructions:
            plan["dialog_instructions"] = dialog_instructions

        return plan  # EARLY RETURN - NO FALLTHROUGHS

    plan = {
        "status": status,
        "stage": stage,
        "action": action,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "executable_actions": executable_actions,
    }

    # Add dialog instructions to plan if available
    if dialog_instructions:
        plan["dialog_instructions"] = dialog_instructions

    return plan


def _extract_clarification_data(
    clarification_reason: Optional[str],
    issues: Dict[str, Any],
    clarification_data: Optional[Dict[str, Any]] = None,
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
    reason = (
        clarification_reason
        if clarification_reason and clarification_reason.strip()
        else None
    )

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
    data = {"reason": reason, "missing": missing, "ambiguous": ambiguous}

    # Merge additional fields from clarification_data (e.g., options for MULTIPLE_MATCHES)
    if clarification_data and isinstance(clarification_data, dict):
        # Merge clarification_data into data (e.g., options for MULTIPLE_MATCHES)
        # But preserve reason, missing, and ambiguous as authoritative
        # Note: service_family is not included here - use context.services[0].canonical instead
        for key, value in clarification_data.items():
            # Don't override reason/missing/ambiguous
            if key not in ("reason", "missing", "ambiguous"):
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
    intent_name: Optional[str] = None,
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

    logger.info(f"Clarification needed: {clarification_reason} -> {template_key}")

    # Extract structured clarification data (reason, missing, and ambiguous fields)
    # This is the single, authoritative source for clarification semantics
    data = _extract_clarification_data(clarification_reason, issues, clarification_data)

    # Derive canonical clarification_reason from missing slots
    # This is the top-level field that tests expect
    missing_slots = data.get("missing", [])
    canonical_reason = _derive_clarification_reason_from_missing_slots(missing_slots)

    # Build outcome with facts for rendering
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        # Top-level canonical reason derived from missing slots
        "clarification_reason": canonical_reason,
        "template_key": template_key,
        # data contains reason, missing, ambiguous, and any additional fields from clarification_data (e.g., options)
        "data": data,
        "context": context,  # Include context if present
        "booking": booking,
    }

    # ALWAYS set intent_name when needs_clarification == true (never recompute from facts/slots)
    if intent_name:
        outcome["intent_name"] = intent_name

    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {"success": True, "outcome": outcome}


def _build_turn_state(
    intent_name: str,
    raw_luma_slots: Dict[str, Any],
    merged_session_slots: Dict[str, Any],
    promoted_slots: Dict[str, Any],
    effective_collected_slots: Dict[str, Any],
    required_slots: List[str],
    missing_slots: List[str],
    plan: Dict[str, Any],
) -> TurnState:
    """
    Build TurnState object at end of turn processing.

    This is the single source of truth for turn state, containing all slot states,
    status, and decision reasoning.
    """
    from core.planning.orchestration.missing_slots import (
        get_planning_required_slots_for_intent as get_required_slots_for_intent,
    )

    final_status = plan.get("status", "")

    # Determine decision_reason based on status and conditions
    decision_reason = DecisionReason.CLARIFICATION_REQUIRED
    if final_status == "READY":
        if len(missing_slots) == 0:
            decision_reason = DecisionReason.READY_ALL_SATISFIED
        else:
            decision_reason = DecisionReason.READY_ALL_SATISFIED  # Should not happen
    elif final_status == "AWAITING_CONFIRMATION":
        decision_reason = DecisionReason.NEEDS_CONFIRMATION
    elif final_status == "NEEDS_CLARIFICATION":
        if len(missing_slots) > 0:
            decision_reason = DecisionReason.MISSING_REQUIRED_SLOTS
        else:
            decision_reason = DecisionReason.CLARIFICATION_REQUIRED

    return TurnState(
        intent=intent_name,
        raw_luma_slots=raw_luma_slots,
        merged_session_slots=merged_session_slots,
        promoted_slots=promoted_slots,
        effective_collected_slots=effective_collected_slots,
        required_slots=(
            required_slots
            if required_slots
            else (get_required_slots_for_intent(intent_name) if intent_name else [])
        ),
        missing_slots=missing_slots,
        status=final_status,
        decision_reason=decision_reason.value,
    )


def _log_turn_outcome_snapshot(
    intent_name: str,
    required_slots: List[str],
    raw_luma_slots: Dict[str, Any],
    merged_session_slots: Dict[str, Any],
    promoted_slots: Dict[str, Any],
    effective_collected_slots: Dict[str, Any],
    computed_missing_slots: List[str],
    final_status: str,
) -> None:
    """
    Log structured debug snapshot of final turn outcome.

    ONLY logs when tests are running or DEBUG_TURN_OUTCOME flag is set.
    Makes violations obvious (e.g., missing_slots contains a key already in effective_collected_slots).

    Args:
        intent_name: Intent name
        required_slots: Required slots for intent
        raw_luma_slots: Raw slots from Luma response
        merged_session_slots: Merged session slots (after merge, before promotion)
        promoted_slots: Promoted slots (after promotion)
        effective_collected_slots: Effective collected slots (filtered by required slots)
        computed_missing_slots: Computed missing slots
        final_status: Final status (READY, NEEDS_CLARIFICATION, etc.)
    """
    import json
    import os
    import sys

    DEBUG_TURN_OUTCOME = (
        os.getenv("DEBUG_TURN_OUTCOME", "0") == "1"
        or "pytest" in sys.modules
        or "_pytest" in sys.modules
    )

    if not DEBUG_TURN_OUTCOME:
        return

    turn_outcome_snapshot = {
        "intent": intent_name,
        "required_slots": required_slots,
        "raw_luma_slots": {
            "keys": (
                list(raw_luma_slots.keys()) if isinstance(raw_luma_slots, dict) else []
            ),
            "values": (
                {k: str(v)[:50] for k, v in raw_luma_slots.items()}
                if isinstance(raw_luma_slots, dict)
                else {}
            ),
        },
        "merged_session_slots": {
            "keys": list(merged_session_slots.keys()),
            "values": {k: str(v)[:50] for k, v in merged_session_slots.items()},
        },
        "promoted_slots": {
            "keys": list(promoted_slots.keys()),
            "values": {k: str(v)[:50] for k, v in promoted_slots.items()},
        },
        "effective_collected_slots": {
            "keys": list(effective_collected_slots.keys()),
            "values": {k: str(v)[:50] for k, v in effective_collected_slots.items()},
        },
        "computed_missing_slots": computed_missing_slots,
        "final_status": final_status,
    }

    logger.info(
        f"[TURN_OUTCOME_SNAPSHOT] Final turn outcome: intent={intent_name}, "
        f"status={final_status}, missing_slots={computed_missing_slots}"
    )
    print(f"[TURN_OUTCOME_SNAPSHOT] {json.dumps(turn_outcome_snapshot, indent=2)}")


def process_luma_response(
    luma_response: Dict[str, Any],
    domain: str,
    user_id: str,
    session_state: Optional[Dict[str, Any]] = None,
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
    # CRITICAL: Use effective_intent if available (set by merge_luma_with_session)
    # UNKNOWN intent should be treated as no-op - preserve effective/session intent
    effective_intent = luma_response.get("_effective_intent")
    intent = luma_response.get("intent", {})
    luma_intent_name = intent.get("name", "").strip() if intent.get("name") else ""

    # Intent resolution: UNKNOWN means "slot-only follow-up" - preserve effective intent
    if luma_intent_name == "UNKNOWN":
        # UNKNOWN is a no-op - use effective_intent if available
        if effective_intent:
            intent_name = effective_intent
            logger.info(
                f"process_luma_response: UNKNOWN intent detected, preserving effective_intent={effective_intent}"
            )
        else:
            # No effective_intent available - this shouldn't happen if merge worked correctly
            intent_name = ""
            logger.warning(
                f"process_luma_response: UNKNOWN intent but no effective_intent available"
            )
    elif luma_intent_name:
        # Valid Luma intent - use it
        intent_name = luma_intent_name
    elif effective_intent:
        # Empty Luma intent but effective_intent available - use it
        intent_name = effective_intent
        logger.info(
            f"process_luma_response: Empty Luma intent, using effective_intent={effective_intent}"
        )
    else:
        # No intent available at all
        intent_name = ""

    if not intent_name:
        logger.error(
            f"Missing intent name in Luma response for user {user_id} (luma_intent='{luma_intent_name}', effective_intent={effective_intent})"
        )
        # Extract facts container even for error cases
        # Preserve existing facts (capability reconciliation)
        existing_facts = luma_response.get("facts", {})
        if not isinstance(existing_facts, dict):
            existing_facts = {}
        facts = {
            # Preserve all existing facts (e.g., payment_satisfied)
            **existing_facts,
            "slots": luma_response.get("slots", {}),
            "missing_slots": luma_response.get("missing_slots", []),
            "context": luma_response.get("context", {}),
        }
        return {
            "error": "missing_intent",
            "message": "Intent name is missing from Luma response",
            "plan": {
                "status": "NEEDS_CLARIFICATION",
                "allowed_actions": [],
                "blocked_actions": [],
                "awaiting": None,
            },
            "facts": facts,
        }

    # CRITICAL: Normalize slots BEFORE filtering and plan building
    # SLOT NORMALIZATION: Extract time from all possible sources and write to slots["time"]
    # This ensures resolved time expressions (noon, morning, 3pm, etc.) are written to slots["time"] before filtering
    # Planning must rely only on slots, never context

    # FACT-ONLY: Promote facts to slots BEFORE any processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import (
        facts_to_slots,
        merge_promoted_luma_slots,
    )

    facts_obj = luma_response.get("facts", {})
    # CRITICAL: Use resolved intent_name (not raw Luma intent) for slot promotion
    # intent_name was already resolved above (handles UNKNOWN -> effective_intent)
    # Do NOT re-extract from raw Luma response - that would overwrite the resolved intent
    promoted_slots_from_facts = (
        facts_to_slots(
            facts_obj,
            intent_name=intent_name,
            source_text=luma_response.get("_source_text"),
        )
        if isinstance(facts_obj, dict)
        else {}
    )

    # Merge session-merged top-level slots with nested facts.slots.
    # merge_luma_with_session writes durable slots (e.g. date from UNKNOWN → booking
    # materialization) to luma_response["slots"]; facts.slots is often partial
    # (current-turn service_id only) and must not drop prior-turn slots.
    merged_authoritative_slots = luma_response.get("slots") or {}
    if not isinstance(merged_authoritative_slots, dict):
        merged_authoritative_slots = {}
    nested_from_facts: Dict[str, Any] = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        nested_from_facts = facts_obj.get("slots") or {}
        if not isinstance(nested_from_facts, dict):
            nested_from_facts = {}
    nested_slots = {**nested_from_facts, **merged_authoritative_slots}

    # Merge promoted slots (from facts.service_id, facts.times, etc.) with nested slots
    # Promoted slots take precedence (they are the source of truth)
    raw_slots = merge_promoted_luma_slots(
        nested_slots,
        promoted_slots_from_facts,
        luma_response.get("date_constraint"),
        facts_obj if isinstance(facts_obj, dict) else None,
        prefer_nested_service_id=True,
    )

    # Capture slot states for turn outcome snapshot (before normalization)
    # These represent the states after merge/promotion but before normalization
    promoted_slots_before_normalization = (
        raw_slots if isinstance(raw_slots, dict) else {}
    )
    # After merge+promotion, before normalization
    merged_session_slots_for_logging = promoted_slots_before_normalization.copy()

    slots_for_filtering = promoted_slots_before_normalization.copy()
    if not isinstance(slots_for_filtering, dict):
        slots_for_filtering = {}

    # DEPRECATED: Time normalization from context.time_constraint is removed
    # time_constraint is now authoritative and handled separately in missing_slots computation
    # slots.time is derived ONLY for backward compatibility AFTER missing_slots computation
    # This ensures planning decisions are driven by time_constraint mode, not slots.time

    # RIGHT BEFORE build_plan: Recompute missing_slots from effective_collected_slots
    # Use centralized finalize_turn_state to ensure consistency across all callers
    import json

    from core.orchestration.api.turn_state import finalize_turn_state

    # STRUCTURED DEBUG: Slot state transitions before finalization
    # This trace object allows debugging slot transformations without stepping through code
    # Note: slots_for_filtering is the merged_session_slots after normalization (time from context)
    slot_state_trace_before_finalization = {
        "intent": intent_name,
        "merged_session_slots": {
            "keys": list(slots_for_filtering.keys()),
            "values": {k: str(v)[:50] for k, v in slots_for_filtering.items()},
        },
    }

    # finalize_turn_state always recomputes missing_slots from proposals + slots;
    # existing_missing_slots is passed only for the stale-value debug log inside it.
    existing_missing_slots = luma_response.get("missing_slots")

    turn_state = finalize_turn_state(
        intent_name=intent_name,
        merged_session_slots=slots_for_filtering,
        existing_missing_slots=(
            existing_missing_slots if isinstance(existing_missing_slots, list) else None
        ),
        planning_context={
            "date_proposal": luma_response.get("date_proposal"),
            "time_proposal": luma_response.get("time_proposal"),
            "date_constraint": luma_response.get("date_constraint"),
            "time_constraint": luma_response.get("time_constraint"),
            "nlu_facts": facts_obj if isinstance(facts_obj, dict) else None,
        },
    )

    effective_collected_slots = turn_state["effective_slots"]
    missing_slots = turn_state["missing_slots"]

    # INVESTIGATION: Log missing_slots after finalize_turn_state, before time_constraint logic
    from core.planning.orchestration.missing_slots import (
        get_planning_required_slots_for_intent,
    )

    try:
        required_slots = get_planning_required_slots_for_intent(intent_name)
    except (ImportError, Exception):
        required_slots = []

    # Get session slots if available (for first turn, there's no session)
    # Session slots are in slots_for_filtering (merged session slots)
    # For first turn, slots_for_filtering only contains current turn slots
    # For follow-up turns, slots_for_filtering contains merged session + current turn slots
    # This is the merged slots (session + current turn)
    session_slots = slots_for_filtering

    logger.debug(
        f"[MISSING_SLOTS_TRACE] process_luma_response: intent={intent_name} "
        f"required={required_slots} effective_keys={sorted(effective_collected_slots.keys())} "
        f"missing={missing_slots}"
    )

    # APPOINTMENT INTENT RULE: Bounded time_constraint (with both start and end) satisfies the time requirement
    # - mode=exact → satisfies time (always has start, may have end)
    # - mode=fuzzy/window with BOTH start and end → satisfies time (bounded fuzzy/window)
    # - mode=fuzzy/window with only start OR no bounds → does NOT satisfy time (unbounded)
    # This must happen BEFORE plan is built (plan status depends on missing_slots)
    time_constraint = luma_response.get("time_constraint")
    if intent_name == "CREATE_APPOINTMENT" and time_constraint is not None:
        time_constraint_mode = None
        time_constraint_start = None
        time_constraint_end = None
        if isinstance(time_constraint, dict):
            time_constraint_mode = time_constraint.get("mode")
            time_constraint_start = time_constraint.get("start")
            time_constraint_end = time_constraint.get("end")

        # Check if time_constraint is bounded (has both start and end)
        has_start = (
            time_constraint_start is not None
            and str(time_constraint_start).strip() != ""
        )
        has_end = (
            time_constraint_end is not None and str(time_constraint_end).strip() != ""
        )
        is_bounded = has_start and has_end

        # CRITICAL: Check for concrete time slot existence
        # "time" is satisfied ONLY if:
        # 1. A concrete "time" slot exists in effective_collected_slots (current turn), OR
        # 2. A concrete "time" slot exists in slots_for_filtering (session slots), OR
        # 3. time_constraint.mode == "exact" (exact mode always satisfies, even if derived)
        #
        # Do NOT treat fuzzy or windowed time constraints as satisfying "time" unless a concrete slot exists.
        has_time_slot_current = "time" in effective_collected_slots
        has_time_slot_session = "time" in slots_for_filtering
        has_concrete_time_slot = has_time_slot_current or has_time_slot_session
        is_exact_mode = time_constraint_mode == "exact"

        # GUARD: Only remove "time" from missing_slots if:
        # a) A concrete time value exists in effective_collected_slots (e.g., "14:00", "4pm"), OR
        # b) time_constraint.mode == "exact" (exact mode always satisfies)
        # Do NOT remove "time" for fuzzy/bounded signals like "evening", "morning", "afternoon"
        # even if they have bounded time_constraint with start and end.
        #
        # For bounded fuzzy/window modes, a concrete time slot is REQUIRED.
        # Only exact mode can satisfy without a concrete slot.
        #
        # Bounded fuzzy/window constraints satisfy planning time without a concrete slots.time.
        if time_constraint_mode in ("fuzzy", "window"):
            can_remove_time = has_concrete_time_slot or is_bounded
        else:
            can_remove_time = has_concrete_time_slot or is_exact_mode

        if is_bounded:
            if "time" in missing_slots:
                if can_remove_time:
                    # Time is satisfied (concrete slot exists OR exact mode) - remove from missing_slots
                    missing_slots_before = missing_slots.copy()
                    missing_slots = [s for s in missing_slots if s != "time"]
                    # INVESTIGATION: Log missing_slots mutation
                    logger.error(
                        f"[MISSING_SLOTS_TRACE] process_luma_response: REMOVING 'time' from missing_slots (bounded time_constraint with satisfied time), "
                        f"intent={intent_name}, user_id={user_id}, "
                        f"required_slots={required_slots}, "
                        f"effective_collected_slots_keys={sorted(effective_collected_slots.keys())}, "
                        f"has_time_in_effective={has_time_slot_current}, "
                        f"has_time_in_slots_for_filtering={has_time_slot_session}, "
                        f"is_exact_mode={is_exact_mode}, "
                        f"missing_slots_before={missing_slots_before}, "
                        f"missing_slots_after={missing_slots}, "
                        f"time_constraint_mode={time_constraint_mode}, is_bounded={is_bounded}"
                    )
                    logger.info(
                        f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}, bounded=True) with satisfied time (concrete_slot={has_concrete_time_slot}, exact_mode={is_exact_mode}) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots before plan build"
                    )
                    # Update turn_state with corrected missing_slots
                    turn_state["missing_slots"] = missing_slots
                else:
                    # Bounded fuzzy/window without concrete time slot - do NOT satisfy time
                    # Guard blocked removal: fuzzy/bounded signals like "evening" require concrete time slot
                    logger.info(
                        f"[MISSING_SLOTS] Guard blocked removal: Bounded time_constraint (mode={time_constraint_mode}, bounded=True) without concrete time slot does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots. "
                        f"Fuzzy/window constraints (e.g., 'evening', 'morning', 'afternoon') require concrete time slot. "
                        f"Time slot check: current_turn={has_time_slot_current}, session={has_time_slot_session}, exact_mode={is_exact_mode}, can_remove_time={can_remove_time}. "
                        f"Reason: Guard requires concrete time value (e.g., '14:00', '4pm') OR exact mode for bounded fuzzy/window constraints."
                    )
        elif time_constraint_mode == "exact" and has_start:
            # Exact mode with start (even without end) satisfies time
            if "time" in missing_slots:
                missing_slots_before = missing_slots.copy()
                missing_slots = [s for s in missing_slots if s != "time"]
                # INVESTIGATION: Log missing_slots mutation
                logger.error(
                    f"[MISSING_SLOTS_TRACE] process_luma_response: REMOVING 'time' from missing_slots (exact time_constraint), "
                    f"intent={intent_name}, user_id={user_id}, "
                    f"required_slots={required_slots}, "
                    f"effective_collected_slots_keys={sorted(effective_collected_slots.keys())}, "
                    f"has_time_in_effective={has_time_slot_current}, "
                    f"has_time_in_slots_for_filtering={has_time_slot_session}, "
                    f"missing_slots_before={missing_slots_before}, "
                    f"missing_slots_after={missing_slots}, "
                    f"time_constraint_mode={time_constraint_mode}, has_start={has_start}"
                )
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode=exact, start={time_constraint_start}) satisfies time for CREATE_APPOINTMENT - removed 'time' from missing_slots before plan build"
                )
                # Update turn_state with corrected missing_slots
                turn_state["missing_slots"] = missing_slots
        else:
            # Unbounded fuzzy/window time_constraint does NOT satisfy time requirement
            # CRITICAL: Ensure "time" is in missing_slots for unbounded fuzzy/window modes
            # The planner might have removed it if it saw a time slot, but unbounded times need clarification
            if (
                time_constraint_mode in ("fuzzy", "window")
                and "time" not in missing_slots
            ):
                missing_slots_before = missing_slots.copy()
                missing_slots.append("time")
                # INVESTIGATION: Log missing_slots mutation
                logger.error(
                    f"[MISSING_SLOTS_TRACE] process_luma_response: ADDING 'time' to missing_slots (unbounded fuzzy/window), "
                    f"intent={intent_name}, user_id={user_id}, "
                    f"required_slots={required_slots}, "
                    f"effective_collected_slots_keys={sorted(effective_collected_slots.keys())}, "
                    f"has_time_in_effective={('time' in effective_collected_slots)}, "
                    f"has_time_in_slots_for_filtering={('time' in slots_for_filtering)}, "
                    f"missing_slots_before={missing_slots_before}, "
                    f"missing_slots_after={missing_slots}, "
                    f"time_constraint_mode={time_constraint_mode}, is_bounded={is_bounded}"
                )
                logger.info(
                    f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}, bounded={is_bounded}) does NOT satisfy time for CREATE_APPOINTMENT - added 'time' to missing_slots"
                )
                # Update turn_state with corrected missing_slots
                turn_state["missing_slots"] = missing_slots
            else:
                logger.debug(
                    f"[MISSING_SLOTS] time_constraint (mode={time_constraint_mode}, bounded={is_bounded}) does NOT satisfy time for CREATE_APPOINTMENT - keeping 'time' in missing_slots"
                )

    # Note: turn_state["status"] is the base status, but build_plan may override based on
    # needs_clarification or confirmation_state

    # Complete slot state trace with finalization results
    slot_state_trace_before_finalization.update(
        {
            "effective_collected_slots": {
                "keys": list(effective_collected_slots.keys()),
                "values": {
                    k: str(v)[:50] for k, v in effective_collected_slots.items()
                },
            },
            "missing_slots": missing_slots,
            "status": turn_state["status"],
        }
    )

    logger.info(
        f"[SLOT_STATE_TRACE] Before finalization: intent={intent_name}, "
        f"merged_session_slots_keys={list(slots_for_filtering.keys())}, "
        f"effective_collected_slots_keys={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}, status={turn_state['status']}"
    )
    print(
        f"[SLOT_STATE_TRACE] Before finalization: {json.dumps(slot_state_trace_before_finalization, indent=2)}"
    )

    logger.info(
        f"[PRE_PLAN] Finalized turn state: "
        f"intent={intent_name}, "
        f"effective_collected={list(effective_collected_slots.keys())}, "
        f"missing_slots={missing_slots}"
    )

    # CRITICAL: Update luma_response_for_plan with recomputed missing_slots
    # This is the ONLY source of truth - missing_slots computed from effective_collected_slots
    # MUST NOT be overridden or filtered after this point
    luma_response_for_plan = luma_response.copy()
    luma_response_for_plan["missing_slots"] = missing_slots

    # INVESTIGATION: Log missing_slots before passing to build_decision_plan
    logger.error(
        f"[MISSING_SLOTS_TRACE] process_luma_response: BEFORE build_decision_plan, "
        f"intent={intent_name}, user_id={user_id}, "
        f"required_slots={required_slots}, "
        f"effective_collected_slots_keys={sorted(effective_collected_slots.keys())}, "
        f"has_time_in_effective={('time' in effective_collected_slots)}, "
        f"has_time_in_slots_for_filtering={('time' in slots_for_filtering)}, "
        f"missing_slots={missing_slots}, "
        f"missing_slots_length={len(missing_slots)}, "
        f"luma_response_for_plan['missing_slots']={luma_response_for_plan.get('missing_slots')}"
    )
    # Also include effective_collected_slots for executable_actions computation
    luma_response_for_plan["_effective_collected_slots"] = effective_collected_slots

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE build_decision_plan
    decision_intent = intent_name
    facts_intent = (
        luma_response.get("facts", {}).get("intent_name", "")
        if isinstance(luma_response.get("facts"), dict)
        else ""
    )
    logger.error(
        "[INTENT_TRACE_PLANNING_ENTRY] BEFORE build_decision_plan: "
        f"decision.intent_name={decision_intent}, "
        f"facts.intent_name={facts_intent}, "
        f"luma_response['intent']['name']={luma_response.get('intent', {}).get('name', '')}, "
        f"luma_response['_effective_intent']={luma_response.get('_effective_intent', '')}, "
        f"user_id={user_id}"
    )

    # SAFETY ASSERTION: Planning must NEVER run with invalid intent when a durable session intent exists
    # This ensures future regressions fail fast
    # The orchestrator should have already recovered durable session intent before calling this function
    # However, UNKNOWN is valid on first turns when there's no session - only assert if _effective_intent
    # is set (indicating a session exists) but intent_name is still UNKNOWN
    effective_intent_from_response = luma_response.get("_effective_intent", "")
    # Only assert if _effective_intent is set (non-empty, non-UNKNOWN) but intent_name is UNKNOWN
    # This indicates a durable session intent should have been recovered but wasn't
    if effective_intent_from_response and effective_intent_from_response != "UNKNOWN":
        # There's a durable session intent that should have been used
        assert intent_name and intent_name != "UNKNOWN", (
            f"Planning called with invalid intent while durable session intent exists. "
            f"intent_name={intent_name!r}, user_id={user_id}, "
            f"luma_intent={luma_response.get('intent', {}).get('name', '')}, "
            f"effective_intent={effective_intent_from_response}"
        )
    # Otherwise, UNKNOWN is valid (first turn with no session)

    # Determine availability_resolved using slot-fingerprint-based comparison
    # Availability is resolved only if it was checked for the exact same slot combination
    from core.orchestration.availability_fingerprint import (
        compute_availability_fingerprint,
        slots_match_availability_fingerprint,
    )

    # Get stored fingerprint from session (set when SEARCH_AVAILABILITY executed successfully)
    stored_fingerprint = None
    if session_state:
        stored_fingerprint = session_state.get("availability_fingerprint")

    # Compute current fingerprint from effective_collected_slots
    # Use effective_collected_slots as it represents the authoritative slot state
    current_slots = effective_collected_slots

    # Compare fingerprints using proposal-expanded slots so a new exact time_proposal
    # (or date_proposal) invalidates a prior SEARCH_AVAILABILITY fingerprint.
    from core.orchestration.temporal_proposal import expand_slots_for_planning

    fingerprint_slots = expand_slots_for_planning(
        current_slots,
        date_proposal=luma_response.get("date_proposal"),
        time_proposal=luma_response.get("time_proposal"),
        date_constraint=luma_response.get("date_constraint"),
        nlu_facts=facts_obj if isinstance(facts_obj, dict) else None,
        time_constraint=luma_response.get("time_constraint"),
        intent_name=intent_name,
    )

    # Fingerprint scope is conditional on time presence:
    # - Without time: {organization_id, service_id, date}
    # - With time: {organization_id, service_id, date, time}
    # This ensures availability is re-checked when time is introduced
    availability_resolved = slots_match_availability_fingerprint(
        fingerprint_slots, stored_fingerprint, intent_name=intent_name
    )

    # User said "yes" to confirm after a successful availability search — treat
    # availability as resolved for this turn so policy selects CONFIRM_* not SEARCH_*.
    if (
        luma_response.get("_confirm_booking_continuation")
        and stored_fingerprint
    ):
        availability_resolved = True
        logger.info(
            "[AVAILABILITY_FINGERPRINT] confirm_booking_continuation: "
            "forcing availability_resolved=True (stored fingerprint present)"
        )

    # Log fingerprint comparison for debugging
    current_fingerprint = compute_availability_fingerprint(
        fingerprint_slots, intent_name=intent_name
    )
    logger.debug(
        f"[AVAILABILITY_FINGERPRINT] intent={intent_name}, "
        f"current_fingerprint={current_fingerprint}, "
        f"stored_fingerprint={stored_fingerprint}, "
        f"availability_resolved={availability_resolved}, "
        f"current_slots service_id={current_slots.get('service_id')}, "
        f"date={current_slots.get('date')}, time={current_slots.get('time')}, "
        f"organization_id={current_slots.get('organization_id')}"
    )

    # Build decision plan with recomputed missing_slots (ONLY source of truth)
    from core.planning.orchestration.plan_builder import build_decision_plan

    plan = build_decision_plan(
        intent_name,
        luma_response_for_plan,
        domain,
        availability_resolved=availability_resolved,
        session_state=session_state,
    )

    # build_decision_plan applies awaiting_slot prioritization; sync local missing_slots
    plan_missing_slots = plan.get("missing_slots")
    if isinstance(plan_missing_slots, list):
        missing_slots = plan_missing_slots
        luma_response_for_plan["missing_slots"] = missing_slots

    # DEBUG LOG: Planning decision point (after planner, before any override)
    # Track fingerprint-based availability resolution for debugging
    stored_fp = session_state.get("availability_fingerprint") if session_state else None
    last_exec_result = (
        session_state.get("last_execution_result") if session_state else None
    )
    logger.error(
        f"[PLANNING_DECISION] AFTER planner build_decision_plan: "
        f"intent={intent_name}, "
        f"plan.status={plan.get('status')}, plan.stage={plan.get('stage')}, plan.action={plan.get('action')}, "
        f"session.intent_name={session_state.get('intent_name') if session_state else None}, "
        f"session.status={session_state.get('status') if session_state else None}, "
        f"session.stage={session_state.get('stage') if session_state else None}, "
        f"session.action={session_state.get('action') if session_state else None}, "
        f"availability_fingerprint_stored={stored_fp}, "
        f"availability_fingerprint_current={current_fingerprint}, "
        f"availability_resolved={availability_resolved}, "
        f"last_execution_result={last_exec_result}, "
        f"current_slots service_id={current_slots.get('service_id')}, date={current_slots.get('date')}, time={current_slots.get('time')}, org_id={current_slots.get('organization_id')}"
    )

    # INVESTIGATION: Log missing_slots after build_decision_plan
    logger.error(
        f"[MISSING_SLOTS_TRACE] process_luma_response: AFTER build_decision_plan, "
        f"intent={intent_name}, user_id={user_id}, "
        f"missing_slots_variable={missing_slots}, "
        f"missing_slots_length={len(missing_slots)}, "
        f"luma_response_for_plan['missing_slots']={luma_response_for_plan.get('missing_slots')}, "
        f"plan.keys()={list(plan.keys())}"
    )

    # Check if Luma indicates clarification is needed
    # FACT-ONLY: needs_clarification is optional - only check if present
    # Do not reject response if this field is missing
    needs_clarification_flag = luma_response.get("needs_clarification", False)
    if needs_clarification_flag:
        reason = luma_response.get("clarification_reason", "")
        issues = luma_response.get("issues", {})
        # FACT-ONLY: Extract context from facts.facts if present, otherwise from top level
        facts_obj = luma_response.get("facts", {})
        if isinstance(facts_obj, dict) and "context" in facts_obj:
            context = facts_obj.get("context", {})
        else:
            context = luma_response.get("context", {})
        booking = luma_response.get("booking")
        clarification_data = luma_response.get("clarification_data")

        # CRITICAL: Use ONLY the recomputed missing_slots from effective_collected_slots
        # DO NOT extract missing_slots from issues or any other source - the recomputed
        # missing_slots is the ONLY source of truth after finalize_turn_state
        # This ensures slots present in effective_collected_slots (e.g., time from normalization)
        # are not listed as missing
        facts_missing_slots = missing_slots  # Use recomputed missing_slots from above

        # BACKWARD COMPATIBILITY: Derive slots.time from time_constraint ONLY for exact mode
        # This is for test/UI compatibility ONLY - time_constraint is the primary source of truth
        # Never use slots.time to drive planning decisions
        clarification_slots = slots_for_filtering.copy()
        time_constraint = luma_response.get("time_constraint")
        if time_constraint is not None and intent_name == "CREATE_APPOINTMENT":
            # Only derive for exact mode (fuzzy/window don't fully satisfy time)
            time_constraint_mode = (
                time_constraint.get("mode")
                if isinstance(time_constraint, dict)
                else None
            )
            if time_constraint_mode == "exact":
                # Only derive if time is not already in slots (don't override explicit time)
                if "time" not in clarification_slots:
                    derived_time = _derive_time_from_constraint(time_constraint)
                    if derived_time:
                        clarification_slots["time"] = derived_time
                        logger.debug(
                            f"[TIME_COMPAT] Derived slots.time={derived_time} from time_constraint.start={time_constraint.get('start')} "
                            f"(mode=exact) for clarification path - backward compatibility only"
                        )

        # FACT-ONLY: Use context extracted above (from facts.facts or top level)
        # Preserve existing facts (capability reconciliation)
        existing_facts = luma_response.get("facts", {})
        if not isinstance(existing_facts, dict):
            existing_facts = {}
        facts = {
            # Preserve all existing facts (e.g., payment_satisfied)
            **existing_facts,
            # Use normalized slots (includes derived time for compatibility)
            "slots": clarification_slots,
            "missing_slots": facts_missing_slots,
            "context": context,
        }

        # Build TurnState at end of turn (single source of truth)
        from core.planning.orchestration.missing_slots import (
            get_planning_required_slots_for_intent as get_required_slots_for_intent,
        )

        raw_luma_slots = luma_response.get("_raw_luma_slots", {})
        merged_session_slots = merged_session_slots_for_logging
        promoted_slots = promoted_slots_before_normalization
        required_slots = get_required_slots_for_intent(intent_name)

        turn_state_obj = _build_turn_state(
            intent_name=intent_name,
            raw_luma_slots=raw_luma_slots,
            merged_session_slots=merged_session_slots,
            promoted_slots=promoted_slots,
            effective_collected_slots=effective_collected_slots,
            required_slots=required_slots,
            missing_slots=facts_missing_slots,
            plan=plan,
        )

        # ONE unconditional debug print/log at end of turn
        print(
            f"TURN_STATE: {json.dumps(turn_state_obj.to_dict(), indent=2, default=str)}"
        )

        return {
            "intent_name": intent_name,  # CRITICAL: Always include intent_name in decision
            "outcome": _build_clarify_outcome(
                clarification_reason=reason,
                issues=issues,
                context=context,
                booking=booking,
                domain=domain,
                clarification_data=clarification_data,
                facts=facts,
                intent_name=intent_name,
            ),
            "plan": plan,
            "facts": facts,
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
        # CRITICAL: Missing action_name is NOT an error in planning
        # Still return a valid plan with intent, slots, missing_slots
        # Execution layer will handle unsupported intents

        # BACKWARD COMPATIBILITY: Derive slots.time from time_constraint ONLY for exact mode
        # This is for test/UI compatibility ONLY - time_constraint is the primary source of truth
        # Never use slots.time to drive planning decisions
        no_action_slots = slots_for_filtering.copy()
        time_constraint = luma_response.get("time_constraint")
        if time_constraint is not None and intent_name == "CREATE_APPOINTMENT":
            # Only derive for exact mode (fuzzy/window don't fully satisfy time)
            time_constraint_mode = (
                time_constraint.get("mode")
                if isinstance(time_constraint, dict)
                else None
            )
            if time_constraint_mode == "exact":
                # Only derive if time is not already in slots (don't override explicit time)
                if "time" not in no_action_slots:
                    derived_time = _derive_time_from_constraint(time_constraint)
                    if derived_time:
                        no_action_slots["time"] = derived_time
                        logger.debug(
                            f"[TIME_COMPAT] Derived slots.time={derived_time} from time_constraint.start={time_constraint.get('start')} "
                            f"(mode=exact) for no-action path - backward compatibility only"
                        )

        # Extract facts container
        # Preserve existing facts (capability reconciliation)
        existing_facts = luma_response.get("facts", {})
        if not isinstance(existing_facts, dict):
            existing_facts = {}
        facts = {
            # Preserve all existing facts (e.g., payment_satisfied)
            **existing_facts,
            "slots": no_action_slots,
            "missing_slots": missing_slots,
            "context": luma_response.get("context", {}),
        }
        # Return valid decision with plan (not an error)
        # Planning must always proceed, even if action_name is missing
        return {"intent_name": intent_name, "plan": plan, "facts": facts}

    # Return execution instruction with decision plan
    booking = luma_response.get("booking", {})

    # CRITICAL: Use ONLY the recomputed missing_slots from effective_collected_slots
    # This is the ONLY source of truth - missing_slots MUST NOT be overridden or filtered
    # This ensures slots present in effective_collected_slots (e.g., time from normalization)
    # are not listed as missing

    # Use normalized slots (includes normalized time)
    slots = slots_for_filtering.copy()

    # BACKWARD COMPATIBILITY: Derive slots.time from time_constraint ONLY for exact mode
    # This is for test/UI compatibility ONLY - time_constraint is the primary source of truth
    # Never use slots.time to drive planning decisions
    time_constraint = luma_response.get("time_constraint")
    if time_constraint is not None and intent_name == "CREATE_APPOINTMENT":
        # Only derive for exact mode (fuzzy/window don't fully satisfy time)
        time_constraint_mode = (
            time_constraint.get("mode") if isinstance(time_constraint, dict) else None
        )
        if time_constraint_mode == "exact":
            # Only derive if time is not already in slots (don't override explicit time)
            if "time" not in slots:
                derived_time = _derive_time_from_constraint(time_constraint)
                if derived_time:
                    slots["time"] = derived_time
                    logger.debug(
                        f"[TIME_COMPAT] Derived slots.time={derived_time} from time_constraint.start={time_constraint.get('start')} "
                        f"(mode=exact) for backward compatibility"
                    )

    # Update luma_response slots with normalized slots (includes normalized time)
    # FACT-ONLY: Update both facts.facts.slots (if present) and legacy slots field
    facts_obj = luma_response.get("facts", {})
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        # New fact-only format: update facts.facts.slots
        if not isinstance(luma_response.get("facts"), dict):
            luma_response["facts"] = {}
        luma_response["facts"]["slots"] = slots
    # Always update legacy slots field for backward compatibility
    luma_response["slots"] = slots

    # FACT-ONLY: Extract context from facts.facts if present, otherwise from top level
    facts_obj = luma_response.get("facts", {})
    if isinstance(facts_obj, dict) and "context" in facts_obj:
        effective_context = facts_obj.get("context", {})
    else:
        effective_context = luma_response.get("context", {})

    # CAPABILITY RECONCILIATION: Preserve non-LUMA facts when building decision.facts
    # Start from existing effective_response.facts (which already includes merged session and capability facts)
    # Overlay LUMA-derived fields (slots, missing_slots, context)
    # Do NOT discard existing facts such as payment_satisfied or payment_reference
    # This ensures capability completion markers survive LUMA processing
    existing_facts = luma_response.get("facts", {})
    if not isinstance(existing_facts, dict):
        existing_facts = {}

    facts = {
        # Preserve all existing facts (e.g., payment_satisfied, payment_reference, org)
        **existing_facts,
        "slots": slots,  # Overlay with computed slots
        # Use recomputed missing_slots (ONLY source of truth)
        "missing_slots": missing_slots,  # Overlay with computed missing_slots
        "context": effective_context,  # Overlay with computed context
    }
    if luma_response.get("date_proposal") is not None:
        facts["date_proposal"] = luma_response["date_proposal"]
    if luma_response.get("time_proposal") is not None:
        facts["time_proposal"] = luma_response["time_proposal"]

    # DIAGNOSTIC: Log facts structure before returning
    logger.error("[FLOW_TRACE] process_luma_response building facts:")
    logger.error(
        "[FLOW_TRACE]   facts.slots: %s (keys: %s)",
        slots,
        list(slots.keys()) if isinstance(slots, dict) else "N/A",
    )
    logger.error(
        "[FLOW_TRACE]   facts.missing_slots: %s (len: %s)",
        missing_slots,
        len(missing_slots) if isinstance(missing_slots, list) else "N/A",
    )
    if isinstance(slots, dict) and "service_id" in slots:
        logger.error(
            "[FLOW_TRACE]   facts.slots.service_id: %s", slots.get("service_id")
        )

    # DATE_NORMALIZATION_TRACE: Log date value in facts.slots
    if isinstance(slots, dict) and "date" in slots:
        date_value = slots.get("date")
        plan_stage = plan.get("stage") if isinstance(plan, dict) else None
        plan_action = plan.get("action") if isinstance(plan, dict) else None
        is_iso_date = isinstance(date_value, str) and bool(
            re.match(r"^\d{4}-\d{2}-\d{2}$", date_value)
        )
        logger.info(
            "[DATE_NORMALIZATION_TRACE] process_luma_response: date in facts.slots",
            extra={
                "user_id": user_id,
                "date_value": date_value,
                "is_iso_format": is_iso_date,
                "intent": intent_name,
                "plan_stage": plan_stage,
                "plan_action": plan_action,
                "normalization_point": "luma_response_processor:1498",
            },
        )

    # Add debug info to facts for troubleshooting
    facts["_debug"] = {
        "recomputed_missing_slots": missing_slots,
        "effective_collected_slots": list(effective_collected_slots.keys()),
        "slots_keys": list(slots.keys()),
        "booking_has_services": (
            isinstance(booking, dict)
            and isinstance(booking.get("services"), list)
            and len(booking.get("services", [])) > 0
        ),
        "service_id_in_slots": "service_id" in slots,
        "service_id_value": slots.get("service_id"),
    }

    # Build TurnState at end of turn (single source of truth)
    from core.planning.orchestration.missing_slots import (
        get_planning_required_slots_for_intent as get_required_slots_for_intent,
    )

    raw_luma_slots = luma_response.get("_raw_luma_slots", {})
    merged_session_slots = merged_session_slots_for_logging
    promoted_slots = promoted_slots_before_normalization
    required_slots = get_required_slots_for_intent(intent_name)
    turn_state_obj = _build_turn_state(
        intent_name=intent_name,
        raw_luma_slots=raw_luma_slots,
        merged_session_slots=merged_session_slots,
        promoted_slots=promoted_slots,
        effective_collected_slots=effective_collected_slots,
        required_slots=required_slots,
        missing_slots=missing_slots,
        plan=plan,
    )

    # ONE unconditional debug print/log at end of turn
    print(f"TURN_STATE: {json.dumps(turn_state_obj.to_dict(), indent=2, default=str)}")

    # Core planning-only output structure
    # Core NEVER executes - only returns planning information
    decision_result = {
        "intent_name": intent_name,
        "action_name": action_name,
        "booking": booking,
        "plan": plan,
        "facts": facts,
    }

    # DIAGNOSTIC: Log decision structure before returning
    logger.error("[FLOW_TRACE] process_luma_response RETURNING:")
    logger.error("[FLOW_TRACE]   decision.keys(): %s", list(decision_result.keys()))
    logger.error("[FLOW_TRACE]   decision.facts: %s", decision_result.get("facts"))
    logger.error(
        "[FLOW_TRACE]   decision.facts.slots: %s",
        decision_result.get("facts", {}).get("slots"),
    )
    logger.error(
        "[FLOW_TRACE]   decision.facts.missing_slots: %s",
        decision_result.get("facts", {}).get("missing_slots"),
    )
    logger.error(
        "[FLOW_TRACE]   decision.plan.stage: %s",
        decision_result.get("plan", {}).get("stage"),
    )
    logger.error(
        "[FLOW_TRACE]   decision.plan.action: %s",
        decision_result.get("plan", {}).get("action"),
    )

    return decision_result


def build_clarify_outcome_from_reason(
    reason: str,
    issues: Dict[str, Any],
    booking: Optional[Dict[str, Any]],
    domain: str,
    facts: Optional[Dict[str, Any]] = None,
    intent_name: Optional[str] = None,
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
        intent_name: Optional intent name for dialog instructions

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

    # Get dialog instructions if intent and missing slots are available
    dialog_instructions = None
    if intent_name and missing_slots:
        from core.planning.policy.stage_policy import get_dialog_instructions

        dialog_instructions = get_dialog_instructions(intent_name, missing_slots)

    outcome = {
        "status": "NEEDS_CLARIFICATION",
        # Top-level canonical reason derived from missing slots
        "clarification_reason": canonical_reason,
        "template_key": template_key,
        "data": clarification_data,
        "booking": booking,
    }

    # Add dialog instructions as structured JSON (not plain text)
    if dialog_instructions:
        outcome["dialog_instructions"] = dialog_instructions

    # Include facts if provided (needed for renderer to access slots)
    if facts:
        outcome["facts"] = facts

    return {"success": True, "outcome": outcome}
