"""Decision finalization for Stage 08 and time-resolution plan mutations.

Terminal reconciliation (recovery / unanswered demotion / promptable demotion /
dead-READY inputs) is projected as immutable ``DecisionFinalizationEvidence``.
Stage 08 consumes that evidence when selecting final outcomes.

Time-match exact/mismatch plan mutations remain in
``finalize_decision_after_time_resolution``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from core.planning.pipeline.clarification_readiness import awaiting_from_ask_next
from core.planning.pipeline.presentation_readiness import (
    PresentationReadinessEvidence,
    has_planner_presentation,
)
from core.planning.time_resolution import (
    TIME_MATCH_EXACT,
    TIME_MATCH_MISMATCH,
    _patch_plan_container,
)
from core.session.confirmation_gate import (
    consume_confirmation_state,
    get_confirmation_state,
    set_confirmation_state,
)

logger = logging.getLogger(__name__)


def _derive_stage_from_status(status: str) -> Optional[str]:
    if status == "AWAITING_CONFIRMATION":
        return "CONFIRM"
    if status == "NEEDS_CLARIFICATION":
        return "AVAILABILITY"
    return None


@dataclass(frozen=True)
class DecisionFinalizationEvidence:
    """Terminal reconciliation facts Stage 08 applies when selecting outcomes.

    Does not mutate payload or assemble ``DecisionPlan``.
    """

    status: str
    action: Optional[str] = None
    awaiting: Optional[str] = None
    stage: Optional[str] = None
    action_branch: Optional[str] = None
    availability_reshow: bool = False
    recovery_presentation_applied: bool = False
    clarification_demotion_applied: bool = False
    awaiting_filled_from_ask: bool = False
    promptable_optional_demotion: bool = False
    violates_dead_ready_invariant: bool = False
    dead_ready_invariant_message: Optional[str] = None


def build_decision_finalization_evidence(
    *,
    status: str,
    action: Optional[str],
    awaiting: Optional[str],
    stage: Optional[str],
    action_branch: Optional[str],
    missing_slots: List[str],
    ask_next: Optional[str],
    promptable_slots: Optional[List[str]] = None,
    availability_reshow: bool,
    availability_browse: Optional[Mapping[str, Any]],
    presentation: PresentationReadinessEvidence,
) -> DecisionFinalizationEvidence:
    """Compute terminal reconciliation / demotion facts without applying them."""
    promptables = list(promptable_slots or [])
    recovery_applied = False
    clarification_demotion = False
    awaiting_filled = False
    promptable_demotion = False

    # Recovery presentation requires UNRECOGNIZED + no planning evidence.
    # UNDERSTOOD + no evidence continues to clarification demotion below.
    if presentation.recovery_presentation_eligible:
        action_branch = "recovery_presentation"
        availability_reshow = False
        awaiting = awaiting_from_ask_next(ask_next) or awaiting
        recovery_applied = True
    elif presentation.unanswered_required_slots_without_presentation:
        status = "NEEDS_CLARIFICATION"
        awaiting = awaiting_from_ask_next(ask_next) or awaiting
        action_branch = "reconcile_unanswered_ask_next"
        availability_reshow = False
        clarification_demotion = True
        if stage is None or stage not in ("AVAILABILITY", "CONFIRM"):
            stage = _derive_stage_from_status(status)
    elif (
        status == "NEEDS_CLARIFICATION"
        and missing_slots
        and awaiting is None
        and ask_next
    ):
        awaiting = awaiting_from_ask_next(ask_next)
        awaiting_filled = True

    # Promptable-only clarification (required complete, optional offer open).
    if (
        status == "READY"
        and action is None
        and not missing_slots
        and promptables
        and ask_next in promptables
        and not has_planner_presentation(
            action_branch=action_branch,
            availability_reshow=availability_reshow,
            availability_browse=availability_browse,
        )
    ):
        status = "NEEDS_CLARIFICATION"
        awaiting = awaiting_from_ask_next(ask_next) or awaiting
        action_branch = action_branch or "promptable_optional"
        promptable_demotion = True
        if stage is None:
            stage = "AVAILABILITY"

    violates = bool(
        status == "READY"
        and action is None
        and missing_slots
        and not has_planner_presentation(
            action_branch=action_branch,
            availability_reshow=availability_reshow,
            availability_browse=availability_browse,
        )
    )
    invariant_message = None
    if violates:
        invariant_message = (
            "Illegal planner terminal state: READY + action=None + missing_slots "
            f"without presentation (missing={missing_slots!r}, ask_next={ask_next!r})"
        )

    return DecisionFinalizationEvidence(
        status=status,
        action=action,
        awaiting=awaiting,
        stage=stage,
        action_branch=action_branch,
        availability_reshow=availability_reshow,
        recovery_presentation_applied=recovery_applied,
        clarification_demotion_applied=clarification_demotion,
        awaiting_filled_from_ask=awaiting_filled,
        promptable_optional_demotion=promptable_demotion,
        violates_dead_ready_invariant=violates,
        dead_ready_invariant_message=invariant_message,
    )


@dataclass(frozen=True)
class TimeResolutionEvidence:
    """Evidence for Decision finalization after time resolution."""

    outcome: str
    """TIME_MATCH_EXACT or TIME_MATCH_MISMATCH."""

    time_resolution: Optional[Dict[str, Any]] = None
    bind_result: Optional[Dict[str, Any]] = None
    time_proposal: Optional[Dict[str, Any]] = None
    enter_confirmation: bool = True
    """When EXACT: enter AWAITING_CONFIRMATION unless availability-op browse."""

    apply_confirmation_transition: bool = True
    """When True, also apply confirmation_state / missing_slots finalization."""


def _composed_planning_incomplete(plan: Dict[str, Any]) -> bool:
    """True when composed planning missing_slots still need clarification."""
    missing = plan.get("missing_slots")
    return isinstance(missing, list) and bool(missing)


def _ask_next_from_plan(plan: Dict[str, Any]) -> Optional[str]:
    ask_next = plan.get("ask_next")
    if isinstance(ask_next, str) and ask_next.strip():
        return ask_next.strip()
    missing = plan.get("missing_slots")
    if isinstance(missing, list) and missing:
        from core.planning.planner.missing_slots import derive_ask_next

        return derive_ask_next(list(missing))
    return None


def _prune_missing_slots_filled_by_bind(
    plan: Dict[str, Any],
    bound_slots: Dict[str, Any],
) -> None:
    """Drop missing_slots that the exact-time bind has now satisfied."""
    missing = plan.get("missing_slots")
    if not isinstance(missing, list) or not missing:
        return
    present = {
        key
        for key, value in bound_slots.items()
        if value is not None and value != ""
    }
    plan_slots = plan.get("slots")
    if isinstance(plan_slots, dict):
        for key, value in plan_slots.items():
            if value is not None and value != "":
                present.add(key)
    pruned = [slot for slot in missing if slot not in present]
    if pruned != list(missing):
        _sync_plan_missing_slots(plan, pruned)


def finalize_decision_after_time_resolution(
    plan: Dict[str, Any],
    *,
    evidence: TimeResolutionEvidence,
) -> Dict[str, Any]:
    """Finalize planner Decision fields from time-resolution evidence.

    Mutates ``plan`` in place and returns it.
    """
    outcome = evidence.outcome
    # Confirmation presentation requires composed planning completeness.
    enter_confirmation = evidence.enter_confirmation
    if outcome == TIME_MATCH_EXACT:
        bind_result = evidence.bind_result or {}
        bound_slots = bind_result.get("slots")
        if isinstance(bound_slots, dict):
            _prune_missing_slots_filled_by_bind(plan, bound_slots)
        if enter_confirmation:
            enter_confirmation = not _composed_planning_incomplete(plan)

    if outcome == TIME_MATCH_EXACT:
        _finalize_exact(
            plan,
            evidence,
            enter_confirmation=enter_confirmation,
        )
    elif outcome == TIME_MATCH_MISMATCH:
        _finalize_mismatch(plan, evidence)
    else:
        return plan

    if evidence.apply_confirmation_transition and outcome in (
        TIME_MATCH_EXACT,
        TIME_MATCH_MISMATCH,
    ):
        # Exact + incomplete requiredness / availability browse: do not enter
        # pending confirmation or clear missing_slots.
        if outcome == TIME_MATCH_EXACT and not enter_confirmation:
            pass
        else:
            _finalize_confirmation_transition(plan, time_match=outcome)

    return plan


def _finalize_exact(
    plan: Dict[str, Any],
    evidence: TimeResolutionEvidence,
    *,
    enter_confirmation: bool,
) -> None:
    bind_result = evidence.bind_result or {}
    bound_slots = bind_result.get("slots")
    resolved_range = bind_result.get("resolved_datetime_range")
    if not isinstance(bound_slots, dict) or not isinstance(resolved_range, dict):
        return

    time_resolution = evidence.time_resolution
    if enter_confirmation:
        _patch_plan_container(
            plan,
            status="AWAITING_CONFIRMATION",
            stage="CONFIRM",
            action=None,
            awaiting="USER_CONFIRMATION",
            time_match_outcome=TIME_MATCH_EXACT,
            time_resolution=time_resolution,
            bound_slots=bound_slots,
            resolved_range=resolved_range,
        )
    elif _composed_planning_incomplete(plan):
        # Bind the selected time but continue required-slot clarification.
        ask_next = _ask_next_from_plan(plan)
        _patch_plan_container(
            plan,
            status="NEEDS_CLARIFICATION",
            stage="AVAILABILITY",
            action=None,
            awaiting=ask_next,
            time_match_outcome=TIME_MATCH_EXACT,
            time_resolution=time_resolution,
            bound_slots=bound_slots,
            resolved_range=resolved_range,
        )
    else:
        _patch_plan_container(
            plan,
            status="READY",
            stage="AVAILABILITY",
            action=None,
            awaiting=None,
            time_match_outcome=TIME_MATCH_EXACT,
            time_resolution=time_resolution,
            bound_slots=bound_slots,
            resolved_range=resolved_range,
        )

    merged = plan.get("_merged_luma_response")
    if isinstance(merged, dict):
        merged_slots = merged.get("slots")
        if not isinstance(merged_slots, dict):
            merged_slots = {}
        merged_slots.update(bound_slots)
        merged["slots"] = merged_slots
        merged["resolved_datetime_range"] = dict(resolved_range)
        merged["time_match_outcome"] = TIME_MATCH_EXACT
        if time_resolution is not None:
            merged["time_resolution"] = dict(time_resolution)


def _finalize_mismatch(plan: Dict[str, Any], evidence: TimeResolutionEvidence) -> None:
    time_resolution = evidence.time_resolution or {"outcome": TIME_MATCH_MISMATCH}
    time_proposal = evidence.time_proposal
    _patch_plan_container(
        plan,
        status="NEEDS_CLARIFICATION",
        stage="AVAILABILITY",
        action=None,
        awaiting="TIME_SELECTION",
        time_match_outcome=TIME_MATCH_MISMATCH,
        time_resolution=time_resolution,
    )
    if isinstance(time_proposal, dict):
        plan["time_proposal"] = time_proposal

    merged = plan.get("_merged_luma_response")
    if isinstance(merged, dict):
        merged["time_match_outcome"] = TIME_MATCH_MISMATCH
        merged["time_resolution"] = dict(time_resolution)
        if isinstance(time_proposal, dict):
            merged["time_proposal"] = time_proposal

    decision = plan.get("_decision")
    if isinstance(decision, dict) and isinstance(time_proposal, dict):
        decision["time_proposal"] = time_proposal
        facts = decision.get("facts")
        if isinstance(facts, dict):
            facts["time_proposal"] = time_proposal


def _sync_plan_missing_slots(plan: Dict[str, Any], missing_slots: list) -> None:
    plan["missing_slots"] = list(missing_slots)
    from core.planning.planner.missing_slots import derive_ask_next

    ask_next = derive_ask_next(list(missing_slots))
    plan["ask_next"] = ask_next
    decision = plan.get("_decision")
    if not isinstance(decision, dict):
        return
    decision["missing_slots"] = list(missing_slots)
    decision["ask_next"] = ask_next
    facts = decision.get("facts")
    if isinstance(facts, dict):
        facts["missing_slots"] = list(missing_slots)
        facts["ask_next"] = ask_next


def _finalize_confirmation_transition(plan: Dict[str, Any], *, time_match: str) -> None:
    """Confirmation_state / missing_slots after time-match Decision patches."""
    if time_match not in (TIME_MATCH_EXACT, TIME_MATCH_MISMATCH):
        return

    merged = plan.get("_merged_luma_response")
    if not isinstance(merged, dict):
        merged = {}
        plan["_merged_luma_response"] = merged

    if time_match == TIME_MATCH_EXACT:
        if _composed_planning_incomplete(plan):
            # Never clear missing_slots or enter pending while required remain.
            ask_next = _ask_next_from_plan(plan)
            if plan.get("status") != "NEEDS_CLARIFICATION":
                plan["status"] = "NEEDS_CLARIFICATION"
            if ask_next and not plan.get("awaiting"):
                plan["awaiting"] = ask_next
            if plan.get("action") is not None:
                plan["action"] = None
            logger.info(
                "[BOOKING_CONFIRMATION] Exact match with incomplete requiredness — "
                "skip confirmation; ask_next=%s missing=%s",
                ask_next,
                plan.get("missing_slots"),
            )
        else:
            previous_conf = get_confirmation_state(merged) or get_confirmation_state(
                plan
            )
            _sync_plan_missing_slots(plan, [])
            set_confirmation_state(merged, "pending")
            set_confirmation_state(plan, "pending")
            try:
                from core.tracing.confirmation import (
                    emit_confirmation_enter_pending_trace,
                )

                emit_confirmation_enter_pending_trace(
                    entered=True,
                    previous_state=previous_conf,
                    missing_slots=[],
                    availability_resolved=True,
                    time_selection_ready=True,
                )
            except ImportError:
                pass
            logger.info(
                "[BOOKING_CONFIRMATION] Decision finalization exact match — "
                "confirmation_state=pending"
            )
    else:
        consume_confirmation_state(merged, reason="time_match_mismatch")
        consume_confirmation_state(plan, reason="time_match_mismatch")
        merged.pop("resolved_datetime_range", None)
        plan.pop("resolved_datetime_range", None)
        slots = plan.get("slots")
        if isinstance(slots, dict):
            cleared = dict(slots)
            for key in ("time", "has_datetime", "datetime_range"):
                cleared.pop(key, None)
            plan["slots"] = cleared
            merged_slots = merged.get("slots")
            if isinstance(merged_slots, dict):
                for key in ("time", "has_datetime", "datetime_range"):
                    merged_slots.pop(key, None)
            if cleared.get("service_id") and cleared.get("date"):
                _sync_plan_missing_slots(plan, ["time"])
        if plan.get("status") != "NEEDS_CLARIFICATION":
            plan["status"] = "NEEDS_CLARIFICATION"
        if not plan.get("awaiting"):
            plan["awaiting"] = "TIME_SELECTION"
        if plan.get("action") is not None:
            plan["action"] = None
        logger.info(
            "[BOOKING_CONFIRMATION] Decision finalization time mismatch — "
            "confirmation cleared"
        )

    _emit_decision_finalization_trace(plan, time_match=time_match)


def _emit_decision_finalization_trace(plan: Dict[str, Any], *, time_match: str) -> None:
    try:
        from core.tracing.decision_trace import TurnTrace, emit_evidence, emit_mutation
        from core.tracing.planner import (
            PLANNER_SELECT_ACTION_ID,
            PLANNER_SELECT_STAGE_ID,
            PLANNER_STATUS_ID,
        )
    except ImportError:
        return

    trace = TurnTrace.current()
    if trace is None:
        return

    status = plan.get("status")
    stage = plan.get("stage")
    action = plan.get("action")
    awaiting = plan.get("awaiting")
    reason_text = (
        "Exact time match after availability; awaiting user confirmation"
        if time_match == TIME_MATCH_EXACT
        else "Requested time unavailable; clarification required"
    )

    if not trace.has_record("evidence.planning.post_execution"):
        emit_evidence(
            "POST_EXECUTION_PLANNING",
            subsystem="planning",
            facts={
                "status": status,
                "stage": stage,
                "action": action,
                "awaiting": awaiting,
                "time_match_outcome": time_match,
            },
            node_id="evidence.planning.post_execution",
            source="decision.finalize_after_time_resolution",
            observed_at_stage="execution",
        )

    if not trace.has_record("evidence.architecture.decision_finalization"):
        emit_evidence(
            "DECISION_FINALIZATION",
            subsystem="planning",
            facts={
                "status": status,
                "stage": stage,
                "action": action,
                "awaiting": awaiting,
                "time_match_outcome": time_match,
                "observational_only": False,
            },
            node_id="evidence.architecture.decision_finalization",
            source="decision_finalization",
            observed_at_stage="architecture",
        )

    trace = TurnTrace.current()
    if trace is None:
        return

    if trace.has_record(PLANNER_STATUS_ID) and status is not None:
        emit_mutation(
            PLANNER_STATUS_ID,
            subsystem="planning",
            field="plan.status",
            previous="READY",
            new=status,
            reason_code=time_match,
            reason_text=reason_text,
            presentation_only=True,
        )
    if trace.has_record(PLANNER_SELECT_ACTION_ID):
        emit_mutation(
            PLANNER_SELECT_ACTION_ID,
            subsystem="planning",
            field="plan.action",
            previous="SEARCH_AVAILABILITY",
            new=action,
            reason_code=time_match,
            reason_text=f"Post-execution plan action set to {action!r}",
            presentation_only=True,
        )
    if trace.has_record(PLANNER_SELECT_STAGE_ID) and stage is not None:
        emit_mutation(
            PLANNER_SELECT_STAGE_ID,
            subsystem="planning",
            field="plan.stage",
            previous="AVAILABILITY",
            new=stage,
            reason_code=time_match,
            reason_text=f"Post-execution plan stage set to {stage!r}",
            presentation_only=True,
        )
