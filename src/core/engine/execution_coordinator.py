"""ExecutionCoordinator — eligibility, prep, dispatch, and post-processing.

Extracted from ConversationEngine so the engine can orchestrate planning →
execution → rendering without owning booking execution details.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionGateResult:
    """Outcome of eligibility + preparation (before tool dispatch)."""

    path: str
    # skipped | missing_client | no_step | ready
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
    error: Optional[str] = None


class ExecutionCoordinator:
    """Coordinates policy eligibility, prep, ActionRunner, and workflow hooks."""

    def resolve(
        self,
        *,
        plan: Dict[str, Any],
        session_state: Optional[Dict[str, Any]],
        session_store: Optional[Any],
        user_id: str,
        availability_client: Optional[Any],
        organization_client: Optional[Any],
        organization_id: Optional[int],
        kwargs: Dict[str, Any],
    ) -> ExecutionGateResult:
        """Eligibility gate + execution preparation (no tool I/O)."""
        from core.config.org_resolver import _get_org_id_from_env
        from core.engine.outcome_builder import (
            build_planning_only_response,
            build_planning_response_from_plan,
        )
        from core.policy.intent_policy import get_execution_steps

        plan_status = plan.get("status")
        intent_name = plan.get("intent_name") or plan.get("intent")
        plan_action = plan.get("action")
        slots = plan.get("slots", {})
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
                                slots.get("booking_id") or slots.get("booking_code")
                            )
                        else:
                            can_execute = action_slots_satisfied
                    else:
                        can_execute = plan_status == "READY" and action_slots_satisfied
                    break

        if not can_execute:
            logger.debug(
                f"Skipping execution: No eligible action found. "
                f"plan_status={plan_status}, plan_action={plan_action}, "
                f"missing_slots={plan.get('missing_slots', [])}"
            )
            return ExecutionGateResult(
                path="skipped",
                response=build_planning_response_from_plan(plan),
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=False,
            )

        logger.debug(
            f"Allowing action execution: plan_action={plan_action}, "
            f"mode={execution_step.get('mode') if execution_step else 'unknown'}, "
            f"plan_status={plan_status}, required_slots_satisfied=True"
        )

        if execution_step and plan_action:
            plan["action"] = plan_action
            if plan_action == "SEARCH_AVAILABILITY":
                plan["stage"] = "AVAILABILITY"
            elif plan_action == "CONFIRM_APPOINTMENT":
                plan["stage"] = "CONFIRM"

        if not execution_step and plan_action:
            for step in steps:
                if step.get("action") == plan_action:
                    execution_step = step
                    break

        if not execution_step:
            return ExecutionGateResult(
                path="no_step",
                response=build_planning_only_response(plan),
                plan=plan,
                plan_status=plan.get("status"),
                plan_action=plan.get("action"),
                can_execute=False,
            )

        action = execution_step.get("action")
        client_name = execution_step.get("client", "")

        execution_client = None
        if client_name == "availability_client":
            execution_client = availability_client
        elif client_name == "booking_client":
            execution_client = kwargs.get("booking_client")
            if not execution_client:
                logger.warning(
                    f"Execution step {action} requires {client_name}, "
                    f"but it was not provided"
                )
                execution_step = None
        else:
            logger.warning(
                f"Unknown client name '{client_name}' for execution step {action}"
            )
            execution_step = None

        if execution_step and execution_client is None:
            logger.debug(
                f"Execution step {action} requires {client_name}, but client not "
                "provided. Returning planning outcome (likely clarification turn)."
            )
            return ExecutionGateResult(
                path="missing_client",
                response=build_planning_response_from_plan(plan),
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=False,
            )

        if not (execution_step and execution_client):
            return ExecutionGateResult(
                path="no_step",
                response=build_planning_only_response(plan),
                plan=plan,
                plan_status=plan.get("status"),
                plan_action=plan.get("action"),
                can_execute=False,
            )

        if not slots.get("organization_id") and organization_id is not None:
            slots["organization_id"] = organization_id
        elif not slots.get("organization_id"):
            slots["organization_id"] = _get_org_id_from_env()

        if action == "SEARCH_AVAILABILITY":
            from core.orchestration.temporal_proposal import (
                resolve_execution_proposals,
                slots_for_availability_search,
            )

            _exec_proposals = resolve_execution_proposals(plan, session_state)
            slots = slots_for_availability_search(
                slots,
                _exec_proposals["date_proposal"],
                _exec_proposals["time_proposal"],
            )

        plan["slots"] = slots

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
            logger.debug("Could not load sku_to_catalog_id for execution: %s", e)
            plan.setdefault("sku_to_catalog_id", {})

        plan["action"] = action

        if action == "FINALIZE_RESERVATION":
            plan_facts: Dict[str, Any] = {}
            if session_state and isinstance(session_state, dict):
                plan_facts = session_state.get("facts", {})
                if not isinstance(plan_facts, dict):
                    plan_facts = {}

            if organization_client and organization_id:
                if not plan_facts.get("org"):
                    try:
                        org_details = organization_client.get_details(organization_id)
                        if isinstance(org_details, dict):
                            org_data = (
                                org_details.get("organization") or org_details
                            )
                            if org_data and isinstance(org_data, dict):
                                if not plan_facts:
                                    plan_facts = {}
                                plan_facts["org"] = org_data
                    except Exception as e:
                        logger.debug(
                            "Failed to fetch org data for FINALIZE_RESERVATION "
                            f"payment verification: {e}"
                        )

            if plan_facts:
                plan["facts"] = plan_facts
                logger.debug(
                    "Added facts to plan for FINALIZE_RESERVATION execution "
                    "(payment verification)"
                )

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
                                session_store.get_session(user_id) or current_session
                            )
                        elif callable(session_store):
                            current_session = (
                                session_store(user_id) or current_session
                            )

                        if isinstance(current_session, dict):
                            resolved_datetime_range = current_session.get(
                                "resolved_datetime_range"
                            )
                    except Exception as e:
                        logger.debug(
                            "Failed to get resolved_datetime_range from "
                            f"session_store: {e}"
                        )

                if resolved_datetime_range and isinstance(
                    resolved_datetime_range, dict
                ):
                    slots["datetime_range"] = resolved_datetime_range
                    plan["slots"] = slots
                    logger.debug(
                        "[DATETIME_RANGE] Injected resolved_datetime_range into "
                        f"slots for CONFIRM_APPOINTMENT: "
                        f"start={resolved_datetime_range.get('start')}, "
                        f"end={resolved_datetime_range.get('end')}"
                    )

        return ExecutionGateResult(
            path="ready",
            plan=plan,
            plan_status=plan_status,
            plan_action=plan_action,
            can_execute=True,
            action=action,
            client_name=client_name,
            execution_client=execution_client,
            intent_name=intent_name,
            slots=slots,
            session_state=session_state,
        )

    def run(
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
        """Dispatch ActionRunner and run booking/availability post-processing."""
        from core.engine.outcome_builder import (
            build_outcome_from_decision,
            build_planning_response_from_plan,
        )
        from core.orchestration.time_resolution import (
            sync_execution_plan_from_time_resolution,
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

        try:
            _route = workflow_router.get_route(client_name)
            if _route == "availability":
                booking_client_for_execution = None
                if intent_name == "MODIFY_BOOKING":
                    booking_client_for_execution = kwargs.get("booking_client")
                execution_result = action_runner.run(
                    plan=plan,
                    availability_client=execution_client,
                    booking_client=booking_client_for_execution,
                )
            elif _route == "booking":
                execution_result = action_runner.run(
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
            )

            if (
                execution_result.get("type") == "availability"
                and execution_result.get("status") == "success"
            ):
                slots, session_state = availability_workflow.process_search_result(
                    execution_result=execution_result,
                    plan=plan,
                    slots=slots,
                    session_state=session_state,
                    session_store=session_store,
                    user_id=user_id,
                    organization_id=organization_id,
                )

            decision = plan.get("_decision")
            if decision:
                outcome_from_decision = build_outcome_from_decision(decision)
                if not isinstance(execution_result, dict):
                    execution_result = {}
                if "plan" not in execution_result or not isinstance(
                    execution_result.get("plan"), dict
                ):
                    execution_result["plan"] = {}
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

            sync_execution_plan_from_time_resolution(plan, execution_result)

            result = {
                "success": True,
                "result": execution_result,
                "outcome": execution_result,
                "plan": plan,
            }
            result.setdefault("ui_actions", [])
            result["_merged_luma_response"] = plan.get("_merged_luma_response")

            return ExecutionRunResult(
                path="executed",
                response=result,
                plan=plan,
                plan_status=plan_status,
                plan_action=plan_action,
                can_execute=True,
                execution_result=execution_result,
                session_state=session_state,
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
