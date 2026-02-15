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
from typing import Any, Dict, List, Optional

from core.config.capabilities_loader import load_capability_policies
from core.orchestration.errors import UnsupportedIntentError
from core.routing import get_action_name, get_template_key

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
            if issue_value == "missing" or (
                isinstance(issue_value, dict) and issue_value.get("status") == "missing"
            ):
                missing.append(slot_name)
        return missing

    return []


def _evaluate_condition(
    condition_expr: str,
    facts: Dict[str, Any],
    slots: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Evaluate a single condition expression.

    Supports:
    - Namespaces: org.*, facts.*, slots.*
    - Operators: ==, !=
    - Boolean values: true, false (case-insensitive)

    Args:
        condition_expr: Condition expression string (e.g., "org.payment_required == true")
        facts: Facts dictionary (may contain facts["org"] or facts["context"]["org"])
        slots: Slots dictionary
        session_state: Optional session state (may contain session_state["org"])

    Returns:
        True if condition is satisfied, False otherwise.
        Missing keys evaluate as None (falsy).
    """
    try:
        # Parse expression: "namespace.key operator value"
        parts = condition_expr.strip().split()
        if len(parts) != 3:
            logger.debug(
                f"[CAPABILITY_EVAL] Invalid condition expression format: {condition_expr}"
            )
            return False

        left_side = parts[0]
        operator = parts[1]
        right_side = parts[2]

        # Extract namespace and key
        if "." not in left_side:
            logger.debug(
                f"[CAPABILITY_EVAL] Condition must use namespace.key format: {condition_expr}"
            )
            return False

        namespace, key = left_side.split(".", 1)

        # Get value from appropriate namespace
        if namespace == "org":
            # Read org data from facts["org"] (preferred) or session_state["org"] (fallback)
            org_data = None
            if isinstance(facts, dict):
                org_data = facts.get("org")
                # Also check facts["context"]["org"] as fallback
                if org_data is None:
                    context = facts.get("context", {})
                    if isinstance(context, dict):
                        org_data = context.get("org")
            # Fallback to session_state if not in facts
            if org_data is None and session_state:
                org_data = session_state.get("org")
            # Extract value from org_data dict
            if isinstance(org_data, dict):
                value = org_data.get(key)
            else:
                value = None
        elif namespace == "facts":
            value = facts.get(key) if isinstance(facts, dict) else None
        elif namespace == "slots":
            value = slots.get(key) if isinstance(slots, dict) else None
        else:
            logger.debug(
                f"[CAPABILITY_EVAL] Unknown namespace in condition: {namespace}"
            )
            return False

        # Parse right side value
        # Handle boolean strings (true/false)
        if right_side.lower() == "true":
            expected_value = True
        elif right_side.lower() == "false":
            expected_value = False
        else:
            # Try to parse as number or use as string
            try:
                expected_value = float(right_side)
                # If value is also numeric, compare as numbers
                if isinstance(value, (int, float)):
                    value = float(value)
            except ValueError:
                expected_value = right_side

        # Evaluate operator
        if operator == "==":
            return value == expected_value
        elif operator == "!=":
            return value != expected_value
        else:
            logger.debug(
                f"[CAPABILITY_EVAL] Unsupported operator in condition: {operator}"
            )
            return False

    except Exception as e:
        logger.debug(
            f"[CAPABILITY_EVAL] Error evaluating condition '{condition_expr}': {e}"
        )
        return False


def _evaluate_capability_blocking(
    intent_name: str,
    next_action: Optional[str],
    effective_slots: Dict[str, Any],
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Evaluate capability blocking constraints from YAML configuration.

    This function checks if any capability blocks the next execution step.
    It implements the conceptual EVALUATE_CAPABILITIES planning gate declaratively.

    This is a planning gate that occurs:
    - AFTER slots are complete (missing_slots == [])
    - AFTER booking hold exists (booking_id present)
    - BEFORE executing blocked step

    Rules are loaded from capabilities.yaml:
    - Iterates through all capabilities
    - Checks applies_to.intent matches intent_name
    - Checks next_action is in blocks list
    - Evaluates when.all (all conditions must be true for blocking to apply)

    Constraints:
    - NEVER runs during slot filling (missing_slots check happens before this)
    - ONLY runs after booking creation (booking_id must exist)
    - Missing slots always take precedence over capabilities

    Args:
        intent_name: Current intent name
        next_action: Next execution step that would be executed (from policy selection)
        effective_slots: Collected slots
        luma_response: Luma API response (contains facts, context)
        session_state: Optional session state

    Returns:
        Capability name if blocking is detected, None otherwise.
        Returns first matching capability if multiple match.
    """
    # GUARDRAIL 1: Never evaluate capabilities during slot filling
    # This check is defensive - missing_slots should already be checked before calling this
    missing_slots = _extract_missing_slots(luma_response)
    if missing_slots:
        logger.debug(
            f"[CAPABILITY_EVAL] Skipping capability evaluation: missing_slots={missing_slots} "
            f"(slot filling takes precedence)"
        )
        return None

    # GUARDRAIL 2: Only evaluate after booking creation
    # Capabilities require booking_id to exist
    booking_id = effective_slots.get("booking_id")
    if not booking_id:
        logger.debug(
            f"[CAPABILITY_EVAL] Skipping capability evaluation: booking_id not present "
            f"(capabilities only apply after booking creation)"
        )
        return None

    # Load capability policies from YAML
    policies = load_capability_policies()
    capabilities = policies.get("capabilities", {})

    if not capabilities:
        # No capabilities configured - no blocking
        logger.debug(
            f"[CAPABILITY_EVAL] No capability policies loaded (empty config or file missing)"
        )
        return None

    # Extract facts for condition evaluation
    # CRITICAL: Read facts from durable session_state, not from luma_response
    # Facts are a first-class, durable part of session state (same status as slots)
    # This ensures capability facts (e.g., payment_satisfied) persist across turns
    facts = {}
    if session_state and isinstance(session_state, dict):
        facts = session_state.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}

    # Merge with luma_response facts for this turn (new facts override old)
    # This allows Luma to provide facts (e.g., org data) that are merged with session facts
    luma_facts = luma_response.get("facts", {})
    if isinstance(luma_facts, dict):
        facts = {**facts, **luma_facts}

    # Ensure facts is a dict (defensive)
    if not isinstance(facts, dict):
        facts = {}

    # Iterate through capabilities and check if any block the next action
    for capability_name, capability_config in capabilities.items():
        if not isinstance(capability_config, dict):
            continue

        # Check applies_to.intent matches
        applies_to = capability_config.get("applies_to", {})
        if not isinstance(applies_to, dict):
            continue

        required_intent = applies_to.get("intent")
        if required_intent and required_intent != intent_name:
            logger.debug(
                f"[CAPABILITY_EVAL] Capability '{capability_name}' does not apply to intent '{intent_name}'"
            )
            continue

        # Check if next_action is in blocks list
        blocks = capability_config.get("blocks", [])
        if not isinstance(blocks, list):
            continue

        if next_action not in blocks:
            logger.debug(
                f"[CAPABILITY_EVAL] Capability '{capability_name}' does not block action '{next_action}'"
            )
            continue

        # Evaluate when.all (all conditions must be true for blocking to apply)
        # "when" defines the condition that must be satisfied to proceed
        # If "when" is true, the capability blocks the step
        when_condition = capability_config.get("when", {})
        if not isinstance(when_condition, dict):
            continue

        when_all = when_condition.get("all", [])
        if not isinstance(when_all, list):
            continue

        # Evaluate all conditions in "when"
        all_conditions_met = True
        for condition_expr in when_all:
            if not isinstance(condition_expr, str):
                all_conditions_met = False
                break

            if not _evaluate_condition(
                condition_expr, facts, effective_slots, session_state
            ):
                all_conditions_met = False
                break

        # If all "when" conditions are met, this capability blocks the action
        if all_conditions_met:
            logger.info(
                f"[CAPABILITY_EVAL] Capability '{capability_name}' blocking detected: "
                f"intent={intent_name}, next_action={next_action}"
            )
            return capability_name

    logger.debug(
        f"[CAPABILITY_EVAL] No capability blocking: "
        f"intent={intent_name}, next_action={next_action}"
    )
    return None


def _enforce_committing_step_invariants(
    intent_name: str,
    selected_step: Dict[str, Any],
    effective_slots: Dict[str, Any],
    flags: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
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
        slot_name
        for slot_name, slot_value in effective_slots.items()
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
        elif requirement == "booking_hold_created":
            # booking_hold_created means booking_id must be present (created by CREATE_BOOKING_HOLD)
            assert (
                "booking_id" in effective_slots
                and effective_slots.get("booking_id") is not None
            ), (
                f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
                f"Requirement 'booking_hold_created' not satisfied (booking_id not in slots or None)"
            )
        elif requirement == "confirmation_state_confirmed":
            assert confirmation_state == "confirmed", (
                f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
                f"Requirement 'confirmation_state_confirmed' not satisfied (confirmation_state={confirmation_state})"
            )
        elif requirement == "booking_id_resolved":
            # booking_id_resolved means booking_id must be present and non-None
            assert (
                "booking_id" in effective_slots
                and effective_slots.get("booking_id") is not None
            ), (
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
    session_state: Optional[Dict[str, Any]] = None,
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
        - status: READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION, or AWAITING_CAPABILITY
        - allowed_actions: List of allowed action names
        - blocked_actions: List of blocked action names
        - awaiting: USER_CONFIRMATION, CAPABILITY, or null
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

    # Soft prioritization of awaiting_slot (optional, backward compatible)
    # Read awaiting_slot from session_state if present
    if session_state and isinstance(session_state, dict):
        awaiting_slot = session_state.get("awaiting_slot")
        # If awaiting_slot exists AND is in missing_slots, move it to index 0
        if awaiting_slot is not None and awaiting_slot in missing_slots:
            # Remove awaiting_slot from its current position
            missing_slots = [s for s in missing_slots if s != awaiting_slot]
            # Insert at index 0, preserving the rest of the order
            missing_slots.insert(0, awaiting_slot)
            logger.debug(
                f"[AWAITING_SLOT] Prioritized slot '{awaiting_slot}' in missing_slots"
            )

    # Determine status
    needs_clarification = luma_response.get("needs_clarification", False)
    booking = luma_response.get("booking", {})
    confirmation_state = (
        booking.get("confirmation_state") if isinstance(booking, dict) else None
    )

    # Extract active_capability from multiple sources (preserve if already set)
    # Priority: 1) existing plan in luma_response, 2) session_state, 3) facts/context
    # This ensures capability gating's active_capability is preserved if build_decision_plan() is called after gating
    active_capability = None

    # FIRST: Check if luma_response contains an existing plan with active_capability
    # This preserves active_capability set by capability gating (decision["plan"]["active_capability"])
    existing_plan = luma_response.get("plan")
    if isinstance(existing_plan, dict) and existing_plan.get("active_capability"):
        active_capability = existing_plan.get("active_capability")
        logger.debug(
            f"[BUILD_PLAN] Preserving active_capability from existing plan: {active_capability}"
        )

    # SECOND: Check session_state (if not already found)
    if not active_capability and session_state:
        active_capability = session_state.get("active_capability")

    # THIRD: Check facts/context if not in session
    if not active_capability:
        facts = luma_response.get("facts", {})
        if isinstance(facts, dict):
            active_capability = facts.get("active_capability")
        # Check context as fallback
        if not active_capability:
            context = luma_response.get("context", {})
            if isinstance(context, dict):
                active_capability = context.get("active_capability")

    # STEP 2: Evaluate capability blocking (conceptual EVALUATE_CAPABILITIES step)
    # This runs BEFORE status determination to check if capabilities block the next step
    # Only evaluates if:
    # - No missing slots (slot filling takes precedence)
    # - Booking exists (booking_id present)
    # - Next action would be a blocked step
    evaluated_capability = None
    if not missing_slots and not needs_clarification and intent_name != "UNKNOWN":
        # Get effective slots for evaluation
        effective_slots = luma_response.get("_effective_collected_slots")
        if effective_slots is None:
            effective_slots = luma_response.get("slots", {})

        # Determine next action from policy (if not already determined)
        next_action = None
        if intent_name:
            from core.policy.intent_policy import select_next_execution_step

            flags = {
                "availability_resolved": availability_resolved,
                "confirmation_state": confirmation_state,
                "booking_hold_created": bool(effective_slots.get("booking_id")),
            }
            selected_step = select_next_execution_step(
                intent_name, effective_slots, flags
            )
            if selected_step:
                next_action = selected_step.get("action")

        # Evaluate capability blocking
        evaluated_capability = _evaluate_capability_blocking(
            intent_name=intent_name,
            next_action=next_action,
            effective_slots=effective_slots,
            luma_response=luma_response,
            session_state=session_state,
        )

        # If capability blocking detected, set active_capability
        if evaluated_capability and not active_capability:
            active_capability = evaluated_capability
            logger.info(
                f"[CAPABILITY_EVAL] Capability blocking activated: "
                f"active_capability={active_capability}, intent={intent_name}, next_action={next_action}"
            )

    logger.debug(
        f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} "
        f"needs_clarification={needs_clarification} confirmation_state={confirmation_state} "
        f"active_capability={active_capability}"
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
    elif active_capability:
        status = "AWAITING_CAPABILITY"
        logger.debug(
            f"[BUILD_PLAN] Setting status=AWAITING_CAPABILITY because active_capability={active_capability}"
        )
    else:
        status = "READY"
        logger.debug(
            f"[BUILD_PLAN] Setting status=READY "
            f"(no missing slots, no clarification needed, no pending confirmation, no active capability)"
        )

    # Determine allowed and blocked actions
    allowed_actions: List[str] = []
    blocked_actions: List[str] = []

    # Get executable_actions from planner (ONLY source of truth for partial execution)
    executable_actions = []
    if intent_name:
        from core.planning.policy.action_policy import load_planning_policy, plan_intent

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
    if confirmation_state == "pending":
        awaiting = "USER_CONFIRMATION"
    elif active_capability:
        awaiting = "CAPABILITY"
    else:
        awaiting = None

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
            "confirmation_state": confirmation_state,
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
            elif action in (
                "CONFIRM_APPOINTMENT",
                "FINALIZE_RESERVATION",
                "APPLY_MODIFICATION",
                "CONFIRM_CANCELLATION",
            ):
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

    plan = {
        "status": status,
        "stage": stage,
        "action": action,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "executable_actions": executable_actions,
    }

    # CRITICAL: Preserve active_capability if it was already set (from capability gating or previous planning)
    # active_capability is a first-class Plan field with same durability as status, stage, action
    if active_capability:
        plan["active_capability"] = active_capability

    # DEFENSIVE ASSERTION: If status is AWAITING_CAPABILITY, active_capability MUST be non-null
    # This enforces the invariant that AWAITING_CAPABILITY always has an associated capability
    if plan["status"] == "AWAITING_CAPABILITY":
        assert plan.get("active_capability"), (
            f"INVARIANT VIOLATION: plan.status='AWAITING_CAPABILITY' but active_capability is missing. "
            f"This indicates capability gating set status but did not set active_capability, "
            f"or build_decision_plan() dropped active_capability. "
            f"intent={intent_name}, status={plan['status']}, active_capability={plan.get('active_capability')}"
        )
        logger.info(
            f"[BUILD_PLAN] Verified invariant: AWAITING_CAPABILITY has active_capability={plan.get('active_capability')}"
        )

    return plan
