from __future__ import annotations

from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.types import WorkingTurn
from core.planning.planning_mutations import (
    apply_assistant_proposal_promotion,
    apply_assistant_proposal_rejection,
)
from core.session.assistant_proposals import AssistantProposalRelationship


def _relationship(canonical_id="premium full service"):
    return AssistantProposalRelationship(
        response_act="CONFIRM_ACTION",
        resolution="BOUND",
        proposal={
            "proposal_id": "p1",
            "slot_key": "service_id",
            "canonical_id": canonical_id,
        },
    )


def test_acceptance_promotes_service_and_preserves_temporal_evidence() -> None:
    temporal = {"start_date": "2026-07-06", "mode": "flexible"}
    turn = WorkingTurn(
        payload={"facts": {}, "slots": {}, "temporal": temporal, "_raw_luma_slots": {}},
        effective_collected_slots={},
    )

    assert apply_assistant_proposal_promotion(turn, _relationship()) is True
    assert turn.payload["slots"]["service_id"] == "premium full service"
    assert turn.payload["temporal"] == temporal
    assert turn.payload["_assistant_proposal_updates"] == [
        {"proposal_id": "p1", "status": "CONSUMED"}
    ]


def test_failed_promotion_does_not_consume_proposal() -> None:
    turn = WorkingTurn(payload={"facts": {}, "slots": {}}, effective_collected_slots={})

    assert apply_assistant_proposal_promotion(turn, _relationship(None)) is False
    assert "_assistant_proposal_updates" not in turn.payload


def test_explicit_service_prevents_proposal_promotion() -> None:
    turn = WorkingTurn(
        payload={
            "facts": {"service_id": "executive oil change"},
            "slots": {"service_id": "executive oil change"},
            "_raw_luma_slots": {"service_id": "executive oil change"},
        },
        effective_collected_slots={"service_id": "executive oil change"},
    )

    assert apply_assistant_proposal_promotion(turn, _relationship()) is False
    assert turn.payload["slots"]["service_id"] == "executive oil change"
    assert "_assistant_proposal_updates" not in turn.payload


def test_attachment_can_carry_authoritative_relationship() -> None:
    relationship = _relationship()
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="AVAILABILITY",
        session_reset_occurred=False,
        assistant_proposal_relationship=relationship,
    )
    assert attached.assistant_proposal_relationship is relationship


def test_rejection_marks_proposal_without_promoting_service() -> None:
    turn = WorkingTurn(payload={"facts": {}, "slots": {}}, effective_collected_slots={})
    relationship = AssistantProposalRelationship(
        response_act="REJECT_ACTION",
        resolution="BOUND",
        proposal={"proposal_id": "p1", "slot_key": "service_id",
                  "canonical_id": "premium full service"},
    )
    assert apply_assistant_proposal_rejection(turn, relationship) is True
    assert turn.payload["slots"] == {}
    assert turn.payload["_assistant_proposal_updates"] == [
        {"proposal_id": "p1", "status": "REJECTED"}
    ]
