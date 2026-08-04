"""ConversationEngine — production orchestration owner.

Called from ``core.api.message`` (HTTP) and ``core.api.compat`` (tests/legacy).
Owns the turn pipeline after the API loads session; does not persist session or
run capability/handler boundaries (those live in ``core.api``).

Turn flow (``process_turn``):
    planning via ``plan_message()`` → ``plan_turn()``
        ↓  planning failure → error result
        ↓  OFF_TOPIC → Core ``ResponseRenderer.render_off_topic``
        ↓  HANDLER_DELEGATED → early result (API runs extension handler boundary)
        ↓  availability browse/pagination → early result (no SEARCH_AVAILABILITY)
        ↓  ``ExecutionCoordinator.resolve()`` — eligibility gate (may skip execution)
        ↓  ``ExecutionCoordinator.run()`` — tool dispatch + workflow post-process
        ↓  ``ResponseRenderer`` — user-facing text on executed paths
        → result { success, outcome, plan?, text, _decision?, _merged_luma_response? }

Ownership:
    - Planning: ``core.planning`` (this module only sequences it).
    - Execution eligibility, client binding, dispatch: ``ExecutionCoordinator``.
    - Outcome dict shaping: ``outcome_builder``.
    - Observability: ``StageRunner`` (stage timing, invariant trace hooks).
    - Session persistence: ``core.api`` + ``core.session`` after ``process_turn`` returns.
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
        catalog_client: Optional[Any],
        organization_client: Optional[Any],
        organization_id: int,
    ) -> Any:
        from core.planning.planning_service import plan_message

        return plan_message(
            text=text,
            user_id=user_id,
            session_state=session_state,
            luma_client=luma_client,
            catalog_client=catalog_client,
            organization_client=organization_client,
            organization_id=organization_id,
        )

    def _browse(
        self,
        plan: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        organization_id: int,
        user_id: str,
        availability_workflow: Any,
    ) -> Optional[Dict[str, Any]]:
        """Browse/pagination short-circuit (branch, not an orchestration stage)."""
        return availability_workflow.try_handle_browse_turn(
            plan=plan,
            session_state=session_state,
            session_store=session_store,
            organization_id=organization_id,
            user_id=user_id,
        )

    def _execution(
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
        """Run tools + post-process for a gate that is already ``ready``."""
        return self._execution_coordinator.run(
            gate,
            session_store=session_store,
            user_id=user_id,
            organization_id=organization_id,
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
            renderer.render_execution(
                result, decision, execution_result, session_state
            )
        return result

    def _finish_gate(
        self,
        stages: StageRunner,
        gate: ExecutionGateResult,
        *,
        session_state: Optional[Dict[str, Any]] = None,
        availability_client: Optional[Any] = None,
        user_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        assert gate.response is not None and gate.plan is not None
        if gate.path in ("skipped", "blocked"):
            stages.tool_execution_skipped(plan_action=gate.plan_action)
            from core.rendering.response_renderer import ResponseRenderer

            renderer = ResponseRenderer()
            decision = (
                gate.plan.get("_decision") if isinstance(gate.plan, dict) else None
            )
            renderer.render_recovery(
                gate.response,
                plan=gate.plan,
                session_state=session_state,
                user_input=user_text,
                availability_client_present=availability_client is not None,
                decision=decision if isinstance(decision, dict) else None,
            )
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
        organization_id: int,
        session_state: Optional[Dict[str, Any]] = None,
        availability_client: Optional[Any] = None,
        organization_client: Optional[Any] = None,
        session_store: Optional[Any] = None,
        frozen_time: Optional[Any] = None,
        luma_client: Optional[Any] = None,
        catalog_client: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Coordinate a complete conversational turn.

        Domain is not accepted here; planning derives it from organization_id.
        ``frozen_time`` is accepted for API compatibility but is not forwarded
        to planning (unused by ``plan_turn``).
        """
        from core.rendering.response_renderer import ResponseRenderer
        from core.workflows.availability.workflow import AvailabilityWorkflow
        from core.workflows.booking.workflow import BookingWorkflow
        from core.workflows.router import WorkflowRouter

        # Planning derives domain from organization_id; never forward HTTP domain.
        kwargs.pop("domain", None)
        # Drop unused clock override so it cannot leak into planning kwargs.
        _ = frozen_time
        # Prefer explicit arg; accept legacy kwargs for callers that only pass **kwargs.
        if catalog_client is None:
            catalog_client = kwargs.pop("catalog_client", None)

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
                catalog_client=catalog_client,
                organization_client=organization_client,
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

            if plan.get("status") == "OFF_TOPIC":
                ot_outcome = {
                    "status": "OFF_TOPIC",
                    "intent_name": plan.get("intent_name", "") or "OFF_TOPIC",
                    "off_topic_query": plan.get("off_topic_query"),
                    "slots": plan.get("slots", {}),
                    "missing_slots": plan.get("missing_slots", []),
                    "facts": plan.get("facts", {}),
                }
                if plan.get("answerable") is not None:
                    ot_outcome["answerable"] = plan.get("answerable")
                if plan.get("answer") is not None:
                    ot_outcome["answer"] = plan.get("answer")
                turn = plan.get("turn")
                if isinstance(turn, dict) and turn:
                    ot_outcome["turn"] = dict(turn)
                nested_plan = plan.get("plan")
                if isinstance(nested_plan, dict) and isinstance(
                    nested_plan.get("turn"), dict
                ):
                    ot_outcome.setdefault("turn", dict(nested_plan["turn"]))
                ot_response: Dict[str, Any] = {
                    "success": True,
                    "outcome": ot_outcome,
                    "result": ot_outcome,
                    "_working_session": session_state,
                }
                if plan.get("_merged_luma_response") is not None:
                    ot_response["_merged_luma_response"] = plan.get(
                        "_merged_luma_response"
                    )
                renderer.render_off_topic(
                    ot_response,
                    outcome=ot_outcome,
                    session_state=session_state
                    if isinstance(session_state, dict)
                    else {},
                    user_input=text,
                )
                from core.adapters.nlu.conversation_memory import update_conversation

                _conv_base = (
                    session_state if isinstance(session_state, dict) else {}
                )
                _updated_session = update_conversation(
                    _conv_base,
                    user_text=text,
                    intent=ot_outcome.get("intent_name", "OFF_TOPIC"),
                    search_query=None,
                    assistant_text=ot_response.get("text") or ot_outcome.get("text"),
                )
                ot_response["_working_session"] = _updated_session
                # Same persistence key as RAG digressions (session projector input).
                ot_response["_handler_conversation_update"] = _updated_session.get(
                    "conversation"
                )
                return stages.finish(
                    ot_response,
                    plan=plan,
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
                turn = plan.get("turn")
                if isinstance(turn, dict) and turn:
                    hd_outcome["turn"] = dict(turn)
                nested_plan = plan.get("plan")
                if isinstance(nested_plan, dict) and isinstance(
                    nested_plan.get("turn"), dict
                ):
                    hd_outcome.setdefault("turn", dict(nested_plan["turn"]))
                hd_response = {
                    "success": True,
                    "outcome": hd_outcome,
                    "result": hd_outcome,
                    "_working_session": session_state,
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
                    organization_id=organization_id,
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

            from core.execution.command import ExecutionCommandError
            from core.execution.command_builder import build_execution_command

            execution_command = None
            try:
                decision_meta = plan.get("_decision")
                policy_client = None
                if isinstance(decision_meta, dict):
                    policy_client = decision_meta.get("policy_client")
                    nested = decision_meta.get("plan")
                    if policy_client is None and isinstance(nested, dict):
                        policy_client = nested.get("policy_client")
                execution_command = build_execution_command(
                    plan=plan,
                    organization_id=organization_id,
                    policy_client=policy_client,
                )
            except ExecutionCommandError as exc:
                logger.warning("ExecutionCommand build failed closed: %s", exc)
                from core.engine.outcome_builder import build_planning_only_response

                return stages.finish_without_invariant_attach(
                    build_planning_only_response(plan),
                    plan=plan,
                    plan_status=plan.get("status"),
                    plan_action=plan.get("action"),
                    can_execute=False,
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
                command=execution_command,
            )
            if gate.path != "ready":
                return self._finish_gate(
                    stages,
                    gate,
                    session_state=session_state,
                    availability_client=availability_client,
                    user_text=text,
                )

            # Timing: route → run → post-process → render (legacy boundary).
            with stages.execution_timer() as timer:
                run = self._execution(
                    gate,
                    session_store=session_store,
                    user_id=user_id,
                    organization_id=organization_id,
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

