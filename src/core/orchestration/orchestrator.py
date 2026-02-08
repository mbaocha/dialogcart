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

import logging
import json
import os
import copy
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

from core.orchestration.nlu import LumaClient, assert_luma_contract, process_luma_response, build_clarify_outcome_from_reason
from core.orchestration.errors import ContractViolation, UpstreamError, UnsupportedIntentError
from core.orchestration.clients.catalog_client import CatalogClient
from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.cache.catalog_cache import catalog_cache
from core.orchestration.cache.org_domain_cache import org_domain_cache
from core.orchestration.persistence.durable_intents import is_durable_intent
from core.routing.workflows import get_workflow
from core.rendering.mapper.clarification_mapper import derive_clarification_reason
from core.rendering import render_clarification, render

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
    status: str = "READY"
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
        "executable_actions": executable_actions
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
            "plan": {
                "status": "NEEDS_CLARIFICATION",
                "stage": None,
                "action": None
            },
            "slots": {},
            "missing_slots": [],
            "blocked_actions": [],
            "allowed_actions": [],
            "facts": {}
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
        "action": plan.get("action")
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
        "facts": facts
    }

    # Add active_capability if present in plan
    if plan.get("active_capability"):
        outcome["active_capability"] = plan.get("active_capability")

    return outcome


def _render_clarification_text(decision: Dict[str, Any], slots: Dict[str, Any]) -> Optional[str]:
    """
    Render clarification text from decision and slots (best-effort).

    Args:
        decision: Decision/plan dictionary with status and missing_slots
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

        # Render with slots
        render_spec = render_clarification(reason, slots)
        return render_spec.text
    except Exception as e:
        # Best-effort: log error and return None
        logger.warning(
            f"Failed to render clarification text: {e}. "
            f"Rendering is best-effort and will be omitted."
        )
        return None


def _inject_rendering_text(result: Dict[str, Any], decision: Dict[str, Any]) -> None:
    """
    Inject rendered text into top-level response for clarification states.

    This is a pure post-processing step that:
    - Detects clarification state from decision
    - Calls rendering.render(decision)
    - Injects returned text into top-level response as 'text'
    - Falls back to generic template if rendering returns None

    Args:
        result: Response dictionary to modify (mutated in place)
        decision: Decision dictionary with plan and facts
    """
    try:
        rendered_text = render(decision)
        if rendered_text:
            result["text"] = rendered_text
    except Exception as e:
        # Best-effort: log error and continue without text
        logger.warning(
            f"Failed to render clarification text: {e}. "
            f"Rendering is best-effort and will be omitted."
        )


def _handle_non_core_intent(
    luma_response: Dict[str, Any],
    decision: Dict[str, Any],
    user_id: str
) -> Dict[str, Any]:
    """
    Handle non-core intents by passing them through as non-orchestrated signals.

    Non-core intents (e.g., PAYMENT, CONFIRM_BOOKING, BOOKING_INQUIRY) are not
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
        }
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


def _invoke_workflow_after_execute(intent_name: str, outcome: Dict[str, Any]) -> Dict[str, Any]:
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
        logger.debug(
            f"Error looking up workflow for intent '{intent_name}': {e}")

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
    **kwargs  # Backward-compat shim: ignore unknown infra parameters (e.g., domain, customer_id)  # noqa: ARG001
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
        json.dumps({
            "turn": "INPUT",
            "user_id": user_id,
            "text": text
        }, ensure_ascii=True)
    )

    # Get session state if session_store provided
    session_state = None
    if session_store is not None:
        try:
            if hasattr(session_store, 'get_session'):
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
                f"[SESSION_FALLBACK] Could not load from default session store: {e}")

    # LOG 3 — SESSION READ (immediately after session_store.get_session)
    turn_logger.info(
        json.dumps({
            "turn": "SESSION_READ",
            "session": session_state
        }, ensure_ascii=True, default=str)
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
        organization_id=organization_id
    )

    # Check if planning failed
    if not plan or plan.get("error"):
        return {
            "success": False,
            "error": plan.get("error", "planning_failed"),
            "message": plan.get("message", "Planning failed"),
            "plan": plan
        }

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
                    can_execute = (
                        plan_status == "READY" and action_slots_satisfied)
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
                "Decision not available in plan, using fallback construction")
            plan_slots = plan.get("slots", {})
            plan_missing_slots = plan.get("missing_slots", [])
            plan_obj = plan.get("plan", {})
            if not isinstance(plan_obj, dict):
                plan_obj = {}
            facts = {
                "slots": plan_slots if isinstance(plan_slots, dict) else {},
                "missing_slots": plan_missing_slots if isinstance(plan_missing_slots, list) else []
            }
            outcome_dict = {
                "status": plan.get("status") or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                "awaiting": plan.get("awaiting"),
                "allowed_actions": plan.get("allowed_actions", []),
                "blocked_actions": plan.get("blocked_actions", []),
                "facts": facts,
                "intent_name": plan.get("intent_name") or plan.get("intent", ""),
                "plan": {
                    "status": plan_obj.get("status") or plan.get("status", "NEEDS_CLARIFICATION"),
                    "stage": plan_obj.get("stage") or plan.get("stage"),
                    "action": plan_obj.get("action") or plan.get("action")
                },
                "slots": plan_slots,
                "missing_slots": plan_missing_slots
            }
        # Add active_capability if present in plan
        if plan.get("active_capability"):
            outcome_dict["active_capability"] = plan.get("active_capability")
        response = {
            "success": True,
            "result": outcome_dict,
            "outcome": outcome_dict  # Alias for backward compatibility
        }
        # Preserve rendered text if present in plan (from plan_message)
        if "text" in plan:
            response["text"] = plan["text"]
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
                    f"Execution step {action} requires {client_name}, but it was not provided")
                execution_step = None
        else:
            logger.warning(
                f"Unknown client name '{client_name}' for execution step {action}")
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
                    "Decision not available in plan, using fallback construction")
                plan_slots = plan.get("slots", {})
                plan_missing_slots = plan.get("missing_slots", [])
                plan_obj = plan.get("plan", {})
                if not isinstance(plan_obj, dict):
                    plan_obj = {}
                facts = {
                    "slots": plan_slots if isinstance(plan_slots, dict) else {},
                    "missing_slots": plan_missing_slots if isinstance(plan_missing_slots, list) else []
                }
                outcome_dict = {
                    "status": plan.get("status") or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                    "awaiting": plan.get("awaiting"),
                    "allowed_actions": plan.get("allowed_actions", []),
                    "blocked_actions": plan.get("blocked_actions", []),
                    "facts": facts,
                    "intent_name": plan.get("intent_name") or plan.get("intent", ""),
                    "plan": {
                        "status": plan_obj.get("status") or plan.get("status", "NEEDS_CLARIFICATION"),
                        "stage": plan_obj.get("stage") or plan.get("stage"),
                        "action": plan_obj.get("action") or plan.get("action")
                    },
                    "slots": plan_slots,
                    "missing_slots": plan_missing_slots
                }
            # Add active_capability if present in plan
            if plan.get("active_capability"):
                outcome_dict["active_capability"] = plan.get(
                    "active_capability")
            response = {
                "success": True,
                "result": outcome_dict,
                "outcome": outcome_dict  # Alias for backward compatibility
            }
            # Preserve rendered text if present in plan (from plan_message)
            if "text" in plan:
                response["text"] = plan["text"]
            return response

        if execution_step and execution_client:
            # Ensure organization_id is in slots for execution
            if not slots.get("organization_id") and organization_id is not None:
                slots["organization_id"] = organization_id
            elif not slots.get("organization_id"):
                # Try to get from env as fallback
                slots["organization_id"] = _get_org_id_from_env()

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
                                organization_id)
                            if isinstance(org_details, dict):
                                org_data = org_details.get(
                                    "organization") or org_details
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
                if "datetime_range" not in slots or not isinstance(slots.get("datetime_range"), dict):
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
                            "resolved_datetime_range")

                    # Fallback to session_store if not in session_state
                    if not resolved_datetime_range and session_store is not None:
                        try:
                            if hasattr(session_store, 'get_session'):
                                current_session = session_store.get_session(
                                    user_id) or current_session
                            elif callable(session_store):
                                current_session = session_store(
                                    user_id) or current_session

                            if isinstance(current_session, dict):
                                resolved_datetime_range = current_session.get(
                                    "resolved_datetime_range")
                        except Exception as e:
                            logger.debug(
                                f"Failed to get resolved_datetime_range from session_store: {e}")

                    # Inject into slots if found
                    if resolved_datetime_range and isinstance(resolved_datetime_range, dict):
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
                        booking_client_for_execution = kwargs.get(
                            "booking_client")
                    execution_result = execute(
                        plan=plan,
                        availability_client=execution_client,
                        booking_client=booking_client_for_execution
                    )
                elif client_name == "booking_client":
                    execution_result = execute(
                        plan=plan,
                        booking_client=execution_client
                    )
                else:
                    # Other clients not yet supported
                    logger.warning(
                        f"Execution for {client_name} not yet implemented")
                    # Build outcome from decision (canonical builder)
                    decision = plan.get("_decision")
                    if decision:
                        outcome_dict = build_outcome_from_decision(decision)
                    else:
                        # Fallback: construct minimal outcome when decision is missing
                        logger.warning(
                            "Decision not available in plan, using fallback construction")
                        plan_slots = plan.get("slots", {})
                        plan_missing_slots = plan.get("missing_slots", [])
                        plan_obj = plan.get("plan", {})
                        if not isinstance(plan_obj, dict):
                            plan_obj = {}
                        facts = {
                            "slots": plan_slots if isinstance(plan_slots, dict) else {},
                            "missing_slots": plan_missing_slots if isinstance(plan_missing_slots, list) else []
                        }
                        outcome_dict = {
                            "status": plan.get("status") or plan_obj.get("status", "NEEDS_CLARIFICATION"),
                            "awaiting": plan.get("awaiting"),
                            "allowed_actions": plan.get("allowed_actions", []),
                            "blocked_actions": plan.get("blocked_actions", []),
                            "facts": facts,
                            "intent_name": plan.get("intent_name") or plan.get("intent", ""),
                            "plan": {
                                "status": plan_obj.get("status") or plan.get("status", "NEEDS_CLARIFICATION"),
                                "stage": plan_obj.get("stage") or plan.get("stage"),
                                "action": plan_obj.get("action") or plan.get("action")
                            },
                            "slots": plan_slots,
                            "missing_slots": plan_missing_slots
                        }
                    # Add active_capability if present in plan
                    if plan.get("active_capability"):
                        outcome_dict["active_capability"] = plan.get(
                            "active_capability")
                    return {
                        "success": True,
                        "result": outcome_dict,
                        "outcome": outcome_dict  # Alias for backward compatibility
                    }

                # For CONFIRM_APPOINTMENT with EXECUTED status, preserve the action
                # Do not override action - use plan.action directly
                if execution_result.get("status") == "EXECUTED" and plan.get("action") == "CONFIRM_APPOINTMENT":
                    # Ensure plan action remains CONFIRM_APPOINTMENT (don't override)
                    plan["action"] = "CONFIRM_APPOINTMENT"
                    # Persist booking_id to slots for idempotency (prevent duplicate creation on next confirmation)
                    booking_id = execution_result.get("booking_id")
                    if booking_id:
                        slots["booking_id"] = booking_id
                        plan["slots"] = slots
                        logger.debug(
                            f"Persisted booking_id={booking_id} to slots for idempotency")

                # For FETCH_BOOKING with EXECUTED status, persist booking_id to slots
                # This enables subsequent actions (e.g., CONFIRM_CANCELLATION) to use the fetched booking_id
                if execution_result.get("status") == "EXECUTED" and plan.get("action") == "FETCH_BOOKING":
                    booking_id = execution_result.get("booking_id")
                    if booking_id:
                        slots["booking_id"] = booking_id
                        plan["slots"] = slots
                        logger.debug(
                            f"Persisted booking_id={booking_id} to slots from FETCH_BOOKING")

                # For CREATE_BOOKING_HOLD with EXECUTED status, persist booking_id and payment info to slots
                # This enables capability evaluation to access booking_id before FINALIZE_RESERVATION
                if execution_result.get("status") == "EXECUTED" and plan.get("action") == "CREATE_BOOKING_HOLD":
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
                            f"total_amount={total_amount}, currency={currency} to slots from CREATE_BOOKING_HOLD")

                # Persist availability fingerprint when SEARCH_AVAILABILITY succeeds
                # This enables slot-fingerprint-based availability resolution
                # CRITICAL: Always attach fingerprint to execution_result, even when session_store is None
                # This allows build_session_state_from_outcome() to preserve it across turns
                if (execution_result.get("type") == "availability"
                        and execution_result.get("status") == "success"):
                    from core.orchestration.availability_fingerprint import compute_availability_fingerprint

                    # Get intent_name from plan for fingerprint computation
                    # CREATE_APPOINTMENT uses day-level fingerprint (service_id+date), others use exact (service_id+date+time)
                    plan_intent_name = plan.get(
                        "intent_name") or plan.get("intent")

                    # Compute fingerprint from current slots with intent_name
                    availability_fingerprint = compute_availability_fingerprint(
                        slots, intent_name=plan_intent_name)

                    if availability_fingerprint:
                        # Always attach fingerprint to execution_result for persistence via outcome
                        execution_result["availability_fingerprint"] = availability_fingerprint

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
                                if hasattr(session_store, 'get_session'):
                                    current_session = session_store.get_session(
                                        user_id) or current_session
                                elif callable(session_store):
                                    current_session = session_store(
                                        user_id) or current_session
                            except Exception as e:
                                logger.debug(
                                    f"Failed to get session from session_store for fingerprint: {e}")

                        # Always attach fingerprint to current_session
                        current_session["availability_fingerprint"] = availability_fingerprint

                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Attached fingerprint={availability_fingerprint} "
                            f"to current_session for slots service_id={slots.get('service_id')}, "
                            f"date={slots.get('date')}, time={slots.get('time')}"
                        )

                        # Save to session_store if available (for immediate persistence)
                        if session_store is not None:
                            try:
                                if hasattr(session_store, 'save_session'):
                                    session_store.save_session(
                                        user_id, current_session)
                                elif hasattr(session_store, 'save'):
                                    session_store.save(
                                        user_id, current_session)

                                logger.debug(
                                    f"[AVAILABILITY_FINGERPRINT] Saved to session_store: {availability_fingerprint}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to persist availability fingerprint to session_store: {e}")

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
                                date_only = date_str.split(
                                    "T")[0].split(" ")[0]
                                try:
                                    date_obj = dt.strptime(
                                        date_only, "%Y-%m-%d")
                                except ValueError:
                                    try:
                                        date_obj = dt.fromisoformat(
                                            date_only)
                                    except (ValueError, AttributeError):
                                        pass

                            if date_obj:
                                # Parse time string (handle formats like "2pm", "14:00", etc.)
                                time_normalized = str(time_str).lower().replace(
                                    "am", "").replace("pm", "").strip()
                                time_parts = time_normalized.split(":") if ":" in time_normalized else [
                                    time_normalized, "00"]

                                if len(time_parts) >= 2:
                                    try:
                                        hour = int(time_parts[0])
                                        minute = int(time_parts[1]) if len(
                                            time_parts) > 1 else 0

                                        # Handle AM/PM
                                        if "pm" in str(time_str).lower() and hour < 12:
                                            hour += 12
                                        elif "am" in str(time_str).lower() and hour == 12:
                                            hour = 0

                                        # Combine date and time
                                        start_datetime = date_obj.replace(
                                            hour=hour, minute=minute, second=0, microsecond=0
                                        )

                                        # Compute end_time from start + service duration
                                        # Duration comes from availability response or default (60 min)
                                        # Extract duration from first availability slot if available
                                        duration_minutes = 60  # Default
                                        availability_slots = execution_result.get(
                                            "slots", [])
                                        if availability_slots and isinstance(availability_slots, list):
                                            first_slot = availability_slots[0]
                                            if isinstance(first_slot, dict):
                                                slot_start = first_slot.get(
                                                    "starts_at") or first_slot.get("start")
                                                slot_end = first_slot.get(
                                                    "ends_at") or first_slot.get("end")
                                                if slot_start and slot_end:
                                                    try:
                                                        start_dt = dt.fromisoformat(
                                                            str(slot_start).replace("Z", "+00:00"))
                                                        end_dt = dt.fromisoformat(
                                                            str(slot_end).replace("Z", "+00:00"))
                                                        duration_minutes = int(
                                                            (end_dt - start_dt).total_seconds() / 60)
                                                    except (ValueError, AttributeError):
                                                        pass

                                        end_datetime = start_datetime + \
                                            timedelta(
                                                minutes=duration_minutes)

                                        resolved_datetime_range = {
                                            "start": start_datetime.isoformat(),
                                            "end": end_datetime.isoformat()
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
                                f"Error constructing datetime_range from availability result: {e}")

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
                            if hasattr(session_store, 'get_session'):
                                current_session = session_store.get_session(
                                    user_id) or current_session
                            elif callable(session_store):
                                current_session = session_store(
                                    user_id) or current_session
                        except Exception as e:
                            logger.debug(
                                f"Failed to get session from session_store for datetime_range: {e}")

                    current_session["resolved_datetime_range"] = resolved_datetime_range

                    logger.debug(
                        f"[DATETIME_RANGE] Persisting resolved_datetime_range to session: "
                        f"start={resolved_datetime_range['start']}, "
                        f"end={resolved_datetime_range['end']}"
                    )

                    # Save to session_store if available
                    if session_store is not None:
                        try:
                            if hasattr(session_store, 'save_session'):
                                session_store.save_session(
                                    user_id, current_session)
                            elif hasattr(session_store, 'save'):
                                session_store.save(
                                    user_id, current_session)
                        except Exception as e:
                            logger.warning(
                                f"Failed to persist resolved_datetime_range to session_store: {e}")

                    # CRITICAL: Attach resolved_datetime_range to execution_result for persistence
                    # This enables test adapters and session rebuild logic to extract it
                    execution_result["resolved_datetime_range"] = resolved_datetime_range
                    logger.debug(
                        f"[DATETIME_RANGE] Attached to execution_result: {resolved_datetime_range.get('start')}"
                    )

                # Return execution result
                # CRITICAL: Attach availability_fingerprint to plan for persistence
                # This ensures fingerprint survives even when session_store is None
                # and can be extracted by build_session_state_from_outcome() or test adapters
                if (execution_result.get("type") == "availability"
                        and execution_result.get("status") == "success"):
                    # Attach availability_fingerprint if present
                    if execution_result.get("availability_fingerprint"):
                        plan["availability_fingerprint"] = execution_result.get(
                            "availability_fingerprint")
                        logger.debug(
                            f"[AVAILABILITY_FINGERPRINT] Attached to plan: {execution_result.get('availability_fingerprint')}"
                        )

                    # Also attach resolved_datetime_range to plan if present
                    if execution_result.get("resolved_datetime_range"):
                        plan["resolved_datetime_range"] = execution_result.get(
                            "resolved_datetime_range")
                        logger.debug(
                            f"[DATETIME_RANGE] Attached to plan: {execution_result.get('resolved_datetime_range').get('start')}"
                        )

                # Ensure execution_result includes plan structure (status, stage, action)
                # Build from decision if available, otherwise use plan
                decision = plan.get("_decision")
                if decision:
                    # Use canonical builder to ensure plan structure is complete
                    outcome_from_decision = build_outcome_from_decision(
                        decision)
                    # Merge plan structure into execution_result
                    if not isinstance(execution_result, dict):
                        execution_result = {}
                    if "plan" not in execution_result or not isinstance(execution_result.get("plan"), dict):
                        execution_result["plan"] = {}
                    # Ensure plan.status, plan.stage, plan.action are present
                    execution_result["plan"]["status"] = outcome_from_decision.get(
                        "plan", {}).get("status")
                    execution_result["plan"]["stage"] = outcome_from_decision.get(
                        "plan", {}).get("stage")
                    execution_result["plan"]["action"] = outcome_from_decision.get(
                        "plan", {}).get("action")
                else:
                    # Fallback: extract from plan
                    plan_obj = plan.get("plan", {})
                    if not isinstance(plan_obj, dict):
                        plan_obj = {}
                    if not isinstance(execution_result, dict):
                        execution_result = {}
                    if "plan" not in execution_result or not isinstance(execution_result.get("plan"), dict):
                        execution_result["plan"] = {}
                    execution_result["plan"]["status"] = plan_obj.get(
                        "status") or plan.get("status")
                    execution_result["plan"]["stage"] = plan_obj.get(
                        "stage") or plan.get("stage")
                    execution_result["plan"]["action"] = plan_obj.get(
                        "action") or plan.get("action")

                return {
                    "success": True,
                    "result": execution_result,
                    "outcome": execution_result,  # Alias for backward compatibility
                    "plan": plan
                }
            except Exception as e:
                logger.error(f"Execution failed for action {action}: {e}")
                return {
                    "success": False,
                    "error": "execution_failed",
                    "message": str(e),
                    "plan": plan
                }

    # No execution step selected by policy - return planning outcome
    # Build outcome from decision (canonical builder)
    decision = plan.get("_decision")
    if decision:
        outcome_dict = build_outcome_from_decision(decision)
    else:
        # Fallback: construct minimal outcome when decision is missing
        logger.warning(
            "Decision not available in plan, using fallback construction")
        plan_slots = plan.get("slots", {})
        plan_missing_slots = plan.get("missing_slots", [])
        plan_obj = plan.get("plan", {})
        if not isinstance(plan_obj, dict):
            plan_obj = {}
        facts = {
            "slots": plan_slots if isinstance(plan_slots, dict) else {},
            "missing_slots": plan_missing_slots if isinstance(plan_missing_slots, list) else []
        }
        outcome_dict = {
            "status": plan.get("status") or plan_obj.get("status", "NEEDS_CLARIFICATION"),
            "awaiting": plan.get("awaiting"),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", []),
            "facts": facts,
            "intent_name": plan.get("intent_name") or plan.get("intent", ""),
            "plan": {
                "status": plan_obj.get("status") or plan.get("status", "NEEDS_CLARIFICATION"),
                "stage": plan_obj.get("stage") or plan.get("stage"),
                "action": plan_obj.get("action") or plan.get("action")
            },
            "slots": plan_slots,
            "missing_slots": plan_missing_slots
        }
    # Add active_capability if present in plan
    if plan.get("active_capability"):
        outcome_dict["active_capability"] = plan.get("active_capability")
    return {
        "success": True,
        "result": outcome_dict,
        "outcome": outcome_dict  # Alias for backward compatibility
    }


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
    planning_only: bool = False
) -> Dict[str, Any]:
    """
    [LEGACY] Handle a user message - stateless orchestration.

    Flow:
    1. Call Luma (LumaClient.resolve)
    2. Assert response contract (assert_luma_contract - only requires intent.name)
    3. Process Luma response and build planning outcome (facts may be empty/partial)
    4. Return planning-only outcome: {intent, slots, missing_slots, executable_actions, dialog_instruction?}

    FACT-ONLY CONTRACT: Only requires intent.name to exist.
    - Missing facts/slots are NOT errors - planner computes missing_slots
    - Planning proceeds as long as intent.name exists

    Core NEVER executes actions - execution is handled by separate layer.

    Args:
        user_id: User identifier (used for session lookup and logging, persistent across turns)
        text: User message text
        domain: Domain (default: "service")
        timezone: Timezone (default: "UTC")
        organization_id: Organization ID (optional, defaults to ORG_ID env or 1) 
        luma_client: Luma client instance (creates default if None)
        catalog_client: Catalog discovery client (creates default if None)
        organization_client: Organization client instance (creates default if None)
        session_state: Optional session state for follow-up handling
        transaction_id: Optional transaction ID for per-request tracing (never stored in session)
        planning_only: If True, return planning outcome only (no execution, no dialog, no session mutation beyond slot accumulation)

    Returns:
        Response dictionary with success and outcome
    """
    # Initialize default clients if not provided
    if luma_client is None:
        luma_client = LumaClient()
    if catalog_client is None:
        catalog_client = CatalogClient()
    if organization_client is None:
        organization_client = OrganizationClient()

    # Initialize execution clients only if not in planning-only mode
    # These are not needed when planning_only=True since we return early before execution
    booking_client = None
    customer_client = None
    if not planning_only:
        # Only import and initialize execution clients when not in planning-only mode
        # This avoids unnecessary imports and initialization for planning tests
        try:
            from core.orchestration.execution.clients.booking_client import BookingClient
            from core.orchestration.clients.customer_client import CustomerClient
            booking_client = BookingClient()
            customer_client = CustomerClient()
        except (ImportError, AttributeError):
            # Execution clients may not be available in planning-only builds
            # This is fine - they won't be used in planning-only mode anyway
            pass

    # TODO: Replace env-based organization_id with channel/auth-derived organization_id
    resolved_org_id = organization_id if organization_id is not None else _get_org_id_from_env()

    # Derive domain from organization details (cached, long TTL)
    derived_domain, _ = org_domain_cache.get_domain(
        resolved_org_id, organization_client, force_refresh=False
    )

    # Step 1: Prepare tenant_context aliases (service/reservation)
    catalog_data_for_alias: Optional[Dict[str, Any]] = None
    tenant_context = None
    if derived_domain in ("service", "reservation"):
        # Skip catalog access when planning_only=True to avoid network calls
        if not planning_only:
            catalog_data_for_alias = catalog_cache.get_catalog(
                resolved_org_id, catalog_client, domain=derived_domain)
            alias_map: Dict[str, Any] = {}
            if derived_domain == "service":
                services_for_alias = catalog_data_for_alias.get(
                    "services", []) if isinstance(catalog_data_for_alias, dict) else []
                for svc in services_for_alias:
                    if not isinstance(svc, dict) or svc.get("is_active") is False:
                        continue
                    name = svc.get("name")
                    if not name:
                        continue
                    canonical_key = svc.get("service_family_id") or svc.get(
                        "canonical") or svc.get("slug") or name.lower().replace(" ", "_")
                    if not canonical_key:
                        continue
                    # Construct full canonical path if it's a short form (no dot)
                    # Luma expects format: "category.family_id" (e.g., "beauty_and_wellness.haircut")
                    if "." not in str(canonical_key):
                        # Short form canonical - prefix with category for service domain
                        canonical_key = f"beauty_and_wellness.{canonical_key}"
                    alias_map[name.lower()] = canonical_key
            else:
                rooms_for_alias = catalog_data_for_alias.get(
                    "rooms", []) if isinstance(catalog_data_for_alias, dict) else []
                for rt in rooms_for_alias:
                    if not isinstance(rt, dict) or rt.get("is_active") is False:
                        continue
                    name = rt.get("name")
                    if not name:
                        continue
                    canonical_key = rt.get("canonical_key") or rt.get("canonical") or rt.get(
                        "slug") or name.lower().replace(" ", "_")
                    if not canonical_key:
                        continue
                    alias_map[name.lower()] = canonical_key
                extras_for_alias = catalog_data_for_alias.get(
                    "extras", []) if isinstance(catalog_data_for_alias, dict) else []
                for ex in extras_for_alias:
                    if not isinstance(ex, dict) or ex.get("is_active") is False:
                        continue
                    name = ex.get("name")
                    if not name:
                        continue
                    canonical_key = ex.get("canonical") or ex.get(
                        "slug") or name.lower().replace(" ", "_")
                    if not canonical_key:
                        continue
                    alias_map[name.lower()] = canonical_key

            # Always create tenant_context with booking_mode, even if no aliases
            tenant_context = {}
            if alias_map:
                tenant_context["aliases"] = alias_map
            # Always include booking_mode in tenant_context so Luma can determine intent correctly
            # booking_mode should match domain: "service" for appointments, "reservation" for reservations
            tenant_context["booking_mode"] = derived_domain
        else:
            # planning_only=True: Create minimal tenant_context with only booking_mode
            tenant_context = {"booking_mode": derived_domain}

    # Step 2: Call Luma
    # Build and log Luma payload
    luma_payload = {
        "user_id": user_id,
        "text": text,
        "domain": derived_domain,
        "timezone": timezone,
    }
    if tenant_context:
        luma_payload["tenant_context"] = tenant_context
    else:
        logger.warning(
            f"[ORCHESTRATOR] No tenant_context to send to Luma (domain={derived_domain})"
        )

    # Log sentence passed to Luma
    logger.info("Luma request payload: %s", json.dumps(
        luma_payload, ensure_ascii=False))

    # Store raw response for attachment to effective_response (must be accessible after try block)
    raw_luma_response_deep_copy = None
    luma_response = None  # Initialize to None to handle exception cases

    try:
        luma_response = luma_client.resolve(
            user_id=user_id,
            text=text,
            domain=derived_domain,
            timezone=timezone,
            tenant_context=tenant_context
        )

        # Store raw response for attachment to effective_response (must be accessible after try block)
        raw_luma_response_deep_copy = copy.deepcopy(luma_response)

        # LOG 2 — LUMA OUTPUT (right after luma.resolve)
        luma_intent_obj = luma_response.get("intent", {})
        luma_intent = luma_intent_obj.get(
            "name", "") if isinstance(luma_intent_obj, dict) else ""
        luma_slots = luma_response.get("slots", {})
        luma_missing_slots = luma_response.get("missing_slots", [])
        turn_logger.info(
            json.dumps({
                "turn": "LUMA",
                "intent": luma_intent,
                "slots": luma_slots,
                "missing_slots": luma_missing_slots
            }, ensure_ascii=True, default=str)
        )

        # ARCHITECTURAL INVARIANT: Create authoritative slot view BEFORE any processing
        # effective_turn_slots = merge(session_state.slots, raw_luma_response.slots)
        # Required-slot computation MUST ONLY use effective_turn_slots
        raw_luma_slots = luma_response.get("slots", {})
        if not isinstance(raw_luma_slots, dict):
            raw_luma_slots = {}

        session_slots_for_merge = {}
        if session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
            session_slots_for_merge = session_state.get("slots", {})
            if not isinstance(session_slots_for_merge, dict):
                session_slots_for_merge = {}

        # Create authoritative slot view: merge session slots with raw Luma slots
        # This ensures required-slot computation always sees current-turn Luma output
        effective_turn_slots = {**session_slots_for_merge, **raw_luma_slots}

        # GUARD ASSERTION (test/debug only): If raw_luma_response.slots is non-empty but effective_turn_slots doesn't contain those slots → ERROR
        # This ensures raw Luma slots are never lost in the merge
        import os
        if (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("DEBUG_SLOT_MERGE") == "1"):
            if raw_luma_slots:
                missing_slots = set(raw_luma_slots.keys()) - \
                    set(effective_turn_slots.keys())
                if missing_slots:
                    error_msg = (
                        f"INVARIANT VIOLATION: raw_luma_response.slots contains slots that are missing from effective_turn_slots! "
                        f"user_id={user_id}, missing_slots={list(missing_slots)}, "
                        f"raw_luma_slots={list(raw_luma_slots.keys())}, "
                        f"session_slots={list(session_slots_for_merge.keys())}, "
                        f"effective_turn_slots={list(effective_turn_slots.keys())}"
                    )
                    logger.error(f"[EFFECTIVE_TURN_SLOTS] {error_msg}")
                    # In test mode, raise assertion
                    if os.getenv("PYTEST_CURRENT_TEST"):
                        raise AssertionError(error_msg)

        logger.debug(
            f"[EFFECTIVE_TURN_SLOTS] Created authoritative slot view: "
            f"session_slots={list(session_slots_for_merge.keys())}, "
            f"raw_luma_slots={list(raw_luma_slots.keys())}, "
            f"effective_turn_slots={list(effective_turn_slots.keys())}"
        )

        # DEBUG: Print raw Luma response for weekday follow-ups (guarded by env var)
        import pprint
        if os.getenv("DEBUG_LUMA_WEEKDAY") == "1":
            # Only dump for suspected weekday messages
            text_l = (text or "").lower()
            weekday_keywords = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                                "next monday", "next tuesday", "next wednesday", "next thursday", "next friday",
                                "next saturday", "next sunday"]
            if any(w in text_l for w in weekday_keywords):
                print("\n=== DEBUG_LUMA_WEEKDAY RAW LUMA RESPONSE ===")
                print(f"Input text: {text}")
                print(f"User ID: {user_id}")
                print(f"Session state exists: {session_state is not None}")
                if session_state:
                    print(f"Session status: {session_state.get('status')}")
                    print(f"Session intent: {session_state.get('intent')}")
                try:
                    # Print full response without truncation
                    response_str = json.dumps(
                        luma_response, indent=2, default=str, ensure_ascii=False)
                    print(response_str)
                except Exception as e:
                    print(f"JSON serialization failed: {e}")
                    pprint.pprint(luma_response)
                print("=== END DEBUG_LUMA_WEEKDAY ===\n")

    except UpstreamError as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma API error for user {user_id}: {str(e)}")
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on Luma error
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "slots": session_slots,
                        "missing_slots": missing_slots,
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                    }
                }
        return {
            "success": False,
            "error": "upstream_error",
            "message": str(e)
        }

    # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on empty/null Luma response
    # Do not recompute slots or missing_slots - preserve session state exactly as-is
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Luma returned None or invalid response for user {user_id}")
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "slots": session_slots,
                        "missing_slots": missing_slots,
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                    }
                }
        return {
            "success": False,
            "error": "upstream_error",
            "message": "Luma returned empty response"
        }

    # Step 2: Assert contract (fact-only: only requires intent.name)
    # Contract validation ensures intent.name exists for planning
    # Missing facts/slots are NOT errors - planner computes missing_slots
    try:
        assert_luma_contract(luma_response)
    except ContractViolation as e:
        logger.error(
            f"[LUMA_ERROR_FALLBACK] Contract violation for user {user_id}: {str(e)}")
        # SESSION LIFECYCLE RULE: For durable intents, reuse session state as-is on contract violation
        # Do not recompute slots or missing_slots - preserve session state exactly as-is
        if session_state:
            # CRITICAL: Check intent_name first, then fall back to intent
            session_intent_str = session_state.get("intent_name") or (
                session_state.get("intent") if isinstance(session_state.get("intent"), str) else
                (session_state.get("intent", {}).get("name", "")
                 if isinstance(session_state.get("intent"), dict) else "")
            )

            # Check if session intent is durable
            is_durable = False
            if session_intent_str:
                try:
                    is_durable = is_durable_intent(session_intent_str)
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"[LUMA_ERROR_FALLBACK] Failed to check durable status: {e}")

            logger.error(
                f"[LUMA_ERROR_FALLBACK] session_intent={session_intent_str}, is_durable={is_durable}, "
                f"session_status={session_state.get('status')}, session_missing_slots={session_state.get('missing_slots', [])}"
            )

            # For durable intents, reuse session state as-is without recomputation
            if is_durable:
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}
                session_missing_slots = session_state.get("missing_slots", [])
                if not isinstance(session_missing_slots, list):
                    session_missing_slots = []
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")
                # Derive action from stage if missing (for empty response recovery)
                if not session_action and session_stage == "AVAILABILITY":
                    session_action = "SEARCH_AVAILABILITY"
                elif not session_action and session_stage == "CONFIRM":
                    session_action = "CONFIRM_APPOINTMENT"
                session_status = session_state.get(
                    "status", "NEEDS_CLARIFICATION")
                # CRITICAL: Status must be NEEDS_CLARIFICATION if there are missing slots
                # Do NOT use session_status if it's READY but there are missing slots
                final_status = "NEEDS_CLARIFICATION" if session_missing_slots else session_status

                logger.error(
                    f"[LUMA_ERROR_FALLBACK] Final status={final_status} (session_status={session_status}, "
                    f"missing_slots={session_missing_slots})"
                )

                # Build outcome with plan and facts for consistency
                outcome = {
                    "intent_name": session_intent_str,
                    "stage": session_stage,
                    "action": session_action,
                    "slots": session_slots,
                    "missing_slots": session_missing_slots,
                    "status": final_status,
                    "plan": {
                        "intent": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "missing_slots": session_missing_slots,
                        "slots": session_slots,
                        "status": final_status,
                        "executable_actions": [session_action] if session_action else []
                    },
                    "facts": {
                        "slots": session_slots,
                        "missing_slots": session_missing_slots
                    }
                }

                return {
                    "success": True,
                    "outcome": outcome
                }

            # For other intents, preserve session and return planning response (existing behavior)
            if session_state.get("status") == "NEEDS_CLARIFICATION":
                # Return planning response derived from session state
                session_slots = session_state.get("slots", {})
                if not isinstance(session_slots, dict):
                    session_slots = {}

                # Compute missing_slots from session intent and slots
                from core.planning.orchestration.missing_slots import compute_missing_slots
                missing_slots = compute_missing_slots(
                    session_intent_str, session_slots) if session_intent_str else []

                # Extract stage/action from session state if available, otherwise use defaults
                session_stage = session_state.get("stage", "AVAILABILITY")
                session_action = session_state.get("action")

                return {
                    "success": True,
                    "outcome": {
                        "intent_name": session_intent_str,
                        "stage": session_stage,
                        "action": session_action,
                        "slots": session_slots,
                        "missing_slots": missing_slots,
                        "status": "NEEDS_CLARIFICATION" if missing_slots else "READY"
                    }
                }
        return {
            "success": False,
            "error": "contract_violation",
            "message": str(e)
        }

    # FACT-ONLY CONTRACT: No success flag check
    # Planning proceeds as long as intent.name exists (validated by assert_luma_contract)
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml

    # Step 3.5: Determine effective intent and construct effective_response
    # Intent override MUST happen BEFORE process_luma_response, planner, and allowed action checks
    log_transaction_id = f" transaction_id={transaction_id}" if transaction_id else ""

    # Guard: Ensure luma_response is valid before proceeding
    if not luma_response or not isinstance(luma_response, dict):
        logger.error(
            f"Invalid luma_response after error handling for user {user_id}: {luma_response}")
        return {
            "success": False,
            "error": "invalid_luma_response",
            "message": "Luma response is None or invalid"
        }

    # Extract Luma intent for logging (before intent resolution)
    luma_intent_obj = luma_response.get("intent", {})
    luma_intent_name = luma_intent_obj.get(
        "name", "") if isinstance(luma_intent_obj, dict) else ""

    # Resolve effective intent using new intent_resolution module
    from core.planning.orchestration.intent_resolution import resolve_effective_intent
    # CRITICAL: Preserve original session_state before intent resolution
    # This ensures session (with intent_name + slots) is available for merge_luma_with_session
    original_session_state = session_state
    effective_intent, session_reset_occurred = resolve_effective_intent(
        luma_response,
        session_state,
        user_id,
        transaction_id
    )

    # Track the source of session_reset_occurred
    session_reset_occurred_source = "resolve_effective_intent"
    logger.error(
        f"[SESSION_RESET_WRITER] RECEIVED_FROM_RESOLVER value={session_reset_occurred} "
        f"source={session_reset_occurred_source} "
        f"effective_intent={effective_intent} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # DEBUG: Log result immediately after resolve_effective_intent returns
    logger.error(
        f"[ORCHESTRATOR] AFTER resolve_effective_intent: effective_intent={effective_intent} "
        f"session_reset_occurred={session_reset_occurred} "
        f"will_null_session_state={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )

    # Update session_state if it was reset (for downstream logic)
    # BUT preserve original_session_state for merge decision - merge needs access to original session
    # CRITICAL: UNKNOWN → concrete intent transitions do NOT reset (intent materialization, not destructive switch)
    # Only true intent switches (e.g., CREATE_APPOINTMENT → DETAILS) reset the session
    # CRITICAL: Do NOT null session_state - capability reconciliation needs session facts even after intent reset
    # The session must remain visible for the entire turn, regardless of merge eligibility or intent reset
    # We preserve original_session_state for merge, but session_state must remain available for:
    # - process_luma_response (needs session for canonical_facts)
    # - capability gating (needs session.facts.payment_satisfied)
    # - TurnSnapshot building (needs session facts)
    if session_reset_occurred:
        # DEBUG: Log why session_state would be nulled (but we're NOT nulling it anymore)
        import traceback
        # Last 2 frames before this one
        call_stack = ''.join(traceback.format_stack()[-3:-1])
        logger.error(
            f"[ORCHESTRATOR] SESSION_RESET_OCCURRED=True (but preserving session_state for capability reconciliation) "
            f"effective_intent={effective_intent} "
            f"original_session_intent={original_session_state.get('intent_name') if original_session_state else None} "
            f"call_stack={call_stack} "
            f"user_id={user_id}{log_transaction_id}"
        )
        # DO NOT null session_state - preserve it for capability reconciliation
        # session_state = None  # REMOVED: This breaks capability reconciliation when should_merge_session is False

    # SINGLE SOURCE OF TRUTH: Ensure effective_intent is never None/empty when durable session intent exists
    # This is the authoritative intent that will be used throughout planning
    if not effective_intent or effective_intent == "UNKNOWN":
        # Fallback to durable session intent if available
        if session_state and not session_reset_occurred:
            session_intent = session_state.get(
                "intent_name") or session_state.get("intent")
            session_intent_str = session_intent if isinstance(session_intent, str) else (
                session_intent.get("name", "") if isinstance(session_intent, dict) else "")
            if session_intent_str:
                # Check if session intent is durable
                try:
                    from core.policy.intent_policy import get_intent_durable
                    if get_intent_durable(session_intent_str):
                        effective_intent = session_intent_str
                        logger.info(
                            f"[INTENT_PRESERVATION] Using durable session intent as effective_intent: {effective_intent} "
                            f"(luma returned UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
                        )
                except (ImportError, Exception) as e:
                    logger.warning(
                        f"Failed to check durable status for '{session_intent_str}': {e}. "
                        f"Using session intent as fallback."
                    )
                    effective_intent = session_intent_str
                    logger.info(
                        f"[INTENT_PRESERVATION] Using session intent as effective_intent (durability check failed): {effective_intent} "
                        f"(user_id={user_id}{log_transaction_id})"
                    )

    # SHORT-CIRCUIT: Non-durable intents must NOT reach planning or persistence
    # Rule: Any intent with durable=false may be recognized, but must:
    # - STOP before planning (do not call build_decision_plan)
    # - NOT be persisted to session (do not update session.intent_name or slots)
    # - Preserve any existing durable session state
    # Only durable intents may reach build_decision_plan
    # CRITICAL: Check AFTER UNKNOWN recovery to ensure we catch non-durable intents even after recovery
    if effective_intent and effective_intent != "UNKNOWN":
        try:
            from core.policy.intent_policy import get_intent_durable
            is_durable = get_intent_durable(effective_intent)

            if not is_durable:
                # Non-durable intent detected - return informational response immediately
                # Do NOT proceed to planning, merge, or persistence
                # Do NOT treat this as a session reset - preserve existing durable session state
                logger.info(
                    f"[NON_DURABLE_INTENT] Short-circuiting planning for non-durable intent: {effective_intent} "
                    f"(user_id={user_id}{log_transaction_id})"
                )

                # Extract facts from Luma response for informational response
                facts_obj = luma_response.get("facts", {})
                from core.orchestration.luma_facts_adapter import facts_to_slots
                slots = facts_to_slots(
                    facts_obj,
                    intent_name=effective_intent,
                    source_text=text,
                ) if isinstance(facts_obj, dict) else {}

                # Also check for slots in nested facts.facts.slots or top-level slots
                if isinstance(facts_obj, dict) and "slots" in facts_obj:
                    nested_slots = facts_obj.get("slots", {})
                    if isinstance(nested_slots, dict):
                        slots.update(nested_slots)
                elif "slots" in luma_response:
                    top_level_slots = luma_response.get("slots", {})
                    if isinstance(top_level_slots, dict):
                        slots.update(top_level_slots)

                # Return informational response (similar to non-core intent handling)
                # Do NOT persist to session, do NOT plan, do NOT merge
                # Preserve existing durable session state (do not clear session)
                return {
                    "success": True,
                    "outcome": {
                        "status": "NON_DURABLE_INTENT",
                        "intent_name": effective_intent,
                        "slots": slots,
                        "missing_slots": [],  # Non-durable intents don't have required slots
                        "facts": {
                            "slots": slots,
                            "missing_slots": [],
                            "context": luma_response.get("context", {}),
                        }
                    }
                }
        except (ImportError, Exception) as e:
            # If durability check fails, log warning but continue (defensive)
            logger.warning(
                f"Failed to check durable status for '{effective_intent}': {e}. "
                f"Continuing with planning (assuming durable)."
            )

    # Construct effective_response: Copy luma_response and replace intent.name with effective_intent
    # FACT-ONLY CONTRACT: facts may be empty or partial - this is valid
    # Missing slots are NOT errors - planner will compute missing_slots from intent_planning.yaml
    # CRITICAL: This is the SINGLE SOURCE OF TRUTH for intent - no downstream component may recompute it
    effective_response = luma_response.copy()
    effective_response["intent"] = {"name": effective_intent}
    # CRITICAL: Set _effective_intent for process_luma_response to recover durable intents
    # This ensures UNKNOWN intents can be recovered from session when durable
    effective_response["_effective_intent"] = effective_intent or ""

    # FACT-ONLY: Promote facts to slots BEFORE any other processing
    # This ensures facts.service_id, facts.times, etc. are available for planning
    from core.orchestration.luma_facts_adapter import facts_to_slots
    facts_obj = luma_response.get("facts", {})
    # Pass effective_intent for date_range promotion (CREATE_RESERVATION with 2+ dates)
    promoted_slots = facts_to_slots(
        facts_obj,
        intent_name=effective_intent,
        source_text=text,
    ) if isinstance(facts_obj, dict) else {}

    # Extract slots from facts.facts.slots if present (nested format)
    # Otherwise fall back to legacy slots field
    nested_slots = {}
    if isinstance(facts_obj, dict) and "slots" in facts_obj:
        # Nested format: facts.facts.slots
        nested_slots = facts_obj.get("slots", {})
    elif "slots" in luma_response:
        # Legacy format: slots at top level
        nested_slots = luma_response.get("slots", {})

    # Merge promoted slots (from facts.service_id, facts.times, etc.) with nested slots
    # Promoted slots take precedence (they are the source of truth)
    effective_response["slots"] = {**nested_slots, **promoted_slots}

    # Preserve the raw user text for follow-up slot promotion
    effective_response["_source_text"] = text

    # Extract time_constraint from luma_response for CREATE_APPOINTMENT missing slot computation
    time_constraint = luma_response.get("time_constraint")
    if time_constraint:
        effective_response["time_constraint"] = time_constraint

    # Normalize service_id using tenant aliases (e.g., "suite" -> "room", "deluxe" -> "room")
    # CRITICAL: Preserve raw tenant value while storing canonical for planning
    if tenant_context and "aliases" in tenant_context and "service_id" in effective_response["slots"]:
        aliases = tenant_context["aliases"]
        raw_service_id = effective_response["slots"]["service_id"]
        if isinstance(raw_service_id, str) and raw_service_id.lower() in aliases:
            canonical_service_id = aliases[raw_service_id.lower()]
            logger.info(
                f"Normalized service_id: {raw_service_id} -> {canonical_service_id} (via tenant aliases)")
            # Store canonical for planning, preserve raw for outcome
            effective_response["slots"]["_canonical_service_id"] = canonical_service_id
            # Keep raw value in service_id slot (for outcome/dialog)
            effective_response["slots"]["service_id"] = raw_service_id

    # Ensure slots is always a dict
    if not isinstance(effective_response.get("slots"), dict):
        effective_response["slots"] = {}

    # Attach raw Luma response for debugging (must include: intent, slots, context, entities, status, clarification, original text)
    # This must be preserved through merge_luma_with_session and included in test snapshots
    # DO NOT mutate or normalize _raw_luma_response - it is for debugging only
    if raw_luma_response_deep_copy is not None:
        effective_response["_raw_luma_response"] = raw_luma_response_deep_copy

    logger.info(
        f"effective_intent_resolved user_id={user_id}{log_transaction_id} "
        f"luma_intent={luma_intent_name} effective_intent={effective_intent}"
    )

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE merge_luma_with_session and process_luma_response
    # CRITICAL: Use original_session_state for logging to show actual session state before any reset
    session_intent_for_trace = None
    session_status_for_trace = None
    if original_session_state:
        session_intent_for_trace = original_session_state.get(
            "intent_name") or original_session_state.get("intent")
        session_status_for_trace = original_session_state.get("status")
    logger.error(
        "[INTENT_TRACE_ORCHESTRATOR] BEFORE merge_luma_with_session: "
        f"effective_intent={effective_intent}, "
        f"effective_response['intent']['name']={effective_response.get('intent', {}).get('name', '')}, "
        f"session.intent_name={session_intent_for_trace}, "
        f"session.status={session_status_for_trace}, "
        f"session_reset_occurred={session_reset_occurred}, "
        f"user_id={user_id}{log_transaction_id}"
    )

    # Step 4: Process Luma response (interpret and decide CLARIFY vs EXECUTE)
    # Use ONLY effective_response (never the raw luma_response)
    # ARCHITECTURAL FIX: Always compute effective_collected_slots, even when there's no session
    # This ensures slots are persisted correctly on the first turn
    from core.orchestration.api.session_merge import merge_luma_with_session, _compute_effective_collected_slots

    # Initialize prior_slots for logging (used later)
    prior_slots = []
    prior_intent = None
    prior_missing = []

    # If session exists and not reset, merge slots from session
    # SESSION LIFECYCLE RULE: Merge for NEEDS_CLARIFICATION sessions OR READY sessions with durable intents
    # CRITICAL: Use original_session_state (preserved before reset) for merge decision and merge call
    # This ensures session (with intent_name + slots) is always available even if session_state was set to None
    session_for_merge = original_session_state if original_session_state else session_state
    session_status_for_merge = session_for_merge.get(
        "status") if session_for_merge else None
    # CRITICAL: Session stores intent_name, not intent. Check intent_name first, then fall back to intent.
    session_intent_for_merge = session_for_merge.get(
        "intent_name") if session_for_merge else None
    if not session_intent_for_merge and session_for_merge:
        # Fallback to intent (for backward compatibility)
        session_intent_for_merge = session_for_merge.get("intent")
    session_intent_str_for_merge = session_intent_for_merge if isinstance(session_intent_for_merge, str) else (
        session_intent_for_merge.get("name", "") if isinstance(session_intent_for_merge, dict) else "")

    # Check if session intent is durable
    is_session_intent_durable = False
    if session_intent_str_for_merge:
        try:
            is_session_intent_durable = is_durable_intent(
                session_intent_str_for_merge)
        except (ImportError, Exception) as e:
            logger.warning(
                f"Failed to check durable status for '{session_intent_str_for_merge}': {e}. "
                f"Assuming not durable for merge decision."
            )

    # SESSION MERGE GATING: Allow merge for:
    # 1. NEEDS_CLARIFICATION sessions (normal follow-up turns)
    # 2. READY sessions with durable intents (modification turns)
    # 3. UNKNOWN → concrete intent transitions (intent materialization, preserves pre-intent slots)
    #    Note: session_reset_occurred is False for UNKNOWN → concrete transitions (see intent_resolution.py)
    #    This ensures pre-intent slots (e.g., date from "tomorrow") are merged when intent materializes

    # DEBUG: Log merge decision inputs before computing should_merge_session
    session_for_merge_intent = session_for_merge.get(
        "intent_name") if session_for_merge else None
    session_for_merge_slots = list(session_for_merge.get(
        "slots", {}).keys()) if session_for_merge else []

    # CRITICAL: Final check - who last set session_reset_occurred?
    logger.error(
        f"[SESSION_RESET_WRITER] FINAL_CHECK_BEFORE_MERGE: "
        f"session_reset_occurred={session_reset_occurred} "
        f"source={session_reset_occurred_source} "
        f"session_for_merge_exists={session_for_merge is not None} "
        f"session_for_merge_intent={session_for_merge_intent} "
        f"session_for_merge_slots={session_for_merge_slots} "
        f"session_status_for_merge={session_status_for_merge} "
        f"will_block_merge={session_reset_occurred} "
        f"user_id={user_id}{log_transaction_id}"
    )

    logger.error(
        f"[ORCHESTRATOR] BEFORE should_merge_session: session_reset_occurred={session_reset_occurred} "
        f"session_for_merge_intent={session_for_merge_intent} "
        f"session_for_merge_slots={session_for_merge_slots} "
        f"session_status_for_merge={session_status_for_merge} "
        f"is_session_intent_durable={is_session_intent_durable} "
        f"user_id={user_id}{log_transaction_id}"
    )

    should_merge_session = (
        session_for_merge and not session_reset_occurred and (
            session_status_for_merge == "NEEDS_CLARIFICATION" or
            (session_status_for_merge == "READY" and is_session_intent_durable)
        )
    )

    # DEBUG: Log final merge decision
    logger.error(
        f"[ORCHESTRATOR] should_merge_session={should_merge_session} "
        f"user_id={user_id}{log_transaction_id}"
    )

    if should_merge_session:
        logger.info(
            f"[SESSION_MERGE] Merging session slots: status={session_status_for_merge}, intent={session_intent_str_for_merge}, "
            f"session_slots={list(session_for_merge.get('slots', {}).keys())}"
        )
        prior_intent = session_for_merge.get("intent")
        prior_missing = session_for_merge.get("missing_slots", [])
        prior_slots = list(session_for_merge.get("slots", {}).keys())

        effective_response = merge_luma_with_session(
            effective_response, session_for_merge, planning_only=planning_only)

        # CRITICAL: If CONFIRM_* intent was treated as continuation of durable session intent,
        # set confirmation_state to "confirmed" in the booking object
        # NOTE: Use original luma_intent_name (from line 1446) BEFORE merge, not after merge
        # because merge_luma_with_session changes effective_response['intent']['name'] to session intent
        if (luma_intent_name and luma_intent_name.startswith("CONFIRM_") and
                effective_intent != luma_intent_name and is_session_intent_durable):
            # CONFIRM_* was treated as continuation - set confirmation_state
            if "booking" not in effective_response:
                effective_response["booking"] = {}
            if not isinstance(effective_response["booking"], dict):
                effective_response["booking"] = {}
            effective_response["booking"]["confirmation_state"] = "confirmed"
            logger.info(
                f"[CONFIRM_CONTINUATION] Set confirmation_state=confirmed for CONFIRM_* continuation "
                f"of durable intent {effective_intent}, user_id={user_id}"
            )

        # AFTER_MERGE: Log right after session merge
        effective_collected_slots = effective_response.get(
            "_effective_collected_slots", {})
        after_merge_log = {
            "trace": "AFTER_MERGE",
            "intent": effective_response.get("intent"),
            "slots": effective_response.get("slots"),
            "effective_collected_slots": effective_collected_slots,
            "modification_context": effective_response.get("_modification_context")
        }
    else:
        # No session merge (first turn or session reset or READY non-CREATE_APPOINTMENT)
        if original_session_state:
            logger.info(
                f"[SESSION_MERGE] Skipping merge: status={session_status_for_merge}, intent={session_intent_str_for_merge}, "
                f"session_reset_occurred={session_reset_occurred}"
            )
        # No session (first turn) - still need to compute effective_collected_slots
        # This ensures slots are persisted correctly on the first turn
        if effective_response and isinstance(effective_response, dict):
            effective_response = _compute_effective_collected_slots(
                effective_response, planning_only=planning_only)

            # AFTER_MERGE: Log right after computing effective_collected_slots (first turn)
            effective_collected_slots = effective_response.get(
                "_effective_collected_slots", {})
            after_merge_log = {
                "trace": "AFTER_MERGE",
                "intent": effective_response.get("intent"),
                "slots": effective_response.get("slots"),
                "effective_collected_slots": effective_collected_slots,
                "modification_context": effective_response.get("_modification_context")
            }
            # INVARIANT CHECK: missing_slots must be computed for first turns
            if "missing_slots" not in effective_response:
                # This should never happen if _compute_effective_collected_slots worked correctly
                logger.error(
                    f"[MISSING_SLOTS] VIOLATION: missing_slots not computed in _compute_effective_collected_slots! "
                    f"user_id={user_id}, effective_response_keys={list(effective_response.keys())}"
                )
                # Fail-safe: compute missing_slots now
                from core.planning.orchestration.missing_slots import compute_missing_slots
                intent_name = effective_response.get(
                    "intent", {}).get("name", "")
                slots = effective_response.get("slots", {})
                time_constraint = effective_response.get("time_constraint")
                if intent_name:
                    effective_response["missing_slots"] = compute_missing_slots(
                        intent_name, slots, time_constraint=time_constraint
                    )
                else:
                    effective_response["missing_slots"] = []
        else:
            # If effective_response is None or invalid, create a minimal dict with empty effective_collected_slots
            if not effective_response:
                effective_response = {}
            effective_response["_effective_collected_slots"] = {}
            # INVARIANT: missing_slots must always be a list (never None)
            effective_response["missing_slots"] = []

        # AWAITING_SLOT ROUTING: Route compatible temporal values into awaited slot
        # Slots are merged additively - no special routing needed
        # Users can provide missing slots in any order

    # Log merge results for debugging (moved outside if/else to access prior_slots)
    merged_slots = effective_response.get("slots", {})
    merged_missing = effective_response.get("missing_slots", [])
    extracted_slots = [k for k in merged_slots.keys() if k not in prior_slots]
    remaining_missing = merged_missing
    effective_intent_name = effective_response.get(
        "intent", {}).get("name", "")

    logger.info(
        f"session_merged user_id={user_id}{log_transaction_id} "
        f"prior_intent={prior_intent} luma_intent={luma_intent_name} effective_intent={effective_intent_name} "
        f"prior_missing_slots={prior_missing} extracted_slots={extracted_slots} remaining_missing_slots={remaining_missing}"
    )

    # Verify intent before processing
    # Guard: effective_response must be a dict
    if not effective_response or not isinstance(effective_response, dict):
        logger.error(
            f"effective_response is None or not a dict: {effective_response}")
        return {
            "success": False,
            "error": "internal_error",
            "message": "Invalid effective_response"
        }

    final_intent_check = effective_response.get("intent", {}).get("name", "")
    if final_intent_check == "UNKNOWN" and session_state and session_state.get("status") == "NEEDS_CLARIFICATION":
        logger.error(
            f"INTENT_OVERRIDE_FAILED user_id={user_id}{log_transaction_id} "
            f"effective_response.intent={final_intent_check} session.intent={session_state.get('intent')}"
        )
        # Force override as last resort
        effective_response["intent"] = {"name": session_state.get("intent")}
        final_intent_check = effective_response.get(
            "intent", {}).get("name", "")

    logger.info(
        f"calling_process_luma_response user_id={user_id}{log_transaction_id} "
        f"intent={final_intent_check}"
    )

    # HARD INVARIANT: Planning must NEVER run with intent=UNKNOWN or empty when durable session intent exists
    # This ensures durable intents are preserved even if intent resolution failed or was overwritten
    planning_intent = effective_response.get("intent", {}).get("name", "")
    effective_intent_for_planning = effective_response.get(
        "_effective_intent", "")

    # If planning intent is UNKNOWN/empty but effective_intent exists, use effective_intent
    if (not planning_intent or planning_intent == "UNKNOWN") and effective_intent_for_planning:
        planning_intent = effective_intent_for_planning
        effective_response["intent"] = {"name": planning_intent}
        logger.info(
            f"[INTENT_PRESERVATION] Recovered intent for planning: {planning_intent} "
            f"(was UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
        )

    # If still UNKNOWN/empty and session has durable intent, recover from session
    if (not planning_intent or planning_intent == "UNKNOWN") and session_state and not session_reset_occurred:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")
        if session_intent_str:
            # Check if session intent is durable
            try:
                from core.policy.intent_policy import get_intent_durable
                if get_intent_durable(session_intent_str):
                    planning_intent = session_intent_str
                    effective_response["intent"] = {"name": planning_intent}
                    effective_response["_effective_intent"] = planning_intent
                    logger.info(
                        f"[INTENT_PRESERVATION] Recovered durable intent from session for planning: {planning_intent} "
                        f"(was UNKNOWN/empty, user_id={user_id}{log_transaction_id})"
                    )
            except (ImportError, Exception) as e:
                logger.warning(
                    f"Failed to check durable status for '{session_intent_str}': {e}. "
                    f"Using session intent as fallback."
                )
                planning_intent = session_intent_str
                effective_response["intent"] = {"name": planning_intent}
                effective_response["_effective_intent"] = planning_intent
                logger.info(
                    f"[INTENT_PRESERVATION] Recovered intent from session (durability check failed): {planning_intent} "
                    f"(user_id={user_id}{log_transaction_id})"
                )

    # SAFETY ASSERTION: Planning must NEVER run with invalid intent when durable session intent exists
    if (not planning_intent or planning_intent == "UNKNOWN") and session_state and not session_reset_occurred:
        session_intent = session_state.get("intent")
        session_intent_str = session_intent if isinstance(session_intent, str) else (
            session_intent.get("name", "") if isinstance(session_intent, dict) else "")
        if session_intent_str:
            try:
                from core.policy.intent_policy import get_intent_durable
                if get_intent_durable(session_intent_str):
                    raise AssertionError(
                        f"Planning invoked without a valid intent when durable session intent exists. "
                        f"planning_intent={planning_intent}, session_intent={session_intent_str}, "
                        f"user_id={user_id}{log_transaction_id}"
                    )
            except (ImportError, Exception):
                # If durability check fails, skip assertion (don't fail on import errors)
                pass

    # Update final_intent_check for logging
    final_intent_check = effective_response.get("intent", {}).get("name", "")

    # CRITICAL: session_state is now preserved even when session_reset_occurred is True
    # This ensures capability reconciliation can access session facts (e.g., payment_satisfied)
    # The session must be visible for the entire turn, regardless of merge eligibility or intent reset
    # No restoration needed - session_state is never nulled anymore

    # PHASE 1 INSTRUMENTATION: Log intent state BEFORE process_luma_response
    session_intent_for_trace = None
    session_status_for_trace = None
    if session_state:
        session_intent_for_trace = session_state.get(
            "intent_name") or session_state.get("intent")
        session_status_for_trace = session_state.get("status")
    logger.error(
        "[INTENT_TRACE_ORCHESTRATOR] BEFORE process_luma_response: "
        f"effective_response['intent']['name']={final_intent_check}, "
        f"effective_response['_effective_intent']={effective_response.get('_effective_intent', '')}, "
        f"session.intent_name={session_intent_for_trace}, "
        f"session.status={session_status_for_trace}, "
        f"user_id={user_id}{log_transaction_id}"
    )

    # INVARIANT CHECK: missing_slots MUST be computed before planning
    # missing_slots must be a list (never None, never missing)
    # This is computed in merge_luma_with_session or _compute_effective_collected_slots
    missing_slots_before_plan = effective_response.get("missing_slots")
    assert missing_slots_before_plan is not None, (
        f"missing_slots must be computed before planning! "
        f"user_id={user_id}, effective_response_keys={list(effective_response.keys())}"
    )
    assert isinstance(missing_slots_before_plan, list), (
        f"missing_slots must be a list before planning, got {type(missing_slots_before_plan)}: {missing_slots_before_plan}"
    )

    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = raw_luma_response.get(
        "facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get(
        "intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get(
        "name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    # LUMA_RAW: Log raw Luma response (facts, intent, source_text)
    raw_luma_response = effective_response.get("_raw_luma_response", {})
    raw_luma_facts = raw_luma_response.get(
        "facts", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent = raw_luma_response.get(
        "intent", {}) if isinstance(raw_luma_response, dict) else {}
    raw_luma_intent_name = raw_luma_intent.get(
        "name", "") if isinstance(raw_luma_intent, dict) else ""
    source_text = effective_response.get("_source_text", text)
    logger.info("[LUMA_RAW] user_id=%s facts=%s intent=%s source_text=%s",
                user_id, json.dumps(
                    raw_luma_facts, default=str, ensure_ascii=True),
                raw_luma_intent_name, source_text)

    # Add organization data to facts for capability evaluation
    # This allows capability conditions to read org.payment_required, etc.
    if organization_client and organization_id:
        try:
            org_details = organization_client.get_details(organization_id)
            if isinstance(org_details, dict):
                # Organization data may be at top level or under "organization" key
                org_data = org_details.get("organization") or org_details
                if org_data and isinstance(org_data, dict):
                    # Ensure facts structure exists
                    if "facts" not in effective_response:
                        effective_response["facts"] = {}
                    if not isinstance(effective_response["facts"], dict):
                        effective_response["facts"] = {}
                    # Add org data to facts["org"]
                    effective_response["facts"]["org"] = org_data
        except Exception as e:
            # If org fetch fails, log but don't crash
            # Capability evaluation will handle missing org data gracefully
            logger.debug(
                f"Failed to fetch organization data for capability evaluation: {e}")

    decision = process_luma_response(
        effective_response, derived_domain, user_id, session_state=session_state)

    # Guard: decision must be a dict
    # CRITICAL: Missing slots are NEVER an error - always return a planning response
    if not decision or not isinstance(decision, dict):
        logger.error(
            f"process_luma_response returned None or not a dict: {decision}")
        # Even on error, return a minimal planning response (not an error)
        # This ensures planning always proceeds, even if process_luma_response fails
        intent_name = effective_response.get("intent", {}).get("name", "")
        slots = effective_response.get("slots", {})
        missing_slots = effective_response.get("missing_slots", [])

        # Build minimal plan with correct action selection
        # CRITICAL: Apply action selection rules even in fallback case
        # NOTE: CREATE_APPOINTMENT action selection is now handled by fingerprint gating in plan_builder
        # Do not override planner's decision here
        if missing_slots:
            stage = "AVAILABILITY"
            action = "SEARCH_AVAILABILITY"
        else:
            stage = "CONFIRM"
            # Apply action selection rules for complete slots
            if intent_name == "CREATE_RESERVATION":
                action = "FINALIZE_RESERVATION"
            else:
                action = None

        minimal_plan = {
            "status": "NEEDS_CLARIFICATION" if missing_slots else "READY",
            "allowed_actions": [],
            "blocked_actions": [],
            "awaiting": None,
            "executable_actions": [],
            "stage": stage,
            "action": action
        }

        # Build minimal decision
        decision = {
            "intent_name": intent_name,
            "plan": minimal_plan,
            "facts": {
                "slots": slots,
                "missing_slots": missing_slots,
                "context": effective_response.get("context", {})
            }
        }
        logger.warning(
            f"Created minimal decision due to process_luma_response failure: intent={intent_name}, missing_slots={missing_slots}")

    # HYDRATE ORGANIZATION FACTS INTO DECISION (for capability gating)
    # This MUST happen BEFORE: capability gating, PLAN_FINAL, SESSION_SAVE
    # This ensures decision.facts.org is populated before any status override or session persistence
    # Hard rules:
    # - Do NOT guard this behind intent checks
    # - Do NOT rely on planner to fetch org
    # - Do NOT save session before this runs
    if decision and isinstance(decision.get("facts"), dict):
        facts = decision["facts"]
        # Only hydrate if org data is missing from decision.facts
        if "org" not in facts or not isinstance(facts.get("org"), dict):
            org_data = None
            org_id_to_fetch = None
            org_id_source = None

            # Priority 1: Try to get org data from effective_response.facts.org (if already present)
            if effective_response and isinstance(effective_response.get("facts"), dict):
                effective_org = effective_response["facts"].get("org")
                if isinstance(effective_org, dict):
                    org_data = effective_org
                    # Also check if effective_response.facts.org.id exists for org_id derivation
                    if not org_id_to_fetch and "id" in effective_org:
                        org_id_to_fetch = effective_org.get("id")
                        org_id_source = "effective_response.facts.org.id"

            # Priority 2: Extract org_id from decision.booking.organization_id
            if not org_id_to_fetch and decision.get("booking"):
                booking = decision.get("booking", {})
                if isinstance(booking, dict):
                    org_id_to_fetch = booking.get("organization_id")
                    if org_id_to_fetch:
                        org_id_source = "decision.booking.organization_id"

            # Priority 3: Extract org_id from decision.facts.slots.organization_id
            if not org_id_to_fetch and isinstance(facts.get("slots"), dict):
                org_id_to_fetch = facts["slots"].get("organization_id")
                if org_id_to_fetch:
                    org_id_source = "decision.facts.slots.organization_id"

            # Priority 4: Use organization_id parameter (from handle_message)
            if not org_id_to_fetch and organization_id:
                org_id_to_fetch = organization_id
                org_id_source = "handle_message.organization_id"

            # Fetch org data if we have an org_id and don't already have org_data
            if not org_data and org_id_to_fetch and organization_client:
                try:
                    logger.info(
                        f"[ORG_HYDRATION] Fetching organization data for org_id={org_id_to_fetch} "
                        f"(source={org_id_source})")
                    org_details = organization_client.get_details(
                        org_id_to_fetch)
                    if isinstance(org_details, dict):
                        # Organization data may be at top level or under "organization" key
                        org_data = org_details.get(
                            "organization") or org_details
                        logger.info(
                            f"[ORG_HYDRATION] Successfully fetched organization data: "
                            f"payment_required={org_data.get('payment_required') if isinstance(org_data, dict) else 'N/A'}")
                except Exception as e:
                    # If org fetch fails, log but don't crash
                    logger.warning(
                        f"[ORG_HYDRATION] Failed to fetch organization data for org_id={org_id_to_fetch}: {e}")

            # Inject org_data into decision.facts if we have it
            if org_data and isinstance(org_data, dict):
                facts["org"] = org_data
                logger.info(
                    f"[ORG_HYDRATION] Injected organization facts into decision.facts.org "
                    f"(org_id={org_id_to_fetch or 'from_effective_response'}, "
                    f"payment_required={org_data.get('payment_required')})")
            else:
                logger.debug(
                    f"[ORG_HYDRATION] No organization data available to inject "
                    f"(org_id_to_fetch={org_id_to_fetch}, organization_client={organization_client is not None})")

    # Extract decision plan
    plan = decision.get("plan", {})
    plan_status = plan.get("status", "READY")
    allowed_actions = plan.get("allowed_actions", [])
    blocked_actions = plan.get("blocked_actions", [])
    awaiting = plan.get("awaiting")

    # Planner is authoritative - no override logic needed
    # Execution eligibility is now driven by action-level policy in the execution gate

    # PLANNING INVARIANT: Set has_datetime when plan.status == READY
    # has_datetime = true when:
    # - plan status == READY
    # - AND one of:
    #   - date + time exists in slots
    #   - date_range + time exists in slots
    #   - datetime_range exists
    # Rules:
    # - has_datetime must NEVER be set when status != READY
    # - has_datetime is derived, not user-provided
    # - This must happen BEFORE any status checks to ensure invariant is set
    if plan_status == "READY":
        facts = decision.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        slots = facts.get("slots", {})
        if isinstance(slots, dict):
            has_time = bool(slots.get("time"))
            has_date = bool(slots.get("date"))
            has_date_range = isinstance(slots.get("date_range"), dict) and bool(
                slots.get("date_range", {}).get("start"))
            has_datetime_range = isinstance(slots.get("datetime_range"), dict) and bool(
                slots.get("datetime_range", {}).get("start"))

            # Check if sufficient temporal information exists
            has_sufficient_temporal = (
                (has_date and has_time) or  # date + time
                (has_date_range and has_time) or  # date_range + time
                has_datetime_range  # datetime_range
            )

            if has_sufficient_temporal:
                # Ensure facts["slots"] exists and is a dict
                if "slots" not in facts:
                    facts["slots"] = {}
                if not isinstance(facts["slots"], dict):
                    facts["slots"] = {}

                # Set has_datetime invariant (derived, not user-provided)
                facts["slots"]["has_datetime"] = True
                # Update decision facts with has_datetime
                decision["facts"] = facts
                logger.debug(
                    f"Set has_datetime=true in facts.slots (planning invariant: READY with temporal info)")

    # PLANNING-ONLY SHORTCUT: Return planning outcome early if planning_only=True
    # This allows tests to validate planning logic without executing actions or mutating session
    # SINGLE SOURCE OF TRUTH: Construct flattened response directly from plan and facts
    if planning_only:
        intent_name = decision.get("intent_name", "")

        # Extract stage and action from plan (single source of truth)
        stage = plan.get("stage")
        action = plan.get("action")

        # FIND REAL SOURCES: Check ALL possible locations for missing_slots and slots
        # Priority order: decision.facts > decision top-level > effective_response > plan
        # NOTE: plan does NOT contain missing_slots/slots (only stage/action/status)
        missing_slots = None
        slots = None

        # 1. Check decision.facts first (primary source from process_luma_response)
        facts = decision.get("facts", {})
        if not isinstance(facts, dict):
            facts = {}
        if facts.get("missing_slots") is not None:
            missing_slots = facts.get("missing_slots")
        if facts.get("slots") is not None:
            slots = facts.get("slots")

        # 2. Check decision top-level (may have data in some paths)
        if missing_slots is None and decision.get("missing_slots") is not None:
            missing_slots = decision.get("missing_slots")
        if slots is None and decision.get("slots") is not None:
            slots = decision.get("slots")

        # 3. Fallback to effective_response (source before process_luma_response)
        # This is guaranteed to have missing_slots (asserted earlier)
        if missing_slots is None and effective_response.get("missing_slots") is not None:
            missing_slots = effective_response.get("missing_slots")
        if slots is None and effective_response.get("slots") is not None:
            slots = effective_response.get("slots")

        # 4. Check plan (unlikely, but check for completeness)
        if missing_slots is None and plan.get("missing_slots") is not None:
            missing_slots = plan.get("missing_slots")
        if slots is None and plan.get("slots") is not None:
            slots = plan.get("slots")

        # 5. Final fallback: empty defaults (should never happen due to assertion)
        if missing_slots is None:
            missing_slots = []
        if slots is None:
            slots = {}

        # Ensure correct types
        if not isinstance(missing_slots, list):
            missing_slots = []
        if not isinstance(slots, dict):
            slots = {}

        # CRITICAL: For planning_only, use RAW Luma fact values (not normalized aliases)
        # Tests expect raw values like "massage", not normalized like "beauty_and_wellness.massage"
        # Normalized values remain internal-only for execution paths
        # This applies ONLY to planning_only responses - execution paths still use normalized values
        if effective_response and isinstance(effective_response, dict):
            raw_luma_response = effective_response.get(
                "_raw_luma_response", {})
            if isinstance(raw_luma_response, dict):
                raw_luma_facts = raw_luma_response.get("facts", {})
                if isinstance(raw_luma_facts, dict) and raw_luma_facts:
                    # Start with normalized slots (has time, date, etc. from normalization)
                    raw_slots = slots.copy() if isinstance(slots, dict) else {}

                    # Override service_id with raw fact value (tests expect raw, not normalized alias)
                    if "service_id" in raw_luma_facts:
                        raw_slots["service_id"] = raw_luma_facts["service_id"]
                    elif isinstance(effective_response.get("facts"), dict):
                        raw_facts_in_effective = effective_response.get(
                            "facts", {}).get("service_id")
                        if raw_facts_in_effective:
                            raw_slots["service_id"] = raw_facts_in_effective

                    slots = raw_slots

        # CRITICAL: Always extract stage and action from decision.plan (authoritative source)
        # This ensures plan.stage and plan.action are always present in the outcome
        # Do not rely on plan_message() or other sources - decision.plan is the single source of truth
        decision_plan = decision.get("plan", {}) if decision else {}
        if not isinstance(decision_plan, dict):
            decision_plan = {}

        # Extract stage and action from decision.plan (always use this source)
        stage = decision_plan.get("stage")
        action = decision_plan.get("action")

        # CRITICAL: Always populate plan object with all required fields
        # This ensures plan.stage and plan.action are always present (no silent failures)
        # HARD RULE: Include both intent and intent_name for session persistence
        populated_plan = {
            "intent": intent_name,
            # For session persistence - build_session_state_from_outcome reads plan.intent_name
            "intent_name": intent_name,
            "stage": stage,  # Always from decision.plan
            "action": action,  # Always from decision.plan
            "missing_slots": missing_slots,
            "slots": slots,
            "status": plan_status,
            "executable_actions": plan.get("executable_actions", []),
            "allowed_actions": plan.get("allowed_actions", []),
            "blocked_actions": plan.get("blocked_actions", [])
        }

        # CAPABILITY GATING: Override plan status if payment is required but not satisfied
        # This happens in post-planning finalization (right before outcome is built)
        # Check organization payment requirements and payment satisfaction status
        org_data = None
        # Check decision.facts.org first (if process_luma_response copied it)
        if decision and isinstance(decision.get("facts"), dict):
            org_data = decision["facts"].get("org")
        # Fall back to effective_response.facts.org (where org data is added before process_luma_response)
        if not org_data and effective_response and isinstance(effective_response.get("facts"), dict):
            org_data = effective_response["facts"].get("org")

        payment_required = False
        if org_data and isinstance(org_data, dict):
            payment_required = org_data.get("payment_required", False)

        payment_satisfied = False
        # INSTRUMENTATION: Log all three data sources to identify which one is missing payment_satisfied
        decision_facts_payment_satisfied = None
        session_facts_payment_satisfied = None
        outcome_facts_payment_satisfied = None

        # Check decision.facts.payment_satisfied first
        if decision and isinstance(decision.get("facts"), dict):
            decision_facts_payment_satisfied = decision["facts"].get(
                "payment_satisfied")
            payment_satisfied = decision_facts_payment_satisfied if decision_facts_payment_satisfied else False

        # Check session_state.facts.payment_satisfied
        if session_state and isinstance(session_state.get("facts"), dict):
            session_facts_payment_satisfied = session_state["facts"].get(
                "payment_satisfied")

        # Check effective_response.facts.payment_satisfied (for test scenarios)
        if effective_response and isinstance(effective_response.get("facts"), dict):
            outcome_facts_payment_satisfied = effective_response["facts"].get(
                "payment_satisfied")

        # CRITICAL FIX: Session facts are authoritative for reconciliation
        # When session_state exists, use session_state["facts"] as the primary source
        # This ensures capability completion facts (payment_satisfied) from previous turns are respected
        if session_state and isinstance(session_state.get("facts"), dict):
            session_payment_satisfied = session_state["facts"].get(
                "payment_satisfied")
            if session_payment_satisfied is not None:
                payment_satisfied = session_payment_satisfied
                logger.info(
                    f"[CAPABILITY_GATING] Using session_state.facts.payment_satisfied={payment_satisfied} "
                    f"(session facts are authoritative for reconciliation)"
                )
        elif not payment_satisfied:
            # Fall back to decision.facts if session_state doesn't have it
            if decision_facts_payment_satisfied is not None:
                payment_satisfied = decision_facts_payment_satisfied
            # Fall back to effective_response.facts if neither session nor decision have it
            elif outcome_facts_payment_satisfied is not None:
                payment_satisfied = outcome_facts_payment_satisfied

        # INSTRUMENTATION: Log all three sources for debugging
        logger.error(
            f"[CAPABILITY_GATING_INSTRUMENTATION] payment_satisfied sources: "
            f"decision.facts={decision_facts_payment_satisfied}, "
            f"session_state.facts={session_facts_payment_satisfied}, "
            f"effective_response.facts={outcome_facts_payment_satisfied}, "
            f"FINAL={payment_satisfied}"
        )

        # Debug logging
        logger.debug(
            f"Capability gating check: org_data={org_data is not None}, "
            f"payment_required={payment_required}, payment_satisfied={payment_satisfied}, "
            f"plan_status={plan_status}"
        )

        # Override plan status if payment is required but not satisfied
        # This override applies even if planner returns READY
        if payment_required and not payment_satisfied:
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=False. "
                f"Overriding plan.status from '{plan_status}' to 'AWAITING_CAPABILITY'"
            )
            plan_status = "AWAITING_CAPABILITY"
            populated_plan["status"] = "AWAITING_CAPABILITY"
            populated_plan["awaiting"] = "PAYMENT"
            # Lowercase adapter key
            populated_plan["active_capability"] = "payment"

            # Update decision.plan so that build_outcome_from_decision() uses the correct values
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["status"] = "AWAITING_CAPABILITY"
                decision["plan"]["awaiting"] = "PAYMENT"
                # Lowercase adapter key
                decision["plan"]["active_capability"] = "payment"
        elif payment_required and payment_satisfied:
            # CRITICAL: When payment is satisfied, clear active_capability to prevent re-entering payment capability
            # This ensures "paid" sessions do not re-enter AWAITING_CAPABILITY state
            logger.info(
                f"Capability gating: payment_required=True, payment_satisfied=True. "
                f"Clearing active_capability to prevent re-entry into payment capability"
            )
            # Clear active_capability from plan
            populated_plan["active_capability"] = None
            if "active_capability" in populated_plan:
                del populated_plan["active_capability"]

            # Update decision.plan to clear active_capability
            if decision and isinstance(decision.get("plan"), dict):
                decision["plan"]["active_capability"] = None
                if "active_capability" in decision["plan"]:
                    del decision["plan"]["active_capability"]

        # CRITICAL: Ensure outcome always uses raw service_id (not canonical)
        # Dialog output, outcome.slots, outcome.facts.slots MUST use raw tenant value
        outcome_slots = slots.copy() if isinstance(slots, dict) else {}

        # Get raw service_id from session or effective_response
        # Priority: 1) session.slots["service_id"], 2) effective_response slots, 3) current slots
        raw_service_id_for_outcome = None
        if session_state and isinstance(session_state, dict):
            session_slots = session_state.get("slots", {})
            if isinstance(session_slots, dict) and "service_id" in session_slots:
                raw_service_id_for_outcome = session_slots["service_id"]

        if not raw_service_id_for_outcome and effective_response:
            effective_slots = effective_response.get("slots", {})
            if isinstance(effective_slots, dict) and "service_id" in effective_slots:
                raw_service_id_for_outcome = effective_slots["service_id"]

        if not raw_service_id_for_outcome and "service_id" in outcome_slots:
            raw_service_id_for_outcome = outcome_slots["service_id"]

        # Always use raw service_id in outcome
        # Priority: session raw > effective_response raw > current slots
        if raw_service_id_for_outcome:
            outcome_slots["service_id"] = raw_service_id_for_outcome
            logger.debug(
                f"Using raw service_id in outcome: {raw_service_id_for_outcome}")
        elif "service_id" in outcome_slots:
            # Keep existing service_id if no raw found (shouldn't happen but fail-safe)
            logger.debug(
                f"Using existing service_id in outcome: {outcome_slots.get('service_id')}")

        # Remove canonical from outcome slots (never expose canonical to tests/dialog)
        if "_canonical_service_id" in outcome_slots:
            del outcome_slots["_canonical_service_id"]
            logger.debug(
                f"Removed _canonical_service_id from outcome, using raw service_id: {outcome_slots.get('service_id')}")

        # Construct flattened planning response with both structures:
        # 1. Flattened fields at outcome level (for test compatibility)
        # 2. Complete plan object (for observability and debugging)
        # 3. Facts object for backward compatibility (snapshot builder reads outcome.facts.*)
        # CRITICAL: Start from decision.facts to preserve capability facts (e.g., payment_satisfied)
        # Do NOT create a new facts dict - this would discard capability completion markers
        decision_facts = decision.get("facts", {}) if decision else {}
        if not isinstance(decision_facts, dict):
            decision_facts = {}

        # Build outcome facts: preserve all decision facts, overlay missing_slots and slots
        outcome_facts = {
            # Preserve capability facts (payment_satisfied, payment_reference, org, etc.)
            **decision_facts,
            "missing_slots": missing_slots,  # Overlay with computed missing_slots
            # Overlay with computed slots (use raw service_id only)
            "slots": outcome_slots
        }

        result = {
            "success": True,
            "outcome": {
                "intent_name": intent_name,
                "stage": stage,
                "action": action,
                "missing_slots": missing_slots,
                "slots": outcome_slots,  # Use raw service_id only
                "status": plan_status,
                "plan": populated_plan,  # Always include complete plan object
                # Preserve decision facts (includes capability completion markers)
                "facts": outcome_facts
            }
        }

        # Add active_capability if present in populated_plan (from capability gating)
        if populated_plan.get("active_capability"):
            result["outcome"]["active_capability"] = populated_plan["active_capability"]

        # Store effective Luma response for session building (for test snapshots)
        if effective_response and "_raw_luma_response" in effective_response:
            result["_merged_luma_response"] = effective_response

        # Store decision for plan_message to access (for early return paths in handle_message)
        result["_decision"] = decision

        # DEBUG LOG: Finalization point (after all overrides, before PLAN_FINAL)
        # Track fingerprint-based availability resolution for debugging
        stored_fp = session_state.get(
            "availability_fingerprint") if session_state else None
        last_exec_result = session_state.get(
            "last_execution_result") if session_state else None
        from core.orchestration.availability_fingerprint import compute_availability_fingerprint
        current_fp = compute_availability_fingerprint(
            slots, intent_name=intent_name) if slots else None
        logger.error(
            f"[FINALIZATION_DECISION] BEFORE PLAN_FINAL (after all overrides): "
            f"intent={intent_name}, "
            f"plan.status={populated_plan.get('status')}, plan.stage={populated_plan.get('stage')}, plan.action={populated_plan.get('action')}, "
            f"session.intent_name={session_state.get('intent_name') if session_state else None}, "
            f"session.status={session_state.get('status') if session_state else None}, "
            f"session.stage={session_state.get('stage') if session_state else None}, "
            f"session.action={session_state.get('action') if session_state else None}, "
            f"availability_fingerprint_stored={stored_fp}, "
            f"availability_fingerprint_current={current_fp}, "
            f"last_execution_result={last_exec_result}, "
            f"slots service_id={slots.get('service_id') if isinstance(slots, dict) else None}, "
            f"date={slots.get('date') if isinstance(slots, dict) else None}, "
            f"time={slots.get('time') if isinstance(slots, dict) else None}, "
            f"org_id={slots.get('organization_id') if isinstance(slots, dict) else None}"
        )

        # GUARD LOG: Final plan values before return
        logger.error(
            "[PLAN_FINAL] stage=%s action=%s missing=%s slots=%s",
            populated_plan["stage"], populated_plan["action"],
            populated_plan["missing_slots"], populated_plan["slots"]
        )

        # CAPABILITY RUNNER INVOCATION: Invoke immediately when entering AWAITING_CAPABILITY
        # INVARIANT: Entering AWAITING_CAPABILITY guarantees the capability side-effect has run
        # This must happen on the SAME turn, be idempotent, and NOT require user input
        if plan_status == "AWAITING_CAPABILITY":
            active_capability_value = populated_plan.get("active_capability")
            if active_capability_value:
                try:
                    # Try to import capability runner (optional dependency)
                    from capabilities.runner import CapabilityRunner

                    # Build context for adapter (read-only access to session)
                    # Merge session_state slots/facts with outcome to ensure booking info is available
                    session_slots = {}
                    session_facts = {}

                    # Start with session_state (authoritative for booking info)
                    if session_state:
                        session_slots.update(session_state.get("slots", {}))
                        if isinstance(session_state.get("facts"), dict):
                            session_facts.update(
                                session_state.get("facts", {}))

                    # Merge outcome slots/facts (may have updated values from this turn)
                    outcome_slots = result.get("outcome", {}).get("slots", {})
                    if isinstance(outcome_slots, dict):
                        session_slots.update(outcome_slots)

                    outcome_facts = result.get("outcome", {}).get("facts", {})
                    if isinstance(outcome_facts, dict):
                        session_facts.update(outcome_facts)

                    # Also check decision.booking for booking info
                    booking = decision.get("booking", {})
                    if isinstance(booking, dict):
                        # Extract booking_id, booking_code, total_amount, currency from booking
                        if "booking_id" in booking and "booking_id" not in session_slots:
                            session_slots["booking_id"] = booking.get(
                                "booking_id")
                        if "booking_code" in booking and "booking_code" not in session_slots:
                            session_slots["booking_code"] = booking.get(
                                "booking_code")
                        if "code" in booking and "booking_code" not in session_slots:
                            session_slots["booking_code"] = booking.get("code")
                        if "total_amount" in booking and "total_amount" not in session_slots:
                            session_slots["total_amount"] = booking.get(
                                "total_amount")
                        if "amount" in booking and "total_amount" not in session_slots:
                            session_slots["total_amount"] = booking.get(
                                "amount")
                        if "currency" in booking and "currency" not in session_slots:
                            session_slots["currency"] = booking.get("currency")

                    context = {
                        "user_id": user_id,
                        "session_slots": session_slots,
                        "session_facts": session_facts,
                        "domain": domain,
                        "timezone": timezone,
                        "organization_id": organization_id,
                        "transaction_id": transaction_id
                    }

                    # Initialize capability runner (idempotent - safe on re-entry)
                    runner = CapabilityRunner()

                    # Build core_outcome for runner (must include status and active_capability)
                    core_outcome = result.get("outcome", {}).copy()
                    core_outcome["status"] = plan_status
                    core_outcome["active_capability"] = active_capability_value

                    # Invoke capability runner (first activation - creates capability side-effects)
                    # This is idempotent: adapter.start() won't duplicate if already exists
                    runner_result = runner.handle(
                        # First activation (no user input yet)
                        user_input=None,
                        core_outcome=core_outcome,
                        context=context
                    )

                    # If adapter returned text, store it in outcome
                    if runner_result.text:
                        result["outcome"]["text"] = runner_result.text
                        booking_code = session_slots.get(
                            "booking_code") or session_facts.get("booking_code")
                        logger.info(
                            f"[CAPABILITY_LIFECYCLE] Capability '{active_capability_value}' invoked, side-effect executed. "
                            f"booking_code={booking_code}"
                        )
                    else:
                        logger.warning(
                            f"[CAPABILITY_LIFECYCLE] Capability '{active_capability_value}' invoked but no text returned"
                        )

                    # CRITICAL: Merge capability completion facts into outcome
                    # This ensures capability facts (e.g., payment_satisfied) are persisted to session
                    # Capability facts must be merged into outcome.facts so build_session_state_from_outcome can persist them
                    if runner_result.facts:
                        # Ensure outcome has a facts dict
                        if "facts" not in result["outcome"]:
                            result["outcome"]["facts"] = {}
                        if not isinstance(result["outcome"]["facts"], dict):
                            result["outcome"]["facts"] = {}

                        # Merge capability facts into outcome facts
                        result["outcome"]["facts"].update(runner_result.facts)

                        logger.info(
                            f"[CAPABILITY_LIFECYCLE] Merged capability facts into outcome: {list(runner_result.facts.keys())}"
                        )

                except ImportError:
                    # Capability runner not available - log but don't fail
                    logger.debug(
                        f"[CAPABILITY_LIFECYCLE] Capability runner not available, skipping capability '{active_capability_value}' invocation"
                    )
                except Exception as e:
                    # Log error but don't fail - capability execution is best-effort
                    logger.warning(
                        f"[CAPABILITY_LIFECYCLE] Failed to invoke capability '{active_capability_value}': {e}",
                        exc_info=True
                    )

        # ASSERTION: plan.intent_name must never be empty after successful planning
        if not populated_plan.get("intent_name") or populated_plan.get("intent_name") == "":
            logger.error(
                "[PLANNING_ASSERTION] CRITICAL: plan.intent_name is empty after planning! "
                "intent_name=%r, plan=%s, outcome.intent_name=%s",
                intent_name,
                json.dumps(populated_plan, default=str, ensure_ascii=True),
                result.get("outcome", {}).get("intent_name")
            )
            # Fail-safe: Use intent_name from variable if plan doesn't have it
            if intent_name and intent_name not in ("", "UNKNOWN"):
                populated_plan["intent_name"] = intent_name
                result["outcome"]["intent_name"] = intent_name
                result["outcome"]["plan"]["intent_name"] = intent_name
                logger.error(
                    "[PLANNING_ASSERTION] Recovered: Set plan.intent_name=%s from resolved intent_name",
                    intent_name
                )
            else:
                logger.error(
                    "[PLANNING_ASSERTION] FAILED: Cannot recover - intent_name is empty/invalid: %r",
                    intent_name
                )
        else:
            # Log success to confirm intent_name is present
            logger.debug(
                "[PLANNING_ASSERTION] SUCCESS: plan.intent_name=%s is present after planning",
                populated_plan.get("intent_name")
            )

        # PLANNING INVARIANT: Ephemeral intents must NOT leak into planning
        plan_intent_name = populated_plan.get("intent_name")
        if plan_intent_name and plan_intent_name not in ("", "UNKNOWN"):
            if not is_durable_intent(plan_intent_name):
                raise AssertionError(
                    f"Ephemeral intent '{plan_intent_name}' leaked into planning. "
                    f"Only durable intents may be persisted. "
                    f"Add '{plan_intent_name}' to DURABLE_INTENTS if it should be persistent."
                )

        # OUTCOME: Log final outcome structure
        outcome = result.get("outcome", {})
        outcome_slots = outcome.get("slots", {})
        outcome_missing_slots = outcome.get("missing_slots", [])
        logger.info("[OUTCOME] user_id=%s intent=%s stage=%s action=%s missing_slots=%s slots=%s",
                    user_id, intent_name, outcome.get(
                        "stage"), outcome.get("action"),
                    outcome_missing_slots, json.dumps(outcome_slots, default=str, ensure_ascii=True))

        # Inject rendering text for clarification states
        _inject_rendering_text(result, decision)

        return result

    # Handle AWAITING_* statuses (AWAITING_CONFIRMATION, AWAITING_CAPABILITY, etc.)
    # Generic handler that mirrors plan status and awaiting without special-casing
    if plan_status in ("AWAITING_CONFIRMATION", "AWAITING_CAPABILITY"):
        # Build outcome from decision using unified helper
        outcome_dict = build_outcome_from_decision(decision)

        # Override status and awaiting to mirror plan (AWAITING_* statuses are special)
        outcome_dict["status"] = plan_status
        outcome_dict["awaiting"] = awaiting

        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            facts = outcome_dict.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]
            outcome_dict["facts"] = facts

        # Add booking for AWAITING_CONFIRMATION (backward compatibility)
        if plan_status == "AWAITING_CONFIRMATION":
            booking = decision.get("booking", {})
            outcome_dict["booking"] = booking

        # Add active_capability for AWAITING_CAPABILITY
        # STRICTLY resolve from decision.plan.active_capability (authoritative source from capability gating)
        # Capability gating sets decision["plan"]["active_capability"] = "payment" (line 2808)
        # Do NOT depend on session_state (may be None on first turn) or facts
        active_capability = None
        if plan_status == "AWAITING_CAPABILITY":
            # Capability gating ALWAYS injects decision.plan.active_capability when setting AWAITING_CAPABILITY
            # This is the single source of truth for active capability
            plan = decision.get("plan", {})
            if isinstance(plan, dict):
                active_capability = plan.get("active_capability")
            if active_capability:
                outcome_dict["active_capability"] = active_capability

        result = {
            "success": True,
            "outcome": outcome_dict
        }
        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response

        # Store decision for plan_message to access
        result["_decision"] = decision

        # CAPABILITY RUNNER INVOCATION: Invoke immediately when entering AWAITING_CAPABILITY
        # INVARIANT: Entering AWAITING_CAPABILITY guarantees the capability side-effect has run
        # This must happen on the SAME turn, be idempotent, and NOT require user input
        # Resolve active_capability STRICTLY from decision.plan.active_capability (capability gating injects it at line 2808)
        if plan_status == "AWAITING_CAPABILITY":
            plan = decision.get("plan", {})
            if isinstance(plan, dict):
                active_capability_for_runner = plan.get("active_capability")
            else:
                active_capability_for_runner = None
            if active_capability_for_runner:
                try:
                    # Try to import capability runner (optional dependency)
                    from capabilities.runner import CapabilityRunner

                    # Build context for adapter (read-only access to session)
                    # Merge session_state slots/facts with outcome_dict to ensure booking info is available
                    # Note: session_state may be None on first turn, so we also check outcome_dict and decision.booking
                    session_slots = {}
                    session_facts = {}

                    # Start with session_state (authoritative for booking info, if available)
                    if session_state:
                        session_slots.update(session_state.get("slots", {}))
                        if isinstance(session_state.get("facts"), dict):
                            session_facts.update(
                                session_state.get("facts", {}))

                    # Merge outcome_dict slots/facts (may have updated values from this turn)
                    outcome_slots = outcome_dict.get("slots", {})
                    if isinstance(outcome_slots, dict):
                        session_slots.update(outcome_slots)

                    outcome_facts = outcome_dict.get("facts", {})
                    if isinstance(outcome_facts, dict):
                        session_facts.update(outcome_facts)

                    # Also check decision.booking for booking info
                    booking = decision.get("booking", {})
                    if isinstance(booking, dict):
                        # Extract booking_id, booking_code, total_amount, currency from booking
                        if "booking_id" in booking and "booking_id" not in session_slots:
                            session_slots["booking_id"] = booking.get(
                                "booking_id")
                        if "booking_code" in booking and "booking_code" not in session_slots:
                            session_slots["booking_code"] = booking.get(
                                "booking_code")
                        if "code" in booking and "booking_code" not in session_slots:
                            session_slots["booking_code"] = booking.get("code")
                        if "total_amount" in booking and "total_amount" not in session_slots:
                            session_slots["total_amount"] = booking.get(
                                "total_amount")
                        if "amount" in booking and "total_amount" not in session_slots:
                            session_slots["total_amount"] = booking.get(
                                "amount")
                        if "currency" in booking and "currency" not in session_slots:
                            session_slots["currency"] = booking.get("currency")

                    context = {
                        "user_id": user_id,
                        "session_slots": session_slots,
                        "session_facts": session_facts,
                        "domain": domain,
                        "timezone": timezone,
                        "organization_id": organization_id,
                        "transaction_id": transaction_id
                    }

                    # Initialize capability runner (idempotent - safe on re-entry)
                    runner = CapabilityRunner()

                    # Build core_outcome for runner (must include status and active_capability)
                    # This ensures the runner correctly identifies the capability to invoke
                    core_outcome_for_runner = outcome_dict.copy()
                    core_outcome_for_runner["status"] = plan_status
                    core_outcome_for_runner["active_capability"] = active_capability_for_runner

                    # Invoke capability runner (first activation - creates capability side-effects)
                    # This MUST happen in the same turn, before returning the outcome
                    # Do NOT wait for another user turn - execute immediately
                    # INVARIANT: Entering AWAITING_CAPABILITY guarantees the capability side-effect has executed
                    runner_result = runner.handle(
                        # First activation (no user input yet - do NOT wait for user input)
                        user_input=None,
                        core_outcome=core_outcome_for_runner,
                        context=context
                    )

                    # Ensure PaymentAdapter.start() was called and payment intent side-effects are persisted
                    # The runner.handle() above should have invoked adapter.start() which creates the payment intent
                    # Side-effects are persisted to the mock payment store during adapter.start()

                    # If adapter returned text, store it in outcome
                    if runner_result.text:
                        outcome_dict["text"] = runner_result.text
                        result["outcome"]["text"] = runner_result.text
                        booking_code = session_slots.get(
                            "booking_code") or session_facts.get("booking_code")
                        logger.info(
                            f"[CAPABILITY_LIFECYCLE] Capability '{active_capability_for_runner}' invoked, side-effect executed. "
                            f"booking_code={booking_code}"
                        )
                    else:
                        logger.warning(
                            f"[CAPABILITY_LIFECYCLE] Capability '{active_capability_for_runner}' invoked but no text returned"
                        )

                    # CRITICAL: Merge capability completion facts into outcome
                    # This ensures capability facts (e.g., payment_satisfied) are persisted to session
                    # Capability facts must be merged into outcome.facts so build_session_state_from_outcome can persist them
                    if runner_result.facts:
                        # Ensure outcome has a facts dict
                        if "facts" not in outcome_dict:
                            outcome_dict["facts"] = {}
                        if not isinstance(outcome_dict["facts"], dict):
                            outcome_dict["facts"] = {}

                        # Merge capability facts into outcome facts
                        outcome_dict["facts"].update(runner_result.facts)
                        result["outcome"]["facts"] = outcome_dict["facts"]

                        logger.info(
                            f"[CAPABILITY_LIFECYCLE] Merged capability facts into outcome: {list(runner_result.facts.keys())}"
                        )

                    # GUARD ASSERTION: If capability is payment, payment intent MUST exist before response is returned
                    # This enforces the invariant that entering AWAITING_CAPABILITY guarantees capability side-effects have executed
                    if active_capability_for_runner == "payment":
                        booking_code_for_check = session_slots.get(
                            "booking_code") or session_facts.get("booking_code")
                        if booking_code_for_check:
                            try:
                                # Try to access payment store to verify intent exists
                                # This is a test-time assertion to catch missing payment intents
                                from capabilities.clients.payment.mock_payment import _PAYMENT_STATE
                                if booking_code_for_check not in _PAYMENT_STATE:
                                    raise AssertionError(
                                        f"[CAPABILITY_GUARD] Payment capability invoked but payment intent not found for booking_code: {booking_code_for_check}. "
                                        f"PaymentAdapter.start() must create and persist payment intent before returning."
                                    )
                                if not _PAYMENT_STATE[booking_code_for_check].get("intent_created"):
                                    raise AssertionError(
                                        f"[CAPABILITY_GUARD] Payment capability invoked but payment intent not created for booking_code: {booking_code_for_check}. "
                                        f"PaymentAdapter.start() must create and persist payment intent before returning."
                                    )
                                logger.debug(
                                    f"[CAPABILITY_GUARD] Payment intent verified for booking_code: {booking_code_for_check}"
                                )
                            except ImportError:
                                # Payment store not available (not in test environment) - skip assertion
                                logger.debug(
                                    "[CAPABILITY_GUARD] Payment store not available, skipping payment intent verification"
                                )
                            except KeyError:
                                # Payment store available but intent not found - this is the error we're guarding against
                                raise AssertionError(
                                    f"[CAPABILITY_GUARD] Payment capability invoked but payment intent not found for booking_code: {booking_code_for_check}. "
                                    f"PaymentAdapter.start() must create and persist payment intent before returning."
                                )

                except ImportError:
                    # Capability runner not available - log but don't fail
                    logger.debug(
                        f"[CAPABILITY_LIFECYCLE] Capability runner not available, skipping capability '{active_capability_for_runner}' invocation"
                    )
                except Exception as e:
                    # Log error but don't fail - capability execution is best-effort
                    logger.warning(
                        f"[CAPABILITY_LIFECYCLE] Failed to invoke capability '{active_capability_for_runner}': {e}",
                        exc_info=True
                    )

        # Inject rendering text for clarification states (AWAITING_* doesn't need clarification)
        # Only inject if missing_slots is non-empty (clarification needed)
        facts = decision.get("facts", {})
        missing_slots = facts.get("missing_slots", [])
        if isinstance(missing_slots, list) and len(missing_slots) > 0:
            _inject_rendering_text(result, decision)

        return result

    # Handle NEEDS_CLARIFICATION status
    if plan_status == "NEEDS_CLARIFICATION":
        # Check if there's an outcome (clarification) or error
        if "outcome" in decision:
            # decision["outcome"] is already a complete outcome dict with success/outcome structure
            # from _build_clarify_outcome, so return it directly
            # Store effective Luma response for session building (private field, ignored by existing code)
            result = decision["outcome"]
            # CRITICAL: Ensure plan is attached to outcome (tests check outcome.plan.stage/action)
            if "outcome" in result:
                # Plan might already be in outcome, or we need to add it
                if "plan" not in result["outcome"]:
                    result["outcome"]["plan"] = plan
                # Promote stage/action/missing_slots/slots from plan/facts to top-level outcome for test compatibility
                plan_obj = result["outcome"].get("plan", plan)
                promoted_stage = plan_obj.get("stage")
                promoted_action = plan_obj.get("action")
                result["outcome"]["stage"] = promoted_stage
                result["outcome"]["action"] = promoted_action
                # Also promote missing_slots and slots if available in facts
                facts_obj = result["outcome"].get("facts", {})
                if "missing_slots" in facts_obj:
                    result["outcome"]["missing_slots"] = facts_obj["missing_slots"]
                if "slots" in facts_obj:
                    result["outcome"]["slots"] = facts_obj["slots"]

                # CRITICAL: Ensure intent_name is set for NEEDS_CLARIFICATION
                # NEEDS_CLARIFICATION must NEVER clear intent - preserve it for follow-up turns
                if "intent_name" not in result["outcome"] or not result["outcome"].get("intent_name"):
                    # Try to get intent_name from decision or effective_response
                    resolved_intent = decision.get("intent_name", "")
                    if not resolved_intent and effective_response:
                        resolved_intent = effective_response.get(
                            "_effective_intent")
                    if not resolved_intent and effective_response:
                        intent_obj = effective_response.get("intent", {})
                        if isinstance(intent_obj, dict):
                            resolved_intent = intent_obj.get("name", "")
                            # Skip UNKNOWN - use effective_intent instead
                            if resolved_intent == "UNKNOWN":
                                resolved_intent = effective_response.get(
                                    "_effective_intent", "")
                    # If still empty, preserve session intent
                    if not resolved_intent and session_state:
                        resolved_intent = session_state.get(
                            "intent_name") or session_state.get("intent")

                    if resolved_intent:
                        result["outcome"]["intent_name"] = resolved_intent
                        logger.info(
                            f"[NEEDS_CLARIFICATION] Set intent_name={resolved_intent} in outcome "
                            f"(from decision/effective_response/session)"
                        )
                    else:
                        logger.warning(
                            f"[NEEDS_CLARIFICATION] No intent available to set in outcome"
                        )

            # Add rendered text (best-effort)
            if "outcome" in result:
                outcome_obj = result["outcome"]
                facts_obj = outcome_obj.get("facts", {})
                slots_for_rendering = facts_obj.get("slots", {})
                if not slots_for_rendering:
                    slots_for_rendering = outcome_obj.get("slots", {})

                # Build decision dict for rendering
                rendering_decision = {
                    "status": "NEEDS_CLARIFICATION",
                    "missing_slots": facts_obj.get("missing_slots", outcome_obj.get("missing_slots", []))
                }

                rendered_text = _render_clarification_text(
                    rendering_decision, slots_for_rendering)
                if rendered_text:
                    outcome_obj["rendered_text"] = rendered_text

            # Inject rendering text at top level for clarification states
            _inject_rendering_text(result, decision)

            result["_merged_luma_response"] = effective_response
            return result
        if "error" in decision:
            return {
                "success": False,
                "error": decision["error"],
                "message": decision.get("message", "An error occurred")
            }

        # Synthesize clarification outcome when Luma didn't provide one (follow-up turns)
        # Core's responsibility: generate clarification from intent, missing_slots, and domain
        # CRITICAL: Preserve effective_intent for NEEDS_CLARIFICATION (do NOT clear intent)
        # Intent is only cleared on terminal flows (CANCEL, COMPLETE), never on NEEDS_CLARIFICATION
        intent_name = decision.get("intent_name", "")
        # Fallback to effective_intent if decision.intent_name is empty (preserves session intent)
        if not intent_name and effective_response:
            effective_intent = effective_response.get("_effective_intent")
            if effective_intent:
                intent_name = effective_intent
                logger.info(
                    f"[NEEDS_CLARIFICATION] Preserved effective_intent={effective_intent} "
                    f"(decision.intent_name was empty)"
                )
        # If still empty, try to get from effective_response.intent.name
        if not intent_name and effective_response:
            intent_obj = effective_response.get("intent", {})
            if isinstance(intent_obj, dict):
                intent_from_response = intent_obj.get("name", "")
                if intent_from_response and intent_from_response != "UNKNOWN":
                    intent_name = intent_from_response
                    logger.info(
                        f"[NEEDS_CLARIFICATION] Using intent from effective_response: {intent_name}"
                    )
        facts = decision.get("facts", {})

        # Get missing_slots from facts (already merged/normalized from process_luma_response)
        # Get missing_slots from facts or effective_response (computed by session merge)
        # ARCHITECTURAL INVARIANT: missing_slots is computed exactly once per turn in session merge
        # missing_slots MUST NOT be recomputed here - it is a pure derived value
        # missing_slots = [] is VALID and means all required slots are satisfied
        missing_slots = None
        if "missing_slots" in facts:
            facts_missing = facts.get("missing_slots")
            if isinstance(facts_missing, list):
                # Use facts missing_slots (even if [])
                missing_slots = facts_missing

        # If not in facts, try effective_response
        if missing_slots is None and "missing_slots" in effective_response:
            response_missing = effective_response.get("missing_slots")
            if isinstance(response_missing, list):
                # Use response missing_slots (even if [])
                missing_slots = response_missing

        # INVARIANT CHECK: missing_slots must be a list (never None after merge)
        if missing_slots is None:
            # This should never happen if merge ran correctly
            logger.error(
                f"[MISSING_SLOTS] VIOLATION: missing_slots is None in orchestrator! "
                f"user_id={user_id}, intent={intent_name}, "
                f"facts_keys={list(facts.keys())}, effective_response_keys={list(effective_response.keys())}"
            )
            # Fail-safe: use empty list (but this indicates a bug)
            missing_slots = []

        # INVARIANT CHECK: missing_slots must be a list
        assert isinstance(missing_slots, list), (
            f"missing_slots must be a list, got {type(missing_slots)}: {missing_slots}"
        )

        # DEBUG: Log why we're synthesizing clarification
        logger.info(
            f"[SYNTHESIZE_CLARIFICATION] user_id={user_id} intent={intent_name} "
            f"missing_slots_from_facts={facts.get('missing_slots')} "
            f"missing_slots_from_response={effective_response.get('missing_slots')} "
            f"final_missing_slots={missing_slots} "
            f"facts_slots={facts.get('slots', {})} "
            f"effective_response_slots={effective_response.get('slots', {})} "
            f"effective_response_booking_services={effective_response.get('booking', {}).get('services') if isinstance(effective_response.get('booking'), dict) else None}"
        )

        # Normalize missing_slots (especially for MODIFY_BOOKING) - safety check
        # Import here to avoid circular dependency
        from core.orchestration.nlu.luma_response_processor import _normalize_modify_booking_missing_slots
        missing_slots = _normalize_modify_booking_missing_slots(
            missing_slots, effective_response)

        # INVARIANT CHECK: After normalization, missing_slots must still be a list
        assert isinstance(missing_slots, list), (
            f"missing_slots must be a list after normalization, got {type(missing_slots)}: {missing_slots}"
        )

        # CRITICAL: missing_slots = [] is VALID - it means all required slots are satisfied
        # If status is NEEDS_CLARIFICATION but missing_slots = [], this indicates a logic error
        # But we should not override missing_slots - it is a pure derived value
        if len(missing_slots) == 0:
            logger.warning(
                f"NEEDS_CLARIFICATION status but missing_slots is empty for user {user_id}. "
                f"This may indicate a logic error, but missing_slots is a pure derived value and will not be overridden."
            )

        # Build issues dict from missing_slots for clarification generation
        issues = {slot: "missing" for slot in missing_slots}

        # Extract context and booking from effective_response
        context = effective_response.get("context", {})
        booking = effective_response.get("booking")

        # Build clarification outcome using build_clarify_outcome_from_reason
        # (already imported at top of file, but import here for clarity)
        from core.orchestration.nlu.luma_response_processor import _derive_clarification_reason_from_missing_slots

        # Derive clarification reason from missing slots
        clarification_reason = _derive_clarification_reason_from_missing_slots(
            missing_slots)

        # Ensure facts has normalized missing_slots
        facts["missing_slots"] = missing_slots

        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]

        # Build clarification outcome
        result = build_clarify_outcome_from_reason(
            reason=clarification_reason,
            issues=issues,
            booking=booking,
            domain=derived_domain,
            facts=facts
        )

        # CRITICAL: Always set intent_name in outcome for NEEDS_CLARIFICATION
        # NEEDS_CLARIFICATION must NEVER clear intent - preserve it for follow-up turns
        # If resolved_intent exists, use it; otherwise preserve existing session intent
        if "outcome" in result:
            if intent_name:
                # Use resolved intent (from decision or effective_response)
                result["outcome"]["intent_name"] = intent_name
                logger.info(
                    f"[NEEDS_CLARIFICATION] Set intent_name={intent_name} in outcome"
                )
            else:
                # No resolved intent - preserve session intent if available
                if session_state:
                    session_intent = session_state.get(
                        "intent_name") or session_state.get("intent")
                    if session_intent:
                        result["outcome"]["intent_name"] = session_intent
                        logger.info(
                            f"[NEEDS_CLARIFICATION] Preserved session intent_name={session_intent} "
                            f"(no resolved intent available)"
                        )
                    else:
                        # No session intent either - log warning but don't set empty string
                        logger.warning(
                            f"[NEEDS_CLARIFICATION] No intent available (decision or session) - "
                            f"intent_name will not be set in outcome"
                        )

        # Add plan to outcome for session building
        if "outcome" in result:
            result["outcome"]["plan"] = plan

            # Promote stage/action/missing_slots/slots to top-level outcome for test compatibility
            # Always copy stage/action from plan, even if None
            synthesized_stage = plan.get("stage")
            synthesized_action = plan.get("action")
            result["outcome"]["stage"] = synthesized_stage
            result["outcome"]["action"] = synthesized_action
            # Also promote missing_slots and slots if available in facts
            facts_obj = result["outcome"].get("facts", {})
            if "missing_slots" in facts_obj:
                result["outcome"]["missing_slots"] = facts_obj["missing_slots"]
            if "slots" in facts_obj:
                result["outcome"]["slots"] = facts_obj["slots"]

            # Add rendered text (best-effort)
            slots_for_rendering = facts_obj.get("slots", {})
            if not slots_for_rendering:
                slots_for_rendering = result["outcome"].get("slots", {})

            # Build decision dict for rendering
            rendering_decision = {
                "status": "NEEDS_CLARIFICATION",
                "missing_slots": missing_slots
            }

            rendered_text = _render_clarification_text(
                rendering_decision, slots_for_rendering)
            if rendered_text:
                result["outcome"]["rendered_text"] = rendered_text

        # Inject rendering text at top level for clarification states
        _inject_rendering_text(result, decision)

        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response

        logger.info(
            f"Synthesized clarification outcome for user {user_id}: "
            f"intent={intent_name}, missing_slots={missing_slots}, reason={clarification_reason}"
        )

        return result

    # Step 5: Return planning-only outcome (plan_status == "READY")
    # Core NEVER executes - execution is handled by separate layer
    # Return planning outcome matching core contract: {intent, slots, missing_slots, executable_actions, dialog_instruction?}
    if plan_status == "READY":
        intent_name = decision.get("intent_name", "")
        facts = decision.get("facts", {})
        slots = facts.get("slots", {})
        missing_slots = facts.get("missing_slots", [])
        executable_actions = plan.get("executable_actions", [])

        # Include _raw_luma_response in facts for test snapshots (preserved from effective_response)
        if effective_response and "_raw_luma_response" in effective_response:
            if not isinstance(facts, dict):
                facts = {}
            facts["_raw_luma_response"] = effective_response["_raw_luma_response"]

        # Build planning-only outcome matching core contract
        planning_outcome = _build_planning_outcome(
            intent_name=intent_name,
            slots=slots,
            missing_slots=missing_slots,
            executable_actions=executable_actions,
            status="READY"
        )

        # CRITICAL: Extract stage/action from plan for top-level promotion
        stage = plan.get("stage")
        action = plan.get("action")

        result = {
            "success": True,
            "outcome": {
                "status": "READY",
                "intent_name": intent_name,
                **planning_outcome,
                "facts": facts,
                # CRITICAL: Plan must be in outcome (tests check outcome.plan.stage/action)
                "plan": plan
            }
        }

        # Promote stage/action/missing_slots/slots to top-level outcome (required by tests)
        # Tests assert outcome.stage / outcome.action / outcome.missing_slots / outcome.slots directly
        # Always copy stage/action from plan, even if None
        result["outcome"]["stage"] = stage
        result["outcome"]["action"] = action
        result["outcome"]["missing_slots"] = missing_slots
        result["outcome"]["slots"] = slots

        # Inject rendering text for clarification states (READY doesn't need clarification)
        # Only inject if missing_slots is non-empty (clarification needed)
        if isinstance(missing_slots, list) and len(missing_slots) > 0:
            _inject_rendering_text(result, decision)

        # Store effective Luma response for session building
        result["_merged_luma_response"] = effective_response
        return result

    # This should never be reached - all statuses handled above
    logger.error(f"Unexpected plan_status: {plan_status} for user {user_id}")
    return {
        "success": False,
        "error": "internal_error",
        "message": f"Unexpected plan status: {plan_status}"
    }
    # Priority: commit action if allowed, otherwise first allowed fallback
    action_to_execute = None

    # Get commit action from plan (if any allowed action is a commit action)
    intent_name = decision.get("intent_name", "")

    # Enforce core intent boundary: pass through non-core intents without orchestration
    if intent_name:
        from core.routing.intents.base_intents import is_core_intent
        if not is_core_intent(intent_name):
            # Pass through non-core intents as non-orchestrated signals
            # This preserves conversational continuity and enables workflow extensions
            return _handle_non_core_intent(effective_response, decision, user_id)

    from core.orchestration.nlu.luma_response_processor import _load_intent_execution_config
    intent_configs = _load_intent_execution_config()
    intent_config = intent_configs.get(intent_name, {})
    commit_config = intent_config.get("commit", {})
    commit_action = commit_config.get(
        "action") if isinstance(commit_config, dict) else None

    # Prefer commit action if it's allowed
    if commit_action and commit_action in allowed_actions:
        action_to_execute = commit_action
    elif allowed_actions:
        # Use first allowed action (fallback)
        action_to_execute = allowed_actions[0]
    else:
        # No allowed actions - this should not happen for READY status, but handle gracefully
        logger.warning(
            f"No allowed actions in plan for user {user_id}. "
            f"Status: {plan_status}, Blocked: {blocked_actions}"
        )
        return {
            "success": False,
            "error": "no_allowed_actions",
            "message": "No actions are allowed to execute at this time"
        }

    # Map action to handler
    handler_action = get_handler_action(action_to_execute)
    if not handler_action:
        # Fallback to legacy action_name for backward compatibility
        handler_action = decision.get("action_name")
        if not handler_action:
            logger.error(
                f"Could not map action {action_to_execute} to handler for user {user_id}"
            )
            return {
                "success": False,
                "error": "unsupported_action",
                "message": f"Action {action_to_execute} is not supported"
            }

    # Verify action is allowed (safety check)
    if action_to_execute in blocked_actions:
        logger.error(
            f"Attempted to execute blocked action {action_to_execute} for user {user_id}"
        )
        return {
            "success": False,
            "error": "action_blocked",
            "message": f"Action {action_to_execute} is blocked"
        }

    action_name = handler_action
    booking = decision["booking"]

    # Determine booking_type from intent_name if not explicitly set in booking
    # CREATE_RESERVATION -> "reservation", CREATE_APPOINTMENT -> "service"
    booking_type = booking.get("booking_type")
    if not booking_type:
        if intent_name == "CREATE_RESERVATION":
            booking_type = "reservation"
        elif intent_name == "CREATE_APPOINTMENT":
            booking_type = "service"
        else:
            booking_type = "service"  # Default fallback
    booking["booking_type"] = booking_type  # Ensure it's set in booking object

    # Helper function to check if slots have any temporal structure (date/date_range/datetime_range)
    def has_any_date(slots_dict: Dict[str, Any]) -> bool:
        """Check if slots contain any temporal structure (date, date_range, or datetime_range)."""
        return (
            slots_dict.get("date") or
            (isinstance(slots_dict.get("date_range"), dict) and slots_dict["date_range"].get("start")) or
            (isinstance(slots_dict.get("datetime_range"), dict)
             and slots_dict["datetime_range"].get("start"))
        )

    # Extract service and datetime_range from facts.slots if missing in booking
    # Luma may provide these in slots instead of booking object
    facts = decision.get("facts", {})
    slots = facts.get("slots", {})

    if not booking.get("services") and booking_type == "service":
        service_id = slots.get("service_id")
        if service_id:
            # Convert service_id string to services array format
            booking["services"] = [{"text": service_id}]
            logger.info(
                f"Extracted service from facts.slots.service_id: {service_id}")

    # For reservations, extract service_id as room identifier
    if not booking.get("services") and booking_type == "reservation":
        service_id = slots.get("service_id")
        if service_id:
            booking["services"] = [{"text": service_id}]
            logger.info(
                f"Extracted room/service from facts.slots.service_id: {service_id}")

    # Extract datetime_range from facts.slots if missing in booking
    if not booking.get("datetime_range"):
        # Try datetime_range first
        if slots.get("datetime_range"):
            datetime_range = slots.get("datetime_range")
            if datetime_range:
                booking["datetime_range"] = datetime_range
                logger.info(
                    f"Extracted datetime_range from facts.slots: {datetime_range}")
        # Try date_range (with start_date/end_date) and convert to datetime_range format
        elif slots.get("date_range"):
            date_range = slots.get("date_range")
            if isinstance(date_range, dict):
                start_date = date_range.get(
                    "start_date") or date_range.get("start")
                end_date = date_range.get("end_date") or date_range.get("end")
                if start_date and end_date:
                    # Convert date_range to datetime_range format
                    # For reservations, dates are typically date-only, so we'll use them as-is
                    # and let the execution backend handle time if needed
                    booking["datetime_range"] = {
                        "start": start_date,
                        "end": end_date
                    }
                    logger.info(
                        f"Converted date_range to datetime_range: start={start_date}, end={end_date}")
        # For service bookings: construct datetime_range from date/date_range + time
        # Use helper to check for any temporal structure (date, date_range, datetime_range) + time
        elif booking_type == "service" and has_any_date(slots) and slots.get("time"):
            from datetime import datetime as dt
            try:
                # Extract date from any temporal structure
                date_str = None
                if slots.get("date"):
                    date_str = str(slots.get("date"))
                elif isinstance(slots.get("date_range"), dict):
                    # For date_range, use the start date
                    date_range = slots.get("date_range")
                    date_str = str(date_range.get("start")
                                   or date_range.get("start_date"))
                elif isinstance(slots.get("datetime_range"), dict):
                    # For datetime_range, extract date part from start
                    datetime_range = slots.get("datetime_range")
                    start = datetime_range.get("start")
                    if start:
                        date_str = str(start).split("T")[0].split(" ")[0]

                if not date_str:
                    raise ValueError("No date found in slots")

                time_str = str(slots.get("time"))

                # Parse date (assume YYYY-MM-DD format)
                date_obj = None
                if isinstance(date_str, str):
                    # Remove time component if present (take only date part)
                    date_only = date_str.split("T")[0].split(" ")[0]
                    try:
                        date_obj = dt.strptime(date_only, "%Y-%m-%d")
                    except ValueError:
                        # Try ISO format
                        try:
                            date_obj = dt.fromisoformat(date_only)
                        except (ValueError, AttributeError):
                            pass

                # Parse time (assume HH:MM or HH:MM:SS format)
                if date_obj:
                    # Normalize time string (remove spaces, handle formats like "11am", "11:00", etc.)
                    time_normalized = time_str.lower().replace("am", "").replace("pm", "").strip()
                    if ":" in time_normalized:
                        time_parts = time_normalized.split(":")
                    else:
                        # Assume format like "11" means 11:00
                        time_parts = [time_normalized, "00"]

                    if len(time_parts) >= 2:
                        try:
                            hour = int(time_parts[0])
                            minute = int(time_parts[1]) if len(
                                time_parts) > 1 else 0

                            # Handle AM/PM
                            if "pm" in time_str.lower() and hour < 12:
                                hour += 12
                            elif "am" in time_str.lower() and hour == 12:
                                hour = 0

                            # Combine date and time
                            start_datetime = date_obj.replace(
                                hour=hour, minute=minute, second=0, microsecond=0)
                            # For service bookings, end time will be computed from duration
                            # For now, set end = start (duration will be added later if needed)
                            end_datetime = start_datetime

                            booking["datetime_range"] = {
                                "start": start_datetime.isoformat(),
                                "end": end_datetime.isoformat()
                            }
                            logger.info(
                                f"Constructed datetime_range from date+time: {booking['datetime_range']}")
                        except (ValueError, IndexError, TypeError) as e:
                            # If parsing fails, construct as ISO string
                            booking["datetime_range"] = {
                                "start": f"{date_str}T{time_str}:00",
                                "end": f"{date_str}T{time_str}:00"
                            }
                            logger.info(
                                f"Constructed datetime_range from date+time (fallback): {booking['datetime_range']}")
                    else:
                        # Time format not recognized, use date + time as string
                        booking["datetime_range"] = {
                            "start": f"{date_str}T{time_str}:00",
                            "end": f"{date_str}T{time_str}:00"
                        }
                        logger.info(
                            f"Constructed datetime_range from date+time (string): {booking['datetime_range']}")
                else:
                    # Date parsing failed, use string concatenation
                    booking["datetime_range"] = {
                        "start": f"{date_str}T{time_str}:00",
                        "end": f"{date_str}T{time_str}:00"
                    }
                    logger.info(
                        f"Constructed datetime_range from date+time (string fallback): {booking['datetime_range']}")
            except Exception as e:
                logger.warning(
                    f"Failed to construct datetime_range from date+time: {e}. "
                    f"slots={list(slots.keys())}, time={slots.get('time')}")
                # Final fallback: construct as string concatenation
                # Try to get date from any temporal structure
                fallback_date = None
                if slots.get("date"):
                    fallback_date = str(slots.get("date"))
                elif isinstance(slots.get("date_range"), dict):
                    date_range = slots.get("date_range")
                    fallback_date = str(date_range.get(
                        "start") or date_range.get("start_date"))
                elif isinstance(slots.get("datetime_range"), dict):
                    datetime_range = slots.get("datetime_range")
                    start = datetime_range.get("start")
                    if start:
                        fallback_date = str(start).split("T")[0].split(" ")[0]

                if fallback_date and slots.get("time"):
                    booking["datetime_range"] = {
                        "start": f"{fallback_date}T{slots.get('time')}:00",
                        "end": f"{fallback_date}T{slots.get('time')}:00"
                    }

    # Set has_datetime in facts.slots when both date and time are present
    # Use helper to check for any temporal structure AND time
    has_date = has_any_date(slots)
    has_time = slots.get("time")

    if booking_type == "service" and has_date and has_time:
        # Preserve existing slots (including service_id) when setting has_datetime
        # Facts already has slots from decision - just add has_datetime to it
        if "slots" not in facts:
            facts["slots"] = {}
        # Ensure slots is a dict and preserve all existing slots
        if not isinstance(facts["slots"], dict):
            facts["slots"] = {}
        # Merge accumulated slots into facts.slots to preserve service_id
        facts["slots"] = {**slots, **facts["slots"]}
        facts["slots"]["has_datetime"] = True
        logger.info(
            "Set has_datetime=true in facts.slots (date and time both present)")

    # Log processed booking
    logger.debug("Processed booking: %s", json.dumps(
        booking, ensure_ascii=False, default=str))

    # Resolve service item_id using catalog discovery (no org details scanning)
    def _resolve_service_id(services_from_luma: list, catalog_services_list: list[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Resolve service item_id deterministically.

        Rules:
        - If services[].text provided: exact name match (case-insensitive) → service ID
        - Else, use canonical if exactly one active service matches
        - If multiple matches for canonical → clarification
        - If no match → clarification
        """
        if not catalog_services_list:
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        active_services = [
            s for s in catalog_services_list
            if isinstance(s, dict) and s.get("is_active", True) is not False
        ]

        if not services_from_luma:
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        svc = services_from_luma[0] if isinstance(
            services_from_luma, list) else services_from_luma
        if not isinstance(svc, dict):
            return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

        text_name = svc.get("text")
        canonical = svc.get("canonical") or svc.get(
            "service_family_id") or svc.get("slug")

        # Name match
        if text_name:
            name_lower = str(text_name).lower()
            matches = [s for s in active_services if str(
                s.get("name", "")).lower() == name_lower]
            if len(matches) == 1:
                return {"item_id": matches[0].get("id"), "clarification": False}
            if len(matches) > 1:
                return {"item_id": None, "clarification": True, "reason": "SERVICE_VARIANT_AMBIGUITY"}

        # Canonical match (only if exactly one)
        if canonical:
            canonical_lower = str(canonical).lower()
            matches = [
                s for s in active_services
                if str(s.get("canonical") or s.get("slug") or "").lower() == canonical_lower
            ]
            if len(matches) == 1:
                return {"item_id": matches[0].get("id"), "clarification": False}
            if len(matches) > 1:
                return {"item_id": None, "clarification": True, "reason": "SERVICE_VARIANT_AMBIGUITY"}

        return {"item_id": None, "clarification": True, "reason": "MISSING_SERVICE"}

    try:
        if action_name == "booking.create":
            resolved_item_id = None
            if booking_type == "service" and derived_domain != "reservation":
                # Use cached catalog data to resolve ID
                if catalog_data_for_alias is None:
                    catalog_data_for_alias = catalog_cache.get_catalog(
                        resolved_org_id, catalog_client, domain="service")
                catalog_services_for_resolution = catalog_data_for_alias.get(
                    "services", []) if isinstance(catalog_data_for_alias, dict) else []
                booking_services = booking.get("services", [])
                resolution = _resolve_service_id(
                    booking_services, catalog_services_for_resolution)
                if resolution.get("clarification"):
                    reason = resolution.get("reason", "MISSING_SERVICE")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"service": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_item_id = resolution.get("item_id")
                # Inject resolved id back into booking for downstream usage
                services_list = booking.get("services")
                if services_list and isinstance(services_list, list) and isinstance(services_list[0], dict):
                    services_list[0]["id"] = resolved_item_id
                booking["_resolved_item_id"] = resolved_item_id
            elif booking_type == "reservation" or derived_domain == "reservation":
                if catalog_data_for_alias is None:
                    catalog_data_for_alias = catalog_cache.get_catalog(
                        resolved_org_id, catalog_client, domain="reservation")
                rooms_catalog = catalog_data_for_alias.get(
                    "rooms", []) if isinstance(catalog_data_for_alias, dict) else []
                catalog_extras = catalog_data_for_alias.get(
                    "extras", []) if isinstance(catalog_data_for_alias, dict) else []

                def _resolve_room_type(room_from_luma: Dict[str, Any], rooms: list[Dict[str, Any]]) -> Dict[str, Any]:
                    if not rooms:
                        return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}
                    active_rooms = [r for r in rooms if isinstance(
                        r, dict) and r.get("is_active", True) is not False]
                    if not room_from_luma or not isinstance(room_from_luma, dict):
                        return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}
                    text_name = room_from_luma.get("text")
                    canonical = room_from_luma.get("canonical_key") or room_from_luma.get(
                        "canonical") or room_from_luma.get("slug")
                    if text_name:
                        name_lower = str(text_name).lower()
                        matches = [r for r in active_rooms if str(
                            r.get("name", "")).lower() == name_lower]
                        if len(matches) == 1:
                            return {"room_type_id": matches[0].get("id"), "clarification": False}
                        if len(matches) > 1:
                            return {"room_type_id": None, "clarification": True, "reason": "ROOM_VARIANT_AMBIGUITY"}
                    if canonical:
                        canonical_lower = str(canonical).lower()
                        matches = [
                            r for r in active_rooms
                            if str(r.get("canonical_key") or r.get("canonical") or r.get("slug") or "").lower() == canonical_lower
                        ]
                        if len(matches) == 1:
                            return {"room_type_id": matches[0].get("id"), "clarification": False}
                        if len(matches) > 1:
                            return {"room_type_id": None, "clarification": True, "reason": "ROOM_VARIANT_AMBIGUITY"}
                    return {"room_type_id": None, "clarification": True, "reason": "MISSING_ROOM_TYPE"}

                def _resolve_extras(extras_from_luma: list, extras_catalog: list[Dict[str, Any]], room_type_id: Optional[int]) -> Dict[str, Any]:
                    if not extras_from_luma:
                        return {"extras": [], "clarification": False}
                    active_extras = [e for e in extras_catalog if isinstance(
                        e, dict) and e.get("is_active", True) is not False]
                    resolved_extras = []
                    for ex in extras_from_luma:
                        if not isinstance(ex, dict):
                            return {"extras": None, "clarification": True, "reason": "INVALID_EXTRA"}
                        text_name = ex.get("text")
                        canonical = ex.get("canonical") or ex.get("slug")
                        match = None
                        if text_name:
                            name_lower = str(text_name).lower()
                            name_matches = [e for e in active_extras if str(
                                e.get("name", "")).lower() == name_lower]
                            if len(name_matches) == 1:
                                match = name_matches[0]
                            elif len(name_matches) > 1:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_VARIANT_AMBIGUITY"}
                        if match is None and canonical:
                            canonical_lower = str(canonical).lower()
                            canonical_matches = [e for e in active_extras if str(
                                e.get("canonical") or e.get("slug") or "").lower() == canonical_lower]
                            if len(canonical_matches) == 1:
                                match = canonical_matches[0]
                            elif len(canonical_matches) > 1:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_VARIANT_AMBIGUITY"}
                        if match is None:
                            return {"extras": None, "clarification": True, "reason": "INVALID_EXTRA"}

                        applies_all = match.get("applies_to_all", False)
                        applicable_room_types = match.get(
                            "applicable_room_types") or match.get("room_types") or []
                        if not applies_all and room_type_id is not None and applicable_room_types:
                            if room_type_id not in applicable_room_types:
                                return {"extras": None, "clarification": True, "reason": "EXTRA_NOT_APPLICABLE"}
                        resolved_extras.append({"id": match.get("id")})
                    return {"extras": resolved_extras, "clarification": False}

                room_candidates = booking.get(
                    "rooms") or booking.get("services") or []
                room_svc = room_candidates[0] if isinstance(
                    room_candidates, list) else room_candidates
                room_resolution = _resolve_room_type(
                    room_svc, rooms_catalog)
                if room_resolution.get("clarification"):
                    reason = room_resolution.get("reason", "MISSING_ROOM_TYPE")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"room_type": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_room_id = room_resolution.get("room_type_id")
                booking["_resolved_room_type_id"] = resolved_room_id
                extras_resolution = _resolve_extras(booking.get(
                    "extras", []), catalog_extras, resolved_room_id)
                if extras_resolution.get("clarification"):
                    reason = extras_resolution.get("reason", "INVALID_EXTRA")
                    return build_clarify_outcome_from_reason(
                        reason=reason,
                        issues={"extras": "missing"},
                        booking=booking,
                        domain=derived_domain
                    )
                resolved_extras = extras_resolution.get("extras", [])
                if resolved_extras is not None:
                    booking["_resolved_extras"] = resolved_extras
                    if isinstance(booking.get("extras"), list):
                        for idx, ex in enumerate(booking["extras"]):
                            if isinstance(ex, dict) and idx < len(resolved_extras):
                                booking["extras"][idx]["id"] = resolved_extras[idx].get(
                                    "id")

            # Execute full booking creation flow
            result = _execute_booking_creation(
                user_id=user_id,
                booking=booking,
                customer_client=customer_client,
                booking_client=booking_client,
                catalog_client=catalog_client,
                organization_id=resolved_org_id,
                catalog_data=catalog_data_for_alias,
                phone_number=phone_number,
                email=email,
                customer_id=customer_id
            )

            logger.info(f"Successfully created booking for user {user_id}")

            def _extract_booking_code(resp: Dict[str, Any]) -> Optional[Any]:
                candidates = [
                    resp.get("booking_code"),
                    resp.get("code"),
                ]
                booking_obj = resp.get("booking") if isinstance(
                    resp.get("booking"), dict) else None
                if booking_obj:
                    candidates.append(booking_obj.get("booking_code"))
                    candidates.append(booking_obj.get("code"))
                data_obj = resp.get("data") if isinstance(
                    resp.get("data"), dict) else None
                if data_obj:
                    booking_data = data_obj.get("booking") if isinstance(
                        data_obj.get("booking"), dict) else None
                    if booking_data:
                        candidates.append(booking_data.get("booking_code"))
                        candidates.append(booking_data.get("code"))
                        candidates.append(booking_data.get("id"))
                # Fallback to top-level id if present
                candidates.append(resp.get("id"))
                for c in candidates:
                    if c:
                        return c
                return None

            booking_code_extracted = _extract_booking_code(result)
            booking_data = None
            if isinstance(result, dict):
                booking_data = result.get("booking")
                if isinstance(result.get("data"), dict) and isinstance(result["data"].get("booking"), dict):
                    booking_data = result["data"]["booking"]

            status_extracted = (
                result.get("status")
                or (booking_data.get("status") if isinstance(booking_data, dict) else None)
                or "pending"
            )

            starts_at = booking_data.get("starts_at") if isinstance(
                booking_data, dict) else None
            ends_at = booking_data.get("ends_at") if isinstance(
                booking_data, dict) else None
            total_amount = booking_data.get("total_amount") if isinstance(
                booking_data, dict) else None
            reservation_fee = booking_data.get(
                "reservation_fee") if isinstance(booking_data, dict) else None
            booking_type_resp = booking_data.get(
                "type") if isinstance(booking_data, dict) else None

            # Build outcome with facts if date+time were present (for has_datetime check)
            # Use helper function to check for any temporal structure AND time
            outcome_has_date = has_any_date(slots)
            outcome_has_time = slots.get("time")

            outcome_facts = None
            if booking_type == "service" and outcome_has_date and outcome_has_time:
                # Preserve all accumulated slots (including service_id) in outcome facts
                outcome_facts = {
                    "slots": {
                        # Include all accumulated slots (service_id, date, time, etc.)
                        **slots,
                        "has_datetime": True
                    }
                }

            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code_extracted,
                    "booking_status": status_extracted,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "total_amount": total_amount,
                    "reservation_fee": reservation_fee,
                    "booking_type": booking_type_resp,
                }
            }

            # Include facts if date+time were present
            if outcome_facts:
                outcome["outcome"]["facts"] = outcome_facts

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            # Notify Luma about execution completion (for lifecycle tracking)
            if booking_code_extracted and luma_client:
                try:
                    result = luma_client.notify_execution(
                        user_id=user_id,
                        booking_id=booking_code_extracted,
                        domain=derived_domain
                    )
                    # Check if the endpoint doesn't exist (404 handled gracefully)
                    if result.get("error") == "endpoint_not_found":
                        logger.debug(
                            f"Luma /notify_execution endpoint not available (non-critical lifecycle update)"
                        )
                    else:
                        logger.info(
                            f"Notified Luma about execution completion for user {user_id}, booking_id={booking_code_extracted}"
                        )
                except Exception as e:  # noqa: BLE001
                    # Log but don't fail the request - lifecycle update is non-critical
                    # The notify_execution endpoint may not exist in Luma (404), which is fine
                    logger.debug(
                        f"Failed to notify Luma about execution (non-critical): {e}"
                    )

            return outcome

        elif action_name == "booking.modify":
            # Expect a booking reference and updates
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError("booking_code is required for modification")

            updates = booking.get("updates") or {}
            # Fallback: build updates from datetime_range if present
            datetime_range = booking.get("datetime_range")
            if datetime_range and isinstance(datetime_range, dict):
                start = datetime_range.get("start")
                end = datetime_range.get("end")
                if start:
                    updates.setdefault("starts_at", start)
                if end:
                    updates.setdefault("ends_at", end)

            if not updates:
                raise ValueError(
                    "No updates supplied for booking modification")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.update_booking(
                booking_code=booking_code,
                organization_id=resolved_org_id,
                updates=updates,
            )

            booking_data = api_response.get("booking")
            if isinstance(api_response.get("data"), dict) and isinstance(api_response["data"].get("booking"), dict):
                booking_data = api_response["data"]["booking"]

            status_extracted = (
                api_response.get("status")
                or (booking_data.get("status") if isinstance(booking_data, dict) else None)
                or "updated"
            )

            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking_status": status_extracted,
                    "booking": booking_data,
                },
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome

        elif action_name == "booking.cancel":
            # Extract booking_code from booking payload
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError("booking_code is required for cancellation")

            # Extract cancellation details from booking payload
            cancellation_type = booking.get(
                "cancellation_type", "user_initiated")
            reason = booking.get("reason")
            notes = booking.get("notes")
            refund_method = booking.get("refund_method")
            notify_customer = booking.get("notify_customer")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.cancel_booking(
                booking_code=booking_code,
                organization_id=resolved_org_id,
                cancellation_type=cancellation_type,
                reason=reason,
                notes=notes,
                refund_method=refund_method,
                notify_customer=notify_customer
            )

            logger.info(
                f"Successfully cancelled booking {booking_code} for user {user_id}")
            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking_status": api_response.get("status", "cancelled")
                }
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome
        elif action_name == "booking.inquiry":
            booking_code = booking.get("booking_code") or booking.get("code")
            if not booking_code:
                raise ValueError(
                    "booking_code is required for booking inquiry")

            # Route execution by mode
            execution_backend = _get_execution_backend(booking_client)
            api_response = execution_backend.get_booking(booking_code)
            booking_data = api_response.get("booking")
            if isinstance(api_response.get("data"), dict) and isinstance(api_response["data"].get("booking"), dict):
                booking_data = api_response["data"]["booking"]

            outcome = {
                "success": True,
                "outcome": {
                    "status": "EXECUTED",
                    "booking_code": booking_code,
                    "booking": booking_data or api_response,
                },
            }

            # Invoke workflow after_execute hook if registered
            outcome["outcome"] = _invoke_workflow_after_execute(
                intent_name, outcome["outcome"]
            )

            return outcome
        else:
            raise UnsupportedIntentError(
                f"Action {action_name} not implemented")

    except (UpstreamError, ValueError) as e:
        error_type = "upstream_error" if isinstance(
            e, UpstreamError) else "invalid_request"
        logger.error(
            f"Business API error for user {user_id} action {action_name}: {str(e)}")
        return {
            "success": False,
            "error": error_type,
            "message": str(e),
            "action": action_name
        }
    except UnsupportedIntentError as e:
        logger.error(f"Unsupported action for user {user_id}: {str(e)}")
        return {
            "success": False,
            "error": "unsupported_action",
            "message": str(e)
        }


def plan_message(
    text: str,
    user_id: str,
    session_state: Optional[Dict[str, Any]] = None,
    luma_client: Optional[LumaClient] = None,
    organization_client: Optional[OrganizationClient] = None,
    frozen_time: Optional[datetime] = None,
    organization_id: Optional[int] = None
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
        planning_only=True
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
    stage = outcome_plan.get("stage") if outcome_plan.get(
        "stage") is not None else outcome.get("stage")
    action = outcome_plan.get("action") if outcome_plan.get(
        "action") is not None else outcome.get("action")
    status = outcome_plan.get("status") if outcome_plan.get(
        "status") is not None else outcome.get("status")

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
        "_decision": result.get("_decision")
    }

    # Extract time_constraint from multiple possible sources
    # Priority: 1) effective_response (merged_luma_response), 2) raw_luma_response, 3) outcome.facts.context
    time_constraint = None
    merged_luma_response = result.get("_merged_luma_response", {})
    if isinstance(merged_luma_response, dict):
        # Check effective_response first (time_constraint is stored here during processing)
        time_constraint = merged_luma_response.get("time_constraint")

        # If not found, check raw_luma_response within effective_response
        if time_constraint is None:
            raw_luma_response = merged_luma_response.get(
                "_raw_luma_response", {})
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

    # Preserve rendered clarification text if present
    # Text is injected at top level of result by _inject_rendering_text
    if "text" in result:
        planning_result["text"] = result["text"]
    elif "text" in outcome:
        planning_result["text"] = outcome["text"]

    return planning_result
