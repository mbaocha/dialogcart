"""Confirmation gate decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from core.tracing.decision_trace import Candidate, TurnTrace, decide, emit_evidence, emit_mutation
from core.tracing.reason_codes import (
    CONFIRMATION_ACCEPT,
    CONFIRMATION_ENTER_PENDING,
    CONFIRMATION_GATE_CLOSED,
    CONFIRMATION_GATE_OPEN,
    CONFIRMATION_REJECT,
    CONFIRMATION_ANOTHER_REQUEST,
    INPUT_IGNORED_NOT_APPLICABLE,
)

CONFIRMATION_EVIDENCE_STATE_ID = "evidence.confirmation.state"
CONFIRMATION_EVIDENCE_RAW_INTENT_ID = "evidence.luma.raw_intent"
CONFIRMATION_EVIDENCE_COMPLETENESS_ID = "evidence.planning.completeness"

CONFIRMATION_GATE_OPEN_ID = "decision.confirmation.gate_open"
CONFIRMATION_CLASSIFY_ID = "decision.confirmation.classify_turn"
CONFIRMATION_ENTER_PENDING_ID = "decision.confirmation.enter_pending"

CONFIRMATION_NODE_IDS = (
    CONFIRMATION_GATE_OPEN_ID,
    CONFIRMATION_CLASSIFY_ID,
    CONFIRMATION_ENTER_PENDING_ID,
)

ROUTING_CATEGORY = "routing"


def confirmation_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in CONFIRMATION_NODE_IDS if trace.has_record(node_id)]


def emit_confirmation_gate_open_trace(
    *,
    session_state: Optional[Mapping[str, Any]],
    gate_open: bool,
    intent_name: str = "",
    confirmation_state: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    if trace.has_record(CONFIRMATION_GATE_OPEN_ID):
        return CONFIRMATION_GATE_OPEN_ID

    state_id = emit_evidence(
        "CONFIRMATION_STATE",
        subsystem="session",
        facts={
            "confirmation_state": confirmation_state,
            "status": status,
            "intent_name": intent_name,
            "gate_open": gate_open,
        },
        node_id=CONFIRMATION_EVIDENCE_STATE_ID,
        source="session_state",
        observed_at_stage="confirmation",
    )

    return decide(
        "CONFIRMATION_GATE_OPEN",
        subsystem="session",
        winner="open" if gate_open else "closed",
        reason_code=CONFIRMATION_GATE_OPEN if gate_open else CONFIRMATION_GATE_CLOSED,
        reason_text=(
            "Confirmation gate is open for this durable booking flow"
            if gate_open
            else "Confirmation gate is closed"
        ),
        node_id=CONFIRMATION_GATE_OPEN_ID,
        depends_on=[state_id] if state_id else [],
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "confirmation_state": confirmation_state,
            "status": status,
            "intent_name": intent_name,
        },
    )


def emit_confirmation_classify_trace(
    *,
    gate_action: str,
    gate_open: bool,
    raw_intent: str = "",
    gate_open_id: Optional[str] = None,
) -> Optional[str]:
    trace = TurnTrace.current()
    if trace is None:
        return None
    if trace.has_record(CONFIRMATION_CLASSIFY_ID):
        return CONFIRMATION_CLASSIFY_ID

    raw_id = emit_evidence(
        "LUMA_RAW_INTENT",
        subsystem="orchestration",
        facts={"raw_intent": raw_intent},
        node_id=CONFIRMATION_EVIDENCE_RAW_INTENT_ID,
        source="luma_response",
        observed_at_stage="confirmation",
    )

    deps = [dep for dep in (gate_open_id, raw_id) if dep]

    if not gate_open or not gate_action:
        return decide(
            "CONFIRMATION_CLASSIFY",
            subsystem="session",
            winner="NONE",
            reason_code=INPUT_IGNORED_NOT_APPLICABLE,
            reason_text="Confirmation gate not active for this turn",
            node_id=CONFIRMATION_CLASSIFY_ID,
            depends_on=deps,
            category=ROUTING_CATEGORY,
            skipped=True,
        )

    reason_map = {
        "YES": (CONFIRMATION_ACCEPT, "User accepted booking confirmation"),
        "NO": (CONFIRMATION_REJECT, "User rejected booking confirmation"),
        "ANOTHER_REQUEST": (
            CONFIRMATION_ANOTHER_REQUEST,
            "Another request superseded the pending confirmation",
        ),
    }
    code, text = reason_map.get(gate_action, (INPUT_IGNORED_NOT_APPLICABLE, "No gate action"))

    return decide(
        "CONFIRMATION_CLASSIFY",
        subsystem="session",
        winner=gate_action,
        reason_code=code,
        reason_text=text,
        node_id=CONFIRMATION_CLASSIFY_ID,
        depends_on=deps,
        category=ROUTING_CATEGORY,
        candidates=[
            Candidate(
                id="YES",
                matched=gate_action == "YES",
                reason_code=CONFIRMATION_ACCEPT,
                reason_text="Raw CONFIRM_ACTION intent",
            ),
            Candidate(
                id="NO",
                matched=gate_action == "NO",
                reason_code=CONFIRMATION_REJECT,
                reason_text="Raw REJECT_ACTION intent",
            ),
            Candidate(
                id="ANOTHER_REQUEST",
                matched=gate_action == "ANOTHER_REQUEST",
                reason_code=CONFIRMATION_ANOTHER_REQUEST,
                reason_text="Any other dominant intent",
            ),
        ],
        inputs_evaluated={"raw_intent": raw_intent},
    )


def emit_confirmation_enter_pending_trace(
    *,
    entered: bool,
    previous_state: Optional[str],
    missing_slots: Optional[List[str]] = None,
    availability_resolved: bool = False,
    time_selection_ready: bool = False,
) -> None:
    if TurnTrace.current() is None:
        return

    completeness_id = emit_evidence(
        "PLANNING_COMPLETENESS",
        subsystem="planning",
        facts={
            "missing_slots": list(missing_slots or []),
            "availability_resolved": availability_resolved,
            "time_selection_ready": time_selection_ready,
        },
        node_id=CONFIRMATION_EVIDENCE_COMPLETENESS_ID,
        source="plan_builder",
        observed_at_stage="confirmation",
    )

    deps = [completeness_id] if completeness_id else []

    if not entered:
        decide(
            "ENTER_CONFIRMATION_PENDING",
            subsystem="planning",
            winner="skip",
            reason_code=INPUT_IGNORED_NOT_APPLICABLE,
            reason_text="Commit-ready confirmation pending not entered",
            node_id=CONFIRMATION_ENTER_PENDING_ID,
            depends_on=deps,
            category=ROUTING_CATEGORY,
            skipped=True,
        )
        return

    decision_id = decide(
        "ENTER_CONFIRMATION_PENDING",
        subsystem="planning",
        winner="pending",
        reason_code=CONFIRMATION_ENTER_PENDING,
        reason_text="CREATE_APPOINTMENT commit-ready; confirmation_state set to pending",
        node_id=CONFIRMATION_ENTER_PENDING_ID,
        depends_on=deps,
        category=ROUTING_CATEGORY,
        inputs_evaluated={
            "availability_resolved": availability_resolved,
            "time_selection_ready": time_selection_ready,
        },
    )

    if decision_id:
        emit_mutation(
            decision_id,
            subsystem="session",
            field="confirmation_state",
            previous=previous_state,
            new="pending",
            reason_code=CONFIRMATION_ENTER_PENDING,
            reason_text="Entered booking confirmation pending state",
        )
