"""Decision finalization after time-resolution evidence (Phase 5).

Sole owner of planner outcome mutations driven by time-match exact/mismatch
evidence (planning-time pre-bind mismatch and post-SEARCH execution).

Production and tests call ``finalize_decision_after_time_resolution`` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

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


def finalize_decision_after_time_resolution(
    plan: Dict[str, Any],
    *,
    evidence: TimeResolutionEvidence,
) -> Dict[str, Any]:
    """Finalize planner Decision fields from time-resolution evidence.

    Mutates ``plan`` in place and returns it.
    """
    outcome = evidence.outcome
    if outcome == TIME_MATCH_EXACT:
        _finalize_exact(plan, evidence)
    elif outcome == TIME_MATCH_MISMATCH:
        _finalize_mismatch(plan, evidence)
    else:
        return plan

    if evidence.apply_confirmation_transition and outcome in (
        TIME_MATCH_EXACT,
        TIME_MATCH_MISMATCH,
    ):
        # Exact + availability browse: presentation stays READY/AVAILABILITY;
        # do not enter pending confirmation.
        if outcome == TIME_MATCH_EXACT and not evidence.enter_confirmation:
            pass
        else:
            _finalize_confirmation_transition(plan, time_match=outcome)

    return plan


def _finalize_exact(plan: Dict[str, Any], evidence: TimeResolutionEvidence) -> None:
    bind_result = evidence.bind_result or {}
    bound_slots = bind_result.get("slots")
    resolved_range = bind_result.get("resolved_datetime_range")
    if not isinstance(bound_slots, dict) or not isinstance(resolved_range, dict):
        return

    time_resolution = evidence.time_resolution
    if evidence.enter_confirmation:
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
    decision = plan.get("_decision")
    if not isinstance(decision, dict):
        return
    decision["missing_slots"] = list(missing_slots)
    facts = decision.get("facts")
    if isinstance(facts, dict):
        facts["missing_slots"] = list(missing_slots)


def _finalize_confirmation_transition(plan: Dict[str, Any], *, time_match: str) -> None:
    """Confirmation_state / missing_slots after time-match Decision patches."""
    if time_match not in (TIME_MATCH_EXACT, TIME_MATCH_MISMATCH):
        return

    merged = plan.get("_merged_luma_response")
    if not isinstance(merged, dict):
        merged = {}
        plan["_merged_luma_response"] = merged

    if time_match == TIME_MATCH_EXACT:
        previous_conf = get_confirmation_state(merged) or get_confirmation_state(plan)
        _sync_plan_missing_slots(plan, [])
        set_confirmation_state(merged, "pending")
        set_confirmation_state(plan, "pending")
        try:
            from core.tracing.confirmation import emit_confirmation_enter_pending_trace

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
