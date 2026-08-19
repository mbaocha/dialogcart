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
    TIME_MATCH_NOT_APPLICABLE,
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
    """TIME_MATCH_EXACT, TIME_MATCH_MISMATCH, or applicable N/A presentation."""

    time_resolution: Optional[Dict[str, Any]] = None
    bind_result: Optional[Dict[str, Any]] = None
    time_proposal: Optional[Dict[str, Any]] = None
    enter_confirmation: bool = True
    """When EXACT: enter AWAITING_CONFIRMATION unless availability-op browse."""

    apply_confirmation_transition: bool = True
    """When True, also apply confirmation_state / missing_slots finalization."""

    presented_options: Optional[List[Dict[str, Any]]] = None
    """Authoritative options presented by the completed fresh search."""


def _composed_planning_incomplete(plan: Dict[str, Any]) -> bool:
    """True when composed planning missing_slots still need clarification."""
    missing = plan.get("missing_slots")
    return isinstance(missing, list) and bool(missing)


def _customer_name_prerequisite_satisfied(plan: Dict[str, Any]) -> bool:
    evidence = plan.get("_customer_name_prerequisite")
    nested = plan.get("plan")
    if not isinstance(evidence, dict) and isinstance(nested, dict):
        evidence = nested.get("_customer_name_prerequisite")
    if not isinstance(evidence, dict):
        return True
    return evidence.get("satisfied") is True


def _finalize_customer_name_clarification(
    plan: Dict[str, Any], evidence: TimeResolutionEvidence
) -> None:
    """Finalize without creating confirmation authorization."""
    bind_result = evidence.bind_result or {}
    bound_slots = bind_result.get("slots")
    resolved_range = bind_result.get("resolved_datetime_range")
    _patch_plan_container(
        plan,
        status="NEEDS_CLARIFICATION",
        stage="CONFIRM",
        action=None,
        awaiting="CUSTOMER_CONTACT_NAME",
        time_match_outcome=TIME_MATCH_EXACT,
        time_resolution=evidence.time_resolution,
        bound_slots=bound_slots if isinstance(bound_slots, dict) else None,
        resolved_range=resolved_range if isinstance(resolved_range, dict) else None,
    )
    plan["ask_next"] = "customer_contact_name"
    plan["action_branch"] = "customer_contact_name_required"
    nested = plan.get("plan")
    if isinstance(nested, dict):
        nested["ask_next"] = "customer_contact_name"
    decision = plan.get("_decision")
    if isinstance(decision, dict):
        decision["ask_next"] = "customer_contact_name"
        facts = decision.get("facts")
        if isinstance(facts, dict):
            facts["ask_next"] = "customer_contact_name"


def finalize_decision_after_customer_name_persisted(
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """Resume an otherwise-ready customer-name clarification after persistence."""
    nested = plan.get("plan")
    otherwise_ready = plan.get("_otherwise_confirmation_ready")
    if otherwise_ready is None and isinstance(nested, dict):
        otherwise_ready = nested.get("_otherwise_confirmation_ready")
    if not otherwise_ready:
        return plan
    evidence = plan.get("_customer_name_prerequisite")
    if not isinstance(evidence, dict) and isinstance(nested, dict):
        evidence = nested.get("_customer_name_prerequisite")
    if isinstance(evidence, dict):
        evidence["satisfied"] = True
        evidence["required_input"] = None
    _patch_plan_container(
        plan,
        status="AWAITING_CONFIRMATION",
        stage="CONFIRM",
        action=None,
        awaiting="USER_CONFIRMATION",
    )
    # The customer-name clarification installed prompt metadata across the
    # runtime plan and Decision projections. Persistence has satisfied that
    # request, so none of those containers may continue to render it.
    plan.pop("text", None)
    plan["ask_next"] = None
    if plan.get("action_branch") == "customer_contact_name_required":
        plan.pop("action_branch", None)
    if isinstance(nested, dict):
        nested["ask_next"] = None
        if nested.get("action_branch") == "customer_contact_name_required":
            nested.pop("action_branch", None)
    decision = plan.get("_decision")
    if isinstance(decision, dict):
        decision["awaiting"] = "USER_CONFIRMATION"
        decision["ask_next"] = None
        decision_plan = decision.get("plan")
        if isinstance(decision_plan, dict):
            decision_plan["ask_next"] = None
            if decision_plan.get("action_branch") == "customer_contact_name_required":
                decision_plan.pop("action_branch", None)
        facts = decision.get("facts")
        if isinstance(facts, dict):
            facts["awaiting"] = "USER_CONFIRMATION"
            facts["ask_next"] = None
    merged = plan.get("_merged_luma_response")
    if not isinstance(merged, dict):
        merged = {}
        plan["_merged_luma_response"] = merged
    set_confirmation_state(merged, "pending")
    set_confirmation_state(plan, "pending")
    return plan


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
        if enter_confirmation:
            enter_confirmation = _customer_name_prerequisite_satisfied(plan)

    if outcome == TIME_MATCH_EXACT:
        _finalize_exact(
            plan,
            evidence,
            enter_confirmation=enter_confirmation,
        )
    elif outcome == TIME_MATCH_MISMATCH:
        _finalize_mismatch(plan, evidence)
    elif outcome == TIME_MATCH_NOT_APPLICABLE:
        _finalize_presented_time_selection(plan, evidence)
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

    if outcome == TIME_MATCH_EXACT and not enter_confirmation:
        _emit_decision_finalization_trace(plan, time_match=outcome)

    return plan


def _plan_slots(plan: Dict[str, Any]) -> Dict[str, Any]:
    slots = plan.get("slots")
    if isinstance(slots, dict):
        return slots
    decision = plan.get("_decision")
    facts = decision.get("facts") if isinstance(decision, dict) else None
    slots = facts.get("slots") if isinstance(facts, dict) else None
    return slots if isinstance(slots, dict) else {}


def _supports_presented_time_selection(
    plan: Dict[str, Any], evidence: TimeResolutionEvidence
) -> bool:
    """Gate the normal post-search transition to trusted booking-time selection."""
    if (plan.get("intent_name") or plan.get("intent")) != "CREATE_APPOINTMENT":
        return False
    if plan.get("action") != "SEARCH_AVAILABILITY":
        return False
    if plan.get("availability_browse"):
        return False
    slots = _plan_slots(plan)
    if slots.get("time") or plan.get("resolved_datetime_range"):
        return False
    missing = plan.get("missing_slots")
    if not isinstance(missing, list) or "time" not in missing:
        return False
    options = evidence.presented_options
    if not isinstance(options, list) or not options:
        return False
    from core.planning.temporal_proposal import _parse_offer_start_parts

    return any(
        isinstance(option, dict)
        and _parse_offer_start_parts(option.get("starts_at") or option.get("start"))
        is not None
        for option in options
    )


def _sync_time_selection_ask(plan: Dict[str, Any]) -> None:
    ask_next = "time"
    plan["ask_next"] = ask_next
    nested = plan.get("plan")
    if isinstance(nested, dict):
        nested["ask_next"] = ask_next
    decision = plan.get("_decision")
    if isinstance(decision, dict):
        decision["ask_next"] = ask_next
        decision_plan = decision.get("plan")
        if isinstance(decision_plan, dict):
            decision_plan["ask_next"] = ask_next
        facts = decision.get("facts")
        if isinstance(facts, dict):
            facts["ask_next"] = ask_next


def _finalize_presented_time_selection(
    plan: Dict[str, Any], evidence: TimeResolutionEvidence
) -> None:
    if not _supports_presented_time_selection(plan, evidence):
        return
    _patch_plan_container(
        plan,
        status=str(plan.get("status") or "READY"),
        awaiting=awaiting_from_ask_next("time"),
        time_match_outcome=TIME_MATCH_NOT_APPLICABLE,
        time_resolution=evidence.time_resolution,
    )
    _sync_time_selection_ask(plan)
    _emit_decision_finalization_trace(plan, time_match=TIME_MATCH_NOT_APPLICABLE)


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
    elif not _customer_name_prerequisite_satisfied(plan):
        _finalize_customer_name_clarification(plan, evidence)
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


@dataclass(frozen=True)
class ExecutionBlockedFinalizationEvidence:
    """Evidence for Decision finalization after an operational execution block."""

    reason: str
    """ExecutionBlocked.reason (e.g. CUSTOMER_ID_REQUIRED)."""

    required_input: Optional[str] = None
    """Optional required_input from ExecutionBlocked."""

    preserve_pending_confirmation: bool = True
    """When True, keep confirmation_state=pending (no side effect occurred)."""


def finalize_decision_after_execution_blocked(
    plan: Dict[str, Any],
    *,
    evidence: ExecutionBlockedFinalizationEvidence,
) -> Dict[str, Any]:
    """Finalize planner Decision after execution blocks without a side effect.

    Stage 08 may have authorized READY + a commit action. When execution then
    returns ``ExecutionBlocked``, this seam owns the post-execution Decision
    completion: demote to ``NEEDS_CLARIFICATION``, clear the runnable action,
    and (for identity blocks) keep pending confirmation authorization.

    Uses ``_patch_plan_container`` so outer plan, nested plan, and
    ``plan[\"_decision\"]`` stay coherent for response builders and projection.

    Mutates ``plan`` in place and returns it.
    """
    reason = evidence.reason
    awaiting: Optional[str] = None
    stage = "CONFIRM"
    if reason == "CUSTOMER_ID_REQUIRED":
        awaiting = evidence.required_input or "phone_or_email"
    elif reason == "BOOKING_IDENTIFICATION_REQUIRED":
        awaiting = evidence.required_input or "booking_identification"
        stage = "AVAILABILITY"

    # Response builders read ``plan["_decision"]``; keep it coherent with the
    # demoted outer plan even when the dispatch copy omitted the Decision envelope.
    if not isinstance(plan.get("_decision"), dict):
        plan["_decision"] = {
            "status": plan.get("status"),
            "intent_name": plan.get("intent_name") or plan.get("intent") or "",
            "plan": {
                "status": plan.get("status"),
                "stage": plan.get("stage"),
                "action": plan.get("action"),
                "awaiting": plan.get("awaiting"),
            },
            "facts": {
                "slots": dict(plan.get("slots") or {}),
                "missing_slots": list(plan.get("missing_slots") or []),
            },
        }

    _patch_plan_container(
        plan,
        status="NEEDS_CLARIFICATION",
        stage=stage,
        action=None,
        awaiting=awaiting,
    )

    if evidence.preserve_pending_confirmation:
        merged = plan.get("_merged_luma_response")
        if not isinstance(merged, dict):
            merged = {}
            plan["_merged_luma_response"] = merged
        # No irreversible side effect — keep authorization resumable.
        set_confirmation_state(merged, "pending")
        set_confirmation_state(plan, "pending")

    _emit_execution_blocked_finalization_trace(plan, reason=reason)
    return plan


def _emit_execution_blocked_finalization_trace(
    plan: Dict[str, Any],
    *,
    reason: str,
) -> None:
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
        f"Execution blocked ({reason}); Decision demoted to clarification"
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
                "execution_blocked_reason": reason,
            },
            node_id="evidence.planning.post_execution",
            source="decision.finalize_after_execution_blocked",
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
                "execution_blocked_reason": reason,
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
            reason_code=reason,
            reason_text=reason_text,
            presentation_only=True,
        )
    if trace.has_record(PLANNER_SELECT_ACTION_ID):
        emit_mutation(
            PLANNER_SELECT_ACTION_ID,
            subsystem="planning",
            field="plan.action",
            previous="CONFIRM_APPOINTMENT",
            new=action,
            reason_code=reason,
            reason_text=reason_text,
            presentation_only=True,
        )
    if trace.has_record(PLANNER_SELECT_STAGE_ID) and stage is not None:
        emit_mutation(
            PLANNER_SELECT_STAGE_ID,
            subsystem="planning",
            field="plan.stage",
            previous="CONFIRM",
            new=stage,
            reason_code=reason,
            reason_text=reason_text,
            presentation_only=True,
        )


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
        "Authoritative customer contact name required before confirmation"
        if awaiting == "CUSTOMER_CONTACT_NAME"
        else (
            "Exact time match after availability; awaiting user confirmation"
            if time_match == TIME_MATCH_EXACT
            else "Requested time unavailable; clarification required"
        )
    )
    reason_code = time_match
    if awaiting == "CUSTOMER_CONTACT_NAME":
        from core.tracing.reason_codes import CUSTOMER_CONTACT_NAME_REQUIRED

        reason_code = CUSTOMER_CONTACT_NAME_REQUIRED

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
            reason_code=reason_code,
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
