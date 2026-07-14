"""PlanningService — public planning API for ConversationEngine.

Owns the stable flat planning contract consumed by the turn engine.
Calls TurnPlanner.plan_turn(..., planning_only=True) and normalizes the result.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient


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
    Planning-only entry point: NLU → session merge → plan, without execution.

    Delegates to plan_turn() with planning_only=True. Returns a structured
    planning result dict. Called from ConversationEngine.process_turn() and
    ConversationEngine.plan_turn().

    Domain is not accepted; plan_turn derives it from organization_id.

    Returns:
        Dict with: intent_name, stage, action, slots, missing_slots,
        time_constraint, status, and plan structure.
    """
    from core.planning.planner.turn_planner import plan_turn

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
        # Include decision information for ConversationEngine / early-return paths
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

    # Carry merged_luma_response so ConversationEngine can persist conversation memory.
    planning_result["_merged_luma_response"] = result.get(
        "_merged_luma_response")

    # Preserve rendered clarification text if present
    # Text is injected at top level of result by _inject_rendering_text
    if "text" in result:
        planning_result["text"] = result["text"]
    elif "text" in outcome:
        planning_result["text"] = outcome["text"]

    return planning_result
