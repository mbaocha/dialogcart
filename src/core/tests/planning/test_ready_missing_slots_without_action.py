"""Regression: READY must not leave missing slots with no execution action."""

from __future__ import annotations

from core.planning.pipeline.decision import DecisionInput, decide
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.types import (
    AvailabilityDecision,
    CapabilityDecision,
    ConfirmationDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)


def _decide_after_availability(*, missing_slots, slots, availability_ready=True):
    """Simulate post-availability booking turn (e.g. user said \"9\")."""
    search_date = "2026-07-24"
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="NONE",
        session_reset_occurred=False,
    )
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(slots),
        "missing_slots": list(missing_slots),
        "facts": {"slots": dict(slots)},
        "date_proposal": {"mode": "single_day", "start": search_date},
        "turn": {"understanding": "UNDERSTOOD"},
    }
    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(slots),
    )
    slot_state = SlotTurnState(
        intent_name="CREATE_APPOINTMENT",
        missing_slots=list(missing_slots),
        ask_next=missing_slots[0] if missing_slots else None,
        effective_collected_slots=dict(slots),
        base_status="READY",
        needs_clarification=False,
    )
    fp_slots = build_availability_fingerprint_slots(
        slots,
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response=payload,
        session_state={
            "date_proposal": {"mode": "single_day", "start": search_date},
        },
    )
    fingerprint = compute_availability_fingerprint(fp_slots)
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": dict(slots),
        "missing_slots": list(missing_slots),
        "date_proposal": {"mode": "single_day", "start": search_date},
        "availability_fingerprint": fingerprint,
        "presented_availability": {
            "search_date": search_date,
            "times": ["9:00 AM", "9:30 AM", "10:00 AM"],
            "slots": [
                {
                    "starts_at": f"{search_date}T09:00:00Z",
                    "ends_at": f"{search_date}T09:30:00Z",
                },
                {
                    "starts_at": f"{search_date}T09:30:00Z",
                    "ends_at": f"{search_date}T10:00:00Z",
                },
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": search_date,
            "availability_fingerprint": fingerprint,
            "slots": [
                {
                    "starts_at": f"{search_date}T09:00:00Z",
                    "ends_at": f"{search_date}T09:30:00Z",
                },
            ],
        },
    }
    return decide(
        DecisionInput(
            attached_request=attached,
            working_turn=working,
            slot_state=slot_state,
            availability=AvailabilityDecision(
                availability_ready=availability_ready,
                stored_fingerprint=fingerprint,
                current_fingerprint=fingerprint,
            ),
            confirmation=ConfirmationDecision(),
            capability=CapabilityDecision(),
            session_state=session_state,
            organization_id=1,
        )
    )


def test_missing_time_with_ready_availability_is_clarification_not_dead_ready():
    """book premium on July 24 → availability shown → \"9\" with no bind.

    Must not emit READY + action=None + missing_slots=[time].
    """
    decision = _decide_after_availability(
        missing_slots=["time"],
        slots={"service_id": "premium haircut"},
        availability_ready=True,
    )
    plan = decision.plan
    assert plan.get("missing_slots") == ["time"]
    assert plan.get("action") is None
    assert plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.get("awaiting") == "time"
    assert plan.get("ask_next") == "time"
    assert not (
        plan.get("status") == "READY"
        and plan.get("action") is None
        and plan.get("missing_slots")
    )


def test_exploratory_ready_preserved_when_search_still_required():
    """Partial slots may still be READY when SEARCH_AVAILABILITY is selected."""
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="NONE",
        session_reset_occurred=False,
    )
    slots = {"service_id": "premium haircut"}
    missing = ["date", "time"]
    working = WorkingTurn(
        payload={
            "intent": {"name": "CREATE_APPOINTMENT"},
            "slots": dict(slots),
            "missing_slots": list(missing),
            "facts": {"slots": dict(slots)},
        },
        effective_collected_slots=dict(slots),
    )
    decision = decide(
        DecisionInput(
            attached_request=attached,
            working_turn=working,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=list(missing),
                effective_collected_slots=dict(slots),
                base_status="NEEDS_CLARIFICATION",
            ),
            availability=AvailabilityDecision(availability_ready=False),
            confirmation=ConfirmationDecision(),
            capability=CapabilityDecision(),
            session_state={"intent_name": "CREATE_APPOINTMENT", "slots": dict(slots)},
            organization_id=1,
        )
    )
    plan = decision.plan
    assert plan.get("status") == "READY"
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    assert plan.get("missing_slots")


def test_reconcile_preserves_availability_reshow_presentation():
    """Explicit planner reshow remains READY + action=None (presentation)."""
    from core.planning.pipeline.stage08_decision_plan import (
        _reconcile_terminal_decision,
    )

    status, action, awaiting, stage, branch, reshow = _reconcile_terminal_decision(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        missing_slots=["time"],
        ask_next="time",
        action_branch="availability_reshow",
        availability_reshow=True,
        availability_browse=None,
    )
    assert status == "READY"
    assert action is None
    assert branch == "availability_reshow"
    assert reshow is True


def test_reconcile_no_planning_evidence_becomes_recovery_presentation():
    from core.planning.pipeline.stage08_decision_plan import (
        _reconcile_terminal_decision,
    )

    status, action, awaiting, stage, branch, reshow = _reconcile_terminal_decision(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        missing_slots=["time"],
        ask_next="time",
        action_branch="no_execution_step",
        availability_reshow=False,
        availability_browse=None,
        has_planning_evidence=False,
        turn_understanding="UNRECOGNIZED_INPUT",
    )
    assert status == "READY"
    assert action is None
    assert branch == "recovery_presentation"
    assert awaiting == "time"
    assert reshow is False


def test_reconcile_understood_no_evidence_demotes_to_clarification():
    """UNDERSTOOD + no evidence must clarify, not recovery presentation."""
    from core.planning.pipeline.stage08_decision_plan import (
        _reconcile_terminal_decision,
    )

    status, action, awaiting, stage, branch, reshow = _reconcile_terminal_decision(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        missing_slots=["time"],
        ask_next="time",
        action_branch="no_execution_step",
        availability_reshow=False,
        availability_browse=None,
        has_planning_evidence=False,
        turn_understanding="UNDERSTOOD",
    )
    assert status == "NEEDS_CLARIFICATION"
    assert action is None
    assert awaiting == "time"
    assert branch == "reconcile_unanswered_ask_next"
    assert reshow is False


def test_reconcile_demotes_dead_ready_to_clarification():
    from core.planning.pipeline.stage08_decision_plan import (
        _reconcile_terminal_decision,
    )

    status, action, awaiting, stage, branch, reshow = _reconcile_terminal_decision(
        status="READY",
        action=None,
        awaiting=None,
        stage="AVAILABILITY",
        missing_slots=["time"],
        ask_next="time",
        action_branch="no_execution_step",
        availability_reshow=False,
        availability_browse=None,
        has_planning_evidence=True,
        turn_understanding="UNDERSTOOD",
    )
    assert status == "NEEDS_CLARIFICATION"
    assert action is None
    assert awaiting == "time"
    assert branch == "reconcile_unanswered_ask_next"
    assert reshow is False
