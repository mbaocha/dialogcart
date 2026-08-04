"""Immutable request models and Attach-boundary interpretation.

``CurrentRequest`` captures current-turn NLU evidence. ``AttachedRequest``
captures the workflow interpretation produced during Attach. Turn-operation
derivation and residual attachment diagnostics live here because they describe
the same boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Dict, Literal, Mapping, Optional, Tuple

from core.session.confirmation_gate import ConfirmationGateTurn

if TYPE_CHECKING:
    from core.planning.pipeline.types import IntentDecision


TurnOperation = Literal[
    "AVAILABILITY",
    "CHECK_AVAILABILITY",
    "CORRECTION",
    "MODIFY_BOOKING",
    "PROVIDE_SLOT_VALUE",
    "INFORMATIONAL",
    "NONE",
]

_REFINEMENT_RAW = frozenset({"AVAILABILITY", "CHECK_AVAILABILITY", "CORRECTION"})
AVAILABILITY_OPERATIONS = frozenset({"AVAILABILITY", "CHECK_AVAILABILITY"})
_INFORMATIONAL_RAW = frozenset(
    {
        "DETAILS",
        "FAQ",
        "GENERAL_INQUIRY",
        "HELP",
        "QUOTE",
        "RECOMMENDATION",
        "DISCOVERY",
        "OFF_TOPIC",
    }
)


def _freeze_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        return MappingProxyType({})
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class CurrentRequest:
    """Semantic result of the current user turn (immutable, workflow-independent)."""

    source_text: str
    raw_luma_intent: str
    facts: Mapping[str, Any]
    raw_slots: Mapping[str, Any]
    temporal: Mapping[str, Any]
    date_proposal: Any
    time_proposal: Any
    operation: Optional[str]
    """Availability browse operation when present (e.g. browse_next)."""

    confirmation_classification_input: str
    """Raw intent string used as confirmation-gate classification input."""

    raw_luma_response: Mapping[str, Any]
    """Frozen shallow copy of the original Luma response for this turn."""


def build_current_request(
    luma_response: Optional[Mapping[str, Any]],
    *,
    source_text: str = "",
) -> CurrentRequest:
    """Build CurrentRequest from the raw NLU response only."""
    from core.planning.temporal_contract import ensure_temporal, get_temporal

    raw = dict(luma_response) if isinstance(luma_response, dict) else {}
    raw = ensure_temporal(raw)
    temporal = get_temporal(raw)

    intent_obj = raw.get("intent", {})
    if isinstance(intent_obj, dict):
        raw_intent = str(intent_obj.get("name") or "")
    elif isinstance(intent_obj, str):
        raw_intent = intent_obj
    else:
        raw_intent = ""

    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}

    operation = raw.get("operation")
    if not isinstance(operation, str) or not operation:
        if isinstance(facts, dict) and isinstance(facts.get("operation"), str):
            operation = facts.get("operation")
        else:
            operation = None

    date_proposal = raw.get("date_proposal")
    time_proposal = raw.get("time_proposal")
    try:
        from core.planning.temporal_proposal import extract_nlu_proposals

        proposals = extract_nlu_proposals(raw)
        if date_proposal is None:
            date_proposal = proposals.get("date_proposal")
        if time_proposal is None:
            time_proposal = proposals.get("time_proposal")
    except Exception:
        pass

    return CurrentRequest(
        source_text=source_text or "",
        raw_luma_intent=raw_intent,
        facts=_freeze_mapping(facts),
        raw_slots=_freeze_mapping(slots),
        temporal=_freeze_mapping(temporal),
        date_proposal=date_proposal,
        time_proposal=time_proposal,
        operation=operation,
        confirmation_classification_input=raw_intent,
        raw_luma_response=_freeze_mapping(raw),
    )


def is_availability_turn_operation(turn_operation: Optional[str] = None) -> bool:
    """True when the current turn is an explicit availability operation."""
    return (turn_operation or "") in AVAILABILITY_OPERATIONS


def _has_slot_evidence(luma_response: Dict[str, Any]) -> bool:
    slots = luma_response.get("slots") or {}
    facts = luma_response.get("facts") or {}
    temporal = luma_response.get("temporal") or {}
    if isinstance(slots, dict) and any(value is not None for value in slots.values()):
        return True
    if isinstance(facts, dict) and facts:
        return True
    if isinstance(temporal, dict) and any(
        temporal.get(k)
        for k in (
            "start_date",
            "start_time",
            "start_date_expression",
            "start_time_expression",
        )
    ):
        return True
    return False


def derive_turn_operation(
    *,
    raw_luma_intent: str,
    planning_intent: str,
    luma_response: Dict[str, Any],
) -> TurnOperation:
    """Map raw NLU intent to the typed operation for this turn."""
    raw = (raw_luma_intent or "").strip()
    if raw in _REFINEMENT_RAW:
        return raw  # type: ignore[return-value]
    if raw in ("MODIFY_BOOKING", "MODIFY_RESERVATION"):
        return "MODIFY_BOOKING"
    if raw in _INFORMATIONAL_RAW:
        return "INFORMATIONAL"
    if raw and raw == planning_intent and _has_slot_evidence(luma_response):
        return "PROVIDE_SLOT_VALUE"
    if raw in ("", "UNKNOWN") and planning_intent and _has_slot_evidence(luma_response):
        return "PROVIDE_SLOT_VALUE"
    return "NONE"


@dataclass(frozen=True)
class AttachedRequest:
    """Current turn after Attach — workflow context applied to NLU evidence."""

    planning_intent: str
    turn_operation: TurnOperation
    session_reset_occurred: bool
    confirm_booking_continuation: bool = False
    gate_action: Optional[ConfirmationGateTurn] = None


def build_attached_request(intent_decision: "IntentDecision") -> AttachedRequest:
    """Project workflow attachment fields from ``IntentDecision`` (single builder)."""
    return AttachedRequest(
        planning_intent=intent_decision.planning_intent or "",
        turn_operation=intent_decision.turn_operation,
        session_reset_occurred=intent_decision.session_reset_occurred,
        confirm_booking_continuation=intent_decision.confirm_booking_continuation,
        gate_action=intent_decision.gate_action,
    )


LEGACY_INTENT_DECISION_READ_SITES: Tuple[str, ...] = (
    "decide_handler_delegation: IntentDecision non-durable / OFF_TOPIC / RAG fields",
    "build_attached_request: single projection IntentDecision → AttachedRequest",
)

REMAINING_PAYLOAD_ATTACHMENT_PROJECTIONS: Tuple[str, ...] = ()

REMAINING_DUPLICATED_ATTACHMENT_FIELDS: Tuple[str, ...] = (
    "IntentDecision attachment fields — Stage 01 reinterpretation; projected once "
    "into AttachedRequest",
)

ATTACHMENT_READS_BYPASSING_ATTACHED_REQUEST: Tuple[str, ...] = ()

TEST_ONLY_ATTACHMENT_FIXTURE_SITES: Tuple[str, ...] = (
    "tests.harness.planning_compat: rebuilds AttachedRequest from optional "
    "fixture payload keys for isolated stage tests",
)


@dataclass(frozen=True)
class LegacyAttachmentReadReport:
    """Diagnostic snapshot after attachment-boundary consolidation."""

    intent_decision_reads: Tuple[str, ...] = LEGACY_INTENT_DECISION_READ_SITES
    payload_attachment_reads: Tuple[str, ...] = REMAINING_PAYLOAD_ATTACHMENT_PROJECTIONS
    duplicated_attachment_fields: Tuple[str, ...] = (
        REMAINING_DUPLICATED_ATTACHMENT_FIELDS
    )
    bypass_reads: Tuple[str, ...] = ATTACHMENT_READS_BYPASSING_ATTACHED_REQUEST
    test_only_fixture_sites: Tuple[str, ...] = TEST_ONLY_ATTACHMENT_FIXTURE_SITES

    @property
    def intent_decision_read_count(self) -> int:
        return len(self.intent_decision_reads)

    @property
    def payload_attachment_read_count(self) -> int:
        return len(self.payload_attachment_reads)

    @property
    def duplicated_field_count(self) -> int:
        return len(self.duplicated_attachment_fields)

    @property
    def bypass_read_count(self) -> int:
        return len(self.bypass_reads)

    @property
    def total_legacy_read_count(self) -> int:
        return (
            self.intent_decision_read_count
            + self.payload_attachment_read_count
            + self.duplicated_field_count
            + self.bypass_read_count
        )


def build_legacy_attachment_read_report() -> LegacyAttachmentReadReport:
    return LegacyAttachmentReadReport()
