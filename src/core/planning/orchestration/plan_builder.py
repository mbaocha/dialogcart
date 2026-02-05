"""
Plan Builder

Builds decision plans from Luma responses using intent_policy.yaml as the single source of truth.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
- All intent-specific logic comes from intent_policy.yaml via select_next_execution_step
"""

import logging
from typing import Dict, Any, Optional, List

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


def _enforce_committing_step_invariants(
    intent_name: str,
    selected_step: Dict[str, Any],
    effective_slots: Dict[str, Any],
    flags: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None
) -> None:
    """
    Enforce runtime invariants before executing a committing step.
    
    Invariants:
    1. All required_slots must be present
    2. All requires must be satisfied
    3. This committing step must not have already executed in this session (idempotency guard)
    
    Args:
        intent_name: Intent name
        selected_step: Selected execution step from policy
        effective_slots: Collected slots
        flags: Session flags (availability_resolved, confirmation_state, etc.)
        session_state: Optional session state for idempotency check
        
    Raises:
        AssertionError: If any invariant is violated
    """
    mode = selected_step.get("mode", "exploratory")
    if mode != "committing":
        # Only enforce invariants for committing steps
        return
    
    action = selected_step.get("action")
    required_slots = selected_step.get("required_slots", [])
    requires = selected_step.get("requires", [])
    
    # Invariant 1: All required_slots must be present
    collected_slot_names = set(
        slot_name for slot_name, slot_value in effective_slots.items()
        if slot_value is not None
    )
    required_slots_set = set(required_slots)
    missing_required = required_slots_set - collected_slot_names
    assert not missing_required, (
        f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
        f"Missing required slots: {sorted(missing_required)}. "
        f"Required: {sorted(required_slots_set)}, Collected: {sorted(collected_slot_names)}"
    )
    
    # Invariant 2: All requires must be satisfied
    availability_resolved = flags.get("availability_resolved", False)
    confirmation_state = flags.get("confirmation_state")
    
    for requirement in requires:
        if requirement == "availability_resolved":
            assert availability_resolved, (
                f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
                f"Requirement 'availability_resolved' not satisfied (availability_resolved={availability_resolved})"
            )
        elif requirement == "confirmation_state_confirmed":
            assert confirmation_state == "confirmed", (
                f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
                f"Requirement 'confirmation_state_confirmed' not satisfied (confirmation_state={confirmation_state})"
            )
        elif requirement == "booking_id_resolved":
            # booking_id_resolved means booking_id must be present and non-None
            assert "booking_id" in effective_slots and effective_slots.get("booking_id") is not None, (
                f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
                f"Requirement 'booking_id_resolved' not satisfied (booking_id not in slots or None)"
            )
    
    # Invariant 3: Idempotency guard - cannot execute same committing step twice in same session
    if session_state:
        executed_actions = session_state.get("executed_actions", [])
        if action in executed_actions:
            logger.warning(
                f"Idempotency guard: {action} for {intent_name} already executed in this session. "
                f"Executed actions: {executed_actions}"
            )
            # Note: We log a warning but don't assert - idempotency is handled at execution layer
            # This is just a guard to catch programming errors


def build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str,
    availability_resolved: bool = False,
    session_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Build a decision plan from Luma response using intent_policy.yaml as the single source of truth.

    PLANNING BOUNDARY:
    - missing_slots MUST come from planner (intent_policy.yaml)
    - executable_actions MUST come from planner (intent_policy.yaml)
    - commit actions come from intent_policy.yaml execution steps with mode=committing
    - All action selection comes from select_next_execution_step()

    Applies rules:
    - commit.action is the irreversible commit step (from intent_policy.yaml)
    - Block commit when needs_clarification == true
    - executable_actions come from planner (partial execution with subsets of slots)

    Args:
        intent_name: Intent name from Luma
        luma_response: Luma API response (must have missing_slots from planner)
        domain: Domain for template key routing
        availability_resolved: Whether availability has been resolved
        session_state: Optional session state for idempotency checks

    Returns:
        Decision plan dictionary with:
        - status: READY, NEEDS_CLARIFICATION, or AWAITING_CONFIRMATION
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION or null
    """
    # SAFETY ASSERTION: Planning must NEVER run with invalid intent when a durable session intent exists
    effective_intent_from_response = luma_response.get("_effective_intent", "")
    if effective_intent_from_response and effective_intent_from_response != "UNKNOWN":
        from core.orchestration.persistence.durable_intents import is_durable_intent
        if is_durable_intent(effective_intent_from_response):
            assert intent_name and intent_name != "UNKNOWN", (
                f"build_decision_plan called with invalid intent while durable session intent exists. "
                f"intent_name={intent_name!r}, effective_intent={effective_intent_from_response}, domain={domain}"
            )
    
    # Get commit action from unified policy (intent_policy.yaml)
    from core.policy.intent_policy import get_commit_action
    commit_action = get_commit_action(intent_name)

    # Extract missing slots
    missing_slots = _extract_missing_slots(luma_response)
    
    logger.debug(
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
    confirmation_state = booking.get("confirmation_state") if isinstance(booking, dict) else None

    logger.debug(
        f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} "
        f"needs_clarification={needs_clarification} confirmation_state={confirmation_state}"
    )

    # CRITICAL PLANNING INVARIANT: UNKNOWN intent ALWAYS requires clarification
    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because intent=UNKNOWN "
            f"(UNKNOWN always requires clarification)"
        )
    # CRITICAL: If missing_slots is non-empty, status MUST be NEEDS_CLARIFICATION
    elif missing_slots:
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}"
        )
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True"
        )
    elif confirmation_state == "pending":
        status = "AWAITING_CONFIRMATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because confirmation_state=pending"
        )
    else:
        status = "READY"
        logger.debug(
            f"[BUILD_PLAN] Setting status=READY "
            f"(no missing slots, no clarification needed, no pending confirmation)"
        )

    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []

    # Get executable_actions from planner (ONLY source of truth for partial execution)
    executable_actions = []
    if intent_name:
        from core.planning.policy.action_policy import plan_intent, load_planning_policy
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})
        policy = load_planning_policy()
        planner_result = plan_intent(intent_name, effective_slots, policy)
        executable_actions = planner_result.get("executable_actions", [])

    # CRITICAL: Allow exploratory actions even when planning slots are incomplete
    # Only block committing actions when missing_slots exist
    if missing_slots:
        # Block committing actions when missing_slots exist
        if commit_action:
            blocked_actions.append(commit_action)
        # Allow exploratory actions from planner (they satisfy their own required_slots)
        allowed_actions.extend(executable_actions)
    else:
        # No missing slots - allow executable_actions from planner (partial execution)
        allowed_actions.extend(executable_actions)

        # Commit action blocking rules
        if commit_action:
            if needs_clarification:
                # Luma explicitly says needs clarification - block commit
                blocked_actions.append(commit_action)
            else:
                # All slots filled and no clarification needed - allow commit
                allowed_actions.append(commit_action)

    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))

    # Determine awaiting
    awaiting = "USER_CONFIRMATION" if confirmation_state == "pending" else None

    # Derive stage and action using policy as the single source of truth
    stage = None
    action = None
    action_branch = None

    # Get slots for policy selection
    effective_slots = luma_response.get("_effective_collected_slots")
    if effective_slots is None:
        effective_slots = luma_response.get("slots", {})

    # POLICY-DRIVEN SELECTION: Always use select_next_execution_step
    # This is the single source of truth for action selection
    if intent_name and intent_name != "UNKNOWN":
        from core.policy.intent_policy import select_next_execution_step
        
        flags = {
            "availability_resolved": availability_resolved,
            "confirmation_state": confirmation_state
        }
        
        selected_step = select_next_execution_step(intent_name, effective_slots, flags)
        
        if selected_step:
            action = selected_step.get("action")
            action_branch = "policy"
            
            # Enforce runtime invariants for committing steps
            _enforce_committing_step_invariants(
                intent_name, selected_step, effective_slots, flags, session_state
            )
            
            # Determine stage based on action
            if action == "FETCH_BOOKING":
                stage = "IDENTIFY"
            elif action == "SEARCH_AVAILABILITY":
                stage = "AVAILABILITY"
            elif action in ("CONFIRM_APPOINTMENT", "CONFIRM_RESERVATION", "APPLY_MODIFICATION", "CONFIRM_CANCELLATION"):
                stage = "CONFIRM"
            else:
                # Default based on mode
                mode = selected_step.get("mode", "exploratory")
                stage = "AVAILABILITY" if mode == "exploratory" else "CONFIRM"
            
            logger.info(
                f"[PLAN_SELECTION] Policy-driven selection: intent={intent_name}, "
                f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                f"selected_action={action}, selected_stage={stage}, "
                f"rule=select_next_execution_step"
            )
        else:
            # Policy returned None - this should not happen for valid intents
            # Log warning but don't fail - allow graceful degradation
            logger.warning(
                f"[PLAN_SELECTION] Policy returned None for intent={intent_name}. "
                f"This may indicate a missing or incomplete policy configuration."
            )
            # Use first executable action as fallback
            if executable_actions:
                action = executable_actions[0]
                action_branch = "fallback_executable"
                stage = "AVAILABILITY"  # Default for exploratory actions
            elif commit_action and commit_action in allowed_actions:
                action = commit_action
                action_branch = "fallback_commit"
                stage = "CONFIRM"
    
    # If still no action selected (e.g., UNKNOWN intent), set defaults
    if action is None:
        if executable_actions:
            action = executable_actions[0]
            action_branch = "fallback_executable"
            stage = "AVAILABILITY"
        else:
            action_branch = "no_action"
            stage = None

    logger.debug(
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
