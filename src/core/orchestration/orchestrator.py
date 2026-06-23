"""
Orchestration Layer - Planning Only

Control flow and decision making for conversation handling.

This module orchestrates the conversation flow by:
- Handling message entry
- Deriving org_id and domain
- Constructing catalog and tenant_context
- Calling Luma API
- Validating contracts
- Planning outcomes based on plan status (NEEDS_CLARIFICATION, AWAITING_CONFIRMATION, READY)

Core NEVER executes actions - it only plans and returns:
{
  intent,
  slots,
  missing_slots,
  executable_actions,
  dialog_instruction?
}

Execution layer consumes executable_actions only.

Constraints:
- No copy, no templates, no WhatsApp formatting
- Must only return structured planning outcomes
- NO execution logic - execution is handled by separate layer
"""

import copy
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.errors import (
    ContractViolation,
    UnsupportedIntentError,
    UpstreamError,
)
from core.orchestration.nlu import (
    LumaClient,
    assert_luma_contract,
    build_clarify_outcome_from_reason,
    process_luma_response,
)
from core.orchestration.persistence.durable_intents import is_durable_intent
from core.rendering import (
    render,
    render_clarification,
    render_outcome,
    render_system,
)
from core.rendering.mapper.clarification_mapper import derive_clarification_reason
from core.routing.workflows import get_workflow

logger = logging.getLogger(__name__)
# Dedicated turn-level logger (clean, minimal logs - ONE log per section)
turn_logger = logging.getLogger("core.turn_log")
turn_logger.setLevel(logging.INFO)

# IMMEDIATELY disable noisy logs - set to ERROR ONLY
logging.getLogger("core.orchestration").setLevel(logging.ERROR)
logging.getLogger("core.planning").setLevel(logging.ERROR)
logging.getLogger("core.slot").setLevel(logging.ERROR)
logging.getLogger("core.execution").setLevel(logging.ERROR)
logging.getLogger("core.nlu").setLevel(logging.ERROR)
logging.getLogger("core.session_merge").setLevel(logging.ERROR)


def _build_planning_outcome(
    intent_name: str,
    slots: Dict[str, Any],
    missing_slots: List[str],
    executable_actions: List[str],
    dialog_instruction: Optional[Dict[str, Any]] = None,
    status: str = "READY",
) -> Dict[str, Any]:
    """
    Build planning-only outcome structure.

    Core NEVER executes - only returns planning information.

    Args:
        intent_name: Intent name
        slots: Collected slots
        missing_slots: Missing required slots
        executable_actions: Actions that can be executed with current slots
        dialog_instruction: Optional dialog instruction (if status is NEEDS_CLARIFICATION)
        status: Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)

    Returns:
        Planning outcome dictionary matching core contract
    """
    outcome = {
        "intent": intent_name,
        "slots": slots,
        "missing_slots": missing_slots,
        "executable_actions": executable_actions,
    }

    if dialog_instruction:
        outcome["dialog_instruction"] = dialog_instruction

    return outcome


def build_outcome_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build outcome dictionary from decision object.

    Unifies outcome construction across all return paths by extracting
    all required fields from the decision object which contains the
    authoritative planning state.

    Args:
        decision: Decision dictionary from process_luma_response containing:
            - intent_name: Intent name
            - plan: Plan dictionary with status, stage, action, etc.
            - facts: Facts dictionary with slots, missing_slots, context

    Returns:
        Outcome dictionary with all required fields:
            - intent_name: Intent name
            - status: Planning status
            - plan: Plan object with stage and action
            - slots: Collected slots
            - missing_slots: Missing required slots
            - blocked_actions: Blocked actions list
            - allowed_actions: Allowed actions list
            - facts: Facts container
    """
    if not decision or not isinstance(decision, dict):
        # Fallback for invalid decision
        return {
            "intent_name": "",
            "status": "NEEDS_CLARIFICATION",
            "plan": {"status": "NEEDS_CLARIFICATION", "stage": None, "action": None},
            "slots": {},
            "missing_slots": [],
            "blocked_actions": [],
            "allowed_actions": [],
            "facts": {},
        }

    plan = decision.get("plan", {})
    facts = decision.get("facts", {})
    if not isinstance(facts, dict):
        facts = {}

    # Extract slots and missing_slots from facts
    slots = facts.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    missing_slots = facts.get("missing_slots", [])
    if not isinstance(missing_slots, list):
        missing_slots = []

    # Build plan object from decision.plan (authoritative source)
    plan_obj = {
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        "stage": plan.get("stage"),
        "action": plan.get("action"),
    }

    outcome = {
        "intent_name": decision.get("intent_name", ""),
        # Top-level status for compatibility
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        # Top-level stage (always from decision.plan)
        "stage": plan.get("stage"),
        # Top-level action (always from decision.plan)
        "action": plan.get("action"),
        "plan": plan_obj,  # Complete plan object with status, stage, action
        "slots": slots,
        "missing_slots": missing_slots,
        "blocked_actions": plan.get("blocked_actions", []),
        "allowed_actions": plan.get("allowed_actions", []),
        "awaiting": plan.get("awaiting"),
        "facts": facts,
    }

    # Add active_capability if present in plan
    if plan.get("active_capability"):
        outcome["active_capability"] = plan.get("active_capability")

    return outcome


def _render_clarification_text(
    decision: Dict[str, Any], slots: Dict[str, Any]
) -> Optional[str]:
    """
    Render clarification text from decision and slots (best-effort).

    Args:
        decision: Decision/plan dictionary with status and missing_slots (optionally facts)
        slots: Dictionary of slot values for template interpolation

    Returns:
        Rendered text string if rendering succeeds, None otherwise.
        Returns None if status is not NEEDS_CLARIFICATION or if rendering fails.
    """
    try:
        # Only render for NEEDS_CLARIFICATION status
        status = decision.get("status")
        if status != "NEEDS_CLARIFICATION":
            return None

        # Derive clarification reason from decision
        reason = derive_clarification_reason(decision)
        if not reason:
            # No reason derived (shouldn't happen for NEEDS_CLARIFICATION, but be safe)
            return None

        # Extract slot_attempts if available in decision.facts
        attempt_count = 0
        facts = decision.get("facts", {})
        if isinstance(facts, dict):
            slot_attempts = facts.get("slot_attempts", {})
            missing_slots = decision.get("missing_slots", [])
            first_missing = (
                missing_slots[0]
                if isinstance(missing_slots, list) and missing_slots
                else None
            )
            if first_missing and isinstance(slot_attempts, dict):
                attempt_count = slot_attempts.get(first_missing, 0)

        # Render with slots and attempt_count
        render_spec = render_clarification(reason, slots, attempt_count)
        return render_spec.text
    except Exception as e:
        # Best-effort: log error and return None
        logger.warning(
            f"Failed to render clarification text: {e}. "
            f"Rendering is best-effort and will be omitted."
        )
        return None


def _inject_rendering_text(
    result: Dict[str, Any],
    decision: Dict[str, Any],
    session_state: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Inject rendered text into top-level response for clarification states.

    This is a pure post-processing step that:
    - Detects clarification state from decision
    - Attaches session_state to decision for rendering
    - Calls rendering.render(decision)
    - Injects returned text into top-level response as 'text'
    - Falls back to generic template if rendering returns None

    Args:
        result: Response dictionary to modify (mutated in place)
        decision: Decision dictionary with plan and facts
        session_state: Optional session state dictionary to attach to decision
    """
    try:
        # Attach session_state to decision for rendering (Phase 2: acknowledgement rendering)
        decision["_session"] = session_state or {}

        # Merge slot_attempts from session_state into decision for rendering
        if session_state and isinstance(session_state, dict):
            slot_attempts = session_state.get("slot_attempts")
            if isinstance(slot_attempts, dict):
                # Prefer top-level slot_attempts in decision (renderer checks this first)
                decision["slot_attempts"] = slot_attempts
                # Also ensure it's in facts as fallback
                facts = decision.get("facts", {})
                if isinstance(facts, dict):
                    facts["slot_attempts"] = slot_attempts

        rendered_text = render(decision)
        if rendered_text:
            result["text"] = rendered_text
    except Exception as e:
        # Best-effort: log error and continue without text
        logger.warning(
            f"Failed to render clarification text: {e}. "
            f"Rendering is best-effort and will be omitted."
        )


def _inject_outcome_text(
    result: Dict[str, Any], decision: Optional[Dict[str, Any]], outcome: Dict[str, Any]
) -> None:
    """
    Inject rendered outcome text into result for EXECUTED or FAILED outcomes.

    This is a pure post-processing step that:
    - Only triggers for outcome.status in ("EXECUTED", "FAILED")
    - Derives template key from decision + outcome
    - Renders text using outcome templates
    - Injects result["text"] = rendered_text

    Args:
        result: Response dictionary to modify (mutated in place)
        decision: Decision dictionary (optional, for intent extraction)
        outcome: Outcome dictionary with status, intent_name, etc.
    """
    # Only render for EXECUTED or FAILED status
    outcome_status = outcome.get("status")
    if outcome_status not in ("EXECUTED", "FAILED"):
        return

    try:
        # Render outcome text
        render_spec = render_outcome(decision, outcome)
        if render_spec and render_spec.text:
            result["text"] = render_spec.text
    except Exception as e:
        # Best-effort: log error and continue without text
        logger.debug(
            f"Failed to render outcome text: {e}. "
            f"Rendering is best-effort and will be omitted."
        )


def _inject_system_text(result: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """
    Inject rendered system text into result for greeting/welcome intents.

    This is a pure post-processing step that:
    - Only triggers for GREETING or WELCOME intents
    - Renders text using system templates
    - Injects result["text"] = rendered_text

    Must NOT override:
    - Clarification rendering
    - Capability rendering
    - Outcome rendering

    Args:
        result: Response dictionary to modify (mutated in place)
        decision: Decision dictionary with intent_name
    """
    try:
        intent_name = decision.get("intent_name")
        render_spec = render_system(intent_name)
        if render_spec and render_spec.text:
            result["text"] = render_spec.text
    except Exception:
        # Best-effort: silently fail (don't break flow)
        pass


def _handle_non_core_intent(
    luma_response: Dict[str, Any], decision: Dict[str, Any], user_id: str
) -> Dict[str, Any]:
    """
    Handle non-core intents by passing them through as non-orchestrated signals.

    Non-core intents (e.g., PAYMENT, CONFIRM_ACTION, BOOKING_INQUIRY) are not
    orchestrated by core but are passed through to preserve conversational continuity.
    This enables workflow extensions to handle these intents in future steps.

    This function wraps the Luma response and produces a valid outcome without
    plan generation, execution, or confirmation gating.

    Args:
        luma_response: Original Luma API response
        decision: Decision plan from process_luma_response
        user_id: User identifier for logging

    Returns:
        Outcome dictionary with:
        - success: True (not an error, just non-orchestrated)
        - outcome.status: "NON_CORE_INTENT"
        - outcome.intent_name: The non-core intent name
        - outcome.facts: Facts container with slots, missing_slots, and context
    """
    intent_name = decision.get("intent_name", "")
    facts = decision.get("facts", {})

    # Ensure facts structure includes slots, missing_slots, and context
    # Facts from decision should already have this structure, but ensure completeness
    if not facts:
        facts = {}

    # Preserve slots from Luma response (decision.facts may already have this)
    slots = luma_response.get("slots", {})
    if slots:
        facts.setdefault("slots", slots)

    # Preserve missing_slots from Luma response (computed by merge from intent contract)
    # ARCHITECTURAL INVARIANT: missing_slots is computed exactly once per turn in session merge
    # missing_slots = [] is VALID and means all required slots are satisfied
    missing_slots = luma_response.get("missing_slots")
    if missing_slots is not None and isinstance(missing_slots, list):
        # Use merged missing_slots (even if []) - this is authoritative
        facts.setdefault("missing_slots", missing_slots)
    else:
        # This should never happen if merge ran correctly
        logger.error(
            f"[MISSING_SLOTS] VIOLATION: missing_slots is None or not a list in non-core intent! "
            f"user_id={user_id}, missing_slots={missing_slots}, luma_response_keys={list(luma_response.keys())}"
        )
        # Fail-safe: use empty list (but this indicates a bug)
        facts["missing_slots"] = []

    # Preserve context from Luma response
    context = luma_response.get("context")
    if context:
        facts.setdefault("context", context)
    elif "context" not in facts:
        facts["context"] = {}

    logger.info(
        f"Passing through non-core intent '{intent_name}' for user {user_id} "
        f"(not orchestrated by core)"
    )

    return {
        "success": True,
        "outcome": {
            "status": "NON_CORE_INTENT",
            "intent_name": intent_name,
            "facts": facts,
        },
    }


def _get_org_id_from_env() -> int:
    """Return organization_id from ORG_ID env var with safe default."""
    value = os.getenv("ORG_ID", "1")
    try:
        org_id = int(value)
        if org_id <= 0:
            raise ValueError("ORG_ID must be positive")
        return org_id
    except Exception:  # noqa: BLE001
        logger.warning("Invalid ORG_ID env value '%s', defaulting to 1", value)
        return 1


def _invoke_workflow_after_execute(
    intent_name: str, outcome: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Invoke workflow after_execute hook if a workflow is registered for the intent.

    This function looks up the workflow for the given intent and calls its
    after_execute method if it exists. If no workflow is registered or the
    workflow doesn't implement after_execute, the outcome is returned unchanged.

    Args:
        intent_name: Intent name to look up workflow for
        outcome: Outcome dictionary to pass to workflow

    Returns:
        Modified outcome dictionary (or original if no workflow or no changes)
    """
    try:
        workflow = get_workflow(intent_name)
        if workflow and hasattr(workflow, "after_execute"):
            try:
                return workflow.after_execute(outcome)
            except Exception as e:
                logger.warning(
                    f"Workflow after_execute hook failed for intent '{intent_name}': {e}. "
                    f"Returning original outcome."
                )
                return outcome
    except Exception as e:
        logger.debug(f"Error looking up workflow for intent '{intent_name}': {e}")

    return outcome


def handle_message(
    text: str,
    user_id: str,
    luma_client: Optional[LumaClient] = None,
    availability_client: Optional[Any] = None,
    organization_client: Optional[OrganizationClient] = None,
    session_store: Optional[Any] = None,
    frozen_time: Optional[datetime] = None,
    organization_id: Optional[int] = None,
    **kwargs,  # Backward-compat shim: ignore unknown infra parameters (e.g., domain, customer_id)  # noqa: ARG001
) -> Dict[str, Any]:
    """
    Canonical Core entrypoint for handling user messages.

    Note: **kwargs is a backward-compatibility shim that accepts but ignores
    unknown infrastructure parameters (e.g., domain, customer_id). These parameters
    are not used internally and are silently ignored.

    This function orchestrates the full flow:
    1. Retrieves session state (if session_store provided)
    2. Calls Luma for NLU (via plan_message)
    3. Gets planning result (via plan_message)
    4. Dispatches execution based on plan.action (if applicable)
    5. Returns execution result or planning result

    This function does NOT:
    - Perform rendering
    - Perform booking or confirmation execution
    - Access catalog or payment systems

    Args:
        text: User message text
        user_id: User identifier
        luma_client: Injected Luma client instance (creates default if None)
        availability_client: Injected availability client instance (required for SEARCH_AVAILABILITY)
        organization_client: Injected organization client instance (creates default if None)
        session_store: Optional session store with get_session(user_id) method
        frozen_time: Optional frozen time for testing
        organization_id: Optional organization ID (defaults to ORG_ID env or 1)
        **kwargs: Backward-compat shim - accepts but ignores unknown infrastructure parameters
                 (e.g., domain, customer_id). These are not used internally.

    Returns:
        Dictionary with:
        - If execution occurred: execution result (e.g., availability slots)
        - If no execution: planning result (stage, action, slots, missing_slots, etc.)
        - On error: {"success": False, "error": "...", "message": "..."}
    """
    # Import execution dispatcher
    from core.orchestration.execution.dispatcher import execute

    # LOG 1 — TURN INPUT (first line of handle_message)
    turn_logger.info(
        json.dumps(
            {"turn": "INPUT", "user_id": user_id, "text": text}, ensure_ascii=True
        )
    )

    # Get session state if session_store provided
    session_state = None
    if session_store is not None:
        try:
            if hasattr(session_store, "get_session"):
                session_state = session_store.get_session(user_id)
            elif callable(session_store):
                session_state = session_store(user_id)
        except Exception as e:
            logger.warning(f"Failed to get session for user {user_id}: {e}")

    # FALLBACK 1: Use session_state from kwargs if session_store didn't provide one
    # This allows tests to pass session_state directly (e.g., test_core_capability_payment_end_to_end)
    if session_state is None and "session_state" in kwargs:
        session_state = kwargs["session_state"]

    # FALLBACK 2: Load from default session store if both session_store and kwargs.session_state are None
    # This ensures handle_message() can always pick up persisted sessions, even when test helpers filter them out
    # This is critical for reconciliation turns where the session exists but was filtered by status checks
    if session_state is None:
        try:
            from core.orchestration.session.session_manager import get_session

            session_state = get_session(user_id)
            if session_state:
                logger.debug(
                    f"[SESSION_FALLBACK] Loaded session from default store for user_id={user_id} "
                    f"(session_store was None, kwargs.session_state was None or filtered out)"
                )
        except (ImportError, Exception) as e:
            # Session manager not available or load failed - continue without session
            logger.debug(
                f"[SESSION_FALLBACK] Could not load from default session store: {e}"
            )

    # LOG 3 — SESSION READ (immediately after session_store.get_session)
    turn_logger.info(
        json.dumps(
            {"turn": "SESSION_READ", "session": session_state},
            ensure_ascii=True,
            default=str,
        )
    )

    # INVARIANT GUARD: Payment capability requires persisted facts
    # This prevents silent regressions where payment_satisfied is lost
    if session_state and isinstance(session_state, dict):
        active_capability = session_state.get("active_capability")
        if active_capability == "payment":
            assert "facts" in session_state, (
                f"Payment capability requires persisted facts. "
                f"session_state keys: {list(session_state.keys())}, "
                f"user_id={user_id}"
            )
            facts = session_state.get("facts", {})
            assert isinstance(facts, dict), (
                f"Payment capability requires facts to be a dict. "
                f"Got type: {type(facts)}, user_id={user_id}"
            )

    # Call plan_message to get planning result
    # plan_message internally calls Luma and handle_message_legacy
    plan = plan_message(
        text=text,
        user_id=user_id,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        frozen_time=frozen_time,
        organization_id=organization_id,
    )

    # Check if planning failed
    if not plan or plan.get("error"):
        return {
            "success": False,
            "error": plan.get("error", "planning_failed"),
            "message": plan.get("message", "Planning failed"),
            "plan": plan,
        }

    # HANDLER_DELEGATED: an intent handler (e.g. RAG) will respond — bypass execution path
    if plan.get("status") == "HANDLER_DELEGATED":
        hd_outcome = {
            "status": "HANDLER_DELEGATED",
            "intent_name": plan.get("intent_name", ""),
            "active_handler": plan.get("active_handler"),
            "search_query": plan.get("search_query"),
            "slots": plan.get("slots", {}),
            "missing_slots": plan.get("missing_slots", []),
            "facts": plan.get("facts", {}),
        }
        return {"success": True, "outcome": hd_outcome, "result": hd_outcome}

    # POLICY-DRIVEN EXECUTION ELIGIBILITY
    # Execution eligibility is driven by action-level policy, not planning completeness
    # - Exploratory actions execute when their action-level required_slots are satisfied
    # - Committing actions require planning completeness (plan.status == READY)
    plan_status = plan.get("status")
    intent_name = plan.get("intent_name") or plan.get("intent")
    plan_action = plan.get("action")
    executable_actions = plan.get("executable_actions", [])
    slots = plan.get("slots", {})

    # Get execution steps from policy to check action-level requirements
    from core.policy.intent_policy import get_execution_steps

    steps = get_execution_steps(intent_name)

    # Check if the planned action or any executable action can execute
    can_execute = False
    execution_step = None

    # Check plan_action first (if set)
    if plan_action:
        for step in steps:
            if step.get("action") == plan_action:
                execution_step = step
                mode = step.get("mode", "exploratory")
                required_slots = step.get("required_slots", [])

                # Check if action-level required_slots are satisfied
                action_slots_satisfied = all(
                    slot_name in slots and slots[slot_name] is not None
                    for slot_name in required_slots
                )

                if mode == "exploratory":
                    # Exploratory actions execute when their required_slots are satisfied
                    # regardless of plan status or missing planning slots
                    can_execute = action_slots_satisfied
                else:
                    # Committing actions require planning completeness
                    can_execute = plan_status == "READY" and action_slots_satisfied
                break

    # If plan_action can't execute, check executable_actions
    if not can_execute and executable_actions:
        for action_name in executable_actions:
            for step in steps:
                if step.get("action") == action_name:
                    mode = step.get("mode", "exploratory")
                    required_slots = step.get("required_slots", [])

                    # Check if action-level required_slots are satisfied
                    action_slots_satisfied = all(
                        slot_name in slots and slots[slot_name] is not None
                        for slot_name in required_slots
                    )

                    if mode == "exploratory" and action_slots_satisfied:
                        # Found an executable exploratory action
                        execution_step = step
                        plan_action = action_name
                        can_execute = True
                        break
            if can_execute:
                break

    # Block execution if no eligible action found
    if not can_execute:
        logger.debug(
            f"Skipping execution: No eligible action found. "
            f"plan_status={plan_status}, plan_action={plan_action}, "
            f"executable_actions={executable_actions}, missing_slots={plan.get('missing_slots', [])}"
        )
        # Build outcome from decision (canonical builder)
        # decision should always be available from plan_message()
        decision = plan.get("_decision")
        if decision:
            outcome_dict = build_outcome_from_decision(decision)
        else:
            # Fallback: construct minimal outcome when decision is missing
            # This should rarely happen if plan_message() is working correctly
            logger.warning(
                "Decision not available in plan, using fallback construction"
            )
            plan_slots = plan.get("slots", {})
            plan_missing_slots = plan.get("missing_slots", [])
            plan_obj = plan.get("plan", {})
            if not isinstance(plan_obj, dict):
                plan_obj = {}
            facts = {
                "slots": plan_slots if isinstance(plan_slots, dict) else {},
                "missing_slots": (
                    plan_missing_slots if isinstance(plan_missing_slots, list) else []
                ),
            }
            outcome_dict = {
                "status": plan.get("status")
                or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                "awaiting": plan.get("awaiting"),
                "allowed_actions": plan.get("allowed_actions", []),
                "blocked_actions": plan.get("blocked_actions", []),
                "facts": facts,
                "intent_name": plan.get("intent_name") or plan.get("intent", ""),
                "plan": {
                    "status": plan_obj.get("status")
                    or plan.get("status", "NEEDS_CLARIFICATION"),
                    "stage": plan_obj.get("stage") or plan.get("stage"),
                    "action": plan_obj.get("action") or plan.get("action"),
                },
                "slots": plan_slots,
                "missing_slots": plan_missing_slots,
            }
        # Add active_capability if present in plan
        if plan.get("active_capability"):
            outcome_dict["active_capability"] = plan.get("active_capability")
        response = {
            "success": True,
            "result": outcome_dict,
            "outcome": outcome_dict,  # Alias for backward compatibility
        }
        # Preserve rendered text if present in plan (from plan_message)
        if "text" in plan:
            response["text"] = plan["text"]
        response.setdefault("ui_actions", [])
        return response

    logger.debug(
        f"Allowing action execution: plan_action={plan_action}, mode={execution_step.get('mode') if execution_step else 'unknown'}, "
        f"plan_status={plan_status}, required_slots_satisfied=True"
    )

    # Update plan with selected action if we found an executable action
    if execution_step and plan_action:
        plan["action"] = plan_action
        # Update stage based on action
        if plan_action == "SEARCH_AVAILABILITY":
            plan["stage"] = "AVAILABILITY"
        elif plan_action == "CONFIRM_APPOINTMENT":
            plan["stage"] = "CONFIRM"

    # If execution_step wasn't set yet, find it for the selected action
    if not execution_step and plan_action:
        for step in steps:
            if step.get("action") == plan_action:
                execution_step = step
                break

    # execution_step is already determined by policy-driven eligibility check above
    # No need to re-select - we've already validated the action can execute

    # Only execute if policy selected a step
    if execution_step:
        action = execution_step.get("action")
        client_name = execution_step.get("client", "")

        # Map client name to actual client instance
        execution_client = None
        if client_name == "availability_client":
            execution_client = availability_client
        elif client_name == "booking_client":
            # Extract booking_client from kwargs
            execution_client = kwargs.get("booking_client")
            if not execution_client:
                logger.warning(
                    f"Execution step {action} requires {client_name}, but it was not provided"
                )
                execution_step = None
        else:
            logger.warning(
                f"Unknown client name '{client_name}' for execution step {action}"
            )
            execution_step = None

        # Only proceed if we have the required client
        if execution_step and execution_client is None:
            # Missing required client - return planning outcome (no error for clarification turns)
            # This prevents "missing_dependency" errors when user is clarifying
            logger.debug(
                f"Execution step {action} requires {client_name}, but client not provided. "
                "Returning planning outcome (likely clarification turn)."
            )
            # Build outcome from decision (canonical builder)
            decision = plan.get("_decision")
            if decision:
                outcome_dict = build_outcome_from_decision(decision)
            else:
                # Fallback: construct minimal outcome when decision is missing
                logger.warning(
                    "Decision not available in plan, using fallback construction"
                )
                plan_slots = plan.get("slots", {})
                plan_missing_slots = plan.get("missing_slots", [])
                plan_obj = plan.get("plan", {})
                if not isinstance(plan_obj, dict):
                    plan_obj = {}
                facts = {
                    "slots": plan_slots if isinstance(plan_slots, dict) else {},
                    "missing_slots": (
                        plan_missing_slots
                        if isinstance(plan_missing_slots, list)
                        else []
                    ),
                }
                outcome_dict = {
                    "status": plan.get("status")
                    or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                    "awaiting": plan.get("awaiting"),
                    "allowed_actions": plan.get("allowed_actions", []),
                    "blocked_actions": plan.get("blocked_actions", []),
                    "facts": facts,
                    "intent_name": plan.get("intent_name") or plan.get("intent", ""),
                    "plan": {
                        "status": plan_obj.get("status")
                        or plan.get("status", "NEEDS_CLARIFICATION"),
                        "stage": plan_obj.get("stage") or plan.get("stage"),
                        "action": plan_obj.get("action") or plan.get("action"),
                    },
                    "slots": plan_slots,
                    "missing_slots": plan_missing_slots,
                }
            # Add active_capability if present in plan
            if plan.get("active_capability"):
                outcome_dict["active_capability"] = plan.get("active_capability")
            response = {
                "success": True,
                "result": outcome_dict,
                "outcome": outcome_dict,  # Alias for backward compatibility
            }
            # Preserve rendered text if present in plan (from plan_message)
            if "text" in plan:
                response["text"] = plan["text"]
            response.setdefault("ui_actions", [])
            return response

        if execution_step and execution_client:
            # Ensure organization_id is in slots for execution
            if not slots.get("organization_id") and organization_id is not None:
                slots["organization_id"] = organization_id
            elif not slots.get("organization_id"):
                # Try to get from env as fallback
                slots["organization_id"] = _get_org_id_from_env()

            if action == "SEARCH_AVAILABILITY":
                from core.orchestration.temporal_proposal import (
                    resolve_execution_proposals,
                    slots_for_availability_search,
                )

                _exec_proposals = resolve_execution_proposals(plan, session_state)
                slots = slots_for_availability_search(
                    slots,
                    _exec_proposals["date_proposal"],
                    _exec_proposals["time_proposal"],
                )

            # Update plan with organization_id
            plan["slots"] = slots

            # Update plan action to match selected step
            plan["action"] = action

            # CRITICAL: Add facts to plan for FINALIZE_RESERVATION execution
            # This enables payment verification in the execution handler
            # Facts are needed for defensive payment checks even if capability blocking is bypassed
            if action == "FINALIZE_RESERVATION":
                # Get facts from session_state (contains org data and payment_satisfied from previous turns)
                # Session facts are durable and persist across turns
                plan_facts = {}
                if session_state and isinstance(session_state, dict):
                    plan_facts = session_state.get("facts", {})
                    if not isinstance(plan_facts, dict):
                        plan_facts = {}

                # If org data is missing from session facts, fetch it from organization_client
                # This ensures org.payment_required is available for payment verification
                if organization_client and organization_id:
                    if not plan_facts.get("org"):
                        try:
                            org_details = organization_client.get_details(
                                organization_id
                            )
                            if isinstance(org_details, dict):
                                org_data = (
                                    org_details.get("organization") or org_details
                                )
                                if org_data and isinstance(org_data, dict):
                                    if not plan_facts:
                                        plan_facts = {}
                                    plan_facts["org"] = org_data
                        except Exception as e:
                            logger.debug(
                                f"Failed to fetch org data for FINALIZE_RESERVATION payment verification: {e}"
                            )

                if plan_facts:
                    plan["facts"] = plan_facts
                    logger.debug(
                        "Added facts to plan for FINALIZE_RESERVATION execution (payment verification)"
                    )

            # Step 2: Inject resolved_datetime_range before CONFIRM_APPOINTMENT
            # If datetime_range is missing in slots, check session for resolved_datetime_range
            if action == "CONFIRM_APPOINTMENT":
                if "datetime_range" not in slots or not isinstance(
                    slots.get("datetime_range"), dict
                ):
                    # Try to get resolved_datetime_range from session
                    resolved_datetime_range = None
                    # Get session_state from outer scope - it's initialized at function start
                    try:
                        current_session = session_state or {}
                        session_state_for_check = session_state
                    except NameError:
                        # Fallback if session_state is not in scope (shouldn't happen, but defensive)
                        current_session = {}
                        session_state_for_check = None

                    # Check session_state first
                    if isinstance(session_state_for_check, dict):
                        resolved_datetime_range = session_state.get(
                            "resolved_datetime_range"
                        )

                    # Fallback to session_store if not in session_state
                    if not resolved_datetime_range and session_store is not None:
                        try:
                            if hasattr(session_store, "get_session"):
                                current_session = (
                                    session_store.get_session(user_id)
                                    or current_session
                                )
                            elif callable(session_store):
                                current_session = (
                                    session_store(user_id) or current_session
                                )

                            if isinstance(current_session, dict):
                                resolved_datetime_range = current_session.get(
                                    "resolved_datetime_range"
                                )
                        except Exception as e:
                            logger.debug(
                                f"Failed to get resolved_datetime_range from session_store: {e}"
                            )

                    # Inject into slots if found
                    if resolved_datetime_range and isinstance(
                        resolved_datetime_range, dict
                    ):
                        slots["datetime_range"] = resolved_datetime_range
                        # Update plan with injected datetime_range
                        plan["slots"] = slots
                        logger.debug(
                            f"[DATETIME_RANGE] Injected resolved_datetime_range into slots for CONFIRM_APPOINTMENT: "
                            f"start={resolved_datetime_range.get('start')}, "
                            f"end={resolved_datetime_range.get('end')}"
                        )

            try:
                # Execute the selected step
                if client_name == "availability_client":
                    # For MODIFY_BOOKING, also pass booking_client to fetch service_id from booking
                    booking_client_for_execution = None
                    if intent_name == "MODIFY_BOOKING":
                        booking_client_for_execution = kwargs.get("booking_client")
                    execution_result = execute(
                        plan=plan,
                        availability_client=execution_client,
                        booking_client=booking_client_for_execution,
                    )
                elif client_name == "booking_client":
                    execution_result = execute(
                        plan=plan, booking_client=execution_client
                    )
                else:
                    # Other clients not yet supported
                    logger.warning(f"Execution for {client_name} not yet implemented")
                    # Build outcome from decision (canonical builder)
                    decision = plan.get("_decision")
                    if decision:
                        outcome_dict = build_outcome_from_decision(decision)
                    else:
                        # Fallback: construct minimal outcome when decision is missing
                        logger.warning(
                            "Decision not available in plan, using fallback construction"
                        )
                        plan_slots = plan.get("slots", {})
                        plan_missing_slots = plan.get("missing_slots", [])
                        plan_obj = plan.get("plan", {})
                        if not isinstance(plan_obj, dict):
                            plan_obj = {}
                        facts = {
                            "slots": plan_slots if isinstance(plan_slots, dict) else {},
                            "missing_slots": (
                                plan_missing_slots
                                if isinstance(plan_missing_slots, list)
                                else []
                            ),
                        }
                        outcome_dict = {
                            "status": plan.get("status")
                            or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                            "awaiting": plan.get("awaiting"),
                            "allowed_actions": plan.get("allowed_actions", []),
                            "blocked_actions": plan.get("blocked_actions", []),
                            "facts": facts,
                            "intent_name": plan.get("intent_name")
                            or plan.get("intent", ""),
                            "plan": {
                                "status": plan_obj.get("status")
                                or plan.get("status", "NEEDS_CLARIFICATION"),
                                "stage": plan_obj.get("stage") or plan.get("stage"),
                                "action": plan_obj.get("action") or plan.get("action"),
                            },
                            "slots": plan_slots,
                            "missing_slots": plan_missing_slots,
                        }
                    # Add active_capability if present in plan
                    if plan.get("active_capability"):
                        outcome_dict["active_capability"] = plan.get(
                            "active_capability"
                        )
                    return {
                        "success": True,
                        "result": outcome_dict,
                        "outcome": outcome_dict,  # Alias for backward compatibility
                    }

                # For CONFIRM_APPOINTMENT with EXECUTED status, preserve the action
                # Do not override action - use plan.action directly
                if (
                    execution_result.get("status") == "EXECUTED"
                    and plan.get("action") == "CONFIRM_APPOINTMENT"
                ):
                    # Ensure plan action remains CONFIRM_APPOINTMENT (don't override)
                    plan["action"] = "CONFIRM_APPOINTMENT"
                    # Persist booking_id to slots for idempotency (prevent duplicate creation on next confirmation)
                    booking_id = execution_result.get("booking_id")
                    if booking_id:
                        slots["booking_id"] = booking_id
                        plan["slots"] = slots
                        logger.debug(
                            f"Persisted booking_id={booking_id} to slots for idempotency"
                        )

                # For FETCH_BOOKING with EXECUTED status, persist booking_id to slots
                # This enables subsequent actions (e.g., CONFIRM_CANCELLATION) to use the fetched booking_id
                if (
                    execution_result.get("status") == "EXECUTED"
                    and plan.get("action") == "FETCH_BOOKING"
                ):
                    booking_id = execution_result.get("booking_id")
                    if booking_id:
                        slots["booking_id"] = booking_id
                        plan["slots"] = slots
                        logger.debug(
                            f"Persisted booking_id={booking_id} to slots from FETCH_BOOKING"
                        )

                # For CREATE_BOOKING_HOLD with EXECUTED status, persist booking_id and payment info to slots
                # This enables capability evaluation to access booking_id before FINALIZE_RESERVATION
                if (
                    execution_result.get("status") == "EXECUTED"
                    and plan.get("action") == "CREATE_BOOKING_HOLD"
                ):
                    booking_id = execution_result.get("booking_id")
                    booking_code = execution_result.get("booking_code")
                    total_amount = execution_result.get("total_amount")
                    currency = execution_result.get("currency")
                    if booking_id:
                        slots["booking_id"] = booking_id
                        if booking_code:
                            slots["booking_code"] = booking_code
                        if total_amount:
                            slots["total_amount"] = total_amount
                        if currency:
                            slots["currency"] = currency
                        plan["slots"] = slots
                        logger.debug(
                            f"Persisted booking_id={booking_id}, booking_code={booking_code}, "
                            f"total_amount={total_amount}, currency={currency} to slots from CREATE_BOOKING_HOLD"
                        )

                # Persist availability fingerprint when SEARCH_AVAILABILITY succeeds
                # This enables slot-fingerprint-based availability resolution
                # CRITICAL: Always attach fingerprint to execution_result, even when session_store is None
                # This allows build_session_state_from_outcome() to preserve it across turns
                if (
                    execution_result.get("type") == "availability"
                    and execution_result.get("status") == "success"
                ):
                    from core.orchestration.availability_fingerprint import (
                        compute_availability_fingerprint,
                    )

                    # Get intent_name from plan for fingerprint computation
                    # CREATE_APPOINTMENT uses day-level fingerprint (service_id+date), others use exact (service_id+date+time)
                    plan_intent_name = plan.get("intent_name") or plan.get("intent")

                    # Compute fingerprint from current slots with intent_name
                    availability_fingerprint = compute_availability_fingerprint(
                        slots, intent_name=plan_intent_name
                    )

                    if availability_fingerprint:
                        # Always attach fingerprint to execution_result for persistence via outcome
                        execution_result["availability_fingerprint"] = (
                            availability_fingerprint
                        )

                        # Always attach fingerprint to current_session (independent of session_store presence)
                        # This ensures fingerprint is available for session rebuild logic
                        try:
                            current_session = session_state or {}
                        except NameError:
                            # Fallback if session_state is not in scope (shouldn't happen, but defensive)
                            current_session = {}
                        if session_store is not None:
                            # Try to get latest session from session_store
                            try:
                                if hasattr(session_store, "get_session"):
                                    current_session = (
                                        session_store.get_session(user_id)
                                        or current_session
                                    )
                                elif callable(session_store):
                                    current_session = (
                                        session_store(user_id) or current_session
                                    )
                            except Exception as e:
                                logger.debug(
                                    f"Failed to get session from session_store for fingerprint: {e}"
                                )

                        # Always attach fingerprint to current_session
                        current_session["availability_fingerprint"] = (
                            availability_fingerprint
                        )

                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Attached fingerprint={availability_fingerprint} "
                            f"to current_session for slots service_id={slots.get('service_id')}, "
                            f"date={slots.get('date')}, time={slots.get('time')}"
                        )

                        # Save to session_store if available (for immediate persistence)
                        if session_store is not None:
                            try:
                                if hasattr(session_store, "save_session"):
                                    session_store.save_session(user_id, current_session)
                                elif hasattr(session_store, "save"):
                                    session_store.save(user_id, current_session)

                                logger.debug(
                                    f"[AVAILABILITY_FINGERPRINT] Saved to session_store: {availability_fingerprint}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to persist availability fingerprint to session_store: {e}"
                                )

                    from core.orchestration.temporal_proposal import (
                        apply_confirmed_datetime,
                        resolve_execution_proposals,
                    )

                    _exec_proposals = resolve_execution_proposals(plan, session_state)
                    confirmed_slots = apply_confirmed_datetime(
                        slots,
                        _exec_proposals["date_proposal"],
                        _exec_proposals["time_proposal"],
                    )
                    slots.clear()
                    slots.update(confirmed_slots)
                    plan["slots"] = confirmed_slots

                # Step 1: Capture datetime_range when SEARCH_AVAILABILITY succeeds
                # Construct datetime_range from date + time if not already present
                # This enables CONFIRM_APPOINTMENT to reuse the validated datetime range
                # NOTE: This runs at the same level as availability_fingerprint (not nested)
                resolved_datetime_range = None

                # Check if datetime_range already exists in slots
                if isinstance(slots.get("datetime_range"), dict):
                    resolved_datetime_range = slots.get("datetime_range")
                else:
                    # Construct from date + time using service duration
                    date_str = slots.get("date")
                    time_str = slots.get("time")

                    if date_str and time_str:
                        try:
                            from datetime import timedelta

                            dt = datetime

                            # Parse date (YYYY-MM-DD format)
                            date_obj = None
                            if isinstance(date_str, str):
                                date_only = date_str.split("T")[0].split(" ")[0]
                                try:
                                    date_obj = dt.strptime(date_only, "%Y-%m-%d")
                                except ValueError:
                                    try:
                                        date_obj = dt.fromisoformat(date_only)
                                    except (ValueError, AttributeError):
                                        pass

                            if date_obj:
                                # Parse time string (handle formats like "2pm", "14:00", etc.)
                                time_normalized = (
                                    str(time_str)
                                    .lower()
                                    .replace("am", "")
                                    .replace("pm", "")
                                    .strip()
                                )
                                time_parts = (
                                    time_normalized.split(":")
                                    if ":" in time_normalized
                                    else [time_normalized, "00"]
                                )

                                if len(time_parts) >= 2:
                                    try:
                                        hour = int(time_parts[0])
                                        minute = (
                                            int(time_parts[1])
                                            if len(time_parts) > 1
                                            else 0
                                        )

                                        # Handle AM/PM
                                        if "pm" in str(time_str).lower() and hour < 12:
                                            hour += 12
                                        elif (
                                            "am" in str(time_str).lower() and hour == 12
                                        ):
                                            hour = 0

                                        # Combine date and time
                                        start_datetime = date_obj.replace(
                                            hour=hour,
                                            minute=minute,
                                            second=0,
                                            microsecond=0,
                                        )

                                        # Compute end_time from start + service duration
                                        # Duration comes from availability response or default (60 min)
                                        # Extract duration from first availability slot if available
                                        duration_minutes = 60  # Default
                                        availability_slots = execution_result.get(
                                            "slots", []
                                        )
                                        if availability_slots and isinstance(
                                            availability_slots, list
                                        ):
                                            first_slot = availability_slots[0]
                                            if isinstance(first_slot, dict):
                                                slot_start = first_slot.get(
                                                    "starts_at"
                                                ) or first_slot.get("start")
                                                slot_end = first_slot.get(
                                                    "ends_at"
                                                ) or first_slot.get("end")
                                                if slot_start and slot_end:
                                                    try:
                                                        start_dt = dt.fromisoformat(
                                                            str(slot_start).replace(
                                                                "Z", "+00:00"
                                                            )
                                                        )
                                                        end_dt = dt.fromisoformat(
                                                            str(slot_end).replace(
                                                                "Z", "+00:00"
                                                            )
                                                        )
                                                        duration_minutes = int(
                                                            (
                                                                end_dt - start_dt
                                                            ).total_seconds()
                                                            / 60
                                                        )
                                                    except (ValueError, AttributeError):
                                                        pass

                                        end_datetime = start_datetime + timedelta(
                                            minutes=duration_minutes
                                        )

                                        resolved_datetime_range = {
                                            "start": start_datetime.isoformat(),
                                            "end": end_datetime.isoformat(),
                                        }

                                        logger.debug(
                                            f"[DATETIME_RANGE] Constructed from date+time: "
                                            f"date={date_str}, time={time_str}, "
                                            f"start={resolved_datetime_range['start']}, "
                                            f"end={resolved_datetime_range['end']}, "
                                            f"duration={duration_minutes}min"
                                        )
                                    except (ValueError, IndexError, TypeError) as e:
                                        logger.warning(
                                            f"Failed to construct datetime_range from date+time: {e}. "
                                            f"date={date_str}, time={time_str}"
                                        )
                        except Exception as e:
                            logger.warning(
                                f"Error constructing datetime_range from availability result: {e}"
                            )

                # Persist resolved_datetime_range to session
                if resolved_datetime_range:
                    # Attach to current_session for persistence
                    # Get session_state from outer scope - it's initialized at function start
                    try:
                        current_session = session_state or {}
                    except NameError:
                        # Fallback if session_state is not in scope (shouldn't happen, but defensive)
                        current_session = {}
                    if session_store is not None:
                        try:
                            if hasattr(session_store, "get_session"):
                                current_session = (
                                    session_store.get_session(user_id)
                                    or current_session
                                )
                            elif callable(session_store):
                                current_session = (
                                    session_store(user_id) or current_session
                                )
                        except Exception as e:
                            logger.debug(
                                f"Failed to get session from session_store for datetime_range: {e}"
                            )

                    current_session["resolved_datetime_range"] = resolved_datetime_range

                    logger.debug(
                        f"[DATETIME_RANGE] Persisting resolved_datetime_range to session: "
                        f"start={resolved_datetime_range['start']}, "
                        f"end={resolved_datetime_range['end']}"
                    )

                    # Save to session_store if available
                    if session_store is not None:
                        try:
                            if hasattr(session_store, "save_session"):
                                session_store.save_session(user_id, current_session)
                            elif hasattr(session_store, "save"):
                                session_store.save(user_id, current_session)
                        except Exception as e:
                            logger.warning(
                                f"Failed to persist resolved_datetime_range to session_store: {e}"
                            )

                    # CRITICAL: Attach resolved_datetime_range to execution_result for persistence
                    # This enables test adapters and session rebuild logic to extract it
                    execution_result["resolved_datetime_range"] = (
                        resolved_datetime_range
                    )
                    logger.debug(
                        f"[DATETIME_RANGE] Attached to execution_result: {resolved_datetime_range.get('start')}"
                    )

                # Return execution result
                # CRITICAL: Attach availability_fingerprint to plan for persistence
                # This ensures fingerprint survives even when session_store is None
                # and can be extracted by build_session_state_from_outcome() or test adapters
                if (
                    execution_result.get("type") == "availability"
                    and execution_result.get("status") == "success"
                ):
                    # Attach availability_fingerprint if present
                    if execution_result.get("availability_fingerprint"):
                        plan["availability_fingerprint"] = execution_result.get(
                            "availability_fingerprint"
                        )
                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Attached to plan: {execution_result.get('availability_fingerprint')}"
                        )

                    # Also attach resolved_datetime_range to plan if present
                    if execution_result.get("resolved_datetime_range"):
                        plan["resolved_datetime_range"] = execution_result.get(
                            "resolved_datetime_range"
                        )
                        logger.debug(
                            f"[DATETIME_RANGE] Attached to plan: {execution_result.get('resolved_datetime_range').get('start')}"
                        )

                # Ensure execution_result includes plan structure (status, stage, action)
                # Build from decision if available, otherwise use plan
                decision = plan.get("_decision")
                if decision:
                    # Use canonical builder to ensure plan structure is complete
                    outcome_from_decision = build_outcome_from_decision(decision)
                    # Merge plan structure into execution_result
                    if not isinstance(execution_result, dict):
                        execution_result = {}
                    if "plan" not in execution_result or not isinstance(
                        execution_result.get("plan"), dict
                    ):
                        execution_result["plan"] = {}
                    # Ensure plan.status, plan.stage, plan.action are present
                    execution_result["plan"]["status"] = outcome_from_decision.get(
                        "plan", {}
                    ).get("status")
                    execution_result["plan"]["stage"] = outcome_from_decision.get(
                        "plan", {}
                    ).get("stage")
                    execution_result["plan"]["action"] = outcome_from_decision.get(
                        "plan", {}
                    ).get("action")
                else:
                    # Fallback: extract from plan
                    plan_obj = plan.get("plan", {})
                    if not isinstance(plan_obj, dict):
                        plan_obj = {}
                    if not isinstance(execution_result, dict):
                        execution_result = {}
                    if "plan" not in execution_result or not isinstance(
                        execution_result.get("plan"), dict
                    ):
                        execution_result["plan"] = {}
                    execution_result["plan"]["status"] = plan_obj.get(
                        "status"
                    ) or plan.get("status")
                    execution_result["plan"]["stage"] = plan_obj.get(
                        "stage"
                    ) or plan.get("stage")
                    execution_result["plan"]["action"] = plan_obj.get(
                        "action"
                    ) or plan.get("action")

                result = {
                    "success": True,
                    "result": execution_result,
                    "outcome": execution_result,  # Alias for backward compatibility
                    "plan": plan,
                }
                result.setdefault("ui_actions", [])

                # Inject outcome rendering text (if EXECUTED)
                decision = plan.get("_decision")
                if decision:
                    _inject_outcome_text(result, decision, execution_result)

                return result
            except Exception as e:
                logger.error(f"Execution failed for action {action}: {e}")
                return {
                    "success": False,
                    "error": "execution_failed",
                    "message": str(e),
                    "plan": plan,
                }

    # No execution step selected by policy - return planning outcome
    # Build outcome from decision (canonical builder)
    decision = plan.get("_decision")
    if decision:
        outcome_dict = build_outcome_from_decision(decision)
    else:
        # Fallback: construct minimal outcome when decision is missing
        logger.warning("Decision not available in plan, using fallback construction")
        plan_slots = plan.get("slots", {})
        plan_missing_slots = plan.get("missing_slots", [])
        plan_obj = plan.get("plan", {})
        if not isinstance(plan_obj, dict):
            plan_obj = {}
        facts = {
            "slots": plan_slots if isinstance(plan_slots, dict) else {},
            "missing_slots": (
                plan_missing_slots if isinstance(plan_missing_slots, list) else []
            ),
        }
        outcome_dict = {
            "status": plan.get("status")
            or plan_obj.get("status", "NEEDS_CLARIFICATION"),
            "awaiting": plan.get("awaiting"),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", []),
            "facts": facts,
            "intent_name": plan.get("intent_name") or plan.get("intent", ""),
            "plan": {
                "status": plan_obj.get("status")
                or plan.get("status", "NEEDS_CLARIFICATION"),
                "stage": plan_obj.get("stage") or plan.get("stage"),
                "action": plan_obj.get("action") or plan.get("action"),
            },
            "slots": plan_slots,
            "missing_slots": plan_missing_slots,
        }
    # Add active_capability if present in plan
    if plan.get("active_capability"):
        outcome_dict["active_capability"] = plan.get("active_capability")
    result = {
        "success": True,
        "result": outcome_dict,
        "outcome": outcome_dict,  # Alias for backward compatibility
    }
    result.setdefault("ui_actions", [])
    return result


def handle_message_legacy(
    user_id: str,
    text: str,
    domain: str = "service",  # caller-provided; will be overridden by org domain
    timezone: str = "UTC",
    phone_number: Optional[str] = None,
    email: Optional[str] = None,
    customer_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    luma_client: Optional[LumaClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    verbose: bool = False,
    session_state: Optional[Dict[str, Any]] = None,
    transaction_id: Optional[str] = None,
    planning_only: bool = False,
) -> Dict[str, Any]:
    """
    [LEGACY] Thin wrapper — delegates to plan_turn() in turn_planner.

    Kept for backward compatibility; plan_message and tests call this entrypoint.
    """
    from core.planning.orchestration.turn_planner import plan_turn

    return plan_turn(
        user_id=user_id,
        text=text,
        domain=domain,
        timezone=timezone,
        phone_number=phone_number,
        email=email,
        customer_id=customer_id,
        organization_id=organization_id,
        luma_client=luma_client,
        catalog_client=catalog_client,
        organization_client=organization_client,
        verbose=verbose,
        session_state=session_state,
        transaction_id=transaction_id,
        planning_only=planning_only,
    )


def plan_message(
    text: str,
    user_id: str,
    session_state: Optional[Dict[str, Any]] = None,
    luma_client: Optional[LumaClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    frozen_time: Optional[datetime] = None,
    organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Extract planning result from handle_message without triggering execution logic.

    This is a thin wrapper around handle_message with planning_only=True that returns
    a clean, structured planning result suitable for E2E testing.

    Args:
        text: User message text
        user_id: User identifier
        session_state: Optional session state for follow-up handling
        luma_client: Optional Luma client instance (creates default if None)
        organization_client: Optional organization client instance (creates default if None)
        frozen_time: Optional frozen time for testing (reserved for future time mocking support)

    Returns:
        Dictionary with planning result containing:
        - intent_name: Intent name
        - stage: Planning stage (e.g., "AVAILABILITY", "CONFIRM")
        - action: Action to execute (e.g., "SEARCH_AVAILABILITY", "CONFIRM_APPOINTMENT")
        - slots: Collected slots dictionary
        - missing_slots: List of missing required slots
        - time_constraint: Optional time constraint from Luma response (if present)
        - status: Planning status (READY, NEEDS_CLARIFICATION, AWAITING_CONFIRMATION)

    Raises:
        Any exceptions from handle_message are propagated.
    """
    # Call handle_message_legacy with planning_only=True to avoid execution logic
    result = handle_message_legacy(
        user_id=user_id,
        text=text,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        organization_id=organization_id,
        planning_only=True,
    )

    # Extract planning result from outcome
    if not result.get("success", False):
        # Propagate errors as-is
        return result

    outcome = result.get("outcome", {})

    # Extract required fields from outcome
    # Include both top-level fields and plan structure for compatibility
    outcome_plan = outcome.get("plan", {})
    if not isinstance(outcome_plan, dict):
        outcome_plan = {}

    # Extract stage, action, and status from plan if available, otherwise from top-level
    stage = (
        outcome_plan.get("stage")
        if outcome_plan.get("stage") is not None
        else outcome.get("stage")
    )
    action = (
        outcome_plan.get("action")
        if outcome_plan.get("action") is not None
        else outcome.get("action")
    )
    status = (
        outcome_plan.get("status")
        if outcome_plan.get("status") is not None
        else outcome.get("status")
    )

    # Build plan structure for tests that expect plan.status, plan.stage, and plan.action
    # Always build from outcome.plan (authoritative source) to ensure consistency
    plan_structure = outcome_plan.copy() if outcome_plan else {}
    if status is not None and "status" not in plan_structure:
        plan_structure["status"] = status
    if stage is not None and "stage" not in plan_structure:
        plan_structure["stage"] = stage
    if action is not None and "action" not in plan_structure:
        plan_structure["action"] = action

    planning_result = {
        "intent_name": outcome.get("intent_name", ""),
        "intent": outcome.get("intent_name", ""),  # Alias for compatibility
        "stage": stage,
        "action": action,
        "slots": outcome.get("slots", {}),
        "missing_slots": outcome.get("missing_slots", []),
        "status": outcome.get("status"),
        # Include plan structure for tests that expect plan.stage and plan.action
        "plan": plan_structure,
        # Include decision information for handle_message early returns
        "_decision": result.get("_decision"),
    }

    # Carry HANDLER_DELEGATED routing fields — stripped by standard planning_result construction
    if outcome.get("status") == "HANDLER_DELEGATED":
        for _k in ("active_handler", "search_query"):
            if outcome.get(_k) is not None:
                planning_result[_k] = outcome[_k]

    # Extract time_constraint from multiple possible sources
    # Priority: 1) effective_response (merged_luma_response), 2) raw_luma_response, 3) outcome.facts.context
    time_constraint = None
    merged_luma_response = result.get("_merged_luma_response", {})
    if isinstance(merged_luma_response, dict):
        # Check effective_response first (time_constraint is stored here during processing)
        time_constraint = merged_luma_response.get("time_constraint")

        # If not found, check raw_luma_response within effective_response
        if time_constraint is None:
            raw_luma_response = merged_luma_response.get("_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                time_constraint = raw_luma_response.get("time_constraint")

    # Fallback: Check outcome.facts.context
    if time_constraint is None:
        facts = outcome.get("facts", {})
        if isinstance(facts, dict):
            context = facts.get("context", {})
            if isinstance(context, dict):
                time_constraint = context.get("time_constraint")

    # Add time_constraint if present
    if time_constraint is not None:
        planning_result["time_constraint"] = time_constraint

    # Propagate proposals from merged response so execution call sites can read them
    # from plan without relying on session_state being mutated by plan_message.
    if isinstance(merged_luma_response, dict):
        for _prop_key in ("date_proposal", "time_proposal"):
            _prop_val = merged_luma_response.get(_prop_key)
            if _prop_val is not None:
                planning_result[_prop_key] = _prop_val

    # Preserve rendered clarification text if present
    # Text is injected at top level of result by _inject_rendering_text
    if "text" in result:
        planning_result["text"] = result["text"]
    elif "text" in outcome:
        planning_result["text"] = outcome["text"]

    return planning_result
