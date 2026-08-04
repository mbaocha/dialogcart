"""Parity: execution-readiness evidence matches Stage 08 readiness construction."""

from __future__ import annotations

from core.planning.pipeline.decision import (
    AvailabilityInvalidationEvidence,
    BoundDatetimeClearEvidence,
)
from core.planning.pipeline.execution_readiness import (
    build_execution_readiness_evidence,
)


def _base_slots():
    return {
        "service_id": "premium haircut",
        "date": "2026-07-06",
        "time": "10:00",
    }


def test_readiness_flags_overlay_availability_invalidation():
    evidence = build_execution_readiness_evidence(
        intent_name="CREATE_APPOINTMENT",
        effective_slots=_base_slots(),
        payload={"_current_turn_has_time": False},
        session_state={"slots": _base_slots()},
        missing_slots=[],
        needs_clarification=False,
        availability_ready=True,
        confirmation_state="pending",
        organization_id=1,
        confirm_booking_continuation=False,
        availability_invalidation=AvailabilityInvalidationEvidence(
            invalidated=True,
            reason_code="AVAILABILITY_SUPERSEDES_PENDING_CONFIRMATION",
        ),
    )
    assert evidence.availability_invalidated is True
    assert evidence.availability_resolved is False
    assert evidence.flags.get("availability_ready") is False
    assert evidence.flags.get("availability_resolved") is False
    assert evidence.flags.get("availability_check_required") is True
    assert evidence.execution_proposal_context["availability_invalidated"] is True
    assert evidence.execution_proposal_context["session_time_proposal_reuse_allowed"] is False


def test_readiness_bound_clear_without_preserve_suppresses_current_turn_time():
    payload = {
        "_current_turn_has_time": True,
        "_current_turn_time": "10:00",
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "temporal": {"mode": "none", "start_time": "10:00"},
        # After planning mutation, resolved range is already cleared on the
        # working payload; only session may still hold the prior bind.
    }
    evidence = build_execution_readiness_evidence(
        intent_name="CREATE_APPOINTMENT",
        effective_slots={"service_id": "premium haircut", "date": "2026-07-06"},
        payload=payload,
        session_state={"slots": _base_slots()},
        missing_slots=["time"],
        needs_clarification=False,
        availability_ready=True,
        confirmation_state=None,
        organization_id=1,
        confirm_booking_continuation=False,
        bound_datetime_clear=BoundDatetimeClearEvidence(
            cleared=True,
            reason_code="BOUND_DATETIME_CLEARED",
            preserve_current_turn_time=False,
        ),
    )
    assert evidence.bound_datetime_cleared is True
    assert evidence.flags.get("time_selection_ready") is False
    assert evidence.flags.get("time_selection_required") is True
    ctx = evidence.execution_proposal_context
    assert ctx["bound_datetime_cleared"] is True
    assert ctx["current_turn_has_explicit_time"] is False
    assert ctx["current_turn_time_proposal"] is None
    assert ctx["current_turn_temporal"] is None
    assert ctx["session_time_proposal_reuse_allowed"] is False
    # Cleared binding must not count session datetime as bound.
    assert evidence.datetime_bound is False


def test_readiness_bound_clear_with_preserve_keeps_current_turn_proposal():
    payload = {
        "_current_turn_has_time": True,
        "_current_turn_time": "11:00",
        "time_proposal": {"mode": "exact", "value": "11:00"},
        "temporal": {"mode": "none", "start_time": "11:00"},
    }
    evidence = build_execution_readiness_evidence(
        intent_name="CREATE_APPOINTMENT",
        effective_slots={"service_id": "premium haircut", "date": "2026-07-06"},
        payload=payload,
        session_state={"slots": _base_slots()},
        missing_slots=["time"],
        needs_clarification=False,
        availability_ready=False,
        confirmation_state=None,
        organization_id=1,
        confirm_booking_continuation=False,
        bound_datetime_clear=BoundDatetimeClearEvidence(
            cleared=True,
            reason_code="BOUND_DATETIME_CLEARED",
            preserve_current_turn_time=True,
        ),
    )
    ctx = evidence.execution_proposal_context
    assert ctx["current_turn_has_explicit_time"] is True
    assert ctx["current_turn_time_proposal"] == {"mode": "exact", "value": "11:00"}
    assert ctx["bound_datetime_cleared"] is True
    assert ctx["session_time_proposal_reuse_allowed"] is False


def test_readiness_revision_flag_invalidates_availability():
    evidence = build_execution_readiness_evidence(
        intent_name="CREATE_APPOINTMENT",
        effective_slots=_base_slots(),
        payload={"_revision_invalidated_availability": True},
        session_state={"slots": _base_slots()},
        missing_slots=[],
        needs_clarification=False,
        availability_ready=True,
        confirmation_state="pending",
        organization_id=1,
        confirm_booking_continuation=False,
    )
    assert evidence.revision_invalidated_availability is True
    assert evidence.availability_invalidated is True
    assert evidence.availability_resolved is False
