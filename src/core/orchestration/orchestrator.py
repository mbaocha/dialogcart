"""
Orchestration — compatibility layer.

Production orchestration is owned by ConversationEngine (core/engine/conversation_engine.py).
This module provides:

- handle_message() — compatibility wrapper for tests and legacy callers;
  loads session via three-fallback chain, delegates to ConversationEngine.process_turn()
- plan_message() — temporary re-export of core.planning.planning_service.plan_message
- Re-exports of symbols moved to neutral modules (outcome_builder, response_renderer,
  session_ops) so existing callers remain unaffected.

Planning ownership lives in core.planning. ConversationEngine consumes Planning via
planning_service; do not grow new planning logic here.
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
    **kwargs,  # Backward-compat shim: ignore unknown infra parameters (e.g., customer_id)  # noqa: ARG001
) -> Dict[str, Any]:
    """Compatibility wrapper around ConversationEngine.process_turn().

    Resolves session state via the three-fallback chain (session_store →
    kwargs["session_state"] → default session manager) and delegates the
    full orchestration lifecycle to ConversationEngine.

    The orchestration lifecycle (planning, browse short-circuit, execution
    eligibility, workflow routing, action execution, availability/booking
    post-processing, rendering, result construction) now lives in
    ConversationEngine.process_turn().

    Args:
        text: User message text.
        user_id: User identifier.
        luma_client: Injected Luma client instance.
        availability_client: Injected availability client (required for SEARCH_AVAILABILITY).
        organization_client: Injected organization client.
        session_store: Optional session store with get_session(user_id) method.
        frozen_time: Optional frozen time for testing.
        organization_id: Optional organization ID (defaults to ORG_ID env or 1).
        **kwargs: Backward-compat shim — accepts but ignores unknown infrastructure
                  parameters (e.g., customer_id). Domain is not forwarded into planning;
                  planning derives domain from organization_id.

    Returns:
        Same structure as ConversationEngine.process_turn():
        - execution result dict when an action was executed
        - planning result dict when no action was eligible
        - {"success": False, "error": ..., "message": ...} on failure
    """
    # Session loading — three-fallback chain retained here for backward compatibility.
    # Callers may pass session_store and expect it to be loaded before delegation.
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

    # session_state is passed explicitly below; drop any copy left in kwargs so
    # process_turn does not get multiple values for the same keyword.
    # Drop domain — planning derives it from organization_id; do not forward.
    kwargs.pop("session_state", None)
    kwargs.pop("domain", None)

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

    from core.engine.conversation_engine import ConversationEngine

    engine = ConversationEngine()
    return engine.process_turn(
        text=text,
        user_id=user_id,
        session_state=session_state,
        availability_client=availability_client,
        organization_client=organization_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=organization_id,
        luma_client=luma_client,
        **kwargs,
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
    """Compatibility re-export of ``core.planning.planning_service.plan_message``.

    Prefer importing from ``core.planning.planning_service`` (or ``core.planning``).
    """
    from core.planning.planning_service import plan_message as _plan_message

    return _plan_message(
        text=text,
        user_id=user_id,
        session_state=session_state,
        luma_client=luma_client,
        organization_client=organization_client,
        frozen_time=frozen_time,
        organization_id=organization_id,
    )
