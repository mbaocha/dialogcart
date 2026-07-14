"""ConversationEngine — production orchestration owner.

This is the canonical per-turn orchestration entrypoint. The HTTP layer
(message.py) delegates directly to process_turn() for all production requests.
handle_message() in orchestrator.py is a compatibility wrapper for tests and
legacy callers.

Production flow:
    HTTP (message.py)
        ↓ session loaded, _raw_session passed
    ConversationEngine.process_turn()
        ↓ plan_message() → browse short-circuit → eligibility gate
        ↓ WorkflowRouter → ActionRunner → dispatcher
        ↓ AvailabilityWorkflow / BookingWorkflow post-processing
        ↓ ResponseRenderer
        → result dict
    HTTP (message.py)
        ↓ capability boundary → handler boundary → persistence → response

Owns:
- planning (via plan_message)
- browse short-circuit
- execution eligibility
- workflow routing
- action execution
- availability post-processing
- booking post-processing
- rendering
- result construction
- decision trace turn lifecycle

Does NOT own:
- session loading or persistence
- HTTP concerns
- capability boundaries
- handler extension delegation
- response serialization
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
# Dedicated turn-level logger — same identity as orchestrator.py turn_logger
turn_logger = logging.getLogger("core.turn_log")


class ConversationEngine:
    """Coordinates the complete turn lifecycle.

    Owns no external I/O. Every stage is delegated to a specialist component.

    Turn flow:
        plan    = plan_message(text, user_id, session_state, ...)
        [browse short-circuit if pagination turn]
        [eligibility gate: can_execute?]
        result  = workflow_router → action_runner → dispatcher
        [availability post-processing]
        [booking slot propagation]
        text    = renderer.render(result)
        return  result
    """

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
        """Coordinate a complete conversational turn (planning + execution + rendering).

        Receives pre-resolved session_state from the caller.
        Does NOT load or persist session state.
        Returns the same result dict structure as the former handle_message().
        """
        from core.execution.action_runner import ActionRunner
        from core.rendering.response_renderer import ResponseRenderer
        from core.workflows.availability.workflow import AvailabilityWorkflow
        from core.workflows.booking.workflow import BookingWorkflow
        from core.workflows.router import WorkflowRouter

        _action_runner = ActionRunner()
        _renderer = ResponseRenderer()
        _availability_workflow = AvailabilityWorkflow()
        _booking_workflow = BookingWorkflow()
        _workflow_router = WorkflowRouter()

        from core.tracing.invariant_trace import (
            TurnInvariantTrace,
            attach_trace_to_result,
            finalize_turn_trace,  # noqa: F401 — imported for symmetry with original
            trace_stage,
        )
        from core.tracing.decision_trace import (
            TurnTrace,
            finalize_turn_trace as finalize_decision_turn_trace,
            is_decision_trace_enabled,
            is_request_decision_trace_bound,
        )
        from core.tracing.stage_checks import (
            check_pagination,
            check_session_load,
            check_tool_execution,
        )
        from core.engine.outcome_builder import (
            build_outcome_from_decision,
            build_planning_response_from_plan,
        )
        from core.config.org_resolver import _get_org_id_from_env
        from core.orchestration.time_resolution import sync_execution_plan_from_time_resolution
        from core.orchestration.orchestrator import (
            plan_message,
            _return_with_execution_spine,
        )

        # LOG 1 — TURN INPUT (suppressed when decision trace will be logged)
        if not is_decision_trace_enabled():
            turn_logger.info(
                json.dumps(
                    {"turn": "INPUT", "user_id": user_id, "text": text}, ensure_ascii=True
                )
            )

        TurnInvariantTrace.begin(user_id, text)
        if is_decision_trace_enabled():
            TurnTrace.begin(
                user_id=user_id,
                text=text,
                transaction_id=str(kwargs.get("transaction_id") or ""),
                force=True,
            )

        # Direct process_turn callers must not leak an open TurnTrace.
        # When the API bound per-request decision-trace ownership, leave
        # the open trace for message.py to finalize/attach.
        _release_direct_decision_trace = not is_request_decision_trace_bound()
        try:
            def _attach_direct_decision_trace(result: Dict[str, Any]) -> Dict[str, Any]:
                """Finalize and attach decision_trace for direct process_turn callers."""
                nonlocal _release_direct_decision_trace
                if _release_direct_decision_trace and isinstance(result, dict):
                    from core.tracing.decision_trace import attach_decision_trace_to_result

                    attach_decision_trace_to_result(result)
                    # attach_decision_trace_to_result finalizes; skip finally finalize
                    _release_direct_decision_trace = False
                return result

            # LOG 3 — SESSION READ (compact; full state is in decision trace when enabled)
            from core.tracing.decision_trace import is_decision_trace_enabled as _trace_enabled
            from core.tracing.server_log import compact_session_snapshot

            if not _trace_enabled():
                turn_logger.info(
                    json.dumps(
                        {
                            "turn": "SESSION_READ",
                            "session": compact_session_snapshot(session_state),
                        },
                        ensure_ascii=True,
                        default=str,
                    )
                )

            # INVARIANT GUARD: Payment capability requires persisted facts
            # This prevents silent regressions where payment_satisfied is lost
            trace_stage(
                "session_load",
                lambda: check_session_load(
                    session_state=session_state, user_id=user_id),
                allowed_mutations=[],
                forbidden_mutations=["session.slots", "session.intent"],
                state_snapshot={
                    "found": session_state is not None,
                    "status": session_state.get("status") if session_state else None,
                    "intent": (
                        session_state.get("intent_name") or session_state.get("intent")
                        if session_state
                        else None
                    ),
                    "slot_keys": sorted((session_state or {}).get("slots", {}).keys()),
                },
            )
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

            # Call plan_message to get planning result (NLU → slot merge → plan)
            plan = plan_message(
                text=text,
                user_id=user_id,
                session_state=session_state,
                luma_client=luma_client,
                organization_client=organization_client,
                frozen_time=frozen_time,
                organization_id=organization_id,
            )

            # Check if planning failed
            if not plan or plan.get("error"):
                failure = {
                    "success": False,
                    "error": plan.get("error", "planning_failed"),
                    "message": plan.get("message", "Planning failed"),
                    "plan": plan,
                }
                attach_trace_to_result(failure)
                return _attach_direct_decision_trace(_return_with_execution_spine(
                    failure,
                    planning_failed=True,
                    plan=plan if isinstance(plan, dict) else None,
                ))

            # HANDLER_DELEGATED: an intent handler (e.g. RAG) will respond — bypass execution path
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
                hd_response = {"success": True,
                               "outcome": hd_outcome, "result": hd_outcome}
                attach_trace_to_result(hd_response)
                return _attach_direct_decision_trace(_return_with_execution_spine(
                    hd_response,
                    handler_delegated=True,
                    plan=plan,
                ))

            pagination_response = _availability_workflow.try_handle_browse_turn(
                plan=plan,
                session_state=session_state,
                session_store=session_store,
                user_id=user_id,
            )
            if pagination_response is None:
                trace_stage(
                    "pagination",
                    lambda: check_pagination(handled=False),
                    skipped=True,
                    forbidden_mutations=["booking.slots", "availability_fingerprint"],
                )
            if pagination_response is not None:
                fp_before = (session_state or {}).get("availability_fingerprint")
                fp_after = fp_before
                trace_stage(
                    "pagination",
                    lambda: check_pagination(
                        handled=True,
                        fingerprint_before=fp_before,
                        fingerprint_after=fp_after,
                        search_executed=False,
                    ),
                    allowed_mutations=["availability_presentation",
                                       "presented_availability"],
                    forbidden_mutations=[
                        "booking.slots",
                        "availability_fingerprint",
                        "last_execution_result",
                    ],
                    state_snapshot={
                        "page_index": (pagination_response.get("availability_pagination") or {}).get(
                            "page_index"
                        ),
                    },
                )
                attach_trace_to_result(pagination_response)
                return _attach_direct_decision_trace(_return_with_execution_spine(
                    pagination_response,
                    pagination_handled=True,
                    plan=plan,
                    plan_status=plan.get("status"),
                    plan_action=plan.get("action"),
                ))

            # Execution uses plan.action only (policy-selected; nullable when nothing runs).
            plan_status = plan.get("status")
            intent_name = plan.get("intent_name") or plan.get("intent")
            plan_action = plan.get("action")
            slots = plan.get("slots", {})

            from core.policy.intent_policy import get_execution_steps

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
                                    slots.get("booking_id") or slots.get(
                                        "booking_code")
                                )
                            else:
                                can_execute = action_slots_satisfied
                        else:
                            can_execute = plan_status == "READY" and action_slots_satisfied
                        break

            # Block execution if no eligible action found
            if not can_execute:
                logger.debug(
                    f"Skipping execution: No eligible action found. "
                    f"plan_status={plan_status}, plan_action={plan_action}, "
                    f"missing_slots={plan.get('missing_slots', [])}"
                )
                response = build_planning_response_from_plan(plan)
                trace_stage(
                    "tool_execution",
                    lambda: check_tool_execution(
                        plan_action=plan_action,
                        execution_result=None,
                        can_execute=False,
                    ),
                    skipped=not plan_action,
                    state_snapshot={"plan_action": plan_action,
                                    "can_execute": can_execute},
                )
                attach_trace_to_result(response)
                return _attach_direct_decision_trace(_return_with_execution_spine(
                    response,
                    plan=plan,
                    plan_status=plan_status,
                    plan_action=plan_action,
                    can_execute=False,
                ))

            logger.debug(
                f"Allowing action execution: plan_action={plan_action}, mode={execution_step.get('mode') if execution_step else 'unknown'}, "
                f"plan_status={plan_status}, required_slots_satisfied=True"
            )

            # Update plan with selected action if we found an executable action
            if execution_step and plan_action:
                plan["action"] = plan_action
                # Update stage based on action
                if plan_action == "SEARCH_AVAILABILITY":
                    plan["stage"] = "AVAILABILITY"
                elif plan_action == "CONFIRM_APPOINTMENT":
                    plan["stage"] = "CONFIRM"

            # If execution_step wasn't set yet, find it for the selected action
            if not execution_step and plan_action:
                for step in steps:
                    if step.get("action") == plan_action:
                        execution_step = step
                        break

            # execution_step is already determined by policy-driven eligibility check above
            # No need to re-select - we've already validated the action can execute

            # Only execute if policy selected a step
            if execution_step:
                action = execution_step.get("action")
                client_name = execution_step.get("client", "")

                # Map client name to actual client instance
                execution_client = None
                if client_name == "availability_client":
                    execution_client = availability_client
                elif client_name == "booking_client":
                    # Extract booking_client from kwargs
                    execution_client = kwargs.get("booking_client")
                    if not execution_client:
                        logger.warning(
                            f"Execution step {action} requires {client_name}, but it was not provided"
                        )
                        execution_step = None
                else:
                    logger.warning(
                        f"Unknown client name '{client_name}' for execution step {action}"
                    )
                    execution_step = None

                # Only proceed if we have the required client
                if execution_step and execution_client is None:
                    # Missing required client - return planning outcome (no error for clarification turns)
                    # This prevents "missing_dependency" errors when user is clarifying
                    logger.debug(
                        f"Execution step {action} requires {client_name}, but client not provided. "
                        "Returning planning outcome (likely clarification turn)."
                    )
                    response = build_planning_response_from_plan(plan)
                    trace_stage(
                        "tool_execution",
                        lambda: check_tool_execution(
                            plan_action=plan_action,
                            execution_result=None,
                            can_execute=False,
                        ),
                        state_snapshot={
                            "plan_action": plan_action,
                            "reason": "missing_execution_client",
                        },
                    )
                    attach_trace_to_result(response)
                    return _attach_direct_decision_trace(_return_with_execution_spine(
                        response,
                        plan=plan,
                        plan_status=plan_status,
                        plan_action=plan_action,
                        can_execute=False,
                    ))

                if execution_step and execution_client:
                    # Ensure organization_id is in slots for execution
                    if not slots.get("organization_id") and organization_id is not None:
                        slots["organization_id"] = organization_id
                    elif not slots.get("organization_id"):
                        # Try to get from env as fallback
                        slots["organization_id"] = _get_org_id_from_env()

                    if action == "SEARCH_AVAILABILITY":
                        from core.orchestration.temporal_proposal import (
                            resolve_execution_proposals,
                            slots_for_availability_search,
                        )

                        _exec_proposals = resolve_execution_proposals(
                            plan, session_state)
                        slots = slots_for_availability_search(
                            slots,
                            _exec_proposals["date_proposal"],
                            _exec_proposals["time_proposal"],
                        )

                    # Update plan with organization_id
                    plan["slots"] = slots

                    # Resolve SKU → catalog item id for execution (slots.service_id stays tenant string)
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
                        logger.debug(
                            "Could not load sku_to_catalog_id for execution: %s", e
                        )
                        plan.setdefault("sku_to_catalog_id", {})

                    # Update plan action to match selected step
                    plan["action"] = action

                    # CRITICAL: Add facts to plan for FINALIZE_RESERVATION execution
                    # This enables payment verification in the execution handler
                    # Facts are needed for defensive payment checks even if capability blocking is bypassed
                    if action == "FINALIZE_RESERVATION":
                        # Get facts from session_state (contains org data and payment_satisfied from previous turns)
                        # Session facts are durable and persist across turns
                        plan_facts = {}
                        if session_state and isinstance(session_state, dict):
                            plan_facts = session_state.get("facts", {})
                            if not isinstance(plan_facts, dict):
                                plan_facts = {}

                        # If org data is missing from session facts, fetch it from organization_client
                        # This ensures org.payment_required is available for payment verification
                        if organization_client and organization_id:
                            if not plan_facts.get("org"):
                                try:
                                    org_details = organization_client.get_details(
                                        organization_id
                                    )
                                    if isinstance(org_details, dict):
                                        org_data = (
                                            org_details.get(
                                                "organization") or org_details
                                        )
                                        if org_data and isinstance(org_data, dict):
                                            if not plan_facts:
                                                plan_facts = {}
                                            plan_facts["org"] = org_data
                                except Exception as e:
                                    logger.debug(
                                        f"Failed to fetch org data for FINALIZE_RESERVATION payment verification: {e}"
                                    )

                        if plan_facts:
                            plan["facts"] = plan_facts
                            logger.debug(
                                "Added facts to plan for FINALIZE_RESERVATION execution (payment verification)"
                            )

                    # Step 2: Inject resolved_datetime_range before CONFIRM_APPOINTMENT
                    # If datetime_range is missing in slots, check session for resolved_datetime_range
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
                                            session_store.get_session(user_id)
                                            or current_session
                                        )
                                    elif callable(session_store):
                                        current_session = session_store(
                                            user_id) or current_session

                                    if isinstance(current_session, dict):
                                        resolved_datetime_range = current_session.get(
                                            "resolved_datetime_range"
                                        )
                                except Exception as e:
                                    logger.debug(
                                        "Failed to get resolved_datetime_range from session_store: %s", e
                                    )

                            # Inject into slots if found
                            if resolved_datetime_range and isinstance(
                                resolved_datetime_range, dict
                            ):
                                slots["datetime_range"] = resolved_datetime_range
                                # Update plan with injected datetime_range
                                plan["slots"] = slots
                                logger.debug(
                                    f"[DATETIME_RANGE] Injected resolved_datetime_range into slots for CONFIRM_APPOINTMENT: "
                                    f"start={resolved_datetime_range.get('start')}, "
                                    f"end={resolved_datetime_range.get('end')}"
                                )

                    try:
                        import time as _time

                        _execution_started = _time.perf_counter()
                        _route = _workflow_router.get_route(client_name)
                        if _route == "availability":
                            # For MODIFY_BOOKING, also pass booking_client to fetch service_id from booking
                            booking_client_for_execution = None
                            if intent_name == "MODIFY_BOOKING":
                                booking_client_for_execution = kwargs.get(
                                    "booking_client")
                            execution_result = _action_runner.run(
                                plan=plan,
                                availability_client=execution_client,
                                booking_client=booking_client_for_execution,
                            )
                        elif _route == "booking":
                            execution_result = _action_runner.run(
                                plan=plan, booking_client=execution_client
                            )
                        else:
                            # Unrecognised route — execution not yet supported for this client
                            logger.warning(
                                f"Execution for {client_name} not yet implemented (_route={_route})")
                            return _attach_direct_decision_trace(_return_with_execution_spine(
                                build_planning_response_from_plan(plan),
                                plan=plan,
                                plan_status=plan_status,
                                plan_action=plan_action,
                                can_execute=False,
                            ))

                        slots = _booking_workflow.process_result(
                            execution_result=execution_result,
                            plan=plan,
                            slots=slots,
                            action=action,
                        )

                        if (
                            execution_result.get("type") == "availability"
                            and execution_result.get("status") == "success"
                        ):
                            slots, session_state = _availability_workflow.process_search_result(
                                execution_result=execution_result,
                                plan=plan,
                                slots=slots,
                                session_state=session_state,
                                session_store=session_store,
                                user_id=user_id,
                                organization_id=organization_id,
                            )

                        # Ensure execution_result includes plan structure (status, stage, action)
                        # Build from decision if available, otherwise use plan
                        decision = plan.get("_decision")
                        if decision:
                            # Use canonical builder to ensure plan structure is complete
                            outcome_from_decision = build_outcome_from_decision(
                                decision)
                            # Merge plan structure into execution_result
                            if not isinstance(execution_result, dict):
                                execution_result = {}
                            if "plan" not in execution_result or not isinstance(
                                execution_result.get("plan"), dict
                            ):
                                execution_result["plan"] = {}
                            # Ensure plan.status, plan.stage, plan.action are present
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
                            # Fallback: extract from plan
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

                        sync_execution_plan_from_time_resolution(
                            plan, execution_result)

                        result = {
                            "success": True,
                            "result": execution_result,
                            "outcome": execution_result,  # Alias for backward compatibility
                            "plan": plan,
                        }
                        result.setdefault("ui_actions", [])
                        result["_merged_luma_response"] = plan.get(
                            "_merged_luma_response")

                        decision = plan.get("_decision")
                        if decision:
                            _renderer.render_availability(
                                result, decision, execution_result, session_state
                            )
                            _renderer.render_outcome(result, decision, execution_result)

                        trace_stage(
                            "tool_execution",
                            lambda er=execution_result: check_tool_execution(
                                plan_action=plan_action,
                                execution_result=er,
                                can_execute=True,
                            ),
                            state_snapshot={
                                "plan_action": plan_action,
                                "execution_status": execution_result.get("status"),
                                "execution_type": execution_result.get("type"),
                            },
                        )
                        attach_trace_to_result(result)
                        try:
                            from core.tracing.decision_trace import TurnTrace

                            trace = TurnTrace.current()
                            if trace is not None:
                                trace.record_stage_timing(
                                    "execution",
                                    (_time.perf_counter() - _execution_started) * 1000.0,
                                )
                        except ImportError:
                            pass
                        return _attach_direct_decision_trace(_return_with_execution_spine(
                            result,
                            plan=plan,
                            plan_status=plan_status,
                            plan_action=plan_action,
                            can_execute=True,
                        ))
                    except Exception as e:
                        logger.error(f"Execution failed for action {action}: {e}")
                        failure = {
                            "success": False,
                            "error": "execution_failed",
                            "message": str(e),
                            "plan": plan,
                        }
                        trace_stage(
                            "tool_execution",
                            lambda: check_tool_execution(
                                plan_action=plan_action,
                                execution_result=None,
                                can_execute=True,
                            ),
                            state_snapshot={"error": str(e)},
                        )
                        attach_trace_to_result(failure)
                        return _attach_direct_decision_trace(_return_with_execution_spine(
                            failure,
                            plan=plan,
                            plan_status=plan_status,
                            plan_action=plan_action,
                            can_execute=True,
                        ))

            # No execution step selected by policy - return planning outcome
            # Build outcome from decision (canonical builder)
            decision = plan.get("_decision")
            if decision:
                outcome_dict = build_outcome_from_decision(decision)
            else:
                # Fallback: construct minimal outcome when decision is missing
                logger.warning(
                    "Decision not available in plan, using fallback construction")
                plan_slots = plan.get("slots", {})
                plan_missing_slots = plan.get("missing_slots", [])
                plan_obj = plan.get("plan", {})
                if not isinstance(plan_obj, dict):
                    plan_obj = {}
                facts = {
                    "slots": plan_slots if isinstance(plan_slots, dict) else {},
                    "missing_slots": (
                        plan_missing_slots if isinstance(
                            plan_missing_slots, list) else []
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
            # Add active_capability if present in plan
            if plan.get("active_capability"):
                outcome_dict["active_capability"] = plan.get("active_capability")
            result = {
                "success": True,
                "result": outcome_dict,
                "outcome": outcome_dict,  # Alias for backward compatibility
            }
            result.setdefault("ui_actions", [])
            return _attach_direct_decision_trace(_return_with_execution_spine(
                result,
                plan=plan,
                plan_status=plan.get("status"),
                plan_action=plan.get("action"),
                can_execute=False,
            ))
        finally:
            if _release_direct_decision_trace:
                finalize_decision_turn_trace()

    def handle_turn(
        self,
        text: str,
        user_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Coordinate a complete conversational turn.

        Compatibility alias — delegates to process_turn().
        Preserved for any callers that used the original handle_turn() stub.
        """
        return self.process_turn(text=text, user_id=user_id, **kwargs)

    def plan_turn(
        self,
        text: str,
        user_id: str,
        session_state: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Return the planning result for a turn without executing."""
        from core.orchestration.orchestrator import plan_message

        return plan_message(
            text=text,
            user_id=user_id,
            session_state=session_state,
            **kwargs,
        )
