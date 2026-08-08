"""Focused regression: identity_resolved_reconfirm fires only for identity recovery."""

from __future__ import annotations

import pytest

from core.planning.pipeline.decision import (
    ConfirmationRejectEvidence,
    DecisionInput,
    decide,
    decide_handler_delegation,
)
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage08_decision_plan import (
    _identity_clarification_requires_reconfirm,
)
from core.planning.pipeline.types import (
    AvailabilityDecision,
    CapabilityDecision,
    ConfirmationDecision,
    IntentDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.session.confirmation_gate import ConfirmationGateTurn, get_confirmation_state


def _predicate(
    *,
    status: str | None,
    customer_id=9,
    action: str | None = "CONFIRM_APPOINTMENT",
    missing_slots=None,
    confirmation_state: str | None = "pending",
    booking_id=None,
):
    session: dict = {}
    if status is not None:
        session["status"] = status
    if customer_id is not None:
        session["customer_id"] = customer_id
    if booking_id is not None:
        session["booking"] = {"booking_id": booking_id}
    return _identity_clarification_requires_reconfirm(
        session_state=session,
        action=action,
        missing_slots=list(missing_slots or []),
        confirmation_state=confirmation_state,
    )


@pytest.mark.parametrize(
    "case,kwargs,expected",
    [
        (
            "identity_recovery_commit_ready",
            dict(status="NEEDS_CLARIFICATION", customer_id=9),
            True,
        ),
        (
            "normal_pending_awaiting",
            dict(status="AWAITING_CONFIRMATION", customer_id=9),
            False,
        ),
        (
            "customer_unresolved",
            dict(status="NEEDS_CLARIFICATION", customer_id=None),
            False,
        ),
        (
            "missing_required_slots",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                missing_slots=["service_id", "date", "time"],
            ),
            False,
        ),
        (
            "search_availability_owns_turn",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                action="SEARCH_AVAILABILITY",
            ),
            False,
        ),
        (
            "capability_action_owns_turn",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                action="COLLECT_PAYMENT",
            ),
            False,
        ),
        (
            "confirmation_cleared_after_rejection",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                confirmation_state=None,
            ),
            False,
        ),
        (
            "service_revision_search",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                action="SEARCH_AVAILABILITY",
                missing_slots=[],
            ),
            False,
        ),
        (
            "datetime_revision_incomplete",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                action=None,
                missing_slots=["time"],
            ),
            False,
        ),
        (
            "committed_booking_no_confirm_action",
            dict(
                status="NEEDS_CLARIFICATION",
                customer_id=9,
                action=None,
                booking_id="bk-committed",
            ),
            False,
        ),
    ],
)
def test_identity_reconfirm_predicate_matrix(case, kwargs, expected):
    assert _predicate(**kwargs) is expected, case


def _commit_ready_slots():
    return {
        "service_id": "premium haircut",
        "date": "2026-07-03",
        "time": "10:00",
        "datetime_range": {
            "start": "2026-07-03T10:00:00+00:00",
            "end": "2026-07-03T10:30:00+00:00",
        },
    }


def _decide_yes_confirm(
    *,
    session_status: str,
    customer_id=9,
    booking_id=None,
    missing_slots=None,
    confirmation_state: str | None = "pending",
    turn_operation: str = "NONE",
):
    slots = _commit_ready_slots()
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(slots),
        "missing_slots": list(missing_slots or []),
        "facts": {"slots": dict(slots)},
        "confirmation_state": confirmation_state,
        "time_match_outcome": "TIME_MATCH_EXACT",
        "resolved_datetime_range": dict(slots["datetime_range"]),
        "_effective_collected_slots": dict(slots),
        "turn": {"understanding": "UNDERSTOOD"},
    }
    session = {
        "status": session_status,
        "confirmation_state": confirmation_state,
        "slots": dict(slots),
    }
    if customer_id is not None:
        session["customer_id"] = customer_id
    if booking_id is not None:
        session["booking"] = {"booking_id": booking_id, "booking_code": "C1"}
        slots["booking_id"] = booking_id
        slots["booking_code"] = "C1"
        payload["slots"] = dict(slots)
        payload["facts"] = {"slots": dict(slots)}
        payload["_effective_collected_slots"] = dict(slots)
        payload["confirmation_state"] = None

    working = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(slots),
    )
    continuation = booking_id is None
    return decide(
        DecisionInput(
            attached_request=AttachedRequest(
                planning_intent="CREATE_APPOINTMENT",
                turn_operation=turn_operation,
                session_reset_occurred=False,
                confirm_booking_continuation=continuation,
                gate_action=ConfirmationGateTurn.YES,
            ),
            working_turn=working,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=list(missing_slots or []),
                effective_collected_slots=dict(slots),
                base_status=session_status,
                needs_clarification=bool(missing_slots),
            ),
            availability=AvailabilityDecision(availability_ready=True),
            confirmation=ConfirmationDecision(
                confirmation_state=None if booking_id else confirmation_state,
                user_confirmation_satisfied=bool(continuation and confirmation_state == "pending"),
                awaiting_user_confirmation=False,
            ),
            capability=CapabilityDecision(),
            session_state=session,
            organization_id=1,
        )
    )


def test_identity_recovery_decide_represents_then_fresh_yes_commits():
    """Lifecycle: NEEDS+customer+YES re-presents; AWAITING+YES selects CONFIRM."""
    first = _decide_yes_confirm(session_status="NEEDS_CLARIFICATION", customer_id=9)
    assert first.plan.get("status") == "AWAITING_CONFIRMATION"
    assert first.plan.get("action") is None
    assert first.plan.get("awaiting") == "USER_CONFIRMATION"

    second = _decide_yes_confirm(session_status="AWAITING_CONFIRMATION", customer_id=9)
    assert second.plan.get("action") == "CONFIRM_APPOINTMENT"
    assert second.plan.get("status") == "READY"


def test_decide_rejects_never_enter_identity_reconfirm_branch():
    slots = _commit_ready_slots()
    working = WorkingTurn(
        payload={
            "slots": dict(slots),
            "confirmation_state": None,
            "_effective_collected_slots": dict(slots),
        },
        effective_collected_slots=dict(slots),
    )
    plan = decide(
        DecisionInput(
            attached_request=AttachedRequest(
                planning_intent="CREATE_APPOINTMENT",
                turn_operation="NONE",
                session_reset_occurred=False,
                gate_action=ConfirmationGateTurn.NO,
            ),
            working_turn=working,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=["time"],
                effective_collected_slots=dict(slots),
                base_status="NEEDS_CLARIFICATION",
                needs_clarification=True,
            ),
            availability=AvailabilityDecision(availability_ready=True),
            confirmation=ConfirmationDecision(
                confirmation_state=None,
                reject_evidence=ConfirmationRejectEvidence(
                    rejected=True,
                    intent_name="CREATE_APPOINTMENT",
                ),
            ),
            capability=CapabilityDecision(),
            session_state={
                "status": "NEEDS_CLARIFICATION",
                "customer_id": 9,
                "confirmation_state": None,
            },
            organization_id=1,
            confirmation_reject=ConfirmationRejectEvidence(
                rejected=True,
                intent_name="CREATE_APPOINTMENT",
            ),
        )
    )
    assert plan.plan.get("status") == "NEEDS_CLARIFICATION"
    assert plan.plan.get("action") is None
    # Reject writer ? not the identity reconfirm presentation branch.
    assert plan.plan.get("awaiting") != "USER_CONFIRMATION"


@pytest.mark.parametrize(
    "intent_decision",
    [
        IntentDecision(
            planning_intent="OFF_TOPIC",
            raw_luma_intent="OFF_TOPIC",
            turn_operation="NONE",
            session_reset_occurred=False,
            non_durable_status="OFF_TOPIC",
            off_topic_query="weather?",
        ),
        IntentDecision(
            planning_intent="CREATE_APPOINTMENT",
            raw_luma_intent="CREATE_APPOINTMENT",
            turn_operation="NONE",
            session_reset_occurred=False,
            handler_delegated=True,
            handler_name="rag",
            delegated_search_query="price?",
        ),
    ],
)
def test_off_topic_and_handler_delegated_never_enter_identity_reconfirm(intent_decision):
    plan = decide_handler_delegation(intent_decision)
    assert plan.plan.get("status") in ("OFF_TOPIC", "HANDLER_DELEGATED")
    assert plan.plan.get("action") is None
    assert plan.plan.get("awaiting") != "USER_CONFIRMATION"


def test_service_revision_turn_does_not_identity_reconfirm():
    """Identity present + availability op must not re-present via this branch."""
    slots = _commit_ready_slots()
    slots["service_id"] = "flexi haircut + prunning"
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(slots),
        "missing_slots": [],
        "facts": {"slots": dict(slots)},
        "confirmation_state": "pending",
        "_effective_collected_slots": dict(slots),
        "turn": {"understanding": "UNDERSTOOD"},
        "_revision_invalidated_availability": True,
    }
    working = WorkingTurn(payload=payload, effective_collected_slots=dict(slots))
    plan = decide(
        DecisionInput(
            attached_request=AttachedRequest(
                planning_intent="CREATE_APPOINTMENT",
                turn_operation="SEARCH",
                session_reset_occurred=False,
                confirm_booking_continuation=False,
                gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
            ),
            working_turn=working,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=[],
                effective_collected_slots=dict(slots),
                base_status="NEEDS_CLARIFICATION",
                needs_clarification=False,
            ),
            availability=AvailabilityDecision(availability_ready=False),
            confirmation=ConfirmationDecision(
                confirmation_state=None,
                user_confirmation_satisfied=False,
                awaiting_user_confirmation=False,
            ),
            capability=CapabilityDecision(),
            session_state={
                "status": "NEEDS_CLARIFICATION",
                "customer_id": 9,
                "confirmation_state": "pending",
            },
            organization_id=1,
        )
    )
    assert plan.plan.get("action") != "CONFIRM_APPOINTMENT"
    # Must not be the identity reconfirm presentation outcome.
    if plan.plan.get("status") == "AWAITING_CONFIRMATION":
        pytest.fail("service revision must not re-present via identity_resolved_reconfirm")


def test_committed_booking_decide_does_not_identity_reconfirm():
    """Committed booking must not select identity reconfirm presentation."""
    plan = _decide_yes_confirm(
        session_status="NEEDS_CLARIFICATION",
        customer_id=9,
        booking_id="bk-1",
    )
    # identity_resolved_reconfirm stamps awaiting=USER_CONFIRMATION with action=None.
    assert plan.plan.get("action") != "CONFIRM_APPOINTMENT"
    assert not (
        plan.plan.get("status") == "AWAITING_CONFIRMATION"
        and plan.plan.get("awaiting") == "USER_CONFIRMATION"
        and plan.plan.get("action") is None
    )
    assert not _predicate(
        status="NEEDS_CLARIFICATION",
        customer_id=9,
        action=plan.plan.get("action"),
        confirmation_state=None,
        booking_id="bk-1",
    )

def test_datetime_revision_turn_does_not_identity_reconfirm():
    """Identity present + time revision (missing time) must not identity-reconfirm."""
    # Predicate-level coverage already asserts missing time blocks the branch.
    # Decide seam: incomplete required slots must not stamp identity reconfirm.
    assert not _predicate(
        status="NEEDS_CLARIFICATION",
        customer_id=9,
        action="CONFIRM_APPOINTMENT",
        missing_slots=["time"],
        confirmation_state="pending",
    )
    slots = _commit_ready_slots()
    slots.pop("time", None)
    slots.pop("datetime_range", None)
    payload = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": dict(slots),
        "missing_slots": ["time"],
        "facts": {"slots": dict(slots)},
        "confirmation_state": "pending",
        "_effective_collected_slots": dict(slots),
        "turn": {"understanding": "UNDERSTOOD"},
        "_current_turn_has_time": True,
        "_current_turn_time": "11:00",
    }
    working = WorkingTurn(payload=payload, effective_collected_slots=dict(slots))
    plan = decide(
        DecisionInput(
            attached_request=AttachedRequest(
                planning_intent="CREATE_APPOINTMENT",
                turn_operation="NONE",
                session_reset_occurred=False,
                confirm_booking_continuation=False,
                gate_action=ConfirmationGateTurn.ANOTHER_REQUEST,
            ),
            working_turn=working,
            slot_state=SlotTurnState(
                intent_name="CREATE_APPOINTMENT",
                missing_slots=["time"],
                ask_next="time",
                effective_collected_slots=dict(slots),
                base_status="NEEDS_CLARIFICATION",
                needs_clarification=True,
            ),
            availability=AvailabilityDecision(availability_ready=False),
            confirmation=ConfirmationDecision(
                confirmation_state="pending",
                user_confirmation_satisfied=False,
                awaiting_user_confirmation=True,
            ),
            capability=CapabilityDecision(),
            session_state={
                "status": "NEEDS_CLARIFICATION",
                "customer_id": 9,
                "confirmation_state": "pending",
            },
            organization_id=1,
        )
    )
    assert not (
        plan.plan.get("status") == "AWAITING_CONFIRMATION"
        and plan.plan.get("awaiting") == "USER_CONFIRMATION"
        and plan.plan.get("action") is None
    )

