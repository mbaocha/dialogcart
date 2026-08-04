"""Outcome dict construction utilities.

Shared helpers for planning and execution response shaping (used by
ConversationEngine / ExecutionCoordinator and planning outcome builders).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Exact user-facing copy for operational blocks (owned here, not Execution).
_EXECUTION_BLOCKED_TEXT: Dict[str, str] = {
    "CUSTOMER_ID_REQUIRED": (
        "Before I can confirm that, I need a phone number or email "
        "so we can attach the booking to your account."
    ),
}


def apply_execution_blocked_text(
    response: Dict[str, Any],
    *,
    reason: str,
) -> Dict[str, Any]:
    """Attach stable blocked wording owned outside ExecutionCoordinator."""
    text = _EXECUTION_BLOCKED_TEXT.get(reason)
    if not text:
        return response
    response["text"] = text
    outcome = response.get("outcome")
    if isinstance(outcome, dict):
        outcome["text"] = text
    result = response.get("result")
    if isinstance(result, dict) and result is not outcome:
        result["text"] = text
    return response


def _copy_fallback_metadata(source: Dict[str, Any], target: Dict[str, Any]) -> None:
    """Preserve explicit NLU-fallback markers through response shaping."""
    for key in ("recovered", "recovery_reason", "message_applied"):
        if key in source:
            target[key] = source[key]


def _apply_turn_understanding(
    outcome: Dict[str, Any],
    *sources: Any,
) -> None:
    """Copy NLU ``turn.understanding`` metadata onto the shaped outcome."""
    turn = None
    for source in sources:
        if not isinstance(source, dict):
            continue
        candidate = source.get("turn")
        if isinstance(candidate, dict) and candidate:
            turn = candidate
            break
        nested = source.get("plan")
        if isinstance(nested, dict):
            candidate = nested.get("turn")
            if isinstance(candidate, dict) and candidate:
                turn = candidate
                break
    if not turn:
        return
    outcome["turn"] = dict(turn)
    nested_plan = outcome.get("plan")
    if isinstance(nested_plan, dict):
        updated_plan = dict(nested_plan)
        updated_plan["turn"] = dict(turn)
        outcome["plan"] = updated_plan


def build_outcome_from_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Build outcome dictionary from decision object.

    Unifies outcome construction across all return paths by extracting
    all required fields from the decision object.
    """
    if not decision or not isinstance(decision, dict):
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

    slots = facts.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    missing_slots = facts.get("missing_slots", [])
    if not isinstance(missing_slots, list):
        missing_slots = []

    plan_obj = {
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        "stage": plan.get("stage"),
        "action": plan.get("action"),
    }
    if plan.get("turn_operation"):
        plan_obj["turn_operation"] = plan.get("turn_operation")
    if plan.get("availability_reshow"):
        plan_obj["availability_reshow"] = True

    outcome: Dict[str, Any] = {
        "intent_name": decision.get("intent_name", ""),
        "status": plan.get("status", "NEEDS_CLARIFICATION"),
        "stage": plan.get("stage"),
        "action": plan.get("action"),
        "plan": plan_obj,
        "slots": slots,
        "missing_slots": missing_slots,
        "blocked_actions": plan.get("blocked_actions", []),
        "allowed_actions": plan.get("allowed_actions", []),
        "awaiting": plan.get("awaiting"),
        "facts": facts,
    }

    if plan.get("active_capability"):
        outcome["active_capability"] = plan.get("active_capability")

    _apply_turn_understanding(outcome, plan, decision)

    return outcome


def build_planning_response_from_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build a full planning response dict from a plan.

    Delegates to build_outcome_from_decision() when a decision exists;
    otherwise constructs the identical fallback outcome.  Consolidates three
    previously duplicated blocks in the turn response path.
    """
    decision = plan.get("_decision")
    if decision:
        outcome_dict = build_outcome_from_decision(decision)
    else:
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
    if plan.get("active_capability"):
        outcome_dict["active_capability"] = plan.get("active_capability")
    _copy_fallback_metadata(plan, outcome_dict)
    # Top-level plan.turn may outlive _decision.plan (Stage 09 stamps outcome/plan
    # after the decision snapshot is taken).
    _apply_turn_understanding(outcome_dict, plan, decision if isinstance(decision, dict) else None)
    response: Dict[str, Any] = {
        "success": True,
        "result": outcome_dict,
        "outcome": outcome_dict,  # Alias for backward compatibility
    }
    if "text" in plan:
        response["text"] = plan["text"]
    response["_merged_luma_response"] = plan.get("_merged_luma_response")
    response.setdefault("ui_actions", [])
    return response


def build_planning_only_response(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build the response when policy selects no runnable execution step.

    Differs from ``build_planning_response_from_plan`` by omitting plan ``text``
    and ``_merged_luma_response`` (legacy planning-only return shape).
    """
    decision = plan.get("_decision")
    if decision:
        outcome_dict = build_outcome_from_decision(decision)
    else:
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
    if plan.get("active_capability"):
        outcome_dict["active_capability"] = plan.get("active_capability")
    _copy_fallback_metadata(plan, outcome_dict)
    _apply_turn_understanding(outcome_dict, plan, decision if isinstance(decision, dict) else None)
    result: Dict[str, Any] = {
        "success": True,
        "result": outcome_dict,
        "outcome": outcome_dict,
    }
    result.setdefault("ui_actions", [])
    return result
