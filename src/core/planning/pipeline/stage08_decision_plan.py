"""Stage 08 — decision plan construction (pure policy interpreter)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.planning.pipeline.requests import (
    AttachedRequest,
    is_availability_turn_operation,
)
from core.planning.pipeline.clarification_readiness import (
    awaiting_from_ask_next,
    build_clarification_readiness_evidence,
)
from core.planning.pipeline.decision_finalization import (
    build_decision_finalization_evidence,
)
from core.planning.pipeline.execution_readiness import (
    build_execution_readiness_evidence,
)
from core.planning.pipeline.presentation_readiness import (
    build_presentation_readiness_evidence,
)
from core.planning.pipeline.progress_clarification_readiness import (
    build_progress_clarification_evidence,
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
from core.policy.intent_policy import (
    evaluate_execution_step_candidates,
    get_commit_action,
)

logger = logging.getLogger(__name__)


def _identity_clarification_requires_reconfirm(
    *,
    session_state: Optional[Dict[str, Any]],
    action: Optional[str],
    missing_slots: List[str],
    confirmation_state: Optional[str],
) -> bool:
    """True when prior identity block must re-present confirmation this turn.

    Prior Decision was ``NEEDS_CLARIFICATION`` after ``CUSTOMER_ID_REQUIRED`` while
    confirmation stayed pending and booking criteria were already complete.
    Customer identity is now present — re-present; do not commit on this turn.

    Distinguisher vs normal pending confirm: durable session status is still
    ``NEEDS_CLARIFICATION`` (identity demotion), not ``AWAITING_CONFIRMATION``.

    This branch relies on persisted AWAITING_CONFIRMATION remaining distinct from
    NEEDS_CLARIFICATION. At present, the only commit-ready pending state persisted
    as NEEDS_CLARIFICATION is an operational identity block.
    """
    if action != "CONFIRM_APPOINTMENT":
        return False
    if confirmation_state != "pending":
        return False
    if missing_slots:
        return False
    if not isinstance(session_state, dict):
        return False
    if session_state.get("status") != "NEEDS_CLARIFICATION":
        return False
    if session_state.get("customer_id") in (None, "", 0):
        return False
    return True


def _availability_operation_precedes_confirm(turn_operation: Optional[str]) -> bool:
    """Explicit availability ops outrank confirm-presentation shortcuts."""
    return is_availability_turn_operation(turn_operation)


def _exact_time_match_presenting_confirmation(
    time_match_outcome: Optional[str],
    *,
    user_confirmation_satisfied: bool,
    turn_operation: Optional[str] = None,
    missing_slots: Optional[List[str]] = None,
) -> bool:
    """True when exact time match may present booking confirmation.

    Composed planning completeness is required: non-empty missing_slots must
    continue clarification instead of confirmation presentation.
    """
    if _availability_operation_precedes_confirm(turn_operation):
        return False
    if missing_slots:
        return False
    return (
        time_match_outcome == TIME_MATCH_EXACT and not user_confirmation_satisfied
    )


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
    clarification = build_clarification_readiness_evidence(
        slot_state=slot_state,
        payload=payload,
    )
    missing_slots = list(clarification.missing_slots)
    promptable_slots = list(clarification.promptable_slots)
    declined_slots = list(clarification.declined_slots)
    needs_clarification = clarification.needs_clarification
    # Stage 04 default ask is a proposal only; Stage 08 may override from the
    # current progress execution step (see progress_clarification precedence).
    default_ask_next = clarification.default_ask_next
    ask_next = default_ask_next
    has_planning_evidence = clarification.has_planning_evidence
    turn_understanding = clarification.turn_understanding
    entity_schema = (
        payload.get("_entity_schema")
        if isinstance(payload.get("_entity_schema"), dict)
        else None
    )
    effective_slots = dict(slot_state.effective_collected_slots)
    confirmation_state = confirmation.confirmation_state
    user_confirmation_satisfied = confirmation.user_confirmation_satisfied
    active_capability = capability.active_capability
    turn_operation = attached_request.turn_operation
    confirm_booking_continuation = attached_request.confirm_booking_continuation
    availability_op = _availability_operation_precedes_confirm(turn_operation)

    readiness = build_execution_readiness_evidence(
        intent_name=intent_name,
        effective_slots=effective_slots,
        payload=payload,
        session_state=session_state,
        missing_slots=missing_slots,
        needs_clarification=needs_clarification,
        availability_ready=availability.availability_ready,
        confirmation_state=confirmation_state,
        organization_id=organization_id,
        confirm_booking_continuation=confirm_booking_continuation,
        availability_invalidation=confirmation.availability_invalidation,
        bound_datetime_clear=confirmation.bound_datetime_clear,
    )
    availability_resolved = readiness.availability_resolved
    availability_invalidated = readiness.availability_invalidated
    bound_datetime_cleared = readiness.bound_datetime_cleared
    executable_actions = list(readiness.executable_actions)

    commit_action = get_commit_action(intent_name)

    time_match_outcome = payload.get("time_match_outcome")
    time_resolution = payload.get("time_resolution")
    if not time_match_outcome and isinstance(time_resolution, dict):
        time_match_outcome = time_resolution.get("outcome")

    presentation_early = build_presentation_readiness_evidence(
        payload=payload,
        session_state=session_state,
        requested_availability_reshow=confirmation.availability_reshow,
        block_auto_reshow=clarification.block_auto_reshow,
    )
    allow_availability_reshow = presentation_early.availability_reshow_allowed
    cache_satisfiable_browse = presentation_early.cache_satisfiable_browse_dict()

    if allow_availability_reshow:
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
            missing_slots=missing_slots,
        ):
            status = "AWAITING_CONFIRMATION"
        elif (
            time_match_outcome == TIME_MATCH_EXACT
            and missing_slots
            and not user_confirmation_satisfied
            and not _availability_operation_precedes_confirm(turn_operation)
        ):
            # Exact time bound, but composed required slots remain — clarify.
            status = "NEEDS_CLARIFICATION"
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
        elif (
            time_match_outcome == TIME_MATCH_EXACT
            and missing_slots
            and not user_confirmation_satisfied
            and not _availability_operation_precedes_confirm(turn_operation)
        ):
            awaiting = awaiting_from_ask_next(ask_next)
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
            flags = dict(readiness.flags)
            selected_step, candidate_evidence = evaluate_execution_step_candidates(
                intent_name,
                effective_slots,
                flags,
                entity_schema=entity_schema,
            )

            # Non-slot awaiting owners (confirmation / capability / time mismatch)
            # must not be rewritten into slot asks.
            skip_progress_clarification = status in (
                "AWAITING_CONFIRMATION",
                "AWAITING_CAPABILITY",
            ) or time_match_outcome == TIME_MATCH_MISMATCH

            progress = build_progress_clarification_evidence(
                selected_step=selected_step,
                candidates=candidate_evidence,
                promptable_slots=promptable_slots,
                entity_schema=entity_schema,
                default_ask_next=default_ask_next,
            )

            if not skip_progress_clarification:
                ask_next = progress.ask_next

            if not skip_progress_clarification and progress.has_progress_clarification:
                action = None
                policy_client = None
                action_branch = progress.progress_branch
                status = "NEEDS_CLARIFICATION"
                awaiting = awaiting_from_ask_next(ask_next)
                stage = "AVAILABILITY"
                progress_meta = progress.progress_meta_dict()
                if progress_meta is not None:
                    payload["_progress_clarification"] = progress_meta
            elif (
                not skip_progress_clarification
                and progress.execution_step_selected
            ):
                action = progress.selected_execution_action
                policy_client = progress.selected_policy_client
                action_branch = "policy"
                stage = progress.selected_stage
            elif skip_progress_clarification:
                # Keep confirmation / capability / time-mismatch owners; do not
                # attach a progress execution action beside non-slot awaiting.
                action = None
                policy_client = None
                action_branch = "non_slot_awaiting"
            else:
                action_branch = "no_execution_step"

            # Invariant: cache-satisfiable page browse must never select SEARCH.
            # Absolute dates / temporal criterion changes always SEARCH.
            browse = cache_satisfiable_browse
            if action == "SEARCH_AVAILABILITY" and browse:
                action = None
                policy_client = None
                action_branch = "cache_satisfiable_browse"
                stage = "AVAILABILITY"
                flags["availability_check_required"] = False
                flags["availability_ready"] = True
                flags["availability_resolved"] = True
                payload["availability_browse"] = browse

        if time_match_outcome == TIME_MATCH_MISMATCH:
            action = None
            action_branch = "time_match_mismatch"
            stage = "AVAILABILITY"
        elif _exact_time_match_presenting_confirmation(
            time_match_outcome,
            user_confirmation_satisfied=user_confirmation_satisfied,
            turn_operation=turn_operation,
            missing_slots=missing_slots,
        ):
            action = None
            action_branch = "time_match_exact"
            stage = "CONFIRM"
        elif (
            time_match_outcome == TIME_MATCH_EXACT
            and missing_slots
            and not user_confirmation_satisfied
            and not _availability_operation_precedes_confirm(turn_operation)
        ):
            # Preserve bound time; do not re-SEARCH or present confirmation.
            action = None
            policy_client = None
            action_branch = "exact_time_incomplete_required"
            status = "NEEDS_CLARIFICATION"
            awaiting = awaiting_from_ask_next(ask_next) or awaiting
            stage = "AVAILABILITY"

        if stage is None:
            if availability_op and status == "READY":
                stage = "AVAILABILITY"
            elif intent_name == "UNKNOWN":
                # Cold-start / unrecognized: keep stage unset so recovery stays
                # intent-neutral (do not inherit booking AVAILABILITY context).
                stage = None
            else:
                stage = _derive_stage_from_status(status)

        # Post-identity-block: criteria complete + pending + customer now present
        # while prior Decision was clarification → re-present, do not commit yet.
        if _identity_clarification_requires_reconfirm(
            session_state=session_state,
            action=action,
            missing_slots=missing_slots,
            confirmation_state=confirmation_state,
        ):
            status = "AWAITING_CONFIRMATION"
            action = None
            policy_client = None
            awaiting = "USER_CONFIRMATION"
            stage = "CONFIRM"
            action_branch = "identity_resolved_reconfirm"
            # Ensure durable pending survives projection (merged is authoritative).
            from core.session.confirmation_gate import set_confirmation_state

            set_confirmation_state(payload, "pending")
            confirmation_state = "pending"

    browse_on_payload = payload.get("availability_browse")
    if not isinstance(browse_on_payload, dict):
        browse_on_payload = None

    presentation = build_presentation_readiness_evidence(
        payload=payload,
        session_state=session_state,
        requested_availability_reshow=confirmation.availability_reshow,
        block_auto_reshow=clarification.block_auto_reshow,
        status=status,
        action=action,
        action_branch=action_branch,
        missing_slots=missing_slots,
        promptable_slots=promptable_slots,
        ask_next=ask_next,
        has_planning_evidence=has_planning_evidence,
        turn_understanding=turn_understanding,
        availability_browse=browse_on_payload,
    )

    finalization = build_decision_finalization_evidence(
        status=status,
        action=action,
        awaiting=awaiting,
        stage=stage,
        action_branch=action_branch,
        missing_slots=missing_slots,
        ask_next=ask_next,
        promptable_slots=promptable_slots,
        availability_reshow=allow_availability_reshow,
        availability_browse=browse_on_payload,
        presentation=presentation,
    )
    if finalization.violates_dead_ready_invariant:
        raise AssertionError(
            finalization.dead_ready_invariant_message
            or "Illegal planner terminal state: READY without presentation"
        )
    status = finalization.status
    action = finalization.action
    awaiting = finalization.awaiting
    stage = finalization.stage
    action_branch = finalization.action_branch
    allow_availability_reshow = finalization.availability_reshow

    allowed_actions: List[str] = []
    blocked_actions: List[str] = []
    if missing_slots:
        if commit_action:
            blocked_actions.append(commit_action)
        allowed_actions.extend(executable_actions)
    else:
        allowed_actions.extend(executable_actions)
        if commit_action:
            needs_bound_datetime = (
                intent_name == "CREATE_APPOINTMENT" and not readiness.datetime_bound
            )
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
        "ask_next": ask_next,
        "promptable_slots": list(promptable_slots),
        "declined_slots": list(declined_slots),
    }
    plan["execution_proposal_context"] = dict(readiness.execution_proposal_context)
    # Carry Stage 03 flag onto the Decision plan so execution proposal resolution
    # sees criteria invalidation even if _merged_luma_response is absent.
    if readiness.revision_invalidated_availability:
        plan["_revision_invalidated_availability"] = True
    if time_match_outcome:
        plan["time_match_outcome"] = time_match_outcome
    if isinstance(time_resolution, dict):
        plan["time_resolution"] = time_resolution
    if active_capability:
        plan["active_capability"] = active_capability
    if turn_operation and turn_operation != "NONE":
        plan["turn_operation"] = turn_operation
    if allow_availability_reshow:
        plan["availability_reshow"] = True
    if isinstance(browse_on_payload, dict) and browse_on_payload.get("direction"):
        plan["availability_browse"] = browse_on_payload

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
        "ask_next": ask_next,
        "promptable_slots": list(promptable_slots),
        "declined_slots": list(declined_slots),
        "context": effective_context,
    }
    if entity_schema is not None:
        facts["_entity_schema"] = entity_schema
        # Execution/workflow read the Decision plan dict (not DecisionPlan.facts).
        plan["_entity_schema"] = entity_schema
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
            action_branch=action_branch,
        )
    except ImportError:
        pass
