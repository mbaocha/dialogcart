"""Observational Relationship Evaluator (Phase 1 architectural boundary).

Answers only: does the Current Request satisfy what the previous Decision was
waiting for?

Phase 1 rules:
- Pure / mutation-free.
- May delegate to existing confirmation-gate classification.
- Result is observational only — must not influence Decision or any stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from core.planning.pipeline.requests import CurrentRequest
from core.session.confirmation_gate import (
    ConfirmationGateTurn,
    get_confirmation_state,
    is_confirmation_gate_open,
)


class ExpectationResolution(str, Enum):
    """Relationship between Current Request and prior Decision ask."""

    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    NO_PENDING_EXPECTATION = "NO_PENDING_EXPECTATION"


@dataclass(frozen=True)
class ConversationContextSnapshot:
    """Previous Decision outputs visible in session (read-only snapshot)."""

    awaiting: Optional[str] = None
    status: Optional[str] = None
    confirmation_state: Optional[str] = None
    missing_slots: tuple = ()
    awaiting_slot: Optional[str] = None
    active_capability: Optional[str] = None
    durable_intent: str = ""


@dataclass(frozen=True)
class RelationshipEvaluation:
    """Observational result of Current Request vs prior Conversation Context."""

    resolution: ExpectationResolution
    expectation_kind: Optional[str] = None
    reason_code: str = ""
    reason_text: str = ""
    gate_action: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


def snapshot_conversation_context(
    session_state: Optional[Mapping[str, Any]],
) -> ConversationContextSnapshot:
    """Read prior Decision projections from session without mutation."""
    if not isinstance(session_state, dict):
        return ConversationContextSnapshot()

    intent = session_state.get("intent_name") or session_state.get("intent") or ""
    if isinstance(intent, dict):
        intent = intent.get("name") or ""

    missing = session_state.get("missing_slots")
    if not isinstance(missing, list):
        missing = []

    return ConversationContextSnapshot(
        awaiting=session_state.get("awaiting"),
        status=str(session_state.get("status") or "") or None,
        confirmation_state=get_confirmation_state(session_state),
        missing_slots=tuple(missing),
        awaiting_slot=session_state.get("awaiting_slot"),
        active_capability=session_state.get("active_capability"),
        durable_intent=str(intent) if intent else "",
    )


def evaluate_relationship(
    *,
    current_request: CurrentRequest,
    conversation_context: ConversationContextSnapshot,
    session_state: Optional[Mapping[str, Any]] = None,
    gate_action: Optional[ConfirmationGateTurn] = None,
) -> RelationshipEvaluation:
    """Evaluate whether Current Request satisfies the prior Decision ask.

    Delegates confirmation waits to the existing confirmation gate classification.
    Other waits are recorded observationally without claiming new satisfaction
    rules beyond gate delegation and simple pending-projection presence.
    """
    # Prefer caller-supplied gate_action (already computed this turn); else classify.
    resolved_gate = gate_action
    if resolved_gate is None and isinstance(session_state, dict):
        from core.session.confirmation_gate import classify_confirmation_gate_turn

        resolved_gate = classify_confirmation_gate_turn(
            dict(current_request.raw_luma_response),
            dict(session_state),
        )

    gate_value = resolved_gate.value if resolved_gate is not None else None

    if is_confirmation_gate_open(session_state) or (
        conversation_context.confirmation_state == "pending"
    ):
        if resolved_gate == ConfirmationGateTurn.YES:
            return RelationshipEvaluation(
                resolution=ExpectationResolution.SATISFIED,
                expectation_kind="USER_CONFIRMATION",
                reason_code="RELATIONSHIP_CONFIRMATION_YES",
                reason_text="Current request is CONFIRM_ACTION while confirmation is pending",
                gate_action=gate_value,
                evidence={"raw_luma_intent": current_request.raw_luma_intent},
            )
        if resolved_gate == ConfirmationGateTurn.NO:
            return RelationshipEvaluation(
                resolution=ExpectationResolution.SATISFIED,
                expectation_kind="USER_CONFIRMATION",
                reason_code="RELATIONSHIP_CONFIRMATION_NO",
                reason_text="Current request is REJECT_ACTION while confirmation is pending",
                gate_action=gate_value,
                evidence={"raw_luma_intent": current_request.raw_luma_intent},
            )
        return RelationshipEvaluation(
            resolution=ExpectationResolution.NOT_SATISFIED,
            expectation_kind="USER_CONFIRMATION",
            reason_code="RELATIONSHIP_CONFIRMATION_ANOTHER_REQUEST",
            reason_text="Current request does not answer the pending confirmation",
            gate_action=gate_value or ConfirmationGateTurn.ANOTHER_REQUEST.value,
            evidence={"raw_luma_intent": current_request.raw_luma_intent},
        )

    if conversation_context.active_capability:
        return RelationshipEvaluation(
            resolution=ExpectationResolution.NOT_SATISFIED,
            expectation_kind="CAPABILITY",
            reason_code="RELATIONSHIP_CAPABILITY_PENDING_OBSERVATIONAL",
            reason_text=(
                "Prior Decision left an active capability; Phase 1 does not "
                "evaluate capability completion (observational only)"
            ),
            gate_action=gate_value,
            evidence={
                "active_capability": conversation_context.active_capability,
            },
        )

    awaiting = conversation_context.awaiting
    if awaiting == "TIME_SELECTION" or conversation_context.awaiting_slot == "time":
        return RelationshipEvaluation(
            resolution=ExpectationResolution.NOT_SATISFIED,
            expectation_kind="TIME_SELECTION",
            reason_code="RELATIONSHIP_SLOT_WAIT_OBSERVATIONAL",
            reason_text=(
                "Prior Decision was waiting for time selection; Phase 1 records "
                "the wait without applying a new satisfaction rule"
            ),
            gate_action=gate_value,
            evidence={
                "awaiting": awaiting,
                "awaiting_slot": conversation_context.awaiting_slot,
                "has_time_proposal": current_request.time_proposal is not None,
            },
        )

    if conversation_context.missing_slots or conversation_context.awaiting_slot:
        kind = conversation_context.awaiting_slot or (
            conversation_context.missing_slots[0]
            if conversation_context.missing_slots
            else "SLOT"
        )
        return RelationshipEvaluation(
            resolution=ExpectationResolution.NOT_SATISFIED,
            expectation_kind=str(kind),
            reason_code="RELATIONSHIP_SLOT_WAIT_OBSERVATIONAL",
            reason_text=(
                "Prior Decision left missing slots; Phase 1 records the wait "
                "without applying a new satisfaction rule"
            ),
            gate_action=gate_value,
            evidence={
                "missing_slots": list(conversation_context.missing_slots),
                "awaiting_slot": conversation_context.awaiting_slot,
                "raw_luma_intent": current_request.raw_luma_intent,
            },
        )

    if awaiting == "USER_CONFIRMATION":
        return RelationshipEvaluation(
            resolution=ExpectationResolution.NOT_SATISFIED,
            expectation_kind="USER_CONFIRMATION",
            reason_code="RELATIONSHIP_AWAITING_CONFIRMATION_WITHOUT_PENDING",
            reason_text=(
                "Session awaiting USER_CONFIRMATION without confirmation_state=pending"
            ),
            gate_action=gate_value,
            evidence={"awaiting": awaiting},
        )

    return RelationshipEvaluation(
        resolution=ExpectationResolution.NO_PENDING_EXPECTATION,
        expectation_kind=None,
        reason_code="RELATIONSHIP_NO_PENDING",
        reason_text="No prior Decision ask detected in conversation context",
        gate_action=gate_value,
        evidence={
            "status": conversation_context.status,
            "durable_intent": conversation_context.durable_intent,
        },
    )
