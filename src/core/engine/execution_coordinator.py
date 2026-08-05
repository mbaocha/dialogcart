"""ExecutionCoordinator — validate, select adapter, dispatch, post-process.

Decision owns whether an action should execute (via ``ExecutionCommand``).
Adapters own action-specific operational preparation.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.execution.adapters import get_execution_adapter
from core.execution.adapters.base import PreparedExecution
from core.execution.command import ExecutionBlocked, ExecutionCommand

logger = logging.getLogger(__name__)


@dataclass
class ExecutionGateResult:
    """Outcome of eligibility + preparation (before tool dispatch)."""

    path: str
    # skipped | blocked | missing_client | no_step | ready
    response: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    plan_status: Any = None
    plan_action: Any = None
    can_execute: bool = False
    # Fields populated when path == "ready"
    action: Any = None
    client_name: str = ""
    execution_client: Any = None
    intent_name: Any = None
    slots: Dict[str, Any] = field(default_factory=dict)
    session_state: Optional[Dict[str, Any]] = None
    blocked: Optional[ExecutionBlocked] = None


@dataclass
class ExecutionRunResult:
    """Outcome of tool dispatch + workflow post-processing (before render)."""

    path: str
    # unsupported | executed | failed
    response: Dict[str, Any]
    plan: Dict[str, Any]
    plan_status: Any = None
    plan_action: Any = None
    can_execute: bool = False
    execution_result: Optional[Dict[str, Any]] = None
    session_state: Optional[Dict[str, Any]] = None
    workflow_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ExecutionCoordinator:
    """Coordinates adapter prep, dispatch, and workflow hooks."""

    def resolve(
        self,
        *,
        plan: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
        availability_client: Optional[Any],
        organization_client: Optional[Any],
        organization_id: int,
        kwargs: Dict[str, Any],
        command: Optional[ExecutionCommand] = None,
    ) -> ExecutionGateResult:
        """Operational gate + execution preparation (no tool I/O).

        Decision authorizes via ``command``. Do not re-evaluate policy.
        """
        _ = session_store, user_id  # Signature compat; session is already loaded.
        from core.engine.outcome_builder import (
            apply_execution_blocked_text,
            build_planning_only_response,
            build_planning_response_from_plan,
        )

        plan_status = plan.get("status")
        if command is None:
            logger.debug(
                "Skipping execution: Decision emitted no ExecutionCommand "
                "(planning-only / no-action turn)."
            )
            return ExecutionGateResult(
                path="skipped",
                response=build_planning_response_from_plan(plan),
                plan=plan,
                plan_status=plan_status,
                plan_action=plan.get("action"),
                can_execute=False,
            )

        action = command.action
        client_name = command.client_name
        intent_name = command.intent_name
        plan_action = action

        execution_client, client_block = self._resolve_execution_client(
            action=action,
            client_name=client_name,
            availability_client=availability_client,
            kwargs=kwargs,
        )
        if client_block == "missing_client":
            return ExecutionGateResult(
                path="missing_client",
                response=build_planning_response_from_plan(plan),
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=False,
                blocked=ExecutionBlocked(
                    reason="MISSING_EXECUTION_CLIENT",
                    action=action,
                ),
            )
        if client_block == "unknown_client" or execution_client is None:
            return ExecutionGateResult(
                path="no_step",
                response=build_planning_only_response(plan),
                plan=plan,
                plan_status=plan.get("status"),
                plan_action=plan.get("action"),
                can_execute=False,
                blocked=ExecutionBlocked(
                    reason="UNKNOWN_CLIENT_OR_ROUTE",
                    action=action,
                ),
            )

        adapter = get_execution_adapter(action)
        if adapter is None:
            return ExecutionGateResult(
                path="no_step",
                response=build_planning_only_response(plan),
                plan=plan,
                plan_status=plan.get("status"),
                plan_action=plan.get("action"),
                can_execute=False,
                blocked=ExecutionBlocked(
                    reason="UNSUPPORTED_ACTION",
                    action=action,
                ),
            )

        prepared = adapter.prepare(
            command,
            session_state,
            organization_id,
            organization_client=organization_client,
            kwargs=kwargs,
            plan_snapshot=plan,
        )
        execution_plan = self._execution_plan_from_prepared(plan, prepared)

        if prepared.blocked is not None:
            blocked = prepared.blocked
            blocked_plan = deepcopy(execution_plan)
            if blocked.reason in (
                "CUSTOMER_ID_REQUIRED",
                "BOOKING_IDENTIFICATION_REQUIRED",
            ):
                from core.planning.pipeline.decision_finalization import (
                    ExecutionBlockedFinalizationEvidence,
                    finalize_decision_after_execution_blocked,
                )

                finalize_decision_after_execution_blocked(
                    blocked_plan,
                    evidence=ExecutionBlockedFinalizationEvidence(
                        reason=blocked.reason,
                        required_input=getattr(blocked, "required_input", None),
                        preserve_pending_confirmation=(
                            blocked.reason == "CUSTOMER_ID_REQUIRED"
                        ),
                    ),
                )
            response = build_planning_response_from_plan(blocked_plan)
            apply_execution_blocked_text(response, reason=blocked.reason)
            return ExecutionGateResult(
                path="blocked",
                response=response,
                plan=blocked_plan,
                plan_status=blocked_plan.get("status"),
                plan_action=plan_action,
                can_execute=False,
                blocked=blocked,
            )

        return ExecutionGateResult(
            path="ready",
            plan=execution_plan,
            plan_status=plan_status,
            plan_action=plan_action,
            can_execute=True,
            action=action,
            client_name=client_name,
            execution_client=execution_client,
            intent_name=intent_name,
            slots=dict(prepared.slots),
            session_state=session_state,
        )

    @staticmethod
    def _execution_plan_from_prepared(
        source_plan: Dict[str, Any],
        prepared: PreparedExecution,
    ) -> Dict[str, Any]:
        """Build a dispatch plan copy; never mutates the Decision plan."""
        execution_plan = deepcopy(source_plan)
        ExecutionCoordinator._apply_prepared_to_plan(execution_plan, prepared)
        return execution_plan

    @staticmethod
    def _apply_prepared_to_plan(
        plan: Dict[str, Any],
        prepared: PreparedExecution,
    ) -> None:
        plan["action"] = prepared.action
        if prepared.stage is not None:
            plan["stage"] = prepared.stage
        plan["slots"] = dict(prepared.slots)
        if prepared.sku_to_catalog_id is not None:
            plan["sku_to_catalog_id"] = dict(prepared.sku_to_catalog_id)
        else:
            plan.setdefault("sku_to_catalog_id", {})
        if prepared.facts is not None:
            plan["facts"] = dict(prepared.facts)
        if prepared.execution_proposal_context is not None:
            plan["execution_proposal_context"] = dict(
                prepared.execution_proposal_context
            )
        if prepared.entity_schema is not None:
            plan["_entity_schema"] = dict(prepared.entity_schema)
        if prepared.turn_operation:
            plan["turn_operation"] = prepared.turn_operation

    @staticmethod
    def _resolve_execution_client(
        *,
        action: Any,
        client_name: str,
        availability_client: Optional[Any],
        kwargs: Dict[str, Any],
    ) -> tuple[Any, Optional[str]]:
        """Return (client, block_reason) where block_reason is None when ok."""
        if client_name == "availability_client":
            return availability_client, (
                "missing_client" if availability_client is None else None
            )
        if client_name == "booking_client":
            execution_client = kwargs.get("booking_client")
            if not execution_client:
                logger.warning(
                    f"Execution step {action} requires {client_name}, "
                    f"but it was not provided"
                )
                return None, "missing_client"
            return execution_client, None
        logger.warning(
            f"Unknown client name '{client_name}' for execution step {action}"
        )
        return None, "unknown_client"

    def run(
        self,
        gate: ExecutionGateResult,
        *,
        session_store: Optional[Any],
        user_id: str,
        organization_id: int,
        workflow_router: Any,
        booking_workflow: Any,
        availability_workflow: Any,
        kwargs: Dict[str, Any],
    ) -> ExecutionRunResult:
        """Dispatch the selected action and run domain post-processing."""
        from core.execution.dispatcher import execute
        from core.engine.outcome_builder import (
            build_planning_response_from_plan,
        )

        assert gate.path == "ready"
        assert gate.plan is not None

        plan = gate.plan
        plan_status = gate.plan_status
        plan_action = gate.plan_action
        action = gate.action
        client_name = gate.client_name
        execution_client = gate.execution_client
        intent_name = gate.intent_name
        slots = gate.slots
        session_state = gate.session_state
        workflow_result = None

        try:
            _route = workflow_router.get_route(client_name)
            if _route == "availability":
                booking_client_for_execution = None
                if intent_name == "MODIFY_BOOKING":
                    booking_client_for_execution = kwargs.get("booking_client")
                execution_result = execute(
                    plan=plan,
                    availability_client=execution_client,
                    booking_client=booking_client_for_execution,
                )
            elif _route == "booking":
                execution_result = execute(
                    plan=plan, booking_client=execution_client
                )
            else:
                logger.warning(
                    f"Execution for {client_name} not yet implemented (_route={_route})"
                )
                return ExecutionRunResult(
                    path="unsupported",
                    response=build_planning_response_from_plan(plan),
                    plan=plan,
                    plan_status=plan_status,
                    plan_action=plan_action,
                    can_execute=False,
                    session_state=session_state,
                )

            slots = booking_workflow.process_result(
                execution_result=execution_result,
                plan=plan,
                slots=slots,
                action=action,
                session_state=session_state,
            )

            if (
                execution_result.get("status") == "succeeded"
                and isinstance(execution_result.get("availability"), dict)
            ):
                (
                    slots,
                    session_state,
                    workflow_result,
                ) = availability_workflow.process_search_result(
                    execution_result=execution_result,
                    plan=plan,
                    slots=slots,
                    session_state=session_state,
                    session_store=session_store,
                    user_id=user_id,
                    organization_id=organization_id,
                )

            # Carry planner-owned missing_slots onto the execution outcome so
            # persistence consumes Planning's canonical list (never recomputes).
            outcome = dict(execution_result)
            plan_missing = plan.get("missing_slots")
            if isinstance(plan_missing, list):
                outcome["missing_slots"] = list(plan_missing)
                plan_facts = plan.get("facts")
                facts = (
                    dict(plan_facts)
                    if isinstance(plan_facts, dict)
                    else {}
                )
                facts.setdefault("missing_slots", list(plan_missing))
                if isinstance(plan.get("slots"), dict):
                    facts.setdefault("slots", dict(plan["slots"]))
                outcome["facts"] = facts
            if not outcome.get("intent_name") and not outcome.get("intent"):
                intent_name = plan.get("intent_name") or plan.get("intent")
                if intent_name:
                    outcome["intent_name"] = intent_name

            # Preserve planner-owned turn_operation on the HTTP outcome envelope.
            if plan.get("turn_operation"):
                nested_plan = outcome.get("plan")
                nested = dict(nested_plan) if isinstance(nested_plan, dict) else {}
                nested["turn_operation"] = plan.get("turn_operation")
                outcome["plan"] = nested

            # Preserve NLU turn understanding metadata (not planner status).
            turn_meta = plan.get("turn") or outcome.get("turn")
            if isinstance(turn_meta, dict) and turn_meta:
                outcome["turn"] = dict(turn_meta)
                nested_plan = outcome.get("plan")
                nested = dict(nested_plan) if isinstance(nested_plan, dict) else {}
                nested.setdefault("turn", dict(turn_meta))
                outcome["plan"] = nested

            result = {
                "success": execution_result.get("status") != "failed",
                "outcome": outcome,
                "plan": plan,
                "_execution_result": execution_result,
                "_working_session": session_state,
            }
            if isinstance(workflow_result, dict):
                result["_workflow_result"] = workflow_result
            result.setdefault("ui_actions", [])
            result["_merged_luma_response"] = plan.get("_merged_luma_response")

            from core.planning.planner.plan_builder import (
                overlay_post_execution_planning_on_outcome,
                post_execution_planner_status,
            )

            overlay_post_execution_planning_on_outcome(plan, outcome)
            _post_status = post_execution_planner_status(result)
            if _post_status:
                plan_status = _post_status
                result["projection_status"] = _post_status

            return ExecutionRunResult(
                path="executed",
                response=result,
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=True,
                execution_result=execution_result,
                session_state=session_state,
                workflow_result=workflow_result,
            )
        except Exception as e:
            logger.error(f"Execution failed for action {action}: {e}")
            failure = {
                "success": False,
                "error": "execution_failed",
                "message": str(e),
                "plan": plan,
            }
            return ExecutionRunResult(
                path="failed",
                response=failure,
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=True,
                error=str(e),
                session_state=session_state,
            )
