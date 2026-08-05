"""Decision finalization after operational execution blocks."""

from __future__ import annotations

from core.planning.pipeline.decision_finalization import (
    ExecutionBlockedFinalizationEvidence,
    finalize_decision_after_execution_blocked,
)
from core.session.confirmation_gate import get_confirmation_state


def _ready_confirm_plan() -> dict:
    return {
        "status": "READY",
        "stage": "CONFIRM",
        "action": "CONFIRM_APPOINTMENT",
        "awaiting": None,
        "confirmation_state": "pending",
        "plan": {
            "status": "READY",
            "stage": "CONFIRM",
            "action": "CONFIRM_APPOINTMENT",
            "awaiting": None,
        },
        "_decision": {
            "status": "READY",
            "plan": {
                "status": "READY",
                "stage": "CONFIRM",
                "action": "CONFIRM_APPOINTMENT",
                "awaiting": None,
            },
            "facts": {"slots": {"service_id": "premium haircut"}},
        },
        "_merged_luma_response": {
            "confirmation_state": "pending",
            "slots": {"service_id": "premium haircut"},
        },
    }


def test_customer_id_block_demotes_decision_and_keeps_pending():
    plan = _ready_confirm_plan()
    finalize_decision_after_execution_blocked(
        plan,
        evidence=ExecutionBlockedFinalizationEvidence(
            reason="CUSTOMER_ID_REQUIRED",
            required_input="phone_or_email",
            preserve_pending_confirmation=True,
        ),
    )
    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["action"] is None
    assert plan["awaiting"] == "phone_or_email"
    assert plan["plan"]["status"] == "NEEDS_CLARIFICATION"
    assert plan["plan"]["action"] is None
    decision_plan = plan["_decision"]["plan"]
    assert decision_plan["status"] == "NEEDS_CLARIFICATION"
    assert decision_plan["action"] is None
    assert decision_plan["awaiting"] == "phone_or_email"
    assert get_confirmation_state(plan) == "pending"
    assert get_confirmation_state(plan["_merged_luma_response"]) == "pending"


def test_booking_identification_block_does_not_force_pending():
    plan = _ready_confirm_plan()
    plan.pop("confirmation_state", None)
    plan["_merged_luma_response"].pop("confirmation_state", None)
    finalize_decision_after_execution_blocked(
        plan,
        evidence=ExecutionBlockedFinalizationEvidence(
            reason="BOOKING_IDENTIFICATION_REQUIRED",
            required_input="booking_id_or_code",
            preserve_pending_confirmation=False,
        ),
    )
    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["action"] is None
    assert plan["awaiting"] == "booking_id_or_code"
    assert get_confirmation_state(plan) is None
