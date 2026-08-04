"""Phase 1 orchestrator spine decision emitters."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.tracing.decision_trace import TurnTrace, decide
from core.tracing.planner import (
    PLANNER_EXECUTION_ROUTE_ID,
    PLANNER_SELECT_ACTION_ID,
    planner_dependencies,
)
from core.tracing.reason_codes import (
    AWAITING_CONFIRMATION,
    HANDLER_DELEGATED,
    NEEDS_CLARIFICATION,
    PAGINATION_SHORT_CIRCUIT,
    PERSIST_SAVE_APPLIED,
    PERSIST_SAVE_SKIPPED_NO_STATE,
    PERSIST_SAVE_SKIPPED_STATUS,
    PLANNING_FAILED,
    PLAN_ACTION_MISSING,
    PLAN_ACTION_PRESENT,
    RELOAD_MATCH,
    RELOAD_MISMATCH,
    RELOAD_SKIPPED,
    TURN_OUTCOME_ERROR,
    TURN_OUTCOME_EXECUTED,
    TURN_OUTCOME_HANDLER_DELEGATED,
)

SPINE_EXECUTION_ID = "decision.execution.eligibility"
SPINE_PERSIST_SAVE_ID = "decision.persist.save"
SPINE_RELOAD_VERIFY_ID = "decision.persist.reload_verify"
SPINE_TURN_OUTCOME_ID = "decision.turn.outcome"

_SPINE_NODE_IDS = (
    SPINE_EXECUTION_ID,
    SPINE_PERSIST_SAVE_ID,
    SPINE_RELOAD_VERIFY_ID,
)


def spine_dependencies() -> List[str]:
    """Return spine node ids emitted so far this turn."""
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in _SPINE_NODE_IDS if trace.has_record(node_id)]


def upstream_dependencies() -> List[str]:
    """Spine and upstream subsystem nodes emitted so far (for turn outcome wiring)."""
    deps = spine_dependencies() + planner_dependencies()
    try:
        from core.tracing.facts import facts_dependencies
        from core.tracing.fingerprint import fingerprint_dependencies
        from core.tracing.merge import merge_dependencies
        from core.tracing.browse import pagination_dependencies
        from core.tracing.confirmation import confirmation_dependencies
        from core.tracing.binding import bind_dependencies

        deps.extend(facts_dependencies())
        deps.extend(fingerprint_dependencies())
        deps.extend(merge_dependencies())
        deps.extend(pagination_dependencies())
        deps.extend(confirmation_dependencies())
        deps.extend(bind_dependencies())
    except ImportError:
        pass
    return list(dict.fromkeys(deps))


def _normalize_intent(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    if value is None:
        return ""
    return str(value)


def resolve_execution_eligibility(
    *,
    pagination_handled: bool = False,
    handler_delegated: bool = False,
    planning_failed: bool = False,
    plan_status: Optional[str] = None,
    plan_action: Optional[str] = None,
    can_execute: bool = False,
) -> Tuple[str, str, str]:
    """Return (winner, reason_code, reason_text) for execution eligibility."""
    if pagination_handled:
        return (
            "skip",
            PAGINATION_SHORT_CIRCUIT,
            "Browse pagination handled the turn without executing plan.action",
        )
    if handler_delegated:
        return (
            "skip",
            HANDLER_DELEGATED,
            "Intent handler delegated; execution path bypassed",
        )
    if planning_failed:
        return (
            "skip",
            PLANNING_FAILED,
            "Planning failed before execution routing",
        )
    if can_execute:
        return (
            "execute",
            PLAN_ACTION_PRESENT,
            f"Executing plan action {plan_action!r}",
        )
    status = (plan_status or "").upper()
    if status == "AWAITING_CONFIRMATION":
        return (
            "skip",
            AWAITING_CONFIRMATION,
            "Awaiting user confirmation; execution not run",
        )
    if status == "NEEDS_CLARIFICATION":
        return (
            "skip",
            NEEDS_CLARIFICATION,
            "Turn needs clarification; execution not run",
        )
    if not plan_action:
        return (
            "skip",
            PLAN_ACTION_MISSING,
            "No plan.action selected for this turn",
        )
    return (
        "skip",
        PLAN_ACTION_PRESENT,
        f"Plan action {plan_action!r} present but not eligible to execute",
    )


def emit_execution_eligibility(
    *,
    pagination_handled: bool = False,
    handler_delegated: bool = False,
    planning_failed: bool = False,
    plan_status: Optional[str] = None,
    plan_action: Optional[str] = None,
    can_execute: bool = False,
    inputs_evaluated: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    winner, reason_code, reason_text = resolve_execution_eligibility(
        pagination_handled=pagination_handled,
        handler_delegated=handler_delegated,
        planning_failed=planning_failed,
        plan_status=plan_status,
        plan_action=plan_action,
        can_execute=can_execute,
    )
    deps: List[str] = []
    trace = TurnTrace.current()
    if trace and trace.has_record(PLANNER_SELECT_ACTION_ID):
        deps.append(PLANNER_SELECT_ACTION_ID)
    elif trace and trace.has_record(PLANNER_EXECUTION_ROUTE_ID):
        deps.append(PLANNER_EXECUTION_ROUTE_ID)
    return decide(
        "EXECUTE_PLAN_ACTION",
        subsystem="execution",
        winner=winner,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=SPINE_EXECUTION_ID,
        depends_on=deps,
        inputs_evaluated=dict(inputs_evaluated or {}),
    )


def _outcome_winner(
    outcome: Mapping[str, Any],
    *,
    result: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    plan = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    intent = _normalize_intent(
        outcome.get("intent_name")
        or outcome.get("intent")
        or plan.get("intent_name")
        or plan.get("intent")
    )
    if not intent and isinstance(result, dict):
        result_plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        intent = _normalize_intent(
            result.get("intent_name")
            or result.get("intent")
            or result_plan.get("intent_name")
            or result_plan.get("intent")
        )
    return {
        "status": outcome.get("status"),
        "stage": plan.get("stage") or outcome.get("stage"),
        "action": plan.get("action") or outcome.get("action"),
        "intent": intent,
    }


def resolve_turn_outcome(
    *,
    outcome: Optional[Mapping[str, Any]],
    success: bool = True,
    handler_delegated: bool = False,
    result: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    if not success or not isinstance(outcome, dict):
        # Preserve planning intent when the tool path failed before an outcome
        # envelope was produced (blocked/failed execution still has result.plan).
        intent = ""
        stage = None
        action = None
        if isinstance(result, dict):
            result_plan = (
                result.get("plan") if isinstance(result.get("plan"), dict) else {}
            )
            intent = _normalize_intent(
                result.get("intent_name")
                or result.get("intent")
                or result_plan.get("intent_name")
                or result_plan.get("intent")
            )
            stage = result_plan.get("stage")
            action = result_plan.get("action")
        return (
            {
                "status": "error",
                "intent": intent,
                "stage": stage,
                "action": action,
            },
            TURN_OUTCOME_ERROR,
            "Request failed before a planning outcome was produced",
        )
    winner = _outcome_winner(outcome, result=result)
    status = str(winner.get("status") or "").upper()
    if handler_delegated or status == "HANDLER_DELEGATED":
        return (
            winner,
            TURN_OUTCOME_HANDLER_DELEGATED,
            "Turn delegated to an external intent handler",
        )
    if status in {"SUCCESS", "SUCCEEDED", "EXECUTED"}:
        return (
            winner,
            TURN_OUTCOME_EXECUTED,
            "Turn completed with an execution artifact",
        )
    if status == "AWAITING_CONFIRMATION":
        return (
            winner,
            AWAITING_CONFIRMATION,
            "Turn awaiting explicit user confirmation",
        )
    if status == "NEEDS_CLARIFICATION":
        return (
            winner,
            NEEDS_CLARIFICATION,
            "Turn requires clarification before proceeding",
        )
    action = winner.get("action")
    if action:
        return (
            winner,
            PLAN_ACTION_PRESENT,
            f"Turn resolved with plan.action={action!r}",
        )
    return (
        winner,
        PLAN_ACTION_MISSING,
        "Turn resolved without an execution action",
    )


def emit_turn_outcome(
    *,
    outcome: Optional[Mapping[str, Any]],
    success: bool = True,
    handler_delegated: bool = False,
    result: Optional[Mapping[str, Any]] = None,
    inputs_evaluated: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    winner, reason_code, reason_text = resolve_turn_outcome(
        outcome=outcome,
        success=success,
        handler_delegated=handler_delegated,
        result=result,
    )
    return decide(
        "TURN_OUTCOME",
        subsystem="api",
        winner=winner,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=SPINE_TURN_OUTCOME_ID,
        depends_on=upstream_dependencies(),
        inputs_evaluated=dict(inputs_evaluated or {}),
        is_root=True,
    )


def emit_persist_save(
    *,
    winner: str,
    reason_code: str,
    reason_text: str,
    inputs_evaluated: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    deps: List[str] = []
    if TurnTrace.current() and TurnTrace.current().has_record(SPINE_EXECUTION_ID):
        deps.append(SPINE_EXECUTION_ID)
    return decide(
        "PERSIST_SAVE",
        subsystem="session",
        winner=winner,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=SPINE_PERSIST_SAVE_ID,
        depends_on=deps,
        inputs_evaluated=dict(inputs_evaluated or {}),
    )


def _session_slot_keys(session: Optional[Mapping[str, Any]]) -> List[str]:
    if not isinstance(session, dict):
        return []
    slots = session.get("slots")
    if not isinstance(slots, dict):
        return []
    return sorted(key for key, value in slots.items() if value is not None)


def emit_reload_verify(
    *,
    saved_state: Optional[Mapping[str, Any]],
    reloaded_state: Optional[Mapping[str, Any]],
) -> Optional[str]:
    deps: List[str] = []
    trace = TurnTrace.current()
    if trace and trace.has_record(SPINE_PERSIST_SAVE_ID):
        deps.append(SPINE_PERSIST_SAVE_ID)

    if saved_state is None:
        return decide(
            "RELOAD_VERIFY",
            subsystem="session",
            winner="skipped",
            reason_code=RELOAD_SKIPPED,
            reason_text="No session was saved this turn",
            node_id=SPINE_RELOAD_VERIFY_ID,
            depends_on=deps,
            skipped=True,
        )

    mismatches: List[str] = []
    for key in ("intent_name", "intent", "status"):
        saved_val = saved_state.get(key) if isinstance(saved_state, dict) else None
        reloaded_val = (
            reloaded_state.get(key) if isinstance(reloaded_state, dict) else None
        )
        if saved_val is not None and saved_val != reloaded_val:
            mismatches.append(f"{key}: saved={saved_val!r} reloaded={reloaded_val!r}")

    saved_slots = _session_slot_keys(saved_state)
    reloaded_slots = _session_slot_keys(reloaded_state)
    if saved_slots != reloaded_slots:
        mismatches.append(
            f"slots keys: saved={saved_slots} reloaded={reloaded_slots}"
        )

    if reloaded_state is None:
        return decide(
            "RELOAD_VERIFY",
            subsystem="session",
            winner="mismatch",
            reason_code=RELOAD_MISMATCH,
            reason_text="Saved session could not be reloaded",
            node_id=SPINE_RELOAD_VERIFY_ID,
            depends_on=deps,
            inputs_evaluated={"mismatches": ["reloaded_state is None"]},
        )

    matched = not mismatches
    return decide(
        "RELOAD_VERIFY",
        subsystem="session",
        winner="match" if matched else "mismatch",
        reason_code=RELOAD_MATCH if matched else RELOAD_MISMATCH,
        reason_text=(
            "Reloaded session matches saved session"
            if matched
            else "; ".join(mismatches)
        ),
        node_id=SPINE_RELOAD_VERIFY_ID,
        depends_on=deps,
        inputs_evaluated={
            "intent": _normalize_intent(
                saved_state.get("intent_name") or saved_state.get("intent")
            ),
            "status": saved_state.get("status"),
            "slot_keys": saved_slots,
        },
    )


def emit_persist_save_for_outcome(
    *,
    outcome_status: Optional[str],
    saved: bool,
    new_session_state: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    if saved and isinstance(new_session_state, dict):
        return emit_persist_save(
            winner="save",
            reason_code=PERSIST_SAVE_APPLIED,
            reason_text=f"Session persisted for outcome status {outcome_status!r}",
            inputs_evaluated={
                "outcome_status": outcome_status,
                "intent": _normalize_intent(
                    new_session_state.get("intent_name")
                    or new_session_state.get("intent")
                ),
                "status": new_session_state.get("status"),
            },
        )

    persist_statuses = {
        "NEEDS_CLARIFICATION",
        "AWAITING_CONFIRMATION",
        "AWAITING_CAPABILITY",
        "READY",
        "EXECUTED",
        "success",
    }
    if outcome_status not in persist_statuses:
        return emit_persist_save(
            winner="skip",
            reason_code=PERSIST_SAVE_SKIPPED_STATUS,
            reason_text=(
                f"Outcome status {outcome_status!r} does not require session persistence"
            ),
            inputs_evaluated={"outcome_status": outcome_status},
        )

    return emit_persist_save(
        winner="skip",
        reason_code=PERSIST_SAVE_SKIPPED_NO_STATE,
        reason_text="Persistence gate passed but no session payload was produced",
        inputs_evaluated={"outcome_status": outcome_status},
    )
