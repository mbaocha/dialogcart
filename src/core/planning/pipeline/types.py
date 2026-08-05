"""Typed outputs for the canonical planning pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.planning.booking_revision import BookingRevision
from core.planning.pipeline.requests import TurnOperation
from core.session.confirmation_gate import ConfirmationGateTurn


@dataclass(frozen=True)
class IntentDecision:
    """Stage 01 — reconciled intent and early-exit signals."""

    planning_intent: str
    raw_luma_intent: str
    turn_operation: TurnOperation
    session_reset_occurred: bool
    confirm_booking_continuation: bool = False
    gate_action: Optional[ConfirmationGateTurn] = None
    handler_delegated: bool = False
    handler_name: Optional[str] = None
    non_durable_status: Optional[str] = None
    delegated_search_query: Optional[str] = None
    # Opaque OFF_TOPIC evidence from NLU — planner must not interpret answer semantics.
    off_topic_query: Optional[str] = None
    off_topic_answerable: Optional[bool] = None
    off_topic_answer: Optional[str] = None
    delegated_slots: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkingTurn:
    """Stage 02 — authoritative per-turn working payload.

    Attachment fields (planning_intent, turn_operation, gate, continuation)
    live on ``AttachedRequest`` — not duplicated here.
    """

    payload: Dict[str, Any]
    effective_collected_slots: Dict[str, Any] = field(default_factory=dict)
    raw_luma_response_deep_copy: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RevisionResult:
    """Stage 03 — booking revision detection."""

    revision: BookingRevision
    revision_summary: Optional[str] = None


@dataclass(frozen=True)
class SlotTurnState:
    """Stage 04 — canonical missing slots and clarification semantics."""

    intent_name: str
    missing_slots: List[str]
    effective_collected_slots: Dict[str, Any]
    base_status: str
    ask_next: Optional[str] = None
    promptable_slots: List[str] = field(default_factory=list)
    declined_slots: List[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_reason: Optional[str] = None
    clarification_data: Optional[Dict[str, Any]] = None
    clarification_issues: Dict[str, Any] = field(default_factory=dict)
    clarification_context: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AvailabilityDecision:
    """Stage 05 — availability trust for current booking parameters."""

    availability_ready: bool
    stored_fingerprint: Optional[str] = None
    current_fingerprint: Optional[str] = None


@dataclass
class ConfirmationDecision:
    """Stage 06 — confirmation authorization evidence (not final outcomes)."""

    confirmation_state: Optional[str] = None
    user_confirmation_satisfied: bool = False
    awaiting_user_confirmation: bool = False
    reject_evidence: Optional[Any] = None
    """ConfirmationRejectEvidence when gate NO; Decision maps to outcome."""
    lifecycle_evidence: Optional[Any] = None
    """ConfirmationLifecycleEvidence — consume/reject/supersede mutation request."""
    consume_evidence: Optional[Any] = None
    """ConfirmationConsumeEvidence — supersede/consume pending authorization."""
    availability_reshow: bool = False
    slots_adjusted: bool = False
    availability_invalidation: Optional[Any] = None
    """AvailabilityInvalidationEvidence when trust must not be reused this turn."""
    bound_datetime_clear: Optional[Any] = None
    """BoundDatetimeClearEvidence when prior selected time was cleared this turn."""


@dataclass(frozen=True)
class CapabilityDecision:
    """Stage 07 — capability gating (planning-only; execution is external)."""

    active_capability: Optional[str] = None
    awaiting_capability: bool = False
    awaiting_kind: Optional[str] = None


@dataclass(frozen=True)
class DecisionPlan:
    """Stage 08 — policy-selected plan."""

    plan: Dict[str, Any]
    facts: Dict[str, Any]
    intent_name: str
    action_name: Optional[str] = None
    booking: Dict[str, Any] = field(default_factory=dict)
    candidate_evidence: List[Dict[str, Any]] = field(default_factory=list)
    policy_client: Optional[str] = None
    service_candidates: List[Any] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowRoute:
    """Workflow route derived inline after Stage 08."""

    route: Optional[str]
    client_name: Optional[str]


@dataclass(frozen=True)
class PlanningOutcome:
    """Stage 09 — stable planning contract for engine / projector."""

    success: bool
    outcome: Dict[str, Any]
    merged_luma_response: Optional[Dict[str, Any]] = None
    decision: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_turn_result(self) -> Dict[str, Any]:
        """Legacy turn envelope consumed by ConversationEngine."""
        if not self.success:
            return {
                "success": False,
                "error": self.error or "planning_failed",
                "message": self.message or "Planning failed",
            }
        result: Dict[str, Any] = {
            "success": True,
            "outcome": self.outcome,
        }
        if self.text is not None:
            result["text"] = self.text
        if self.merged_luma_response is not None:
            result["_merged_luma_response"] = self.merged_luma_response
        if self.decision is not None:
            result["_decision"] = self.decision
        return result
