"""Availability fingerprint decision trace emitters (observational only)."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.tracing.decision_trace import Candidate, IgnoredInput, TurnTrace, decide, emit_evidence
from core.tracing.reason_codes import (
    BOUND_DATETIME_BYPASS,
    CONFIRM_CONTINUATION_BYPASS,
    FINGERPRINT_MATCH,
    FINGERPRINT_MISMATCH,
    INPUT_IGNORED_NOT_APPLICABLE,
)

FINGERPRINT_EVIDENCE_SLOTS_ID = "evidence.fingerprint.slots"
FINGERPRINT_EVIDENCE_COMPUTED_ID = "evidence.fingerprint.computed"
FINGERPRINT_EVIDENCE_STORED_ID = "evidence.fingerprint.stored"
FINGERPRINT_TRUST_ID = "decision.fingerprint.trust"

FINGERPRINT_NODE_IDS = (FINGERPRINT_TRUST_ID,)

INFERENCE_CATEGORY = "inference"


def fingerprint_dependencies() -> List[str]:
    trace = TurnTrace.current()
    if trace is None:
        return []
    return [node_id for node_id in FINGERPRINT_NODE_IDS if trace.has_record(node_id)]


def emit_fingerprint_trace(
    *,
    fingerprint_slots: Mapping[str, Any],
    stored_fingerprint: Optional[str],
    current_fingerprint: Optional[str],
    availability_resolved: bool,
    intent_name: str = "",
    has_bound_datetime: bool = False,
    confirm_continuation: bool = False,
    continuation_bypass: bool = False,
    matched: bool = False,
) -> None:
    """Emit fingerprint evidence and trust decision."""
    trace = TurnTrace.current()
    if trace is None:
        return
    if trace.has_record(FINGERPRINT_TRUST_ID):
        return

    slots_id = emit_evidence(
        "FINGERPRINT_SLOTS",
        subsystem="orchestration",
        facts={
            "criteria_slot_keys": sorted(fingerprint_slots.keys()),
            "criteria_slots": dict(fingerprint_slots),
            "intent_name": intent_name,
            "ignored_fields": ["time", "time_proposal", "page_index", "presented_availability"],
        },
        node_id=FINGERPRINT_EVIDENCE_SLOTS_ID,
        source="build_availability_fingerprint_slots",
        observed_at_stage="fingerprint",
    )

    computed_id = emit_evidence(
        "FINGERPRINT_COMPUTED",
        subsystem="orchestration",
        facts={
            "fingerprint_hash": (current_fingerprint or "")[:16] if current_fingerprint else None,
            "fingerprint_full": current_fingerprint,
            "skipped": current_fingerprint is None,
        },
        node_id=FINGERPRINT_EVIDENCE_COMPUTED_ID,
        source="compute_availability_fingerprint",
        observed_at_stage="fingerprint",
    )

    stored_id = emit_evidence(
        "FINGERPRINT_STORED",
        subsystem="session",
        facts={
            "fingerprint_hash": (stored_fingerprint or "")[:16] if stored_fingerprint else None,
            "fingerprint_full": stored_fingerprint,
            "present": stored_fingerprint is not None,
        },
        node_id=FINGERPRINT_EVIDENCE_STORED_ID,
        source="session_state",
        observed_at_stage="fingerprint",
    )

    deps = [dep for dep in (slots_id, computed_id, stored_id) if dep]

    if has_bound_datetime:
        winner = "bound_datetime"
        reason_code = BOUND_DATETIME_BYPASS
        reason_text = "Bound booking datetime bypasses fingerprint comparison"
    elif continuation_bypass:
        winner = "confirm_continuation"
        reason_code = CONFIRM_CONTINUATION_BYPASS
        reason_text = "Confirm-booking continuation bypasses fingerprint mismatch"
    elif matched:
        winner = "trusted"
        reason_code = FINGERPRINT_MATCH
        reason_text = "Stored fingerprint matches current search criteria"
    elif availability_resolved:
        winner = "trusted"
        reason_code = FINGERPRINT_MATCH
        reason_text = "Availability marked resolved"
    else:
        winner = "stale"
        reason_code = FINGERPRINT_MISMATCH
        reason_text = "Stored fingerprint missing or does not match current criteria"

    candidates = [
        Candidate(
            id="bound_datetime",
            matched=has_bound_datetime,
            reason_code=BOUND_DATETIME_BYPASS,
            reason_text="Datetime already bound on slots/session",
        ),
        Candidate(
            id="fingerprint_match",
            matched=matched,
            reason_code=FINGERPRINT_MATCH if matched else FINGERPRINT_MISMATCH,
            reason_text="Search-criteria fingerprint comparison",
        ),
        Candidate(
            id="confirm_continuation",
            matched=continuation_bypass,
            reason_code=CONFIRM_CONTINUATION_BYPASS,
            reason_text="User confirmed after availability search",
        ),
    ]

    changed_fields: List[str] = []
    if stored_fingerprint and current_fingerprint and stored_fingerprint != current_fingerprint:
        for key in set(fingerprint_slots.keys()):
            changed_fields.append(key)

    decide(
        "FINGERPRINT_TRUST",
        subsystem="orchestration",
        winner=winner,
        reason_code=reason_code,
        reason_text=reason_text,
        node_id=FINGERPRINT_TRUST_ID,
        depends_on=deps,
        candidates=candidates,
        category=INFERENCE_CATEGORY,
        inputs_evaluated={
            "availability_resolved": availability_resolved,
            "has_bound_datetime": has_bound_datetime,
            "confirm_continuation": confirm_continuation,
            "matched": matched,
            "changed_fields": changed_fields,
        },
        inputs_ignored={
            "slots.time": IgnoredInput(
                reason_code=INPUT_IGNORED_NOT_APPLICABLE,
                reason_text="Time selection excluded from fingerprint criteria",
            )
        },
    )
