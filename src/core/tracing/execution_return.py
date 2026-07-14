"""Trace helpers for wrapping turn results with execution-eligibility spine emit."""

from __future__ import annotations

from typing import Any, Dict, Optional


def execution_spine_inputs(
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


def return_with_execution_spine(
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
        plan_status=plan_status
        or (plan.get("status") if isinstance(plan, dict) else None),
        plan_action=plan_action
        if plan_action is not None
        else (plan.get("action") if isinstance(plan, dict) else None),
        can_execute=can_execute,
        inputs_evaluated=execution_spine_inputs(
            plan=plan,
            plan_status=plan_status,
            plan_action=plan_action,
            can_execute=can_execute,
        ),
    )
    return result


# Historic private names used by StageRunner / tests
_execution_spine_inputs = execution_spine_inputs
_return_with_execution_spine = return_with_execution_spine
