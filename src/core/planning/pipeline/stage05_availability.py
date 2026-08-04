"""Stage 05 — availability readiness."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.planning.facts import evaluate_availability_evidence_ready
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.types import AvailabilityDecision, SlotTurnState, WorkingTurn
from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
    slots_match_availability_fingerprint_for_readiness,
)


def resolve_availability(
    *,
    slot_state: SlotTurnState,
    working_turn: WorkingTurn,
    session_state: Optional[Dict[str, Any]],
    organization_id: int,
    attached_request: AttachedRequest,
) -> AvailabilityDecision:
    payload = working_turn.payload
    intent_name = slot_state.intent_name
    current_slots = slot_state.effective_collected_slots
    confirm_booking_continuation = attached_request.confirm_booking_continuation

    stored_fingerprint = None
    if isinstance(session_state, dict) and not payload.get(
        "_revision_invalidated_availability"
    ):
        stored_fingerprint = session_state.get("availability_fingerprint")

    availability_ready = evaluate_availability_evidence_ready(
        intent_name=intent_name or "",
        slots=current_slots,
        session_state=(
            None
            if payload.get("_revision_invalidated_availability")
            else session_state
        ),
        luma_response=payload,
        organization_id=organization_id,
        confirm_booking_continuation=confirm_booking_continuation,
    )

    if confirm_booking_continuation:
        stored_range = (
            session_state.get("resolved_datetime_range")
            if isinstance(session_state, dict)
            else None
        )
        if stored_fingerprint:
            availability_ready = True
        elif isinstance(stored_range, dict) and stored_range.get("start"):
            availability_ready = True
        elif (
            intent_name in ("MODIFY_BOOKING", "MODIFY_RESERVATION")
            and isinstance(session_state, dict)
            and session_state.get("status") == "READY"
        ):
            availability_ready = True

    facts_obj = payload.get("facts", {})
    fingerprint_slots = build_availability_fingerprint_slots(
        current_slots,
        intent_name=intent_name,
        organization_id=organization_id,
        luma_response=payload,
        session_state=session_state,
        nlu_facts=facts_obj if isinstance(facts_obj, dict) else None,
    )
    current_fingerprint = (
        compute_availability_fingerprint(fingerprint_slots, intent_name=intent_name)
        if fingerprint_slots
        else None
    )

    fingerprint_matched = slots_match_availability_fingerprint_for_readiness(
        fingerprint_slots,
        stored_fingerprint,
        intent_name=intent_name,
        session_state=(
            None
            if payload.get("_revision_invalidated_availability")
            else session_state
        ),
    )
    continuation_bypass = bool(
        confirm_booking_continuation
        and availability_ready
        and not fingerprint_matched
    )
    try:
        from core.planning.temporal_proposal import has_bound_booking_datetime
        from core.tracing.fingerprint import emit_fingerprint_trace

        emit_fingerprint_trace(
            fingerprint_slots=fingerprint_slots or {},
            stored_fingerprint=stored_fingerprint,
            current_fingerprint=current_fingerprint,
            availability_resolved=availability_ready,
            intent_name=intent_name or "",
            has_bound_datetime=has_bound_booking_datetime(
                current_slots, session_state, payload
            ),
            confirm_continuation=confirm_booking_continuation,
            continuation_bypass=continuation_bypass,
            matched=fingerprint_matched,
        )
    except ImportError:
        pass

    return AvailabilityDecision(
        availability_ready=availability_ready,
        stored_fingerprint=stored_fingerprint,
        current_fingerprint=current_fingerprint,
    )
