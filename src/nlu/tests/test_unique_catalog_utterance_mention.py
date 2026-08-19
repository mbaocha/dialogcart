"""Unique spoken catalog mention recovery after Stage 2 NOT_MENTIONED."""

from nlu.entity_resolution import MentionState, serialize_entity_resolutions
from nlu.pipeline import NLUPipeline, _strip_ungrounded_schema_entity_values
from nlu.stages.stage2.entity_schema import (
    apply_unique_catalog_mention_to_slm,
    compile_business_entities,
)
from nlu.stages.stage2.groups.create import _merge


SERVICE_SCHEMA = {
    "version": 1,
    "fields": [{
        "name": "service",
        "type": "catalog",
        "role": "bookable_item",
        "description": "The requested service.",
        "catalog": {
            "premium haircut": 1001,
            "flexi haircut + prunning": 1002,
        },
    }],
}


def _temporal(**overrides):
    payload = {
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
        "confidence": 0.95,
    }
    payload.update(overrides)
    return payload


def _not_mentioned_raw(intent="CREATE_APPOINTMENT", **temporal_overrides):
    return {
        "validated_intent": intent,
        "confidence": 0.95,
        "entity_results": {"service": {"status": "NOT_MENTIONED"}},
        "temporal": _temporal(**temporal_overrides),
        "operation": None,
        "declined_entities": [],
    }


def _public_resolutions(merged, text, conversation_context=None):
    compiled = compile_business_entities(SERVICE_SCHEMA)
    stripped = _strip_ungrounded_schema_entity_values(text, merged)
    stripped = apply_unique_catalog_mention_to_slm(text, stripped, compiled)
    resolved = NLUPipeline()._resolve_schema_entities(
        stripped,
        conversation_context or {},
        compiled,
        text=text,
    )
    return stripped, serialize_entity_resolutions(resolved["entity_resolutions"])


def test_booking_request_pronoun_is_not_a_service_mention_or_resolution():
    text = "yes. i want to book another appointment"
    schema = {
        "version": 1,
        "fields": [{
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "The requested service.",
            "catalog": {
                "integration spa treatment": 1000,
                "premium haircut": 1001,
            },
        }],
    }
    compiled = compile_business_entities(schema)
    merged = _merge(
        {
            "validated_intent": "CREATE_APPOINTMENT",
            "confidence": 0.95,
            "entity_results": {
                "service": {"status": "MENTIONED_VALUE", "value": "i"},
            },
            "temporal": _temporal(),
            "operation": None,
            "declined_entities": [],
        },
        "CREATE_APPOINTMENT",
        text=text,
        compiled=compiled,
    )

    stripped = _strip_ungrounded_schema_entity_values(text, merged, compiled)
    stripped = apply_unique_catalog_mention_to_slm(text, stripped, compiled)
    resolved = NLUPipeline()._resolve_schema_entities(
        stripped, {}, compiled, text=text
    )

    assert stripped["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    assert stripped["service_term"] is None
    assert resolved["facts"]["service_id"] is None
    assert resolved["service_candidates"] == []


def test_flexi_revision_recovers_spoken_subset_while_time_outstanding():
    text = "rather book flexi haircut"
    raw = _not_mentioned_raw("CORRECTION")
    compiled = compile_business_entities(SERVICE_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["time"],
        "resolved_service_id": 1001,
    }
    merged = _merge(
        raw, "CORRECTION", text=text, conversation_context=ctx, compiled=compiled
    )

    assert raw["entity_results"]["service"] == {"status": "NOT_MENTIONED"}
    mention = merged["_entity_mentions"]["service"]
    assert mention.state is MentionState.MENTIONED_VALUE
    assert mention.raw_value == "flexi haircut"
    assert merged["facts"]["service"] == "flexi haircut"

    stripped, public = _public_resolutions(merged, text, ctx)
    assert stripped["_entity_mentions"]["service"].raw_value == "flexi haircut"
    assert public["service"] == {"resolution": "RESOLVED", "value": 1002}


def test_premium_short_pick_resolves_when_service_missing():
    text = "premium"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["service_id", "date", "time"],
    }
    merged = _merge(
        _not_mentioned_raw(),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context=ctx,
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].raw_value == "premium"
    _, public = _public_resolutions(merged, text, ctx)
    assert public["service"] == {"resolution": "RESOLVED", "value": 1001}


def test_integer_service_candidates_do_not_block_catalog_recovery():
    text = "premium"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["service_id", "date", "time"],
        "service_candidates": [1001, 1002],
    }
    merged = _merge(
        _not_mentioned_raw(),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context=ctx,
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].raw_value == "premium"
    _, public = _public_resolutions(merged, text, ctx)
    assert public["service"] == {"resolution": "RESOLVED", "value": 1001}


def test_ambiguous_haircut_prefix_stays_unassigned():
    text = "haircut"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        _not_mentioned_raw(),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context={"missing_slots": ["service_id"]},
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    _, public = _public_resolutions(merged, text)
    assert public == {}


def test_not_premium_is_not_silently_selected():
    text = "not premium"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        _not_mentioned_raw(),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context={"missing_slots": ["service_id"]},
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    _, public = _public_resolutions(merged, text)
    assert public == {}


def test_time_only_reply_is_not_converted_into_service():
    text = "10am"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        _not_mentioned_raw(
            start_time_expression="10am",
            start_time="10:00",
            expression="10am",
        ),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context={
            "missing_slots": ["date", "time"],
            "resolved_service_id": 1001,
        },
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    assert merged["facts"]["times"] == ["10:00"]
    stripped, public = _public_resolutions(merged, text)
    assert stripped["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    assert public == {}


def test_service_comparison_question_is_not_a_revision():
    text = "which is better, premium or flexi?"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        _not_mentioned_raw(),
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context={
            "missing_slots": ["time"],
            "resolved_service_id": 1001,
        },
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    _, public = _public_resolutions(merged, text)
    assert public == {}


def test_stripped_context_haircut_still_recovers_premium_short_pick():
    text = "premium"
    compiled = compile_business_entities(SERVICE_SCHEMA)
    ctx = {
        "last_intent": "CREATE_APPOINTMENT",
        "missing_slots": ["service_id", "date", "time"],
    }
    merged = _merge(
        {
            "validated_intent": "CREATE_APPOINTMENT",
            "confidence": 0.95,
            "entity_results": {
                "service": {
                    "status": "MENTIONED_VALUE",
                    "value": "haircut",
                },
            },
            "temporal": _temporal(),
            "operation": None,
            "declined_entities": [],
        },
        "CREATE_APPOINTMENT",
        text=text,
        conversation_context=ctx,
        compiled=compiled,
    )
    assert merged["_entity_mentions"]["service"].raw_value == "haircut"

    stripped, public = _public_resolutions(merged, text, ctx)
    assert stripped["_entity_mentions"]["service"].raw_value == "premium"
    assert stripped["facts"]["service"] == "premium"
    assert public["service"] == {"resolution": "RESOLVED", "value": 1001}


def test_ungrounded_catalogue_label_on_time_reply_is_still_stripped():
    compiled = compile_business_entities(SERVICE_SCHEMA)
    merged = _merge(
        {
            "validated_intent": "CREATE_APPOINTMENT",
            "confidence": 0.95,
            "entity_results": {
                "service": {
                    "status": "MENTIONED_VALUE",
                    "value": "premium haircut",
                },
            },
            "temporal": _temporal(
                expression="10am",
                start_time="10:00",
                start_time_expression="10am",
            ),
            "operation": None,
            "declined_entities": [],
        },
        "CREATE_APPOINTMENT",
        text="10am",
        conversation_context={"resolved_service_id": 1001},
        compiled=compiled,
    )
    stripped, public = _public_resolutions(merged, "10am")
    assert stripped["_entity_mentions"]["service"].state is MentionState.NOT_MENTIONED
    assert stripped["facts"]["service"] is None
    assert public == {}
