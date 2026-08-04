"""Phase 4 architectural Decision Trace emitters (observational only).

Records Current Request, Attached Request, Decision inputs, relationship,
final Decision outputs, and remaining external Decision writers.
"""

from __future__ import annotations

from typing import Optional

from core.planning.pipeline.decision import (
    DecisionInput,
    PLANNER_ADMISSION_BOUNDARIES,
    REMAINING_EXTERNAL_DECISION_WRITERS,
)
from core.planning.pipeline.relationship_evaluator import (
    ConversationContextSnapshot,
    RelationshipEvaluation,
)
from core.planning.pipeline.requests import (
    AttachedRequest,
    CurrentRequest,
    LegacyAttachmentReadReport,
)
from core.planning.pipeline.types import DecisionPlan
from core.tracing.decision_trace import decide, emit_evidence
from core.tracing.reason_codes import (
    ARCH_EXTERNAL_DECISION_WRITERS,
    ARCH_FINAL_DECISION,
    ARCH_RELATIONSHIP_EVALUATION,
)

ARCH_EVIDENCE_CURRENT_REQUEST_ID = "evidence.architecture.current_request"
ARCH_EVIDENCE_ATTACHED_REQUEST_ID = "evidence.architecture.attached_request"
ARCH_EVIDENCE_LEGACY_ATTACHMENT_READS_ID = (
    "evidence.architecture.legacy_attachment_reads"
)
ARCH_EVIDENCE_DECISION_INPUT_ID = "evidence.architecture.decision_input"
ARCH_EVIDENCE_EXTERNAL_WRITERS_ID = "evidence.architecture.external_decision_writers"
ARCH_EVIDENCE_CONVERSATION_CONTEXT_ID = "evidence.architecture.conversation_context"
ARCH_RELATIONSHIP_ID = "decision.architecture.relationship"
ARCH_FINAL_DECISION_ID = "decision.architecture.final_decision"


def emit_architecture_current_request_trace(
    current_request: CurrentRequest,
) -> Optional[str]:
    return emit_evidence(
        "CURRENT_REQUEST",
        subsystem="planning",
        facts={
            "raw_luma_intent": current_request.raw_luma_intent,
            "operation": current_request.operation,
            "has_facts": bool(current_request.facts),
            "has_raw_slots": bool(current_request.raw_slots),
            "has_time_proposal": current_request.time_proposal is not None,
            "has_date_proposal": current_request.date_proposal is not None,
            "confirmation_classification_input": (
                current_request.confirmation_classification_input
            ),
        },
        node_id=ARCH_EVIDENCE_CURRENT_REQUEST_ID,
        source="current_request",
        observed_at_stage="architecture",
    )


def emit_architecture_attached_request_trace(
    attached_request: AttachedRequest,
    *,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    return emit_evidence(
        "ATTACHED_REQUEST",
        subsystem="planning",
        facts={
            "planning_intent": attached_request.planning_intent,
            "turn_operation": attached_request.turn_operation,
            "session_reset_occurred": attached_request.session_reset_occurred,
            "confirm_booking_continuation": (
                attached_request.confirm_booking_continuation
            ),
            "gate_action": (
                attached_request.gate_action.value
                if attached_request.gate_action is not None
                else None
            ),
        },
        node_id=ARCH_EVIDENCE_ATTACHED_REQUEST_ID,
        source="attached_request",
        observed_at_stage="architecture",
        parent_id=parent_id,
    )


def emit_legacy_attachment_reads_trace(
    report: LegacyAttachmentReadReport,
    *,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    return emit_evidence(
        "LEGACY_ATTACHMENT_READS",
        subsystem="planning",
        facts={
            "intent_decision_read_count": report.intent_decision_read_count,
            "payload_attachment_read_count": report.payload_attachment_read_count,
            "duplicated_attachment_field_count": report.duplicated_field_count,
            "bypass_read_count": report.bypass_read_count,
            "total_legacy_read_count": report.total_legacy_read_count,
            "intent_decision_reads": list(report.intent_decision_reads),
            "payload_attachment_projections": list(report.payload_attachment_reads),
            "duplicated_attachment_fields": list(report.duplicated_attachment_fields),
            "attachment_reads_bypassing_attached_request": list(report.bypass_reads),
            "test_only_fixture_sites": list(report.test_only_fixture_sites),
            "observational_only": True,
        },
        node_id=ARCH_EVIDENCE_LEGACY_ATTACHMENT_READS_ID,
        source="legacy_attachment_reads",
        observed_at_stage="architecture",
        parent_id=parent_id,
    )


def emit_decision_input_trace(
    decision_input: DecisionInput,
    *,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    conf = decision_input.confirmation
    reject = decision_input.confirmation_reject
    return emit_evidence(
        "DECISION_INPUT",
        subsystem="planning",
        facts={
            "planning_intent": decision_input.attached_request.planning_intent,
            "turn_operation": decision_input.attached_request.turn_operation,
            "missing_slots": list(decision_input.slot_state.missing_slots),
            "availability_ready": decision_input.availability.availability_ready,
            "confirmation_state": conf.confirmation_state,
            "user_confirmation_satisfied": conf.user_confirmation_satisfied,
            "awaiting_user_confirmation": conf.awaiting_user_confirmation,
            "availability_reshow": conf.availability_reshow,
            "availability_invalidation": bool(
                conf.availability_invalidation
                and getattr(conf.availability_invalidation, "invalidated", False)
            ),
            "bound_datetime_clear": bool(
                conf.bound_datetime_clear
                and getattr(conf.bound_datetime_clear, "cleared", False)
            ),
            "confirmation_reject": bool(reject and reject.rejected),
            "active_capability": decision_input.capability.active_capability,
            "awaiting_capability": decision_input.capability.awaiting_capability,
            "has_relationship": decision_input.relationship is not None,
            "observational_only": True,
        },
        node_id=ARCH_EVIDENCE_DECISION_INPUT_ID,
        source="decision_input",
        observed_at_stage="architecture",
        parent_id=parent_id,
    )


def emit_external_decision_writers_trace(
    *,
    parent_id: Optional[str] = None,
) -> Optional[str]:
    writers = list(REMAINING_EXTERNAL_DECISION_WRITERS)
    admission = list(PLANNER_ADMISSION_BOUNDARIES)
    return emit_evidence(
        "REMAINING_EXTERNAL_DECISION_WRITERS",
        subsystem="planning",
        facts={
            "count": len(writers),
            "remaining_external_decision_writers": writers,
            "planner_admission_boundaries": admission,
            "desired_production_value": "empty",
            "observational_only": True,
            "reason_code": ARCH_EXTERNAL_DECISION_WRITERS,
        },
        node_id=ARCH_EVIDENCE_EXTERNAL_WRITERS_ID,
        source="decision",
        observed_at_stage="architecture",
        parent_id=parent_id,
    )


def emit_architecture_conversation_context_trace(
    context: ConversationContextSnapshot,
) -> Optional[str]:
    return emit_evidence(
        "CONVERSATION_CONTEXT",
        subsystem="planning",
        facts={
            "awaiting": context.awaiting,
            "status": context.status,
            "confirmation_state": context.confirmation_state,
            "missing_slots": list(context.missing_slots),
            "awaiting_slot": context.awaiting_slot,
            "active_capability": context.active_capability,
            "durable_intent": context.durable_intent,
        },
        node_id=ARCH_EVIDENCE_CONVERSATION_CONTEXT_ID,
        source="session_state",
        observed_at_stage="architecture",
    )


def emit_architecture_relationship_trace(
    evaluation: RelationshipEvaluation,
    *,
    depends_on: Optional[list] = None,
) -> Optional[str]:
    deps = [node for node in (depends_on or ()) if node]
    return decide(
        "RELATIONSHIP_EVALUATION",
        subsystem="planning",
        winner=evaluation.resolution.value,
        reason_code=evaluation.reason_code or ARCH_RELATIONSHIP_EVALUATION,
        reason_text=evaluation.reason_text
        or "Observational relationship evaluation (Phase 4; Decision may consume)",
        node_id=ARCH_RELATIONSHIP_ID,
        category="routing",
        depends_on=deps,
        inputs_evaluated={
            "expectation_kind": evaluation.expectation_kind,
            "gate_action": evaluation.gate_action,
            "evidence": evaluation.evidence,
            "observational_only": True,
        },
    )


def emit_architecture_final_decision_trace(
    decision_plan: DecisionPlan,
    *,
    depends_on: Optional[list] = None,
    selected_rule: Optional[str] = None,
) -> Optional[str]:
    plan = decision_plan.plan if isinstance(decision_plan.plan, dict) else {}
    deps = [node for node in (depends_on or ()) if node]
    return decide(
        "FINAL_DECISION",
        subsystem="planning",
        winner=str(plan.get("action") or plan.get("status") or "none"),
        reason_code=ARCH_FINAL_DECISION,
        reason_text=(
            "Sole Decision builder output (Phase 4); action/status/stage/awaiting "
            "owned here"
        ),
        node_id=ARCH_FINAL_DECISION_ID,
        category="routing",
        depends_on=deps,
        inputs_evaluated={
            "intent_name": decision_plan.intent_name,
            "status": plan.get("status"),
            "stage": plan.get("stage"),
            "action": plan.get("action"),
            "awaiting": plan.get("awaiting"),
            "missing_slots": plan.get("missing_slots"),
            "turn_operation": plan.get("turn_operation"),
            "selected_rule": selected_rule,
        },
    )


def emit_phase1_architecture_traces(
    *,
    current_request: CurrentRequest,
    attached_request: AttachedRequest,
    conversation_context: ConversationContextSnapshot,
    relationship: RelationshipEvaluation,
    decision_plan: Optional[DecisionPlan] = None,
    legacy_reads: Optional[LegacyAttachmentReadReport] = None,
    decision_input: Optional[DecisionInput] = None,
) -> None:
    """Emit Phase 4 architectural traces. Safe no-op when tracing is off."""
    try:
        cr_id = emit_architecture_current_request_trace(current_request)
        ar_id = emit_architecture_attached_request_trace(
            attached_request,
            parent_id=cr_id,
        )
        legacy_id = None
        if legacy_reads is not None:
            legacy_id = emit_legacy_attachment_reads_trace(
                legacy_reads,
                parent_id=ar_id,
            )
        di_id = None
        if decision_input is not None:
            di_id = emit_decision_input_trace(
                decision_input,
                parent_id=ar_id,
            )
        writers_id = emit_external_decision_writers_trace(parent_id=ar_id)
        ctx_id = emit_architecture_conversation_context_trace(conversation_context)
        evidence_ids = [
            node
            for node in (cr_id, ar_id, legacy_id, di_id, writers_id, ctx_id)
            if node
        ]
        rel_id = emit_architecture_relationship_trace(
            relationship,
            depends_on=evidence_ids or None,
        )
        if decision_plan is not None:
            final_depends = list(evidence_ids)
            if rel_id:
                final_depends.append(rel_id)
            plan = decision_plan.plan if isinstance(decision_plan.plan, dict) else {}
            selected_rule = None
            if plan.get("status") == "HANDLER_DELEGATED":
                selected_rule = "handler_delegation"
            elif plan.get("status") == "OFF_TOPIC":
                selected_rule = "off_topic_digression"
            emit_architecture_final_decision_trace(
                decision_plan,
                depends_on=final_depends or None,
                selected_rule=selected_rule,
            )
    except Exception:
        # Observational only — never fail the turn on trace errors.
        pass
