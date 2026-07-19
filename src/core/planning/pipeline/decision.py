"""Cohesive planner Decision surface.

``decide()`` selects action, status, stage, awaiting, and related presentation
fields for a planning turn from ``DecisionInput`` evidence.

Post-execution / time-resolution completion uses
``finalize_decision_after_time_resolution`` (Decision finalization).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.planning.pipeline.relationship_evaluator import RelationshipEvaluation
from core.planning.pipeline.requests import AttachedRequest, CurrentRequest
from core.planning.pipeline.stage08_decision_plan import (
    build_decision_plan_from_evidence,
)
from core.planning.pipeline.types import (
    AvailabilityDecision,
    CapabilityDecision,
    ConfirmationDecision,
    DecisionPlan,
    IntentDecision,
    SlotTurnState,
    WorkingTurn,
)


@dataclass(frozen=True)
class MissingSlotsEvidence:
    """Slot-completeness evidence (Evaluate)."""

    missing_slots: Tuple[str, ...] = ()
    needs_clarification: bool = False
    clarification_reason: Optional[str] = None
    slot_completeness_status: str = "READY"
    """Legacy turn_state status — evidence only; Decision must not inherit blindly."""


@dataclass(frozen=True)
class ConfirmationRejectEvidence:
    """Gate NO — rejection evidence for Decision (not a pre-built outcome)."""

    rejected: bool = False
    intent_name: str = ""
    slots: Mapping[str, Any] = field(default_factory=dict)
    missing_slots: Tuple[str, ...] = ()
    reject_text: Optional[str] = None


@dataclass(frozen=True)
class AvailabilityInvalidationEvidence:
    """Evaluate evidence: prior availability trust must not be reused this turn.

    Produced by Stage 06 when an explicit AVAILABILITY operation supersedes
    pending confirmation. Consumed when assembling DecisionInput for ``decide()``.
    """

    invalidated: bool = False
    reason_code: str = "AVAILABILITY_SUPERSEDES_PENDING_CONFIRMATION"


@dataclass(frozen=True)
class BoundDatetimeClearEvidence:
    """Evaluate evidence: prior bound datetime must be ignored this turn."""

    cleared: bool = False
    reason_code: str = "BOUND_DATETIME_CLEARED"


@dataclass(frozen=True)
class DecisionInput:
    """Immutable evidence bundle for the sole Decision builder.

    Must not contain final action, status, stage, or awaiting values.
    """

    attached_request: AttachedRequest
    working_turn: WorkingTurn
    slot_state: SlotTurnState
    availability: AvailabilityDecision
    confirmation: ConfirmationDecision
    capability: CapabilityDecision
    session_state: Optional[Dict[str, Any]]
    organization_id: int
    current_request: Optional[CurrentRequest] = None
    relationship: Optional[RelationshipEvaluation] = None
    confirmation_reject: Optional[ConfirmationRejectEvidence] = None
    intent_decision: Optional[IntentDecision] = None
    """Retained for handler-delegation early Decision only."""


def missing_slots_evidence_from_slot_state(
    slot_state: SlotTurnState,
) -> MissingSlotsEvidence:
    return MissingSlotsEvidence(
        missing_slots=tuple(slot_state.missing_slots or ()),
        needs_clarification=slot_state.needs_clarification,
        clarification_reason=slot_state.clarification_reason,
        slot_completeness_status=slot_state.base_status,
    )


def apply_confirmation_evidence_to_availability(
    availability: AvailabilityDecision,
    confirmation: ConfirmationDecision,
) -> AvailabilityDecision:
    """Project Stage 06 confirmation evidence onto AvailabilityDecision."""
    inv = confirmation.availability_invalidation
    if inv is not None and inv.invalidated:
        return AvailabilityDecision(
            availability_ready=False,
            stored_fingerprint=availability.stored_fingerprint,
            current_fingerprint=availability.current_fingerprint,
        )
    cleared = confirmation.bound_datetime_clear
    if cleared is None or not cleared.cleared:
        return availability
    if availability.stored_fingerprint and (
        availability.stored_fingerprint == availability.current_fingerprint
    ):
        return availability
    return AvailabilityDecision(
        availability_ready=False,
        stored_fingerprint=availability.stored_fingerprint,
        current_fingerprint=availability.current_fingerprint,
    )


def decide(decision_input: DecisionInput) -> DecisionPlan:
    """Select the final planner Decision for this turn."""
    reject = decision_input.confirmation_reject
    if reject is not None and reject.rejected:
        return _decide_confirmation_reject(reject)

    availability = apply_confirmation_evidence_to_availability(
        decision_input.availability,
        decision_input.confirmation,
    )
    return build_decision_plan_from_evidence(
        attached_request=decision_input.attached_request,
        working_turn=decision_input.working_turn,
        slot_state=decision_input.slot_state,
        availability=availability,
        confirmation=decision_input.confirmation,
        capability=decision_input.capability,
        session_state=decision_input.session_state,
        organization_id=decision_input.organization_id,
    )


def decide_handler_delegation(intent_decision: IntentDecision) -> DecisionPlan:
    """Decision for non-durable / handler-delegated Attach early exit."""
    intent_name = intent_decision.planning_intent or ""
    slots = dict(intent_decision.delegated_slots or {})
    if intent_decision.handler_delegated:
        status = "HANDLER_DELEGATED"
    else:
        status = intent_decision.non_durable_status or "NON_DURABLE_INTENT"
    plan: Dict[str, Any] = {
        "status": status,
        "stage": None,
        "action": None,
        "awaiting": None,
        "missing_slots": [],
        "allowed_actions": [],
        "blocked_actions": [],
        "executable_actions": [],
    }
    if intent_decision.handler_delegated:
        plan["active_handler"] = intent_decision.handler_name
        plan["search_query"] = intent_decision.delegated_search_query
    return DecisionPlan(
        plan=plan,
        facts={"slots": slots, "missing_slots": []},
        intent_name=intent_name,
        booking={},
    )


def _decide_confirmation_reject(reject: ConfirmationRejectEvidence) -> DecisionPlan:
    """Map confirmation-reject evidence to the sole Decision outcome."""
    slots = dict(reject.slots) if isinstance(reject.slots, dict) else {}
    missing = list(reject.missing_slots)
    plan: Dict[str, Any] = {
        "status": "NEEDS_CLARIFICATION",
        "stage": "AVAILABILITY",
        "action": None,
        "awaiting": None,
        "missing_slots": missing,
        "allowed_actions": [],
        "blocked_actions": [],
        "executable_actions": [],
    }
    return DecisionPlan(
        plan=plan,
        facts={"slots": slots, "missing_slots": missing},
        intent_name=reject.intent_name,
        booking={},
    )


# Intentional non-Decision admission boundary only (not a Decision writer).
PLANNER_ADMISSION_BOUNDARIES: tuple = (
    "nlu_failure_fallback.build_nlu_failure_fallback — planner admission "
    "boundary when NLU cannot process the turn (before Attach/Decision)",
)

# Production Decision writers outside decide()/finalize should be empty.
REMAINING_EXTERNAL_DECISION_WRITERS: tuple = ()
