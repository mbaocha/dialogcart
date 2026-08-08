"""Core planning-evidence: schema facts must advance planning despite NLU labels."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any, Dict, Optional

from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage02_working_turn import build_working_turn
from core.planning.pipeline.stage03_revision import apply_revision_policy
from core.planning.pipeline.stage04_slots import resolve_slot_turn_state
from core.planning.planning_evidence import (
    has_current_turn_planning_evidence,
    stamp_planning_evidence,
)
from core.rendering.recovery_renderer import should_render_recovery

_MULTI_SCHEMA: Dict[str, Any] = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "required": True,
            "catalog": {"Oil Change": 101, "Premium Haircut": 18},
        },
        {
            "name": "engine_type",
            "type": "enum",
            "required": True,
            "availability_criteria": True,
            "values": ["petrol", "diesel", "hybrid", "ev"],
        },
        {
            "name": "hair_length",
            "type": "enum",
            "required": True,
            "availability_criteria": True,
            "values": ["short", "medium", "long"],
        },
        {
            "name": "registration_number",
            "type": "text",
            "required": True,
            "description": "Vehicle registration",
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "required": False,
            "catalog": {"Mike": 7, "John": 8},
        },
    ],
}


def _attached(turn_operation: str = "PROVIDE_SLOT_VALUE") -> AttachedRequest:
    kwargs = {f.name: None for f in fields(AttachedRequest)}
    kwargs.update(
        {
            "planning_intent": "CREATE_APPOINTMENT",
            "turn_operation": turn_operation,
            "session_reset_occurred": False,
            "confirm_booking_continuation": False,
        }
    )
    return AttachedRequest(**kwargs)


def _session(**overrides: Any) -> Dict[str, Any]:
    base = {
        "intent_name": "CREATE_APPOINTMENT",
        "intent": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": {"service_id": "oil-change", "_catalog_item_id": 101},
        "missing_slots": ["date", "time", "engine_type", "registration_number"],
        "ask_next": "engine_type",
    }
    base.update(overrides)
    return base


def _luma(
    *,
    facts: Optional[Dict[str, Any]] = None,
    understanding: str = "UNRECOGNIZED_INPUT",
    intent: str = "CREATE_APPOINTMENT",
    declined_entities: Optional[list] = None,
    operation: Optional[str] = None,
    temporal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "intent": {"name": intent, "confidence": 0.9},
        "facts": dict(facts or {}),
        "turn": {"understanding": understanding},
        "temporal": temporal
        or {
            "mode": "none",
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "expression": None,
            "confidence": 0.0,
        },
    }
    if declined_entities is not None:
        payload["declined_entities"] = list(declined_entities)
    if operation is not None:
        payload["operation"] = operation
    return payload


def _run_turn(
    luma: Dict[str, Any],
    session: Dict[str, Any],
    *,
    entity_schema: Optional[Dict[str, Any]] = _MULTI_SCHEMA,
    text: str = "petrol",
):
    attached = _attached()
    response = {**deepcopy(luma), "_entity_schema": entity_schema}
    working = build_working_turn(
        luma_response=response,
        raw_luma_response_deep_copy=deepcopy(luma),
        attached_request=attached,
        session_state=deepcopy(session),
        original_session_state=deepcopy(session),
        source_text=text,
        tenant_context=None,
        apply_domain_filter=True,
        entity_schema=entity_schema,
    )
    apply_revision_policy(working, session)
    slot_state = resolve_slot_turn_state(
        working_turn=working,
        intent_name="CREATE_APPOINTMENT",
        session_state=session,
        attached_request=attached,
    )
    return working, slot_state


def test_service_revision_rebuilds_service_identity_group_before_merge():
    session = _session(
        slots={
            "service_id": "old service",
            "_canonical_service_id": "old-canonical",
            "_catalog_item_id": 101,
        },
        facts={
            "slots": {
                "service_id": "old service",
                "_canonical_service_id": "old-canonical",
                "_catalog_item_id": 101,
            }
        },
    )
    luma = _luma(facts={"service_id": "new service"})

    working = build_working_turn(
        luma_response={**deepcopy(luma), "_entity_schema": _MULTI_SCHEMA},
        raw_luma_response_deep_copy=deepcopy(luma),
        attached_request=_attached(),
        session_state=deepcopy(session),
        original_session_state=deepcopy(session),
        source_text="new service",
        tenant_context={"aliases": {"new service": 202}},
        apply_domain_filter=True,
        entity_schema=_MULTI_SCHEMA,
    )
    apply_revision_policy(working, session)

    assert working.payload["slots"] == {
        "service_id": "new service",
        "_catalog_item_id": 202,
    }


def test_helper_schema_enum_is_planning_evidence():
    payload = _luma(facts={"engine_type": "petrol"})
    payload["_entity_schema"] = _MULTI_SCHEMA
    assert has_current_turn_planning_evidence(
        payload,
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={"engine_type": "petrol"},
        entity_schema=_MULTI_SCHEMA,
    )


def test_helper_schema_text_is_planning_evidence():
    payload = _luma(facts={"registration_number": "AB12CDE"})
    assert has_current_turn_planning_evidence(
        payload,
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={"registration_number": "AB12CDE"},
        entity_schema=_MULTI_SCHEMA,
    )


def test_helper_catalog_staff_is_planning_evidence():
    assert has_current_turn_planning_evidence(
        _luma(facts={"staff_id": 7}),
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={"staff_id": 7},
        entity_schema=_MULTI_SCHEMA,
    )


def test_helper_decline_is_planning_evidence():
    payload = _luma(facts={}, declined_entities=["staff"])
    assert has_current_turn_planning_evidence(
        payload,
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
    )


def test_helper_session_only_is_not_planning_evidence():
    payload = _luma(facts={"service_id": None, "engine_type": None})
    assert not has_current_turn_planning_evidence(
        payload,
        session_slots={"service_id": "oil-change", "engine_type": "diesel"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
    )


def test_helper_sticky_same_service_id_alone_is_not_evidence():
    """Re-emitting session service_id without new keys is not a turn delta."""
    assert not has_current_turn_planning_evidence(
        _luma(facts={"service_id": "oil-change"}),
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={"service_id": "oil-change"},
        entity_schema=_MULTI_SCHEMA,
    )


def test_engine_type_unrecognized_merges_into_effective_slots():
    """Live bug: facts.engine_type + UNRECOGNIZED_INPUT must still collect."""
    session = _session()
    working, slot_state = _run_turn(
        _luma(facts={"engine_type": "petrol"}, understanding="UNRECOGNIZED_INPUT"),
        session,
        text="petrol",
    )
    assert working.payload.get("_current_turn_planning_evidence") is True
    assert working.effective_collected_slots.get("engine_type") == "petrol"
    assert "engine_type" not in slot_state.missing_slots
    assert slot_state.effective_collected_slots.get("engine_type") == "petrol"


def test_hair_length_unrecognized_merges_identically():
    session = _session(
        slots={"service_id": "premium haircut", "_catalog_item_id": 18},
        missing_slots=["date", "time", "hair_length"],
        ask_next="hair_length",
    )
    working, slot_state = _run_turn(
        _luma(facts={"hair_length": "long"}, understanding="UNRECOGNIZED_INPUT"),
        session,
        text="Long",
    )
    assert working.payload.get("_current_turn_planning_evidence") is True
    assert working.effective_collected_slots.get("hair_length") == "long"
    assert "hair_length" not in slot_state.missing_slots


def test_registration_text_unrecognized_counts():
    session = _session(
        slots={"service_id": "oil-change", "engine_type": "petrol"},
        missing_slots=["date", "time", "registration_number"],
        ask_next="registration_number",
    )
    working, slot_state = _run_turn(
        _luma(
            facts={"registration_number": "AB12CDE"},
            understanding="UNRECOGNIZED_INPUT",
        ),
        session,
        text="AB12CDE",
    )
    assert working.effective_collected_slots.get("registration_number") == "AB12CDE"
    assert "registration_number" not in slot_state.missing_slots


def test_gibberish_preserves_session_without_schema_delta():
    """Genuine nonsense: no planning evidence; session proposals preserved."""
    from core.planning.pipeline.requests import AttachedRequest as AR

    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "premium haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-21"},
        "missing_slots": ["time"],
    }
    attached = AR(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="NONE",
        session_reset_occurred=False,
    )
    luma = _luma(
        facts={
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        understanding="UNRECOGNIZED_INPUT",
    )
    working = build_working_turn(
        luma_response={**deepcopy(luma), "_entity_schema": _MULTI_SCHEMA},
        raw_luma_response_deep_copy=deepcopy(luma),
        attached_request=attached,
        session_state=deepcopy(session),
        original_session_state=deepcopy(session),
        source_text="asdfasdf",
        tenant_context={"aliases": {}, "booking_mode": "service"},
        apply_domain_filter=True,
        entity_schema=_MULTI_SCHEMA,
    )
    assert working.payload.get("_current_turn_planning_evidence") is False
    assert working.payload.get("date_proposal", {}).get("start") == "2026-07-21"
    assert working.effective_collected_slots.get("service_id") == "premium haircut"
    assert "engine_type" not in working.effective_collected_slots


def test_recovery_suppressed_when_schema_evidence_stamped():
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": ["date", "time"],
        "slots": {"service_id": "oil-change", "engine_type": "petrol"},
        "intent_name": "CREATE_APPOINTMENT",
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "facts": {"current_turn_planning_evidence": True},
    }
    assert (
        should_render_recovery(
            result={"success": True, "outcome": outcome},
            plan={"status": "NEEDS_CLARIFICATION"},
            availability_client_present=True,
        )
        is False
    )


def test_recovery_still_fires_for_cold_start_unrecognized():
    outcome = {
        "status": "NEEDS_CLARIFICATION",
        "stage": "AVAILABILITY",
        "missing_slots": [],
        "slots": {},
        "intent_name": "UNKNOWN",
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "facts": {"current_turn_planning_evidence": False},
    }
    assert (
        should_render_recovery(
            result={"success": True, "outcome": outcome},
            plan={"status": "NEEDS_CLARIFICATION"},
            availability_client_present=True,
        )
        is True
    )


def test_stamp_idempotent_on_payload():
    payload = _luma(facts={"engine_type": "diesel"})
    payload["_entity_schema"] = _MULTI_SCHEMA
    assert stamp_planning_evidence(
        payload,
        session_slots={"service_id": "x"},
        raw_turn_slots={"engine_type": "diesel"},
        entity_schema=_MULTI_SCHEMA,
    )
    assert payload["_current_turn_planning_evidence"] is True
    # Second call must not recompute or flip.
    assert (
        stamp_planning_evidence(
            payload,
            session_slots={},
            raw_turn_slots={},
            entity_schema=_MULTI_SCHEMA,
        )
        is True
    )


def test_helper_raw_confirm_is_planning_evidence():
    assert has_current_turn_planning_evidence(
        _luma(facts={}, intent="CREATE_APPOINTMENT"),
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
        raw_dialogue_act="CONFIRM_ACTION",
    )


def test_helper_raw_reject_is_planning_evidence():
    assert has_current_turn_planning_evidence(
        _luma(facts={}, intent="CREATE_APPOINTMENT"),
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
        raw_dialogue_act="REJECT_ACTION",
    )


def test_helper_normalized_intent_alone_is_not_confirm_evidence():
    """Normalized CREATE_APPOINTMENT must not count as confirmation evidence."""
    assert not has_current_turn_planning_evidence(
        _luma(facts={}, intent="CREATE_APPOINTMENT"),
        session_slots={"service_id": "oil-change", "date": "2026-07-21", "time": "10:00"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
        raw_dialogue_act="",
    )


def test_helper_browse_operation_is_planning_evidence():
    assert has_current_turn_planning_evidence(
        _luma(facts={}, operation="browse_next"),
        session_slots={"service_id": "oil-change"},
        raw_turn_slots={},
        entity_schema=_MULTI_SCHEMA,
    )


def test_confirm_survives_planning_intent_rewrite():
    """Stage 02 overwrites intent to CREATE_APPOINTMENT; raw act still stamps."""
    session = _session(
        status="AWAITING_CONFIRMATION",
        slots={"service_id": "oil-change", "date": "2026-07-21", "time": "10:00"},
        missing_slots=[],
        ask_next=None,
    )
    working, _ = _run_turn(
        _luma(facts={}, intent="CONFIRM_ACTION", understanding="UNDERSTOOD"),
        session,
        text="yes",
    )
    assert working.payload.get("intent", {}).get("name") == "CREATE_APPOINTMENT"
    assert working.payload.get("_current_turn_planning_evidence") is True


def test_same_value_service_restatement_is_not_evidence():
    session = _session(
        slots={"service_id": "oil-change", "_catalog_item_id": 101},
        missing_slots=["date", "time", "engine_type"],
        ask_next="engine_type",
    )
    working, _ = _run_turn(
        _luma(
            facts={"service_id": "oil-change"},
            understanding="UNDERSTOOD",
        ),
        session,
        text="oil change",
    )
    assert working.payload.get("_current_turn_planning_evidence") is False
