"""Stage 08 — decision plan construction (pure policy interpreter)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.planning.pipeline.requests import (
    AttachedRequest,
    is_availability_turn_operation,
)
from core.planning.pipeline.types import (
    AvailabilityDecision,
    CapabilityDecision,
    ConfirmationDecision,
    DecisionPlan,
    SlotTurnState,
    WorkingTurn,
)
from core.planning.policy.intent_router import get_action_name
from core.planning.time_resolution import TIME_MATCH_EXACT, TIME_MATCH_MISMATCH
from core.planning.temporal_proposal import ExecutionProposalResolutionContext
from core.policy.intent_policy import (
    evaluate_execution_step_candidates,
    get_commit_action,
)
from core.planning.facts import build_policy_execution_flags

logger = logging.getLogger(__name__)


def _availability_operation_precedes_confirm(turn_operation: Optional[str]) -> bool:
    """Explicit availability ops outrank confirm-presentation shortcuts."""
    return is_availability_turn_operation(turn_operation)


def _exact_time_match_presenting_confirmation(
    time_match_outcome: Optional[str],
    *,
    user_confirmation_satisfied: bool,
    turn_operation: Optional[str] = None,
) -> bool:
    if _availability_operation_precedes_confirm(turn_operation):
        return False
    return (
        time_match_outcome == TIME_MATCH_EXACT and not user_confirmation_satisfied
    )


def _stage_for_execution_action(action: Optional[str], selected_step: Dict[str, Any]) -> Optional[str]:
    if action == "FETCH_BOOKING":
        return "IDENTIFY"
    if action == "SEARCH_AVAILABILITY":
        return "AVAILABILITY"
    if action in (
        "CONFIRM_APPOINTMENT",
        "FINALIZE_RESERVATION",
        "APPLY_MODIFICATION",
        "CONFIRM_CANCELLATION",
    ):
        return "CONFIRM"
    mode = selected_step.get("mode", "exploratory")
    return "AVAILABILITY" if mode == "exploratory" else "CONFIRM"


def _derive_stage_from_status(status: str) -> Optional[str]:
    if status == "AWAITING_CONFIRMATION":
        return "CONFIRM"
    if status == "NEEDS_CLARIFICATION":
        return "AVAILABILITY"
    return None


def _apply_has_datetime_invariant(decision_plan: DecisionPlan) -> None:
    plan = decision_plan.plan
    if plan.get("status") != "READY":
        return
    facts = decision_plan.facts
    slots = facts.get("slots", {})
    if not isinstance(slots, dict):
        return
    has_time = bool(slots.get("time"))
    has_date = bool(slots.get("date"))
    has_date_range = isinstance(slots.get("date_range"), dict) and bool(
        slots.get("date_range", {}).get("start")
    )
    has_datetime_range = isinstance(slots.get("datetime_range"), dict) and bool(
        slots.get("datetime_range", {}).get("start")
    )
    if (has_date and has_time) or (has_date_range and has_time) or has_datetime_range:
        slots = dict(slots)
        slots["has_datetime"] = True
        facts["slots"] = slots


def build_decision_plan_from_evidence(
    *,
    attached_request: AttachedRequest,
    working_turn: WorkingTurn,
    slot_state: SlotTurnState,
    availability: AvailabilityDecision,
    confirmation: ConfirmationDecision,
    capability: CapabilityDecision,
    session_state: Optional[Dict[str, Any]],
    organization_id: int,
) -> DecisionPlan:
    """Decision evidence → plan (called only from ``decide()``)."""
    intent_name = slot_state.intent_name
    payload = working_turn.payload
    missing_slots = list(slot_state.missing_slots)
    effective_slots = dict(slot_state.effective_collected_slots)
    needs_clarification = slot_state.needs_clarification
    availability_resolved = availability.availability_ready
    confirmation_state = confirmation.confirmation_state
    user_confirmation_satisfied = confirmation.user_confirmation_satisfied
    active_capability = capability.active_capability
    turn_operation = attached_request.turn_operation
    confirm_booking_continuation = attached_request.confirm_booking_continuation
    availability_op = _availability_operation_precedes_confirm(turn_operation)
    availability_invalidation = confirmation.availability_invalidation
    bound_datetime_clear = confirmation.bound_datetime_clear
    bound_datetime_cleared = bool(
        bound_datetime_clear is not None
        and getattr(bound_datetime_clear, "cleared", False)
    )
    availability_invalidated = bool(
        availability_invalidation is not None
        and getattr(availability_invalidation, "invalidated", False)
    )
    if availability_invalidated:
        # Stage 06 typed evidence — same effect as prior payload trust invalidation.
        availability_resolved = False

    commit_action = get_commit_action(intent_name)

    time_match_outcome = payload.get("time_match_outcome")
    time_resolution = payload.get("time_resolution")
    if not time_match_outcome and isinstance(time_resolution, dict):
        time_match_outcome = time_resolution.get("outcome")

    from core.planning.policy.action_policy import load_planning_policy, plan_intent

    executable_actions: List[str] = []
    if intent_name and intent_name != "UNKNOWN":
        _ea_policy = load_planning_policy()
        _ea_result = plan_intent(intent_name, effective_slots, _ea_policy)
        executable_actions = _ea_result.get("executable_actions", [])

    if confirmation.availability_reshow:
        status = "READY"
        awaiting = None
        action = None
        stage = "AVAILABILITY"
        action_branch = "availability_reshow"
        policy_client = "availability_client"
        flags: Dict[str, Any] = {}
        candidate_evidence: List[Dict[str, Any]] = []
    else:
        if intent_name == "UNKNOWN":
            status = "NEEDS_CLARIFICATION"
        elif time_match_outcome == TIME_MATCH_MISMATCH:
            status = "NEEDS_CLARIFICATION"
        elif _exact_time_match_presenting_confirmation(
            time_match_outcome,
            user_confirmation_satisfied=user_confirmation_satisfied,
            turn_operation=turn_operation,
        ):
            status = "AWAITING_CONFIRMATION"
        elif missing_slots:
            status = "READY" if executable_actions else "NEEDS_CLARIFICATION"
        elif needs_clarification:
            status = "NEEDS_CLARIFICATION"
        elif (
            confirmation.awaiting_user_confirmation
            and not availability_op
        ):
            status = "AWAITING_CONFIRMATION"
        elif capability.awaiting_capability:
            status = "AWAITING_CAPABILITY"
        else:
            status = "READY"

        if time_match_outcome == TIME_MATCH_MISMATCH:
            awaiting = "TIME_SELECTION"
        elif confirmation.awaiting_user_confirmation and not availability_op:
            awaiting = "USER_CONFIRMATION"
        elif capability.awaiting_capability:
            awaiting = capability.awaiting_kind or "CAPABILITY"
        else:
            awaiting = None

        flags = {}
        action = None
        stage = None
        action_branch = None
        policy_client = None
        candidate_evidence = []

        if intent_name and intent_name != "UNKNOWN":
            flags = build_policy_execution_flags(
                intent_name=intent_name,
                slots=effective_slots,
                session_state=session_state,
                luma_response=payload,
                missing_slots=missing_slots,
                needs_clarification=needs_clarification,
                availability_resolved=availability_resolved,
                confirmation_state=confirmation_state,
                organization_id=organization_id,
                confirm_booking_continuation=confirm_booking_continuation,
            )
            if (
                availability_invalidation is not None
                and getattr(availability_invalidation, "invalidated", False)
            ):
                # Policy facts still read session cache unless Stage 03 set a
                # payload flag; apply Stage 06 typed evidence to selector flags.
                flags["availability_ready"] = False
                flags["availability_resolved"] = False
                flags["availability_check_required"] = True
            if bound_datetime_cleared:
                flags["time_selection_ready"] = False
                flags["time_selection_required"] = intent_name == "CREATE_APPOINTMENT"
            selected_step, candidate_evidence = evaluate_execution_step_candidates(
                intent_name, effective_slots, flags
            )
            if selected_step:
                action = selected_step.get("action")
                policy_client = selected_step.get("client")
                action_branch = "policy"
                stage = _stage_for_execution_action(action, selected_step)
            else:
                action_branch = "no_execution_step"

        if time_match_outcome == TIME_MATCH_MISMATCH:
            action = None
            action_branch = "time_match_mismatch"
            stage = "AVAILABILITY"
        elif _exact_time_match_presenting_confirmation(
            time_match_outcome,
            user_confirmation_satisfied=user_confirmation_satisfied,
            turn_operation=turn_operation,
        ):
            action = None
            action_branch = "time_match_exact"
            stage = "CONFIRM"

        if stage is None:
            if availability_op and status == "READY":
                stage = "AVAILABILITY"
            else:
                stage = _derive_stage_from_status(status)

    allowed_actions: List[str] = []
    blocked_actions: List[str] = []
    if missing_slots:
        if commit_action:
            blocked_actions.append(commit_action)
        allowed_actions.extend(executable_actions)
    else:
        allowed_actions.extend(executable_actions)
        if commit_action:
            from core.planning.temporal_proposal import has_bound_booking_datetime

            datetime_bound = has_bound_booking_datetime(
                effective_slots,
                None if bound_datetime_cleared else session_state,
                payload,
            )
            needs_bound_datetime = intent_name == "CREATE_APPOINTMENT" and not datetime_bound
            needs_user_confirmation = (
                intent_name == "CREATE_APPOINTMENT"
                and not user_confirmation_satisfied
            )
            if (
                needs_clarification
                or not availability_resolved
                or needs_bound_datetime
                or needs_user_confirmation
                or availability_op
            ):
                blocked_actions.append(commit_action)
            else:
                allowed_actions.append(commit_action)

    allowed_actions = list(set(allowed_actions))
    blocked_actions = list(set(blocked_actions))

    plan: Dict[str, Any] = {
        "status": status,
        "stage": stage,
        "action": action,
        "allowed_actions": allowed_actions,
        "blocked_actions": blocked_actions,
        "awaiting": awaiting,
        "executable_actions": executable_actions,
        "missing_slots": missing_slots,
    }
    current_turn_has_explicit_time = bool(payload.get("_current_turn_has_time"))
    current_turn_time_proposal = (
        payload.get("time_proposal")
        if current_turn_has_explicit_time
        and isinstance(payload.get("time_proposal"), dict)
        else None
    )
    current_turn_temporal = (
        payload.get("temporal")
        if current_turn_has_explicit_time
        and isinstance(payload.get("temporal"), dict)
        else None
    )
    proposal_resolution_context: ExecutionProposalResolutionContext = {
        "current_turn_time_proposal": current_turn_time_proposal,
        "current_turn_temporal": current_turn_temporal,
        "current_turn_has_explicit_time": current_turn_has_explicit_time,
        "session_time_proposal_reuse_allowed": not (
            availability_invalidated or bound_datetime_cleared
        ),
        "confirmation_continuation": confirm_booking_continuation,
        "availability_invalidated": availability_invalidated,
        "bound_datetime_cleared": bound_datetime_cleared,
    }
    plan["execution_proposal_context"] = proposal_resolution_context
    if time_match_outcome:
        plan["time_match_outcome"] = time_match_outcome
    if isinstance(time_resolution, dict):
        plan["time_resolution"] = time_resolution
    if active_capability:
        plan["active_capability"] = active_capability
    if turn_operation and turn_operation != "NONE":
        plan["turn_operation"] = turn_operation
    if confirmation.availability_reshow:
        plan["availability_reshow"] = True

    existing_facts = payload.get("facts", {})
    if not isinstance(existing_facts, dict):
        existing_facts = {}
    facts_obj = payload.get("facts", {})
    if isinstance(facts_obj, dict) and "context" in facts_obj:
        effective_context = facts_obj.get("context", {})
    else:
        effective_context = payload.get("context", {})
    if not isinstance(effective_context, dict):
        effective_context = {}

    slots = dict(effective_slots)
    facts = {
        **existing_facts,
        "slots": slots,
        "missing_slots": missing_slots,
        "context": effective_context,
    }
    for key in ("date_proposal", "time_proposal", "time_match_outcome", "time_resolution"):
        if payload.get(key) is not None:
            facts[key] = payload[key]

    action_name = get_action_name(intent_name)
    booking = payload.get("booking", {})
    if not isinstance(booking, dict):
        booking = {}

    _emit_plan_trace(
        intent_name=intent_name,
        payload=payload,
        plan=plan,
        flags=flags,
        effective_slots=effective_slots,
        missing_slots=missing_slots,
        needs_clarification=needs_clarification,
        confirmation_state=confirmation_state,
        active_capability=active_capability,
        executable_actions=executable_actions,
        availability_resolved=availability_resolved,
        action_branch=action_branch,
    )

    # Pre-bind TIME_MATCH_MISMATCH: same Decision finalization as post-SEARCH.
    if time_match_outcome == TIME_MATCH_MISMATCH:
        from core.planning.pipeline.decision_finalization import (
            TimeResolutionEvidence,
            finalize_decision_after_time_resolution,
        )

        plan["_merged_luma_response"] = payload
        finalize_decision_after_time_resolution(
            plan,
            evidence=TimeResolutionEvidence(
                outcome=TIME_MATCH_MISMATCH,
                time_resolution=(
                    time_resolution
                    if isinstance(time_resolution, dict)
                    else {"outcome": TIME_MATCH_MISMATCH}
                ),
                time_proposal=payload.get("time_proposal"),
                apply_confirmation_transition=True,
            ),
        )
        if plan.get("status"):
            facts["time_match_outcome"] = TIME_MATCH_MISMATCH
            if isinstance(plan.get("time_resolution"), dict):
                facts["time_resolution"] = plan["time_resolution"]
            if isinstance(plan.get("missing_slots"), list):
                facts["missing_slots"] = list(plan["missing_slots"])

    decision_plan = DecisionPlan(
        plan=plan,
        facts=facts,
        intent_name=intent_name,
        action_name=action_name,
        booking=booking,
        candidate_evidence=candidate_evidence,
        policy_client=policy_client,
        service_candidates=payload.get("service_candidates") or [],
    )
    _apply_has_datetime_invariant(decision_plan)
    return decision_plan


def _emit_plan_trace(
    *,
    intent_name: str,
    payload: Dict[str, Any],
    plan: Dict[str, Any],
    flags: Dict[str, Any],
    effective_slots: Dict[str, Any],
    missing_slots: List[str],
    needs_clarification: bool,
    confirmation_state: Optional[str],
    active_capability: Optional[str],
    executable_actions: List[str],
    availability_resolved: bool,
    action_branch: Optional[str],
) -> None:
    try:
        from core.tracing.planner import emit_planner_decision_graph_from_plan_builder

        emit_planner_decision_graph_from_plan_builder(
            intent_name=intent_name,
            luma_response=payload,
            plan=plan,
            flags=flags,
            effective_slots=effective_slots,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            confirmation_state=confirmation_state,
            active_capability=active_capability,
            executable_actions=executable_actions,
            availability_resolved=availability_resolved,
            stage_from_action=action_branch == "policy",
        )
    except ImportError:
        pass
