"""Schema-aware CREATE ownership of exact enum slot replies."""

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())

from nlu.entity_resolution import MentionState, serialize_entity_resolutions
from nlu.pipeline import NLUPipeline
from nlu.stages.stage2.entity_schema import compile_business_entities
from nlu.stages.stage2.groups.create import _merge


CAR_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service requested.",
            "catalog": {
                "Executive Oil Change": 26,
                "Premium Full Service": 27,
                "Brake Pad Change": 28,
            },
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Vehicle engine type.",
            "values": ["petrol", "diesel", "hybrid", "ev"],
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration number.",
        },
        {
            "name": "staff",
            "type": "catalog",
            "role": "staff",
            "description": "Preferred mechanic.",
            "catalog": {"John": 201, "Mike": 202},
        },
    ],
}

SHARED_ENUM_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service.",
            "catalog": {"Oil Change": 26},
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Vehicle engine type.",
            "values": ["petrol", "diesel"],
        },
        {
            "name": "fuel_preference",
            "type": "enum",
            "description": "Preferred fuel.",
            "values": ["petrol", "electric"],
        },
    ],
}


def _create_raw(entity_results, *, intent="CREATE_APPOINTMENT"):
    return {
        "validated_intent": intent,
        "confidence": 0.9,
        "entity_results": entity_results,
        "temporal": {
            "expression": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "mode": "none",
            "confidence": 0.9,
        },
        "operation": None,
        "declined_entities": [],
    }


def _not_mentioned(*names):
    return {name: {"status": "NOT_MENTIONED"} for name in names}


def _petrol_misclassified_as_service():
    return {
        "service": {"status": "MENTIONED_VALUE", "value": "petrol"},
        "engine_type": {"status": "NOT_MENTIONED"},
        "registration_number": {"status": "NOT_MENTIONED"},
        "staff": {"status": "NOT_MENTIONED"},
    }


def _slot_fill_context():
    return {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["date", "time", "engine_type", "registration_number"],
        "messages": [
            {"role": "user", "text": "Book me an Executive Oil Change"},
            {"role": "assistant", "text": "What engine type does your vehicle use?"},
        ],
        "turns": [
            {
                "user": "Book me an Executive Oil Change",
                "intent": "CREATE_APPOINTMENT",
            }
        ],
    }


def test_petrol_resolves_engine_type_not_service():
    compiled = compile_business_entities(CAR_SCHEMA)
    merged = _merge(
        _create_raw(_petrol_misclassified_as_service()),
        "CREATE_APPOINTMENT",
        text="petrol",
        conversation_context=_slot_fill_context(),
        compiled=compiled,
    )
    mentions = merged["_entity_mentions"]
    assert mentions["engine_type"].state == MentionState.MENTIONED_VALUE
    assert mentions["engine_type"].raw_value == "petrol"
    assert mentions["service"].state == MentionState.NOT_MENTIONED

    resolved = NLUPipeline()._resolve_schema_entities(
        merged, _slot_fill_context(), compiled, text="petrol"
    )
    resolutions = serialize_entity_resolutions(resolved["entity_resolutions"])
    assert resolutions["engine_type"] == {"resolution": "RESOLVED", "value": "petrol"}
    assert "service" not in resolutions
    assert resolved["facts"]["engine_type"] == "petrol"
    assert resolved["facts"]["service"] is None


def test_diesel_hybrid_and_ev_follow_the_same_schema_rule():
    compiled = compile_business_entities(CAR_SCHEMA)
    for enum_value in ("diesel", "hybrid", "ev"):
        raw = _petrol_misclassified_as_service()
        raw["service"] = {"status": "MENTIONED_VALUE", "value": enum_value}
        merged = _merge(
            _create_raw(raw),
            "CREATE_APPOINTMENT",
            text=enum_value,
            conversation_context=_slot_fill_context(),
            compiled=compiled,
        )
        mentions = merged["_entity_mentions"]
        assert mentions["engine_type"].state == MentionState.MENTIONED_VALUE, enum_value
        assert mentions["engine_type"].raw_value == enum_value
        assert mentions["service"].state == MentionState.NOT_MENTIONED


def test_genuine_service_revision_still_resolves_service():
    compiled = compile_business_entities(CAR_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["date", "time", "engine_type", "registration_number"],
        "turns": [
            {
                "user": "Book me an Executive Oil Change",
                "intent": "CREATE_APPOINTMENT",
                "assistant": "What engine type does your vehicle use?",
            }
        ],
    }
    merged = _merge(
        _create_raw(
            {
                "service": {"status": "MENTIONED_VALUE", "value": "Brake Pad Change"},
                **_not_mentioned("engine_type", "registration_number", "staff"),
            }
        ),
        "CORRECTION",
        text="No, switch it to Brake Pad Change instead.",
        conversation_context=ctx,
        compiled=compiled,
    )
    mentions = merged["_entity_mentions"]
    assert mentions["service"].state == MentionState.MENTIONED_VALUE
    assert mentions["service"].raw_value == "Brake Pad Change"
    assert mentions["engine_type"].state == MentionState.NOT_MENTIONED

    resolved = NLUPipeline()._resolve_schema_entities(
        merged, ctx, compiled, text="No, switch it to Brake Pad Change instead."
    )
    resolutions = serialize_entity_resolutions(resolved["entity_resolutions"])
    assert resolutions["service"] == {"resolution": "RESOLVED", "value": 28}
    assert "engine_type" not in resolutions


def test_shared_enum_value_is_not_silently_assigned():
    compiled = compile_business_entities(SHARED_ENUM_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["engine_type", "fuel_preference"],
        "turns": [
            {
                "user": "Book an oil change",
                "intent": "CREATE_APPOINTMENT",
                "assistant": "Please provide the remaining vehicle details.",
            }
        ],
    }
    merged = _merge(
        _create_raw(
            {
                "service": {"status": "NOT_MENTIONED"},
                "engine_type": {"status": "NOT_MENTIONED"},
                "fuel_preference": {"status": "NOT_MENTIONED"},
            }
        ),
        "CREATE_APPOINTMENT",
        text="petrol",
        conversation_context=ctx,
        compiled=compiled,
    )
    mentions = merged["_entity_mentions"]
    assert mentions["engine_type"].state == MentionState.NOT_MENTIONED
    assert mentions["fuel_preference"].state == MentionState.NOT_MENTIONED
    assert mentions["service"].state == MentionState.NOT_MENTIONED


def test_shared_enum_uses_preceding_ask_to_disambiguate():
    compiled = compile_business_entities(SHARED_ENUM_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["engine_type", "fuel_preference"],
        "turns": [
            {
                "user": "Book an oil change",
                "intent": "CREATE_APPOINTMENT",
                "assistant": "What engine type does your vehicle use?",
            }
        ],
    }
    merged = _merge(
        _create_raw(
            {
                "service": {"status": "MENTIONED_VALUE", "value": "petrol"},
                "engine_type": {"status": "NOT_MENTIONED"},
                "fuel_preference": {"status": "NOT_MENTIONED"},
            }
        ),
        "CREATE_APPOINTMENT",
        text="petrol",
        conversation_context=ctx,
        compiled=compiled,
    )
    mentions = merged["_entity_mentions"]
    assert mentions["engine_type"].state == MentionState.MENTIONED_VALUE
    assert mentions["engine_type"].raw_value == "petrol"
    assert mentions["fuel_preference"].state == MentionState.NOT_MENTIONED
    assert mentions["service"].state == MentionState.NOT_MENTIONED
