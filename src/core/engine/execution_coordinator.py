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
    workflow_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ExecutionCoordinator:
    """Coordinates policy eligibility, dispatch, and workflow hooks."""

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
    ) -> ExecutionGateResult:
        """Eligibility gate + execution preparation (no tool I/O)."""
        _ = session_store, user_id  # Compatibility-only; session is already loaded.
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

        slot_org = slots.get("organization_id")
        if slot_org is not None and int(slot_org) != int(organization_id):
            logger.warning(
                "Ignoring conflicting slots.organization_id=%s (request=%s)",
                slot_org,
                organization_id,
            )
        slots["organization_id"] = organization_id

        customer_id = kwargs.get("customer_id")
        if customer_id is not None:
            try:
                customer_id = int(customer_id)
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid customer_id=%r", customer_id)
                customer_id = None
            if customer_id is not None and customer_id > 0:
                slots["customer_id"] = customer_id

        if action == "SEARCH_AVAILABILITY":
            from core.planning.temporal_proposal import (
                resolve_execution_proposals,
                slots_for_availability_search,
            )

            _exec_proposals = resolve_execution_proposals(
                plan,
                session_state,
                context=plan.get("execution_proposal_context"),
            )
            slots = slots_for_availability_search(
                slots,
                _exec_proposals["date_proposal"],
                _exec_proposals["time_proposal"],
            )

        plan["slots"] = slots

        try:
            from core.execution.catalog_resolver import (
                load_sku_to_catalog_id_for_org,
            )

            plan["sku_to_catalog_id"] = load_sku_to_catalog_id_for_org(
                organization_id, organization_client
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

            if organization_client:
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

                if isinstance(session_state, dict):
                    resolved_datetime_range = session_state.get(
                        "resolved_datetime_range"
                    )
                    if not resolved_datetime_range:
                        planning = session_state.get("planning")
                        if isinstance(planning, dict):
                            resolved_datetime_range = planning.get(
                                "bound_datetime"
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
