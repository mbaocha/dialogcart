"""
Plan Builder

Builds decision plans from Luma responses using intent_policy.yaml as the single source of truth.

This module is pure and side-effect free:
- No external API calls
- No state mutation
- Deterministic interpretation of Luma responses

Responsibilities:
- Building decision plans with status, allowed_actions, blocked_actions, awaiting
- plan.action is the policy-selected execution step only (nullable when nothing runs)
- plan.stage is derived from execution action or status (presentation phase)
- All intent-specific logic comes from intent_policy.yaml via select_next_execution_step
"""

import logging
from typing import Any, Dict, List, Optional

from core.config.capabilities_loader import load_capability_policies

logger = logging.getLogger(__name__)


def _resolve_confirmation_state(
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve confirmation_state from response booking or persisted session."""
    from core.session.confirmation_gate import get_confirmation_state

    confirmation_state = get_confirmation_state(luma_response)
    if confirmation_state is not None or not session_state:
        return confirmation_state
    return get_confirmation_state(session_state)


def _ensure_booking_on_response(luma_response: Dict[str, Any]) -> Dict[str, Any]:
    booking = luma_response.get("booking")
    if not isinstance(booking, dict):
        booking = {}
        luma_response["booking"] = booking
    return booking


def _exact_time_match_presenting_confirmation(
    time_match_outcome: Optional[str],
    *,
    user_confirmation_satisfied: bool,
) -> bool:
    """TIME_MATCH_EXACT blocks commit only while the confirm prompt is outstanding."""
    from core.planning.time_resolution import TIME_MATCH_EXACT

    return (
        time_match_outcome == TIME_MATCH_EXACT and not user_confirmation_satisfied
    )


def _maybe_enter_booking_confirmation_pending(
    intent_name: Optional[str],
    luma_response: Dict[str, Any],
    *,
    missing_slots: List[str],
    needs_clarification: bool,
    availability_resolved: bool,
    confirmation_state: Optional[str],
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """When CREATE_APPOINTMENT is commit-ready, require explicit confirmation first."""
    if intent_name != "CREATE_APPOINTMENT":
        return confirmation_state

    from core.session.confirmation_gate import has_committed_create_appointment

    effective_slots = luma_response.get("_effective_collected_slots")
    if effective_slots is None:
        effective_slots = luma_response.get("slots", {})
    session_slots = (
        session_state.get("slots") if isinstance(session_state, dict) else None
    )
    if has_committed_create_appointment(effective_slots) or has_committed_create_appointment(
        session_slots
    ):
        return confirmation_state

    if confirmation_state is not None:
        return confirmation_state
    if missing_slots or needs_clarification or not availability_resolved:
        return confirmation_state

    from core.planning.temporal_proposal import has_bound_booking_datetime

    bound_datetime = has_bound_booking_datetime(
        effective_slots, session_state, luma_response
    )
    if not bound_datetime:
        return confirmation_state

    from core.session.confirmation_gate import set_confirmation_state

    previous_state = confirmation_state
    _ensure_booking_on_response(luma_response)
    set_confirmation_state(luma_response, "pending")
    try:
        from core.tracing.confirmation import emit_confirmation_enter_pending_trace

        emit_confirmation_enter_pending_trace(
            entered=True,
            previous_state=previous_state,
            missing_slots=missing_slots,
            availability_resolved=availability_resolved,
            time_selection_ready=bound_datetime,
        )
    except ImportError:
        pass
    logger.info(
        "[BOOKING_CONFIRMATION] CREATE_APPOINTMENT commit-ready — "
        "setting confirmation_state=pending"
    )
    return "pending"


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
                isinstance(issue_value, dict) and issue_value.get(
                    "status") == "missing"
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
    from core.policy.intent_policy import _evaluate_step_requirement

    for requirement in requires:
        satisfied = _evaluate_step_requirement(requirement, flags=flags)
        assert satisfied, (
            f"Invariant violation: Cannot execute committing step {action} for {intent_name}. "
            f"Requirement {requirement!r} not satisfied (flags={flags})"
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


def _build_policy_execution_flags(
    *,
    intent_name: str,
    effective_slots: Dict[str, Any],
    luma_response: Dict[str, Any],
    session_state: Optional[Dict[str, Any]],
    missing_slots: List[str],
    needs_clarification: bool,
    availability_resolved: bool,
    confirmation_state: Optional[str],
    organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    from core.planning.facts import build_policy_execution_flags

    return build_policy_execution_flags(
        intent_name=intent_name,
        slots=effective_slots,
        session_state=session_state,
        luma_response=luma_response,
        missing_slots=missing_slots,
        needs_clarification=needs_clarification,
        availability_resolved=availability_resolved,
        confirmation_state=confirmation_state,
        organization_id=organization_id,
    )


def _stage_for_execution_action(
    action: Optional[str], selected_step: Dict[str, Any]
) -> Optional[str]:
    """Map a policy-selected execution action to a presentation stage."""
    if action == "FETCH_BOOKING":
        return "IDENTIFY"
    if action == "SEARCH_AVAILABILITY":
        return "AVAILABILITY"
    if action in (
        "CONFIRM_APPOINTMENT",
        "FINALIZE_RESERVATION",
        "APPLY_MODIFICATION",
        "CONFIRM_CANCELLATION",
    ):
        return "CONFIRM"
    mode = selected_step.get("mode", "exploratory")
    return "AVAILABILITY" if mode == "exploratory" else "CONFIRM"


def _derive_stage_from_status(status: str) -> Optional[str]:
    """Derive presentation stage when no execution action is selected."""
    if status == "AWAITING_CONFIRMATION":
        return "CONFIRM"
    if status == "NEEDS_CLARIFICATION":
        return "AVAILABILITY"
    return None


def build_decision_plan(
    intent_name: str,
    luma_response: Dict[str, Any],
    domain: str,
    availability_resolved: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
    organization_id: Optional[int] = None,
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
        from core.session.durable_intents import is_durable_intent

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
        "[PLAN_BUILDER_INPUT] intent=%s missing=%s",
        intent_name,
        missing_slots,
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
    confirmation_state = _resolve_confirmation_state(
        luma_response, session_state)
    confirmation_state = _maybe_enter_booking_confirmation_pending(
        intent_name,
        luma_response,
        missing_slots=missing_slots,
        needs_clarification=needs_clarification,
        availability_resolved=availability_resolved,
        confirmation_state=confirmation_state,
        session_state=session_state,
    )
    booking = luma_response.get("booking", {})

    from core.planning.time_resolution import (
        TIME_MATCH_EXACT,
        TIME_MATCH_MISMATCH,
    )

    time_match_outcome = luma_response.get("time_match_outcome")
    time_resolution = luma_response.get("time_resolution")
    if not time_match_outcome and isinstance(time_resolution, dict):
        time_match_outcome = time_resolution.get("outcome")

    from core.planning.facts import derive_user_confirmation_satisfied

    user_confirmation_satisfied = derive_user_confirmation_satisfied(
        confirmation_state, luma_response
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

            flags = _build_policy_execution_flags(
                intent_name=intent_name,
                effective_slots=effective_slots,
                luma_response=luma_response,
                session_state=session_state,
                missing_slots=missing_slots,
                needs_clarification=needs_clarification,
                availability_resolved=availability_resolved,
                confirmation_state=confirmation_state,
                organization_id=organization_id,
            )
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

    # Compute executable_actions before status determination — a satisfied executable_with
    # subset allows READY even when required slots (date/time) are still missing.
    _ea_slots = luma_response.get("_effective_collected_slots")
    if _ea_slots is None:
        _ea_slots = luma_response.get("slots", {})
    executable_actions: List[str] = []
    if intent_name and intent_name != "UNKNOWN":
        from core.planning.policy.action_policy import load_planning_policy, plan_intent
        _ea_policy = load_planning_policy()
        _ea_result = plan_intent(intent_name, _ea_slots, _ea_policy)
        executable_actions = _ea_result.get("executable_actions", [])

    logger.debug(
        f"[BUILD_PLAN] intent={intent_name} missing_slots={missing_slots} "
        f"needs_clarification={needs_clarification} confirmation_state={confirmation_state} "
        f"active_capability={active_capability} executable_actions={executable_actions}"
    )

    # CRITICAL PLANNING INVARIANT: UNKNOWN intent ALWAYS requires clarification
    if intent_name == "UNKNOWN":
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because intent=UNKNOWN "
            f"(UNKNOWN always requires clarification)"
        )
    elif time_match_outcome == TIME_MATCH_MISMATCH:
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            "[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because "
            "time_match_outcome=TIME_MATCH_MISMATCH"
        )
    elif _exact_time_match_presenting_confirmation(
        time_match_outcome,
        user_confirmation_satisfied=user_confirmation_satisfied,
    ):
        status = "AWAITING_CONFIRMATION"
        logger.debug(
            "[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because "
            "time_match_outcome=TIME_MATCH_EXACT (confirmation prompt outstanding)"
        )
    elif missing_slots:
        if executable_actions:
            # executable_with subset satisfied — proceed to exploratory action;
            # missing required slots (date/time) come from availability results.
            status = "READY"
            logger.debug(
                f"[BUILD_PLAN] Setting status=READY: executable_with satisfied "
                f"(missing_slots={missing_slots}, executable_actions={executable_actions})"
            )
        else:
            status = "NEEDS_CLARIFICATION"
            logger.debug(
                f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because missing_slots={missing_slots}"
            )
    elif needs_clarification:
        status = "NEEDS_CLARIFICATION"
        logger.debug(
            f"[BUILD_PLAN] Setting status=NEEDS_CLARIFICATION because needs_clarification=True"
        )
    elif confirmation_state == "pending" and not user_confirmation_satisfied:
        status = "AWAITING_CONFIRMATION"
        logger.debug(
            "[BUILD_PLAN] Setting status=AWAITING_CONFIRMATION because "
            "confirmation is outstanding (pending, not yet accepted)"
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

    effective_slots = luma_response.get("_effective_collected_slots")
    if effective_slots is None:
        effective_slots = luma_response.get("slots", {})

    # executable_actions already computed above before status determination

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
            from core.planning.temporal_proposal import has_bound_booking_datetime

            datetime_bound = has_bound_booking_datetime(
                effective_slots, session_state, luma_response
            )
            needs_bound_datetime = intent_name == "CREATE_APPOINTMENT" and not datetime_bound
            needs_user_confirmation = (
                intent_name == "CREATE_APPOINTMENT"
                and not user_confirmation_satisfied
            )
            if (
                needs_clarification
                or not availability_resolved
                or needs_bound_datetime
                or needs_user_confirmation
            ):
                # Block commit when clarification needed, availability not resolved,
                # CREATE_APPOINTMENT lacks a confirmed datetime binding, or user
                # has not confirmed the booking yet.
                blocked_actions.append(commit_action)
            else:
                # All slots filled, no clarification needed, availability resolved - allow commit
                allowed_actions.append(commit_action)

    # Deduplicate
    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))

    # Determine awaiting
    if time_match_outcome == TIME_MATCH_MISMATCH:
        awaiting = "TIME_SELECTION"
    elif confirmation_state == "pending" and not user_confirmation_satisfied:
        awaiting = "USER_CONFIRMATION"
    elif active_capability:
        awaiting = "CAPABILITY"
    else:
        awaiting = None

    # Execution action: policy-selected only (nullable when nothing should execute).
    stage = None
    action = None
    action_branch = None
    flags: Dict[str, Any] = {}

    if intent_name and intent_name != "UNKNOWN":
        from core.policy.intent_policy import select_next_execution_step

        flags = _build_policy_execution_flags(
            intent_name=intent_name,
            effective_slots=effective_slots,
            luma_response=luma_response,
            session_state=session_state,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            availability_resolved=availability_resolved,
            confirmation_state=confirmation_state,
            organization_id=organization_id,
        )

        selected_step = select_next_execution_step(
            intent_name, effective_slots, flags)

        if selected_step:
            action = selected_step.get("action")
            action_branch = "policy"

            _enforce_committing_step_invariants(
                intent_name, selected_step, effective_slots, flags, session_state
            )

            stage = _stage_for_execution_action(action, selected_step)

            logger.info(
                f"[PLAN_SELECTION] Policy-driven selection: intent={intent_name}, "
                f"missing_slots={missing_slots}, availability_resolved={availability_resolved}, "
                f"selected_action={action}, selected_stage={stage}, "
                f"rule=select_next_execution_step"
            )
        else:
            action_branch = "no_execution_step"
            logger.debug(
                f"[PLAN_SELECTION] No execution step for intent={intent_name} "
                f"(status={status}, awaiting={awaiting})"
            )

    if time_match_outcome == TIME_MATCH_MISMATCH:
        action = None
        action_branch = "time_match_mismatch"
        stage = "AVAILABILITY"
        logger.info(
            "[PLAN_SELECTION] Forcing action=None for TIME_MATCH_MISMATCH "
            "(conversational response required)"
        )
    elif _exact_time_match_presenting_confirmation(
        time_match_outcome,
        user_confirmation_satisfied=user_confirmation_satisfied,
    ):
        action = None
        action_branch = "time_match_exact"
        stage = "CONFIRM"
        logger.info(
            "[PLAN_SELECTION] Forcing action=None for TIME_MATCH_EXACT "
            "(confirmation prompt outstanding — user has not accepted yet)"
        )

    if stage is None:
        stage = _derive_stage_from_status(status)

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
        "missing_slots": missing_slots,
    }
    if time_match_outcome:
        plan["time_match_outcome"] = time_match_outcome
    if isinstance(time_resolution, dict):
        plan["time_resolution"] = time_resolution

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

    stage_from_action = action_branch == "policy"
    try:
        from core.tracing.planner import emit_planner_decision_graph_from_plan_builder

        emit_planner_decision_graph_from_plan_builder(
            intent_name=intent_name,
            luma_response=luma_response,
            plan=plan,
            flags=flags,
            effective_slots=effective_slots,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            confirmation_state=confirmation_state,
            active_capability=active_capability,
            executable_actions=executable_actions,
            availability_resolved=availability_resolved,
            stage_from_action=stage_from_action,
        )
    except ImportError:
        pass

    return plan
