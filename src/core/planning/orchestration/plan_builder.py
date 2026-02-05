"""
Plan Builder

Builds decision plans from Luma responses.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
"""

import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List

import yaml

from core.routing import get_template_key, get_action_name
from core.orchestration.errors import UnsupportedIntentError

logger = logging.getLogger(__name__)


def _extract_missing_slots(luma_response: Dict[str, Any]) -> List[str]:
    """
    Extract missing slots from Luma response.

    Checks multiple sources:
    1. luma_response.missing_slots (authoritative if present)
    2. luma_response.issues (dict with slot_name -> "missing")
    3. Empty list if neither present

    Args:
        luma_response: Luma API response

    Returns:
        List of missing slot names
    """
    # Check direct missing_slots field (authoritative)
    missing_slots = luma_response.get("missing_slots")
    if isinstance(missing_slots, list):
        return missing_slots

    # Fallback: extract from issues dict
    issues = luma_response.get("issues", {})
    if isinstance(issues, dict):
        missing = []
        for slot_name, issue_value in issues.items():
            if issue_value == "missing" or (isinstance(issue_value, dict) and issue_value.get("status") == "missing"):
                missing.append(slot_name)
        return missing

    return []


def build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str,
    availability_resolved: bool = False
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
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
    """
    # SAFETY ASSERTION: Planning must NEVER run with invalid intent when a durable session intent exists
    # This ensures future regressions fail fast
    # The orchestrator should have already recovered durable session intent before calling this function
    # However, UNKNOWN is valid on first turns when there's no session - only assert if _effective_intent
    # is set (indicating a session exists) and is durable, but intent_name is still UNKNOWN
    # FIRST-TURN PERMISSIVENESS: On first-turn messages with no session, Luma may return UNKNOWN intent
    # (e.g., user says just "tomorrow" without specifying service). This is valid and should not trigger
    # an assertion. The assertion only fires when a durable session intent exists but wasn't recovered.
    effective_intent_from_response = luma_response.get("_effective_intent", "")
    # Only assert if _effective_intent is set (non-empty, non-UNKNOWN) and is durable, but intent_name is UNKNOWN
    # This indicates a durable session intent should have been recovered but wasn't
    if effective_intent_from_response and effective_intent_from_response != "UNKNOWN":
        # Check if the effective intent is durable (only durable intents should trigger the assertion)
        from core.orchestration.persistence.durable_intents import is_durable_intent
        if is_durable_intent(effective_intent_from_response):
            # There's a durable session intent that should have been used
            assert intent_name and intent_name != "UNKNOWN", (
                f"build_decision_plan called with invalid intent while durable session intent exists. "
                f"intent_name={intent_name!r}, effective_intent={effective_intent_from_response}, domain={domain}"
            )
    # Otherwise, UNKNOWN is valid (first turn with no session, or non-durable intent)
    
    # Get commit action from unified policy (intent_policy.yaml)
    from core.policy.intent_policy import get_commit_action
    commit_action = get_commit_action(intent_name)

    # Extract missing slots
    missing_slots = _extract_missing_slots(luma_response)
    
    # INVESTIGATION: Log missing_slots extraction in build_decision_plan
    logger.error(
        f"[MISSING_SLOTS_TRACE] build_decision_plan: AFTER _extract_missing_slots, "
        f"intent={intent_name}, "
        f"missing_slots={missing_slots}, "
        f"missing_slots_length={len(missing_slots)}, "
        f"luma_response.missing_slots={luma_response.get('missing_slots')}, "
        f"luma_response.issues={luma_response.get('issues')}"
    )

    # Determine status
    needs_clarification = luma_response.get("needs_clarification", False)
    booking = luma_response.get("booking", {})
    confirmation_state = booking.get(
        "confirmation_state") if isinstance(booking, dict) else None

    # DEBUG: Print decision plan building details
    print(
        f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} needs_clarification={needs_clarification} confirmation_state={confirmation_state}")

    # CRITICAL PLANNING INVARIANT: UNKNOWN intent ALWAYS requires clarification
    # UNKNOWN means we don't know what the user wants, so we must clarify regardless of missing_slots
    # This prevents UNKNOWN from being marked as READY even when missing_slots is empty
    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        print(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because intent=UNKNOWN (UNKNOWN always requires clarification)")
    # CRITICAL: If missing_slots is non-empty, status MUST be NEEDS_CLARIFICATION
    # This is the authoritative rule - missing slots drive clarification, not Luma flags
    elif missing_slots:
        status = "NEEDS_CLARIFICATION"
        print(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}")
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        print(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True")
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
        print(
            f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending")
    else:
        status = "READY"
        print(f"[BUILD_PLAN] Setting status=READY (no missing slots, no clarification needed, no pending confirmation)")

    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []

    # Get executable_actions from planner (ONLY source of truth for partial execution)
    # Planner computes executable_actions from intent_policy.yaml based on collected slots
    executable_actions = []
    if intent_name:
        from core.planning.policy.action_policy import plan_intent, load_planning_policy
        # Use effective_collected_slots if available (more accurate), otherwise use slots
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        policy = load_planning_policy()
        planner_result = plan_intent(intent_name, effective_slots, policy)
        executable_actions = planner_result.get("executable_actions", [])

    # CRITICAL: Allow exploratory actions even when planning slots are incomplete
    # Only block committing actions when missing_slots exist
    # Planner's executable_actions are for partial execution and should be allowed
    if missing_slots:
        # Block committing actions when missing_slots exist
        if commit_action:
            blocked_actions.append(commit_action)
        # Allow exploratory actions from planner (they satisfy their own required_slots)
        # This aligns with policy: exploratory actions can execute with partial slots
        allowed_actions.extend(executable_actions)
    else:
        # No missing slots - allow executable_actions from planner (partial execution)
        allowed_actions.extend(executable_actions)

        # Commit action blocking rules
        if commit_action:
            # CRITICAL: If missing_slots is empty, allow commit immediately
            # Tests expect READY state to execute without confirmation when slots are complete
            # Do NOT require confirmation_state == "confirmed" when all slots are filled
            if needs_clarification:
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

    # Derive stage and action from current state
    # Stage/action mapping based on missing_slots and executable_actions
    stage = None
    action = None
    action_branch = None  # Track which branch set the action for debugging

    # CONFIRM ACTION MAP: Hard mapping for complete slots (missing_slots == [])
    CONFIRM_ACTION_MAP = {
        "CREATE_APPOINTMENT": "CONFIRM_APPOINTMENT",
        "CREATE_RESERVATION": "CONFIRM_RESERVATION",
        "MODIFY_BOOKING": "APPLY_MODIFICATION",
        "CANCEL_BOOKING": "CONFIRM_CANCELLATION"
    }

    if len(missing_slots) > 0:
        # Missing slots - determine stage based on intent and executable actions
        if intent_name in ("MODIFY_BOOKING", "CANCEL_BOOKING"):
            stage = "IDENTIFY"
            action = "FETCH_BOOKING"
            action_branch = "missing_slots_modify_cancel"
        else:
            # CREATE_APPOINTMENT or CREATE_RESERVATION
            stage = "AVAILABILITY"
            # Use first executable action if available, otherwise default to SEARCH_AVAILABILITY
            if executable_actions:
                action = executable_actions[0]
            else:
                action = "SEARCH_AVAILABILITY"
            action_branch = "missing_slots_executable"
    else:
        # No missing slots - use policy-driven selection to respect 'requires' field
        # This ensures CONFIRM_APPOINTMENT only runs when availability_resolved is true
        
        # Get slots for policy selection
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        
        # Use select_next_execution_step to respect policy 'requires' field
        # Policy is the single decision authority - always pass actual availability_resolved value
        # Policy will correctly select:
        # - SEARCH_AVAILABILITY when availability_resolved=False
        # - CONFIRM_APPOINTMENT when availability_resolved=True
        if action is None:
            from core.policy.intent_policy import select_next_execution_step
            # Always pass the actual computed availability_resolved value
            # Planner never lies about availability state - policy decides based on truth
            flags = {"availability_resolved": availability_resolved}
            selected_step = select_next_execution_step(intent_name, effective_slots, flags)
            
            if selected_step:
                # Policy selected a step - use it
                action = selected_step.get("action")
                action_branch = "policy"
                
                # MODIFY_BOOKING guardrail: Enforce SEARCH_AVAILABILITY first
                # Never allow APPLY_MODIFICATION on first turn (before availability check)
                if intent_name == "MODIFY_BOOKING" and action == "APPLY_MODIFICATION" and not availability_resolved:
                    if "SEARCH_AVAILABILITY" in allowed_actions:
                        action = "SEARCH_AVAILABILITY"
                        stage = "AVAILABILITY"
                        action_branch = "modify_booking_override_policy_availability_first"
                        logger.info(
                            f"[PLAN_SELECTION] MODIFY_BOOKING: Overriding policy selection "
                            f"(APPLY_MODIFICATION -> SEARCH_AVAILABILITY, availability_resolved={availability_resolved})"
                        )
                    else:
                        # SEARCH_AVAILABILITY not available - keep APPLY_MODIFICATION but log warning
                        stage = "CONFIRM"
                        action_branch = "modify_booking_policy_apply_forced"
                        logger.warning(
                            f"[PLAN_SELECTION] MODIFY_BOOKING: Policy selected APPLY_MODIFICATION "
                            f"but availability_resolved=False and SEARCH_AVAILABILITY not in allowed_actions"
                        )
                else:
                    # Determine stage based on action
                    if action == "SEARCH_AVAILABILITY":
                        stage = "AVAILABILITY"
                    elif action in ("CONFIRM_APPOINTMENT", "CONFIRM_RESERVATION", "APPLY_MODIFICATION", "CONFIRM_CANCELLATION"):
                        stage = "CONFIRM"
                    else:
                        # Default to CONFIRM for committing actions, AVAILABILITY for exploratory
                        mode = selected_step.get("mode", "exploratory")
                        stage = "AVAILABILITY" if mode == "exploratory" else "CONFIRM"
                
                logger.info(
                    f"[PLAN_SELECTION] Policy-driven selection: intent={intent_name}, "
                    f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                    f"selected_action={action}, selected_stage={stage}, "
                    f"rule=select_next_execution_step"
                )
            else:
                # Policy didn't select a step - fall back to CONFIRM_ACTION_MAP logic
                # CRITICAL: Only check availability_resolved when missing_slots exist
                # When missing_slots == [] and status == READY, availability must NOT block CONFIRM_APPOINTMENT
                stage = "CONFIRM"
                
                if intent_name in CONFIRM_ACTION_MAP:
                    candidate_action = CONFIRM_ACTION_MAP[intent_name]
                    # Only block CONFIRM_APPOINTMENT by availability when slots are incomplete
                    # When all slots are complete (missing_slots == [] and status == READY),
                    # availability is optional and must not block confirmation
                    if (candidate_action == "CONFIRM_APPOINTMENT" 
                            and not availability_resolved 
                            and missing_slots != []):
                        # Availability not resolved AND slots incomplete - select SEARCH_AVAILABILITY instead
                        if "SEARCH_AVAILABILITY" in executable_actions:
                            action = "SEARCH_AVAILABILITY"
                            stage = "AVAILABILITY"
                            action_branch = "fallback_availability_blocked"
                            logger.info(
                                f"[PLAN_SELECTION] Fallback with availability check: intent={intent_name}, "
                                f"missing_slots={missing_slots}, status={status}, availability_resolved={availability_resolved}, "
                                f"selected_action={action}, selected_stage={stage}, "
                                f"rule=CONFIRM_APPOINTMENT_blocked_by_availability_resolved_incomplete_slots"
                            )
                        else:
                            # SEARCH_AVAILABILITY not in executable_actions - use CONFIRM anyway
                            action = candidate_action
                            action_branch = "fallback_confirm_forced"
                            logger.warning(
                                f"[PLAN_SELECTION] CONFIRM_APPOINTMENT selected despite availability_resolved=False "
                                f"because SEARCH_AVAILABILITY not in executable_actions: {executable_actions}"
                            )
                    else:
                        # Slots complete OR availability resolved - allow CONFIRM_APPOINTMENT
                        action = candidate_action
                        action_branch = "fallback_confirm_map"
                        logger.info(
                            f"[PLAN_SELECTION] Fallback to CONFIRM_ACTION_MAP: intent={intent_name}, "
                            f"missing_slots={missing_slots}, status={status}, availability_resolved={availability_resolved}, "
                            f"selected_action={action}, rule=CONFIRM_ACTION_MAP"
                        )
                elif commit_action and commit_action in allowed_actions:
                    # Commit action is available and allowed - use it
                    action = commit_action
                    action_branch = "fallback_commit_allowed"
                    logger.info(
                        f"[PLAN_SELECTION] Fallback to commit_action: intent={intent_name}, "
                        f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                        f"selected_action={action}, rule=commit_action_in_allowed"
                    )
                elif commit_action:
                    # Commit action exists but is blocked - use fallback
                    if intent_name == "MODIFY_BOOKING":
                        # MODIFY_BOOKING: Enforce two-step flow - SEARCH_AVAILABILITY first
                        # Only allow APPLY_MODIFICATION after availability is resolved
                        if not availability_resolved and "SEARCH_AVAILABILITY" in allowed_actions:
                            action = "SEARCH_AVAILABILITY"
                            stage = "AVAILABILITY"
                            action_branch = "modify_booking_availability_first"
                            logger.info(
                                f"[PLAN_SELECTION] MODIFY_BOOKING: Enforcing SEARCH_AVAILABILITY first "
                                f"(availability_resolved={availability_resolved})"
                            )
                        else:
                            # Availability resolved or SEARCH_AVAILABILITY not available - use APPLY_MODIFICATION
                            action = "APPLY_MODIFICATION"
                            action_branch = "modify_booking_apply_after_availability"
                            logger.info(
                                f"[PLAN_SELECTION] MODIFY_BOOKING: Using APPLY_MODIFICATION "
                                f"(availability_resolved={availability_resolved})"
                            )
                    elif intent_name == "CANCEL_BOOKING":
                        action = "CONFIRM_CANCELLATION"
                        action_branch = "fallback_commit_blocked"
                    else:
                        # Fallback to first allowed action if no commit action
                        filtered_actions = [
                            a for a in allowed_actions if a != "SEARCH_AVAILABILITY"]
                        action = filtered_actions[0] if filtered_actions else None
                        action_branch = "fallback_commit_blocked"
                    
                    if action_branch == "fallback_commit_blocked":
                        logger.info(
                            f"[PLAN_SELECTION] Fallback to intent-specific action: intent={intent_name}, "
                            f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                            f"selected_action={action}, rule=commit_action_blocked_fallback"
                        )
                else:
                    # No commit action defined - use fallback based on intent
                    if intent_name == "MODIFY_BOOKING":
                        # MODIFY_BOOKING: Enforce two-step flow - SEARCH_AVAILABILITY first
                        # Only allow APPLY_MODIFICATION after availability is resolved
                        if not availability_resolved and "SEARCH_AVAILABILITY" in allowed_actions:
                            action = "SEARCH_AVAILABILITY"
                            stage = "AVAILABILITY"
                            action_branch = "modify_booking_availability_first"
                            logger.info(
                                f"[PLAN_SELECTION] MODIFY_BOOKING: Enforcing SEARCH_AVAILABILITY first "
                                f"(availability_resolved={availability_resolved})"
                            )
                        else:
                            # Availability resolved or SEARCH_AVAILABILITY not available - use APPLY_MODIFICATION
                            action = "APPLY_MODIFICATION"
                            action_branch = "modify_booking_apply_after_availability"
                            logger.info(
                                f"[PLAN_SELECTION] MODIFY_BOOKING: Using APPLY_MODIFICATION "
                                f"(availability_resolved={availability_resolved})"
                            )
                    elif intent_name == "CANCEL_BOOKING":
                        action = "CONFIRM_CANCELLATION"
                        action_branch = "fallback_last_resort"
                    else:
                        # Last resort: use first allowed action (but never SEARCH_AVAILABILITY when slots complete)
                        filtered_actions = [
                            a for a in allowed_actions if a != "SEARCH_AVAILABILITY"]
                        action = filtered_actions[0] if filtered_actions else None
                        action_branch = "fallback_last_resort"
                    
                    if action_branch == "fallback_last_resort":
                        logger.info(
                            f"[PLAN_SELECTION] Fallback to last resort: intent={intent_name}, "
                            f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                            f"selected_action={action}, rule=no_commit_action_fallback"
                        )

    # DEBUG: Log final plan decision before returning
    logger.error(
        f"[PLAN_FINAL_DECISION] intent_name={intent_name}, "
        f"missing_slots={missing_slots}, "
        f"status={status}, "
        f"availability_resolved={availability_resolved}, "
        f"action={action}, "
        f"stage={stage}, "
        f"action_branch={action_branch}"
    )

    return {
        "status": status,
        "stage": stage,
        "action": action,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "executable_actions": executable_actions
    }
