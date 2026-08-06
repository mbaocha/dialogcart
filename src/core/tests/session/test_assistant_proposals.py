from __future__ import annotations

import pytest

from core.rendering.llm_renderer import HandlerEntitySelection, HandlerRenderResult
from core.session.assistant_proposals import create_assistant_proposals
from core.session.assistant_proposals import bind_assistant_proposal
from core.session.session_projector import SessionProjectorV2
from core.session.session_schema_v2 import empty_session_v2


CONTEXT = {
    "services": [
        {"id": 27, "name": "Premium Full Service", "type": "service", "is_active": True},
        {"id": 26, "name": "Executive Oil Change", "type": "service", "is_active": True},
    ]
}


def _create(selection: HandlerEntitySelection, text: str = "I recommend Premium Full Service."):
    return create_assistant_proposals(
        HandlerRenderResult(text=text, selected_entities=[selection]),
        structured_context=CONTEXT,
        handler_name="rag",
        transaction_id="turn-1",
    )


def test_valid_catalog_selection_becomes_non_booking_pending_proposal() -> None:
    proposal = _create(HandlerEntitySelection("service", catalog_id=27))[0]

    assert proposal["catalog_id"] == 27
    assert proposal["canonical_id"] == "premium full service"
    assert proposal["slot_key"] == "service_id"
    assert proposal["status"] == "PENDING"
    assert proposal["source"] == {"handler": "rag", "transaction_id": "turn-1"}


def test_unavailable_catalog_selection_is_rejected() -> None:
    with pytest.raises(ValueError, match="unavailable or has conflicting"):
        _create(HandlerEntitySelection("service", catalog_id=999))


def test_conflicting_catalog_identifiers_are_rejected() -> None:
    with pytest.raises(ValueError, match="unavailable or has conflicting"):
        _create(HandlerEntitySelection(
            "service", catalog_id=27, display_name="Executive Oil Change"
        ))


def test_text_and_selected_entity_cannot_conflict() -> None:
    with pytest.raises(ValueError, match="does not name"):
        _create(
            HandlerEntitySelection("service", catalog_id=27),
            text="I recommend Executive Oil Change.",
        )


def _proposal(proposal_id: str = "p1", status: str = "PENDING"):
    return {
        "proposal_id": proposal_id,
        "proposal_type": "ENTITY_RECOMMENDATION",
        "status": status,
        "entity_type": "service",
        "slot_key": "service_id",
        "canonical_id": "premium full service",
        "expected_responses": ["ACCEPT", "REJECT"],
    }


def test_generic_response_binds_exactly_one_active_compatible_proposal() -> None:
    relationship = bind_assistant_proposal(
        response_act="CONFIRM_ACTION", proposals=[_proposal()], explicit_slots={}
    )
    assert relationship is not None
    assert relationship.resolution == "BOUND"
    assert relationship.proposal["proposal_id"] == "p1"


@pytest.mark.parametrize("status", ["EXPIRED", "INACTIVE", "SUPERSEDED"])
def test_inactive_lifecycle_states_do_not_bind(status: str) -> None:
    relationship = bind_assistant_proposal(
        response_act="CONFIRM_ACTION",
        proposals=[_proposal(status=status)],
        explicit_slots={},
    )
    assert relationship.resolution == "NO_MATCH"


def test_zero_and_multiple_compatible_proposals_do_not_guess() -> None:
    zero = bind_assistant_proposal(
        response_act="CONFIRM_ACTION", proposals=[], explicit_slots={}
    )
    multiple = bind_assistant_proposal(
        response_act="CONFIRM_ACTION",
        proposals=[_proposal("p1"), _proposal("p2")],
        explicit_slots={},
    )
    assert zero.resolution == "NO_MATCH"
    assert multiple.resolution == "AMBIGUOUS"


def test_explicit_current_turn_selection_overrides_proposal() -> None:
    relationship = bind_assistant_proposal(
        response_act="CONFIRM_ACTION",
        proposals=[_proposal()],
        explicit_slots={"service_id": "executive oil change"},
    )
    assert relationship.resolution == "NO_MATCH"


def test_ordinary_turn_carries_and_updates_existing_proposals() -> None:
    previous = empty_session_v2()
    previous["planning"].update({
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": ["date"],
        "slots": {"service_id": "premium full service"},
    })
    previous["conversation"]["pending_proposals"] = [_proposal()]

    projected = SessionProjectorV2().project(
        outcome={
            "status": "NEEDS_CLARIFICATION",
            "intent_name": "CREATE_APPOINTMENT",
            "missing_slots": ["date"],
            "slots": {"service_id": "premium full service"},
        },
        outcome_status="NEEDS_CLARIFICATION",
        organization_id=2,
        previous_session_state=previous,
        working_session_state=previous,
        assistant_proposal_updates=[{"proposal_id": "p1", "status": "CONSUMED"}],
        user_id="proposal-carry",
    )

    assert projected is not None
    proposals = projected["conversation"]["pending_proposals"]
    assert proposals[0]["status"] == "CONSUMED"
