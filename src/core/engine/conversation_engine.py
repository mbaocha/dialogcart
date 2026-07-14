"""ConversationEngine — production orchestration owner.

Coordinates planning → (browse branch) → execution → rendering.
Observability is owned by ``StageRunner``. Booking execution details live in
``ExecutionCoordinator``; outcome shaping lives in ``outcome_builder``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.engine.execution_coordinator import (
    ExecutionCoordinator,
    ExecutionGateResult,
    ExecutionRunResult,
)
from core.tracing.stage_runner import StageRunner

logger = logging.getLogger(__name__)
turn_logger = logging.getLogger("core.turn_log")


class ConversationEngine:
    """Coordinates the complete turn lifecycle."""

    def __init__(self) -> None:
        self._execution_coordinator = ExecutionCoordinator()

    def _session_load(
        self,
        session_state: Optional[Dict[str, Any]],
        user_id: str,
    ) -> None:
        """Payment capability requires persisted facts."""
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

    def _planning(
        self,
        text: str,
        user_id: str,
        session_state: Optional[Dict[str, Any]],
        luma_client: Optional[Any],
        organization_client: Optional[Any],
        frozen_time: Optional[Any],
        organization_id: Optional[int],
    ) -> Any:
        from core.orchestration.orchestrator import plan_message

        return plan_message(
            text=text,
            user_id=user_id,
            session_state=session_state,
            luma_client=luma_client,
            organization_client=organization_client,
            frozen_time=frozen_time,
            organization_id=organization_id,
        )

    def _browse(
        self,
        plan: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
        availability_workflow: Any,
    ) -> Optional[Dict[str, Any]]:
        """Browse/pagination short-circuit (branch, not an orchestration stage)."""
        return availability_workflow.try_handle_browse_turn(
            plan=plan,
            session_state=session_state,
            session_store=session_store,
            user_id=user_id,
        )

    def _execution(
        self,
        gate: ExecutionGateResult,
        *,
        session_store: Optional[Any],
        user_id: str,
        organization_id: Optional[int],
        action_runner: Any,
        workflow_router: Any,
        booking_workflow: Any,
        availability_workflow: Any,
        kwargs: Dict[str, Any],
    ) -> ExecutionRunResult:
        """Run tools + post-process for a gate that is already ``ready``."""
        return self._execution_coordinator.run(
            gate,
            session_store=session_store,
            user_id=user_id,
            organization_id=organization_id,
            action_runner=action_runner,
            workflow_router=workflow_router,
            booking_workflow=booking_workflow,
            availability_workflow=availability_workflow,
            kwargs=kwargs,
        )

    def _rendering(
        self,
        *,
        result: Dict[str, Any],
        plan: Dict[str, Any],
        execution_result: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        renderer: Any,
    ) -> Dict[str, Any]:
        decision = plan.get("_decision")
        if decision:
            renderer.render_availability(
                result, decision, execution_result, session_state
            )
            renderer.render_outcome(result, decision, execution_result)
        return result

    def _finish_gate(
        self, stages: StageRunner, gate: ExecutionGateResult
    ) -> Dict[str, Any]:
        assert gate.response is not None and gate.plan is not None
        if gate.path == "skipped":
            stages.tool_execution_skipped(plan_action=gate.plan_action)
            return stages.finish(
                gate.response,
                plan=gate.plan,
                plan_status=gate.plan_status,
                plan_action=gate.plan_action,
                can_execute=False,
            )
        if gate.path == "missing_client":
            stages.tool_execution_missing_client(plan_action=gate.plan_action)
            return stages.finish(
                gate.response,
                plan=gate.plan,
                plan_status=gate.plan_status,
                plan_action=gate.plan_action,
                can_execute=False,
            )
        return stages.finish_without_invariant_attach(
            gate.response,
            plan=gate.plan,
            plan_status=gate.plan_status,
            plan_action=gate.plan_action,
            can_execute=False,
        )

    def _finish_run(
        self, stages: StageRunner, run: ExecutionRunResult
    ) -> Dict[str, Any]:
        if run.path == "unsupported":
            return stages.finish_without_invariant_attach(
                run.response,
                plan=run.plan,
                plan_status=run.plan_status,
                plan_action=run.plan_action,
                can_execute=False,
            )
        stages.tool_execution_failed(
            plan_action=run.plan_action,
            error=run.error or "",
        )
        return stages.finish(
            run.response,
            plan=run.plan,
            plan_status=run.plan_status,
            plan_action=run.plan_action,
            can_execute=True,
        )

    def process_turn(
        self,
        text: str,
        user_id: str,
        session_state: Optional[Dict[str, Any]] = None,
        availability_client: Optional[Any] = None,
        organization_client: Optional[Any] = None,
        session_store: Optional[Any] = None,
        frozen_time: Optional[Any] = None,
        organization_id: Optional[int] = None,
        luma_client: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Coordinate a complete conversational turn."""
        from core.execution.action_runner import ActionRunner
        from core.rendering.response_renderer import ResponseRenderer
        from core.workflows.availability.workflow import AvailabilityWorkflow
        from core.workflows.booking.workflow import BookingWorkflow
        from core.workflows.router import WorkflowRouter

        action_runner = ActionRunner()
        renderer = ResponseRenderer()
        availability_workflow = AvailabilityWorkflow()
        booking_workflow = BookingWorkflow()
        workflow_router = WorkflowRouter()

        with StageRunner.turn(
            user_id=user_id,
            text=text,
            transaction_id=str(kwargs.get("transaction_id") or ""),
            session_state=session_state,
        ) as stages:
            stages.session_load(
                lambda: self._session_load(session_state, user_id),
                session_state=session_state,
                user_id=user_id,
            )

            plan = self._planning(
                text=text,
                user_id=user_id,
                session_state=session_state,
                luma_client=luma_client,
                organization_client=organization_client,
                frozen_time=frozen_time,
                organization_id=organization_id,
            )

            if not plan or plan.get("error"):
                failure = {
                    "success": False,
                    "error": (
                        plan.get("error", "planning_failed")
                        if plan
                        else "planning_failed"
                    ),
                    "message": (
                        plan.get("message", "Planning failed")
                        if plan
                        else "Planning failed"
                    ),
                    "plan": plan,
                }
                return stages.finish(
                    failure,
                    planning_failed=True,
                    plan=plan if isinstance(plan, dict) else None,
                )

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
                hd_response = {
                    "success": True,
                    "outcome": hd_outcome,
                    "result": hd_outcome,
                }
                return stages.finish(
                    hd_response,
                    handler_delegated=True,
                    plan=plan,
                )

            browse = stages.pagination(
                lambda: self._browse(
                    plan=plan,
                    session_state=session_state,
                    session_store=session_store,
                    user_id=user_id,
                    availability_workflow=availability_workflow,
                ),
                session_state=session_state,
            )
            if browse is not None:
                return stages.finish(
                    browse,
                    pagination_handled=True,
                    plan=plan,
                    plan_status=plan.get("status"),
                    plan_action=plan.get("action"),
                )

            gate = self._execution_coordinator.resolve(
                plan=plan,
                session_state=session_state,
                session_store=session_store,
                user_id=user_id,
                availability_client=availability_client,
                organization_client=organization_client,
                organization_id=organization_id,
                kwargs=kwargs,
            )
            if gate.path != "ready":
                return self._finish_gate(stages, gate)

            # Timing: route → run → post-process → render (legacy boundary).
            with stages.execution_timer() as timer:
                run = self._execution(
                    gate,
                    session_store=session_store,
                    user_id=user_id,
                    organization_id=organization_id,
                    action_runner=action_runner,
                    workflow_router=workflow_router,
                    booking_workflow=booking_workflow,
                    availability_workflow=availability_workflow,
                    kwargs=kwargs,
                )

                if run.path != "executed":
                    return self._finish_run(stages, run)

                assert run.execution_result is not None
                response = self._rendering(
                    result=run.response,
                    plan=run.plan,
                    execution_result=run.execution_result,
                    session_state=(
                        run.session_state
                        if run.session_state is not None
                        else session_state
                    ),
                    renderer=renderer,
                )
                stages.tool_execution_executed(
                    plan_action=run.plan_action,
                    execution_result=run.execution_result,
                )
                timer.mark_recorded()
                return stages.finish(
                    response,
                    plan=run.plan,
                    plan_status=run.plan_status,
                    plan_action=run.plan_action,
                    can_execute=True,
                )

    def handle_turn(
        self,
        text: str,
        user_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.process_turn(text=text, user_id=user_id, **kwargs)

    def plan_turn(
        self,
        text: str,
        user_id: str,
        session_state: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        from core.orchestration.orchestrator import plan_message

        return plan_message(
            text=text,
            user_id=user_id,
            session_state=session_state,
            **kwargs,
        )
