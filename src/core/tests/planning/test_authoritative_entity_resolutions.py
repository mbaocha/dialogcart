"""Focused Core consumption tests for authoritative NLU entity evidence."""

from core.adapters.nlu.entity_resolution_contract import (
    parse_entity_resolutions,
    project_authoritative_entity_values,
)
from core.planning.pipeline.types import WorkingTurn
from core.planning.pipeline.execution_readiness import (
    build_execution_readiness_evidence,
)
from core.planning.planning_evidence import has_explicit_current_turn_service
from core.planning.planning_mutations import (
    apply_authoritative_entity_resolution_mutations,
)
from core.session.persist import _apply_pending_entity_resolutions_to_session


SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Service",
            "catalog": {"premium": 101, "basic": 102},
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "description": "Staff",
            "catalog": {"sam": 12, "sue": 19},
        },
    ],
}


def _working(resolutions, slots=None, facts=None):
    response = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "entity_resolutions": resolutions,
    }
    evidence = dict(parse_entity_resolutions(response, entity_schema=SCHEMA))
    return WorkingTurn(
        payload={
            "_entity_schema": SCHEMA,
            "_entity_resolutions_authoritative": True,
            "slots": dict(slots or {}),
            "facts": dict(facts or {}),
        },
        effective_collected_slots=dict(slots or {}),
        entity_resolution_evidence=evidence,
    )


def test_resolved_canonical_value_overrides_conflicting_legacy_projection():
    turn = _working(
        {"service": {"resolution": "RESOLVED", "value": 101}},
        slots={"service_id": "basic"},
        facts={"service_id": "basic"},
    )
    apply_authoritative_entity_resolution_mutations(turn)
    assert turn.payload["slots"]["service_id"] == 101
    assert turn.payload["facts"]["service_id"] == 101


def test_omitted_entity_preserves_prior_value_and_empty_map_never_reads_text():
    turn = _working({}, slots={"service_id": 101})
    apply_authoritative_entity_resolution_mutations(turn)
    assert turn.payload["slots"]["service_id"] == 101
    assert not has_explicit_current_turn_service(
        {
            "_entity_resolutions_authoritative": True,
            "_entity_resolution_evidence": {},
            "_entity_schema": SCHEMA,
            "_source_text": "Premium—actually, the cheaper one.",
            "facts": {"service_id": "basic"},
        },
        source_text="Premium—actually, the cheaper one.",
        tenant_aliases={"premium": 101, "basic": 102},
    )
    projected = project_authoritative_entity_values(
        {
            "entity_resolutions": {},
            "facts": {"service_id": "basic"},
            "slots": {"service_id": "basic"},
        },
        entity_schema=SCHEMA,
    )
    assert "service_id" not in projected["facts"]
    assert "service_id" not in projected["slots"]


def test_customer_contact_resolution_is_evidence_not_a_booking_slot():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "customer_contact_name",
                "type": "text",
                "required": True,
                "description": "Booking contact name",
            },
        ],
    }

    projected = project_authoritative_entity_values(
        {
            "entity_resolutions": {
                "customer_contact_name": {
                    "resolution": "RESOLVED",
                    "value": "Godswill Mbaocha",
                },
            },
            "facts": {"customer_contact_name": "Godswill Mbaocha"},
            "slots": {"customer_contact_name": "Godswill Mbaocha"},
        },
        entity_schema=schema,
    )

    assert "customer_contact_name" not in projected["facts"]
    assert "customer_contact_name" not in projected["slots"]
    assert projected["entity_resolutions"]["customer_contact_name"] == {
        "resolution": "RESOLVED",
        "value": "Godswill Mbaocha",
    }


def test_customer_contact_resolution_is_not_applied_as_planning_mutation():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "customer_contact_name",
                "type": "text",
                "required": True,
                "description": "Booking contact name",
            },
        ],
    }
    response = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "entity_resolutions": {
            "customer_contact_name": {
                "resolution": "RESOLVED",
                "value": "Godswill Mbaocha",
            },
        },
    }
    evidence = dict(parse_entity_resolutions(response, entity_schema=schema))
    turn = WorkingTurn(
        payload={
            "_entity_schema": schema,
            "slots": {},
            "facts": {},
        },
        effective_collected_slots={},
        entity_resolution_evidence=evidence,
    )

    apply_authoritative_entity_resolution_mutations(turn)

    assert "customer_contact_name" not in turn.payload["facts"]
    assert "customer_contact_name" not in turn.payload["slots"]
    assert evidence["customer_contact_name"].value == "Godswill Mbaocha"


def test_not_mentioned_service_preserves_durable_value_while_time_advances():
    turn = _working({}, slots={"service_id": 101, "time": "10:00"})

    apply_authoritative_entity_resolution_mutations(turn)

    assert turn.payload["slots"] == {"service_id": 101, "time": "10:00"}


def test_unresolved_correction_blocks_stale_service_and_requires_clarification():
    turn = _working(
        {"service": {"resolution": "UNRESOLVED"}},
        slots={"service_id": 101, "time": "09:00"},
    )
    apply_authoritative_entity_resolution_mutations(turn)
    assert "service_id" not in turn.payload["slots"]
    assert turn.payload["_blocked_entity_slots"] == ["service_id"]
    assert turn.payload["needs_clarification"] is True
    assert turn.payload["clarification_reason"] == "ENTITY_UNRESOLVED"


def test_ambiguous_generic_correction_preserves_canonical_candidates():
    turn = _working(
        {"staff": {"resolution": "AMBIGUOUS", "candidate_values": [12, 19]}},
        slots={"service_id": 101, "staff_id": 12},
    )
    apply_authoritative_entity_resolution_mutations(turn)
    assert "staff_id" not in turn.payload["slots"]
    pending = turn.payload["_pending_entity_resolutions"]
    assert pending == [{
        "entity_name": "staff",
        "slot_key": "staff_id",
        "resolution": "AMBIGUOUS",
        "candidate_values": [12, 19],
    }]
    assert turn.payload["clarification_data"]["entity_resolution"] == pending[0]


def test_blocked_entity_evidence_cannot_select_an_execution_action():
    readiness = build_execution_readiness_evidence(
        intent_name="CREATE_APPOINTMENT",
        effective_slots={"service_id": 101, "date": "2026-08-10", "time": "09:00"},
        payload={"_blocked_entity_slots": ["service_id"]},
        session_state=None,
        missing_slots=[],
        needs_clarification=True,
        availability_ready=True,
        confirmation_state="confirmed",
        organization_id=1,
        confirm_booking_continuation=False,
    )
    assert readiness.executable_actions == ()


def test_pending_ambiguity_persists_as_workflow_evidence_not_as_a_slot():
    session = {"status": "NEEDS_CLARIFICATION", "slots": {"service_id": 101}}
    pending = [{
        "entity_name": "staff",
        "slot_key": "staff_id",
        "resolution": "AMBIGUOUS",
        "candidate_values": [12, 19],
    }]
    _apply_pending_entity_resolutions_to_session(
        session,
        None,
        {"facts": {"pending_entity_resolutions": pending}},
    )
    assert session["pending_entity_resolutions"] == pending
    assert "staff_id" not in session["slots"]
