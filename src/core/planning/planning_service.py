"""PlanningService — public planning API for ConversationEngine.

Owns the stable flat planning contract consumed by the turn engine.
Calls TurnPlanner.plan_turn(..., planning_only=True) and normalizes the result.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient


def plan_message(
    text: str,
    user_id: str,
    organization_id: int,
    session_state: Optional[Dict[str, Any]] = None,
    luma_client: Optional[LumaClient] = None,
    catalog_client: Optional[CatalogClient] = None,
    organization_client: Optional[OrganizationClient] = None,
) -> Dict[str, Any]:
    """
    Planning-only entry point: NLU → session merge → plan, without execution.

    Delegates to plan_turn() with planning_only=True. Returns a structured
    planning result dict. Called from ConversationEngine.process_turn().

    Domain is not accepted; plan_turn derives it from organization_id.

    Returns:
        Dict with: intent_name, stage, action, slots, missing_slots,
        planning result containing slots, Temporal, status, and plan structure.
    """
    from core.planning.planner.turn_planner import plan_turn

    result = plan_turn(
        user_id=user_id,
        text=text,
        session_state=session_state,
        luma_client=luma_client,
        catalog_client=catalog_client,
        organization_client=organization_client,
        organization_id=organization_id,
        planning_only=True,
        apply_domain_filter=True,
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
    turn_operation = outcome_plan.get("turn_operation") or outcome.get("turn_operation")
    if turn_operation:
        planning_result["turn_operation"] = turn_operation
        plan_structure.setdefault("turn_operation", turn_operation)
    turn_meta = outcome.get("turn") or outcome_plan.get("turn")
    if isinstance(turn_meta, dict) and turn_meta:
        planning_result["turn"] = dict(turn_meta)
        plan_structure.setdefault("turn", dict(turn_meta))
    if outcome_plan.get("availability_reshow"):
        planning_result["availability_reshow"] = True
        plan_structure.setdefault("availability_reshow", True)
    proposal_context = outcome_plan.get("execution_proposal_context")
    if isinstance(proposal_context, dict):
        planning_result["execution_proposal_context"] = dict(proposal_context)
        plan_structure.setdefault(
            "execution_proposal_context", dict(proposal_context)
        )
    entity_schema = outcome_plan.get("_entity_schema")
    if not isinstance(entity_schema, dict):
        facts = outcome.get("facts")
        if isinstance(facts, dict):
            entity_schema = facts.get("_entity_schema")
    if isinstance(entity_schema, dict):
        planning_result["_entity_schema"] = entity_schema
        plan_structure.setdefault("_entity_schema", entity_schema)

    # Preserve explicit degraded-turn metadata through the engine boundary.
    for fallback_key in ("recovered", "recovery_reason", "message_applied"):
        if fallback_key in outcome:
            planning_result[fallback_key] = outcome[fallback_key]

    # Carry digression / RAG routing fields — stripped by standard planning_result construction
    if outcome.get("status") in ("HANDLER_DELEGATED", "OFF_TOPIC"):
        for _k in (
            "active_handler",
            "search_query",
            "off_topic_query",
            "answerable",
            "answer",
            "turn",
        ):
            if outcome.get(_k) is not None:
                planning_result[_k] = outcome[_k]
                if _k == "turn" and isinstance(outcome[_k], dict):
                    plan_structure["turn"] = dict(outcome[_k])

    merged_luma_response = result.get("_merged_luma_response", {})
    if not isinstance(merged_luma_response, dict):
        merged_luma_response = {}

    from core.planning.temporal_contract import get_temporal

    planning_result["temporal"] = get_temporal(
        merged_luma_response
        if merged_luma_response
        else {"temporal": result.get("temporal")}
    )

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
