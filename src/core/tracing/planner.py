"""Phase 2 planner decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.policy.intent_policy import (
    evaluate_execution_step_candidates,
    get_execution_steps,
)
from core.tracing.decision_trace import (
    Candidate,
    FailedPredicate,
    IgnoredInput,
    TurnTrace,
    decide,
    emit_evidence,
    emit_mutation,
)
from core.tracing.reason_codes import (
    CLARIFICATION_REQUIRED,
    CONFIRMATION_REQUIRED,
    EXECUTION_ROUTE_BROWSE,
    EXECUTION_ROUTE_PLAN,
    INPUT_IGNORED_NOT_APPLICABLE,
    NO_STEP_REQUIRES_SATISFIED,
    REQUIREMENT_UNSATISFIED,
    SLOTS_INCOMPLETE,
    STAGE_DERIVED_FROM_ACTION,
    STAGE_DERIVED_FROM_STATUS,
    STATUS_AWAITING_CAPABILITY,
    STATUS_READY,
    STEP_SELECTED,
    TIME_SELECTION_READY,
)
PLANNER_EVIDENCE_MISSING_SLOTS_ID = "evidence.planning.missing_slots"
PLANNER_EVIDENCE_POLICY_STEPS_ID = "evidence.policy.steps"
PLANNER_EVIDENCE_BUSINESS_FACTS_ID = "evidence.facts.business"

PLANNER_STATUS_ID = "decision.planner.status"
PLANNER_CLARIFICATION_ID = "decision.planner.clarification"
PLANNER_CONFIRMATION_ID = "decision.planner.confirmation"
PLANNER_SELECT_ACTION_ID = "decision.planner.select_action"
PLANNER_SELECT_STAGE_ID = "decision.planner.select_stage"
PLANNER_EXECUTION_ROUTE_ID = "decision.planner.execution_route"

PLANNER_NODE_IDS = (
    PLANNER_STATUS_ID,
    PLANNER_CLARIFICATION_ID,
    PLANNER_CONFIRMATION_ID,
    PLANNER_SELECT_ACTION_ID,
    PLANNER_SELECT_STAGE_ID,
    PLANNER_EXECUTION_ROUTE_ID,
)

ROUTING_CATEGORY = "routing"
_BROWSE_OPERATIONS = frozenset({"browse_next", "browse_previous"})


def planner_dependencies() -> List[str]:
    """Return planner node ids emitted so far this turn."""
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in PLANNER_NODE_IDS if trace.has_record(node_id)]


def _candidate_from_eval(raw: Mapping[str, Any]) -> Candidate:
    failed = tuple(
        FailedPredicate(
            predicate=str(item.get("predicate", "")),
            reason_code=str(item.get("reason_code", REQUIREMENT_UNSATISFIED)),
            actual=item.get("actual"),
        )
        for item in raw.get("failed_predicates", ())
        if isinstance(item, dict)
    )
    return Candidate(
        id=str(raw.get("id", "")),
        matched=bool(raw.get("matched")),
        reason_code=str(raw.get("reason_code", REQUIREMENT_UNSATISFIED)),
        reason_text=str(raw.get("reason_text", "")),
        blocking_requirements=tuple(raw.get("blocking_requirements", ())),
        missing_requirements=tuple(raw.get("missing_requirements", ())),
        failed_predicates=failed,
        missing_slots=tuple(raw.get("missing_slots", ())),
    )


def _extract_browse_operation(luma_response: Mapping[str, Any]) -> Optional[str]:
    operation = luma_response.get("operation")
    if isinstance(operation, str) and operation:
        return operation
    facts = luma_response.get("facts")
    if isinstance(facts, dict):
        facts_operation = facts.get("operation")
        if isinstance(facts_operation, str) and facts_operation:
            return facts_operation
    context = luma_response.get("context")
    if isinstance(context, dict):
        context_operation = context.get("operation")
        if isinstance(context_operation, str) and context_operation:
            return context_operation
    return None


_PRESENTATION_READY_BRANCHES = frozenset(
    {
        "availability_reshow",
        "cache_satisfiable_browse",
        "recovery_presentation",
    }
)


def _final_status_trace(
    *,
    status: str,
    action: Optional[str],
    action_branch: Optional[str],
    missing_slots: Sequence[str],
) -> Tuple[str, str]:
    """Reason code/text for the finalized Stage 08 plan status."""
    if status == "NEEDS_CLARIFICATION":
        return CLARIFICATION_REQUIRED, "NEEDS_CLARIFICATION"
    if status == "AWAITING_CONFIRMATION":
        return CONFIRMATION_REQUIRED, "AWAITING_CONFIRMATION"
    if status == "AWAITING_CAPABILITY":
        return STATUS_AWAITING_CAPABILITY, "AWAITING_CAPABILITY"
    if status == "READY":
        if action:
            return STATUS_READY, "READY_EXECUTION"
        if action_branch in _PRESENTATION_READY_BRANCHES:
            return STATUS_READY, "READY_PRESENTATION"
        if missing_slots:
            # Should be unreachable after Stage 08 reconciliation.
            return STATUS_READY, "READY_PRESENTATION"
        return STATUS_READY, "READY"
    return STATUS_READY, f"Plan status is {status}"


def _status_branch_candidates(
    *,
    intent_name: str,
    missing_slots: Sequence[str],
    needs_clarification: bool,
    confirmation_state: Optional[str],
    active_capability: Optional[str],
    executable_actions: Sequence[str],
    selected_action: Optional[str] = None,
    action_branch: Optional[str] = None,
) -> Tuple[List[Candidate], str, str, str]:
    """Return status candidates, winner status, reason_code, reason_text.

    Heuristic candidates for forensic comparison. The emit path overrides the
    winner with Stage 08 ``plan.status`` when it differs so plan and trace share
    one authoritative status.
    """
    candidates: List[Candidate] = []

    def _add(
        branch_id: str,
        matched: bool,
        reason_code: str,
        reason_text: str,
        *,
        blocking: Sequence[str] = (),
        missing_reqs: Sequence[str] = (),
    ) -> None:
        candidates.append(
            Candidate(
                id=branch_id,
                matched=matched,
                reason_code=reason_code,
                reason_text=reason_text,
                blocking_requirements=tuple(blocking),
                missing_requirements=tuple(missing_reqs),
            )
        )

    if intent_name == "UNKNOWN":
        _add(
            "UNKNOWN",
            True,
            CLARIFICATION_REQUIRED,
            "UNKNOWN intent always requires clarification",
        )
        return candidates, "NEEDS_CLARIFICATION", CLARIFICATION_REQUIRED, (
            "Intent is UNKNOWN; clarification required"
        )

    if missing_slots:
        presentation_ready = action_branch in _PRESENTATION_READY_BRANCHES
        if executable_actions or presentation_ready or selected_action:
            _add(
                "MISSING_SLOTS_EXECUTABLE",
                True,
                STATUS_READY,
                "executable_with subset satisfied despite missing planning slots",
                missing_reqs=list(missing_slots),
            )
            return candidates, "READY", STATUS_READY, (
                "Executable subset satisfied; exploratory action allowed"
            )
        _add(
            "MISSING_SLOTS",
            True,
            CLARIFICATION_REQUIRED,
            "Required slots missing and no executable subset",
            missing_reqs=list(missing_slots),
        )
        return candidates, "NEEDS_CLARIFICATION", CLARIFICATION_REQUIRED, (
            f"Missing slots {list(missing_slots)!r} require clarification"
        )

    if needs_clarification:
        _add(
            "NEEDS_CLARIFICATION_FLAG",
            True,
            CLARIFICATION_REQUIRED,
            "Luma flagged needs_clarification",
        )
        return candidates, "NEEDS_CLARIFICATION", CLARIFICATION_REQUIRED, (
            "Luma response requires clarification"
        )

    if confirmation_state == "pending":
        _add(
            "CONFIRMATION_PENDING",
            True,
            CONFIRMATION_REQUIRED,
            "Booking confirmation pending",
        )
        return candidates, "AWAITING_CONFIRMATION", CONFIRMATION_REQUIRED, (
            "User confirmation pending before commit"
        )

    if active_capability:
        _add(
            "ACTIVE_CAPABILITY",
            True,
            STATUS_AWAITING_CAPABILITY,
            f"Capability {active_capability!r} blocks progression",
            blocking=[active_capability],
        )
        return candidates, "AWAITING_CAPABILITY", STATUS_AWAITING_CAPABILITY, (
            f"Awaiting capability {active_capability!r}"
        )

    _add("READY", True, STATUS_READY, "Planning inputs complete")
    return candidates, "READY", STATUS_READY, "Plan status is READY"


def _collect_ignored_inputs(
    luma_response: Mapping[str, Any],
    *,
    browse_operation: Optional[str],
    evaluated_keys: Sequence[str],
) -> Dict[str, IgnoredInput]:
    ignored: Dict[str, IgnoredInput] = {}
    if browse_operation is None:
        for key in ("operation", "facts.operation", "context.operation"):
            if key.split(".")[0] in luma_response or (
                key.startswith("facts.")
                and isinstance(luma_response.get("facts"), dict)
            ):
                ignored[key] = IgnoredInput(
                    reason_code=INPUT_IGNORED_NOT_APPLICABLE,
                    reason_text="No browse operation detected for this turn",
                )
    for key, value in luma_response.items():
        if key not in evaluated_keys and key not in {
            "slots",
            "facts",
            "context",
            "booking",
            "plan",
            "missing_slots",
            "needs_clarification",
            "intent",
            "_effective_collected_slots",
            "_effective_intent",
        }:
            ignored[key] = IgnoredInput(
                reason_code=INPUT_IGNORED_NOT_APPLICABLE,
                reason_text="Field not consulted by planner routing",
            )
        if value is None:
            continue
    return ignored


def emit_planner_decision_graph(
    *,
    intent_name: str,
    luma_response: Mapping[str, Any],
    plan: Mapping[str, Any],
    flags: Mapping[str, Any],
    effective_slots: Mapping[str, Any],
    missing_slots: Sequence[str],
    needs_clarification: bool,
    confirmation_state: Optional[str],
    active_capability: Optional[str],
    executable_actions: Sequence[str],
    availability_resolved: bool,
    stage_from_action: bool,
    selected_step: Optional[Mapping[str, Any]],
    step_candidates: Sequence[Mapping[str, Any]],
    action_branch: Optional[str] = None,
) -> None:
    """Emit the planner routing decision graph for the current turn."""
    if TurnTrace.current() is None:
        return

    browse_operation = _extract_browse_operation(luma_response)
    evidence_ids: List[str] = []
    browse_evidence: Optional[str] = None

    missing_evidence = emit_evidence(
        "PLANNING_INPUTS",
        subsystem="planning",
        facts={
            "intent_name": intent_name,
            "missing_slots": list(missing_slots),
            "needs_clarification": needs_clarification,
            "confirmation_state": confirmation_state,
            "availability_resolved": availability_resolved,
            "executable_actions": list(executable_actions),
            "collected_slot_keys": sorted(
                key for key, value in effective_slots.items() if value is not None
            ),
        },
        node_id=PLANNER_EVIDENCE_MISSING_SLOTS_ID,
        source="plan_builder",
        observed_at_stage="planning",
    )
    if missing_evidence:
        evidence_ids.append(missing_evidence)

    steps = get_execution_steps(intent_name) if intent_name else []
    steps_evidence = emit_evidence(
        "POLICY_EXECUTION_STEPS",
        subsystem="planning",
        facts={
            "intent_name": intent_name,
            "steps": [
                {
                    "action": step.get("action"),
                    "mode": step.get("mode"),
                    "required_slots": step.get("required_slots", []),
                    "requires": step.get("requires", []),
                }
                for step in steps
            ],
        },
        node_id=PLANNER_EVIDENCE_POLICY_STEPS_ID,
        source="intent_policy.yaml",
        observed_at_stage="planning",
    )
    if steps_evidence:
        evidence_ids.append(steps_evidence)

    business_facts = {
        key: value
        for key, value in flags.items()
        if key.endswith(
            (
                "_required",
                "_ready",
                "_satisfied",
                "_identified",
                "_resolved",
            )
        )
        or key in {
            "availability_resolved",
            "confirmation_state",
            "needs_clarification",
        }
    }
    facts_evidence = emit_evidence(
        "BUSINESS_FACTS",
        subsystem="planning",
        facts=business_facts,
        node_id=PLANNER_EVIDENCE_BUSINESS_FACTS_ID,
        source="build_policy_execution_flags",
        observed_at_stage="planning",
    )
    if facts_evidence:
        evidence_ids.append(facts_evidence)

    # Browse owns ``evidence.browse.signal`` (emit_browse_resolve_trace). Planner
    # only depends on that record when present — never re-emits the same id.
    if browse_operation:
        try:
            from core.tracing.browse import BROWSE_EVIDENCE_SIGNAL_ID
        except ImportError:
            BROWSE_EVIDENCE_SIGNAL_ID = "evidence.browse.signal"
        trace = TurnTrace.current()
        if trace is not None and trace.has_record(BROWSE_EVIDENCE_SIGNAL_ID):
            browse_evidence = BROWSE_EVIDENCE_SIGNAL_ID
            evidence_ids.append(browse_evidence)

    evaluated_keys = (
        "intent_name",
        "missing_slots",
        "needs_clarification",
        "confirmation_state",
        "availability_resolved",
        "executable_actions",
        "slots",
        "operation",
        "facts",
        "context",
    )
    ignored_inputs = _collect_ignored_inputs(
        luma_response,
        browse_operation=browse_operation,
        evaluated_keys=evaluated_keys,
    )

    plan_action = plan.get("action")
    selected_action = (
        str(plan_action)
        if isinstance(plan_action, str) and plan_action.strip()
        else None
    )
    status_candidates, status_winner, status_code, status_text = (
        _status_branch_candidates(
            intent_name=intent_name,
            missing_slots=missing_slots,
            needs_clarification=needs_clarification,
            confirmation_state=confirmation_state,
            active_capability=active_capability,
            executable_actions=executable_actions,
            selected_action=selected_action,
            action_branch=action_branch,
        )
    )
    # Stage 08 ``plan.status`` is the single authority. Trace must not invent a
    # second winner that disagrees with the finalized Decision plan.
    final_status = plan.get("status")
    if isinstance(final_status, str) and final_status.strip():
        status_winner = final_status
        status_code, status_text = _final_status_trace(
            status=final_status,
            action=selected_action if selected_action is not None else plan.get("action"),
            action_branch=action_branch,
            missing_slots=missing_slots,
        )
    elif status_winner == "NEEDS_CLARIFICATION":
        status_code = CLARIFICATION_REQUIRED
        status_text = "NEEDS_CLARIFICATION"
    elif status_winner == "AWAITING_CONFIRMATION":
        status_code = CONFIRMATION_REQUIRED
        status_text = "AWAITING_CONFIRMATION"
    elif status_winner == "AWAITING_CAPABILITY":
        status_code = STATUS_AWAITING_CAPABILITY
        status_text = "AWAITING_CAPABILITY"
    else:
        status_code, status_text = _final_status_trace(
            status=str(status_winner),
            action=selected_action,
            action_branch=action_branch,
            missing_slots=missing_slots,
        )

    status_id = decide(
        "PLAN_STATUS",
        subsystem="planning",
        winner=status_winner,
        reason_code=status_code,
        reason_text=status_text,
        node_id=PLANNER_STATUS_ID,
        depends_on=evidence_ids,
        candidates=status_candidates,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "intent_name": intent_name,
            "missing_slots": list(missing_slots),
            "needs_clarification": needs_clarification,
            "confirmation_state": confirmation_state,
            "active_capability": active_capability,
            "executable_actions": list(executable_actions),
            "selected_action": selected_action,
            "action_branch": action_branch,
            "plan_status": final_status,
        },
        inputs_ignored=ignored_inputs,
    )

    clarification_deps = [dep for dep in (status_id, *evidence_ids) if dep]
    if status_winner == "NEEDS_CLARIFICATION":
        decide(
            "CLARIFICATION",
            subsystem="planning",
            winner="clarify",
            reason_code=CLARIFICATION_REQUIRED,
            reason_text=status_text,
            node_id=PLANNER_CLARIFICATION_ID,
            depends_on=clarification_deps,
            category=ROUTING_CATEGORY,
            inputs_evaluated={
                "missing_slots": list(missing_slots),
                "needs_clarification": needs_clarification,
            },
        )
    else:
        decide(
            "CLARIFICATION",
            subsystem="planning",
            winner="skip",
            reason_code=INPUT_IGNORED_NOT_APPLICABLE,
            reason_text="Clarification path not taken",
            node_id=PLANNER_CLARIFICATION_ID,
            depends_on=clarification_deps,
            category=ROUTING_CATEGORY,
            skipped=True,
        )

    confirmation_deps = [dep for dep in (status_id, *evidence_ids) if dep]
    if status_winner == "AWAITING_CONFIRMATION":
        decide(
            "CONFIRMATION",
            subsystem="planning",
            winner="await_confirmation",
            reason_code=CONFIRMATION_REQUIRED,
            reason_text=status_text,
            node_id=PLANNER_CONFIRMATION_ID,
            depends_on=confirmation_deps,
            category=ROUTING_CATEGORY,
            inputs_evaluated={"confirmation_state": confirmation_state},
        )
    else:
        decide(
            "CONFIRMATION",
            subsystem="planning",
            winner="skip",
            reason_code=INPUT_IGNORED_NOT_APPLICABLE,
            reason_text="Confirmation path not taken",
            node_id=PLANNER_CONFIRMATION_ID,
            depends_on=confirmation_deps,
            category=ROUTING_CATEGORY,
            skipped=True,
        )

    action_winner = plan.get("action")
    action_candidates = [_candidate_from_eval(item) for item in step_candidates]
    if action_winner:
        action_code = STEP_SELECTED
        action_text = f"Selected execution action {action_winner!r}"
    else:
        action_code = NO_STEP_REQUIRES_SATISFIED
        action_text = "No execution step eligible for current state"

    try:
        from core.tracing.facts import FACTS_DERIVE_ALL_ID
        from core.tracing.fingerprint import FINGERPRINT_TRUST_ID
    except ImportError:
        FACTS_DERIVE_ALL_ID = "decision.facts.derive_all"
        FINGERPRINT_TRUST_ID = "decision.fingerprint.trust"

    action_deps = [
        dep
        for dep in (
            status_id,
            facts_evidence,
            steps_evidence,
            missing_evidence,
            FACTS_DERIVE_ALL_ID if TurnTrace.current() and TurnTrace.current().has_record(FACTS_DERIVE_ALL_ID) else None,
            FINGERPRINT_TRUST_ID if TurnTrace.current() and TurnTrace.current().has_record(FINGERPRINT_TRUST_ID) else None,
        )
        if dep
    ]
    action_id = decide(
        "PLAN_ACTION",
        subsystem="planning",
        winner=action_winner,
        reason_code=action_code,
        reason_text=action_text,
        node_id=PLANNER_SELECT_ACTION_ID,
        depends_on=action_deps,
        candidates=action_candidates,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "intent_name": intent_name,
            "collected_slot_keys": sorted(
                key for key, value in effective_slots.items() if value is not None
            ),
            "flags": dict(business_facts),
        },
    )
    if action_id and plan.get("action") is not None:
        emit_mutation(
            action_id,
            subsystem="planning",
            field="plan.action",
            previous=None,
            new=plan.get("action"),
            reason_code=STEP_SELECTED,
            reason_text=f"Plan action set to {plan.get('action')!r}",
            presentation_only=True,
        )

    stage_winner = plan.get("stage")
    if stage_from_action and action_winner:
        stage_code = STAGE_DERIVED_FROM_ACTION
        stage_text = f"Stage {stage_winner!r} derived from action {action_winner!r}"
    else:
        stage_code = STAGE_DERIVED_FROM_STATUS
        stage_text = f"Stage {stage_winner!r} derived from status {status_winner!r}"

    stage_deps = [dep for dep in (action_id, status_id) if dep]
    stage_id = decide(
        "SELECT_STAGE",
        subsystem="planning",
        winner=stage_winner,
        reason_code=stage_code,
        reason_text=stage_text,
        node_id=PLANNER_SELECT_STAGE_ID,
        depends_on=stage_deps,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "action": action_winner,
            "status": status_winner,
            "stage_from_action": stage_from_action,
        },
    )
    if stage_id and stage_winner is not None:
        emit_mutation(
            stage_id,
            subsystem="planning",
            field="plan.stage",
            previous=None,
            new=stage_winner,
            reason_code=stage_code,
            reason_text=stage_text,
            presentation_only=True,
        )

    if status_id and plan.get("status"):
        emit_mutation(
            status_id,
            subsystem="planning",
            field="plan.status",
            previous=None,
            new=plan.get("status"),
            reason_code=status_code,
            reason_text=status_text,
            presentation_only=True,
        )

    route_winner = "browse_pagination" if browse_operation in _BROWSE_OPERATIONS else "plan_execution"
    if browse_operation in _BROWSE_OPERATIONS:
        route_code = EXECUTION_ROUTE_BROWSE
        route_text = (
            f"Browse operation {browse_operation!r} routes to pagination handling"
        )
    else:
        route_code = EXECUTION_ROUTE_PLAN
        route_text = "Normal plan execution routing"

    route_deps = [
        dep
        for dep in (
            action_id,
            browse_evidence if browse_operation else None,
            status_id,
        )
        if dep
    ]
    decide(
        "EXECUTION_ROUTE",
        subsystem="planning",
        winner=route_winner,
        reason_code=route_code,
        reason_text=route_text,
        node_id=PLANNER_EXECUTION_ROUTE_ID,
        depends_on=route_deps,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "browse_operation": browse_operation,
            "plan_action": action_winner,
            "plan_status": status_winner,
            "time_selection_ready": flags.get("time_selection_ready"),
        },
    )


def emit_planner_decision_graph_from_plan_builder(
    *,
    intent_name: str,
    luma_response: Mapping[str, Any],
    plan: Mapping[str, Any],
    flags: Mapping[str, Any],
    effective_slots: Mapping[str, Any],
    missing_slots: Sequence[str],
    needs_clarification: bool,
    confirmation_state: Optional[str],
    active_capability: Optional[str],
    executable_actions: Sequence[str],
    availability_resolved: bool,
    stage_from_action: bool,
    action_branch: Optional[str] = None,
) -> None:
    """Evaluate policy candidates and emit the planner graph."""
    selected_step, step_candidates = evaluate_execution_step_candidates(
        intent_name,
        dict(effective_slots),
        dict(flags),
        entity_schema=(
            luma_response.get("_entity_schema")
            if isinstance(luma_response.get("_entity_schema"), dict)
            else None
        ),
    )
    emit_planner_decision_graph(
        intent_name=intent_name,
        luma_response=luma_response,
        plan=plan,
        flags=flags,
        effective_slots=effective_slots,
        missing_slots=missing_slots,
        needs_clarification=needs_clarification,
        confirmation_state=confirmation_state,
        active_capability=active_capability,
        executable_actions=executable_actions,
        availability_resolved=availability_resolved,
        stage_from_action=stage_from_action,
        selected_step=selected_step,
        step_candidates=step_candidates,
        action_branch=action_branch,
    )
