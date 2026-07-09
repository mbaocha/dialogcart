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
from core.rendering.availability_renderer import build_availability_render_request
from core.rendering.llm_renderer import LlmRenderRequest, render_llm
from core.orchestration.time_resolution import sync_execution_plan_from_time_resolution
from core.routing.workflows import get_workflow

# ---------------------------------------------------------------------------
# Phase 1: import from neutral modules (breaks the orchestrator ↔ turn_planner
# circular dependency).  All names remain importable from this module so that
# existing callers (availability_pagination, tests, etc.) are unaffected.
# ---------------------------------------------------------------------------
from core.engine.outcome_builder import (  # noqa: E402, F401
    _build_planning_outcome,
    build_outcome_from_decision,
)
from core.config.org_resolver import _get_org_id_from_env  # noqa: F401
from core.rendering.response_renderer import (  # noqa: F401
    _inject_rendering_text,
    _inject_rendering_text_impl,
    _inject_availability_text,
    _inject_outcome_text,
    _inject_system_text,
    _structured_context_from_decision,
)
# Phase 2: _persist_to_session moved to session_ops; re-exported for backward compat
from core.orchestration.session_ops import _persist_to_session  # noqa: F401

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


# _build_planning_outcome — implemented in core.engine.outcome_builder; imported above


# build_outcome_from_decision — implemented in core.engine.outcome_builder; imported above


# _structured_context_from_decision — implemented in core.rendering.response_renderer; imported above


# _inject_rendering_text — implemented in core.rendering.response_renderer; imported above


# _inject_rendering_text_impl — implemented in core.rendering.response_renderer; imported above
# _inject_availability_text   — implemented in core.rendering.response_renderer; imported above
# _inject_outcome_text        — implemented in core.rendering.response_renderer; imported above
# _inject_system_text         — implemented in core.rendering.response_renderer; imported above


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


# _get_org_id_from_env — implemented in core.config.org_resolver; imported above


# _persist_to_session — implemented in core.orchestration.session_ops; imported above


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
        logger.debug(
            f"Error looking up workflow for intent '{intent_name}': {e}")

    return outcome


def _execution_spine_inputs(
    *,
    plan: Optional[Dict[str, Any]] = None,
    plan_status: Optional[str] = None,
    plan_action: Optional[str] = None,
    can_execute: bool = False,
) -> Dict[str, Any]:
    plan_obj = plan if isinstance(plan, dict) else {}
    return {
        "plan_status": plan_status or plan_obj.get("status"),
        "plan_action": plan_action if plan_action is not None else plan_obj.get("action"),
        "can_execute": can_execute,
        "missing_slots": plan_obj.get("missing_slots", []),
    }


def _return_with_execution_spine(
    result: Dict[str, Any],
    *,
    pagination_handled: bool = False,
    handler_delegated: bool = False,
    planning_failed: bool = False,
    plan: Optional[Dict[str, Any]] = None,
    plan_status: Optional[str] = None,
    plan_action: Optional[str] = None,
    can_execute: bool = False,
) -> Dict[str, Any]:
    from core.tracing.spine import emit_execution_eligibility

    emit_execution_eligibility(
        pagination_handled=pagination_handled,
        handler_delegated=handler_delegated,
        planning_failed=planning_failed,
        plan_status=plan_status or (
            plan.get("status") if isinstance(plan, dict) else None),
        plan_action=plan_action if plan_action is not None else (
            plan.get("action") if isinstance(plan, dict) else None
        ),
        can_execute=can_execute,
        inputs_evaluated=_execution_spine_inputs(
            plan=plan,
            plan_status=plan_status,
            plan_action=plan_action,
            can_execute=can_execute,
        ),
    )
    return result


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
    # Phase 2: execution, rendering, and availability go through their owners
    from core.execution.action_runner import ActionRunner
    from core.rendering.response_renderer import ResponseRenderer
    from core.workflows.availability.workflow import AvailabilityWorkflow
    from core.workflows.router import WorkflowRouter

    _action_runner = ActionRunner()
    _renderer = ResponseRenderer()
    _availability_workflow = AvailabilityWorkflow()
    _workflow_router = WorkflowRouter()

    from core.tracing.invariant_trace import (
        TurnInvariantTrace,
        attach_trace_to_result,
        finalize_turn_trace,
        trace_stage,
    )
    from core.tracing.decision_trace import TurnTrace, is_decision_trace_enabled
    from core.tracing.stage_checks import (
        check_pagination,
        check_session_load,
        check_tool_execution,
    )

    # LOG 1 — TURN INPUT (suppressed when decision trace will be logged)
    if not is_decision_trace_enabled():
        turn_logger.info(
            json.dumps(
                {"turn": "INPUT", "user_id": user_id, "text": text}, ensure_ascii=True
            )
        )

    TurnInvariantTrace.begin(user_id, text)
    if is_decision_trace_enabled():
        TurnTrace.begin(
            user_id=user_id,
            text=text,
            transaction_id=str(kwargs.get("transaction_id") or ""),
            force=True,
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

    # LOG 3 — SESSION READ (compact; full state is in decision trace when enabled)
    from core.tracing.decision_trace import is_decision_trace_enabled as _trace_enabled
    from core.tracing.server_log import compact_session_snapshot

    if not _trace_enabled():
        turn_logger.info(
            json.dumps(
                {
                    "turn": "SESSION_READ",
                    "session": compact_session_snapshot(session_state),
                },
                ensure_ascii=True,
                default=str,
            )
        )

    # INVARIANT GUARD: Payment capability requires persisted facts
    # This prevents silent regressions where payment_satisfied is lost
    trace_stage(
        "session_load",
        lambda: check_session_load(
            session_state=session_state, user_id=user_id),
        allowed_mutations=[],
        forbidden_mutations=["session.slots", "session.intent"],
        state_snapshot={
            "found": session_state is not None,
            "status": session_state.get("status") if session_state else None,
            "intent": (
                session_state.get("intent_name") or session_state.get("intent")
                if session_state
                else None
            ),
            "slot_keys": sorted((session_state or {}).get("slots", {}).keys()),
        },
    )
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

    # Call plan_message to get planning result (NLU → slot merge → plan)
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
        failure = {
            "success": False,
            "error": plan.get("error", "planning_failed"),
            "message": plan.get("message", "Planning failed"),
            "plan": plan,
        }
        attach_trace_to_result(failure)
        return _return_with_execution_spine(
            failure,
            planning_failed=True,
            plan=plan if isinstance(plan, dict) else None,
        )

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
        hd_response = {"success": True,
                       "outcome": hd_outcome, "result": hd_outcome}
        attach_trace_to_result(hd_response)
        return _return_with_execution_spine(
            hd_response,
            handler_delegated=True,
            plan=plan,
        )

    pagination_response = _availability_workflow.try_handle_browse_turn(
        plan=plan,
        session_state=session_state,
        session_store=session_store,
        user_id=user_id,
    )
    if pagination_response is None:
        trace_stage(
            "pagination",
            lambda: check_pagination(handled=False),
            skipped=True,
            forbidden_mutations=["booking.slots", "availability_fingerprint"],
        )
    if pagination_response is not None:
        fp_before = (session_state or {}).get("availability_fingerprint")
        fp_after = fp_before
        trace_stage(
            "pagination",
            lambda: check_pagination(
                handled=True,
                fingerprint_before=fp_before,
                fingerprint_after=fp_after,
                search_executed=False,
            ),
            allowed_mutations=["availability_presentation",
                               "presented_availability"],
            forbidden_mutations=[
                "booking.slots",
                "availability_fingerprint",
                "last_execution_result",
            ],
            state_snapshot={
                "page_index": (pagination_response.get("availability_pagination") or {}).get(
                    "page_index"
                ),
            },
        )
        attach_trace_to_result(pagination_response)
        return _return_with_execution_spine(
            pagination_response,
            pagination_handled=True,
            plan=plan,
            plan_status=plan.get("status"),
            plan_action=plan.get("action"),
        )

    # Execution uses plan.action only (policy-selected; nullable when nothing runs).
    plan_status = plan.get("status")
    intent_name = plan.get("intent_name") or plan.get("intent")
    plan_action = plan.get("action")
    slots = plan.get("slots", {})

    from core.policy.intent_policy import get_execution_steps

    steps = get_execution_steps(intent_name)

    can_execute = False
    execution_step = None

    if plan_action:
        for step in steps:
            if step.get("action") == plan_action:
                execution_step = step
                mode = step.get("mode", "exploratory")
                required_slots = step.get("required_slots", [])

                action_slots_satisfied = all(
                    slot_name in slots and slots[slot_name] is not None
                    for slot_name in required_slots
                )

                if mode == "exploratory":
                    if plan_action == "FETCH_BOOKING":
                        can_execute = bool(
                            slots.get("booking_id") or slots.get(
                                "booking_code")
                        )
                    else:
                        can_execute = action_slots_satisfied
                else:
                    can_execute = plan_status == "READY" and action_slots_satisfied
                break

    # Block execution if no eligible action found
    if not can_execute:
        logger.debug(
            f"Skipping execution: No eligible action found. "
            f"plan_status={plan_status}, plan_action={plan_action}, "
            f"missing_slots={plan.get('missing_slots', [])}"
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
                    plan_missing_slots if isinstance(
                        plan_missing_slots, list) else []
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
        response["_merged_luma_response"] = plan.get("_merged_luma_response")
        response.setdefault("ui_actions", [])
        trace_stage(
            "tool_execution",
            lambda: check_tool_execution(
                plan_action=plan_action,
                execution_result=None,
                can_execute=False,
            ),
            skipped=not plan_action,
            state_snapshot={"plan_action": plan_action,
                            "can_execute": can_execute},
        )
        attach_trace_to_result(response)
        return _return_with_execution_spine(
            response,
            plan=plan,
            plan_status=plan_status,
            plan_action=plan_action,
            can_execute=False,
        )

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
                outcome_dict["active_capability"] = plan.get(
                    "active_capability")
            response = {
                "success": True,
                "result": outcome_dict,
                "outcome": outcome_dict,  # Alias for backward compatibility
            }
            # Preserve rendered text if present in plan (from plan_message)
            if "text" in plan:
                response["text"] = plan["text"]
            response["_merged_luma_response"] = plan.get(
                "_merged_luma_response")
            response.setdefault("ui_actions", [])
            trace_stage(
                "tool_execution",
                lambda: check_tool_execution(
                    plan_action=plan_action,
                    execution_result=None,
                    can_execute=False,
                ),
                state_snapshot={
                    "plan_action": plan_action,
                    "reason": "missing_execution_client",
                },
            )
            attach_trace_to_result(response)
            return _return_with_execution_spine(
                response,
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=False,
            )

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

                _exec_proposals = resolve_execution_proposals(
                    plan, session_state)
                slots = slots_for_availability_search(
                    slots,
                    _exec_proposals["date_proposal"],
                    _exec_proposals["time_proposal"],
                )

            # Update plan with organization_id
            plan["slots"] = slots

            # Resolve SKU → catalog item id for execution (slots.service_id stays tenant string)
            try:
                from core.orchestration.catalog_resolver import (
                    load_sku_to_catalog_id_for_org,
                )

                _org_for_catalog = int(
                    slots.get("organization_id") or organization_id or 1
                )
                plan["sku_to_catalog_id"] = load_sku_to_catalog_id_for_org(
                    _org_for_catalog, organization_client
                )
            except Exception as e:
                logger.debug(
                    "Could not load sku_to_catalog_id for execution: %s", e
                )
                plan.setdefault("sku_to_catalog_id", {})

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
                                    org_details.get(
                                        "organization") or org_details
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
                    resolved_datetime_range = None
                    current_session = session_state or {}

                    if isinstance(session_state, dict):
                        resolved_datetime_range = session_state.get(
                            "resolved_datetime_range"
                        )

                    if not resolved_datetime_range and session_store is not None:
                        try:
                            if hasattr(session_store, "get_session"):
                                current_session = (
                                    session_store.get_session(user_id)
                                    or current_session
                                )
                            elif callable(session_store):
                                current_session = session_store(
                                    user_id) or current_session

                            if isinstance(current_session, dict):
                                resolved_datetime_range = current_session.get(
                                    "resolved_datetime_range"
                                )
                        except Exception as e:
                            logger.debug(
                                "Failed to get resolved_datetime_range from session_store: %s", e
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
                import time as _time

                _execution_started = _time.perf_counter()
                # Phase 2: WorkflowRouter selects route; ActionRunner owns execution
                _route = _workflow_router.get_route(client_name)
                if _route == "availability":
                    # For MODIFY_BOOKING, also pass booking_client to fetch service_id from booking
                    booking_client_for_execution = None
                    if intent_name == "MODIFY_BOOKING":
                        booking_client_for_execution = kwargs.get(
                            "booking_client")
                    execution_result = _action_runner.run(
                        plan=plan,
                        availability_client=execution_client,
                        booking_client=booking_client_for_execution,
                    )
                elif _route == "booking":
                    execution_result = _action_runner.run(
                        plan=plan, booking_client=execution_client
                    )
                else:
                    # Unrecognised route — execution not yet supported for this client
                    logger.warning(
                        f"Execution for {client_name} not yet implemented (_route={_route})")
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
                    return _return_with_execution_spine(
                        {
                            "success": True,
                            "result": outcome_dict,
                            "outcome": outcome_dict,
                        },
                        plan=plan,
                        plan_status=plan_status,
                        plan_action=plan_action,
                        can_execute=False,
                    )

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

                # Phase 2: AvailabilityWorkflow owns all post-search processing
                # (fingerprint, time resolution, presentation, session persistence)
                if (
                    execution_result.get("type") == "availability"
                    and execution_result.get("status") == "success"
                ):
                    slots, session_state = _availability_workflow.process_search_result(
                        execution_result=execution_result,
                        plan=plan,
                        slots=slots,
                        session_state=session_state,
                        session_store=session_store,
                        user_id=user_id,
                        organization_id=organization_id,
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

                sync_execution_plan_from_time_resolution(
                    plan, execution_result)

                result = {
                    "success": True,
                    "result": execution_result,
                    "outcome": execution_result,  # Alias for backward compatibility
                    "plan": plan,
                }
                result.setdefault("ui_actions", [])
                result["_merged_luma_response"] = plan.get(
                    "_merged_luma_response")

                decision = plan.get("_decision")
                if decision:
                    # Phase 2: ResponseRenderer owns rendering
                    _renderer.render_availability(
                        result, decision, execution_result, session_state
                    )
                    _renderer.render_outcome(result, decision, execution_result)

                trace_stage(
                    "tool_execution",
                    lambda er=execution_result: check_tool_execution(
                        plan_action=plan_action,
                        execution_result=er,
                        can_execute=True,
                    ),
                    state_snapshot={
                        "plan_action": plan_action,
                        "execution_status": execution_result.get("status"),
                        "execution_type": execution_result.get("type"),
                    },
                )
                attach_trace_to_result(result)
                try:
                    from core.tracing.decision_trace import TurnTrace

                    trace = TurnTrace.current()
                    if trace is not None:
                        trace.record_stage_timing(
                            "execution",
                            (_time.perf_counter() - _execution_started) * 1000.0,
                        )
                except ImportError:
                    pass
                return _return_with_execution_spine(
                    result,
                    plan=plan,
                    plan_status=plan_status,
                    plan_action=plan_action,
                    can_execute=True,
                )
            except Exception as e:
                logger.error(f"Execution failed for action {action}: {e}")
                failure = {
                    "success": False,
                    "error": "execution_failed",
                    "message": str(e),
                    "plan": plan,
                }
                trace_stage(
                    "tool_execution",
                    lambda: check_tool_execution(
                        plan_action=plan_action,
                        execution_result=None,
                        can_execute=True,
                    ),
                    state_snapshot={"error": str(e)},
                )
                attach_trace_to_result(failure)
                return _return_with_execution_spine(
                    failure,
                    plan=plan,
                    plan_status=plan_status,
                    plan_action=plan_action,
                    can_execute=True,
                )

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
            "missing_slots": (
                plan_missing_slots if isinstance(
                    plan_missing_slots, list) else []
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
    return _return_with_execution_spine(
        result,
        plan=plan,
        plan_status=plan.get("status"),
        plan_action=plan.get("action"),
        can_execute=False,
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
    from core.planning.orchestration.turn_planner import plan_turn

    result = plan_turn(
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

    # Propagate proposals from merged response so execution call sites can read them
    # from plan without relying on session_state being mutated by plan_message.
    if isinstance(merged_luma_response, dict):
        for _prop_key in ("date_proposal", "time_proposal"):
            _prop_val = merged_luma_response.get(_prop_key)
            if _prop_val is not None:
                planning_result[_prop_key] = _prop_val

    # Carry merged_luma_response so handle_message can persist conversation memory.
    planning_result["_merged_luma_response"] = result.get(
        "_merged_luma_response")

    # Preserve rendered clarification text if present
    # Text is injected at top level of result by _inject_rendering_text
    if "text" in result:
        planning_result["text"] = result["text"]
    elif "text" in outcome:
        planning_result["text"] = outcome["text"]

    return planning_result
