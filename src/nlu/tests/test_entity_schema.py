"""Tests for optional entity_schema — CREATE schema-driven business entities."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())
sys.modules.setdefault("dotenv", MagicMock())

from nlu.api import app
from nlu.catalog import resolve_service
from nlu.pipeline import NLUPipeline, PipelineResult
from nlu.stages.stage2.base_prompt import build_tool, service_rules
from nlu.stages.stage2.entity_schema import (
    EntitySchemaValidationError,
    compile_business_entities,
    effective_tenant_context,
    extract_declared_facts,
    resolved_id_key,
    service_term_from_facts,
)
from nlu.stages.stage2.groups.create import (
    _LEGACY_CREATE_TOOL_KWARGS,
    _merge,
    _system_prompt,
    build_create_tool,
)


SAMPLE_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "description": "The requested service.",
            "catalog": {
                "Premium Haircut": "svc_1",
                "Flexi Haircut": "svc_2",
            },
        }
    ],
}

MULTI_CATALOG_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "The requested service.",
            "catalog": {
                "Premium Haircut": "svc_1",
                "Flexi Haircut": "svc_2",
            },
        },
        {
            "name": "technician",
            "type": "catalog",
            "role": "staff",
            "description": "Preferred technician.",
            "catalog": {
                "Alex Tech": "tech_1",
                "Sam Tech": "tech_2",
            },
        },
    ],
}

CATALOG_ENUM_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service.",
            "catalog": {"Oil Change": "oil_1", "Brake Service": "brake_1"},
        },
        {
            "name": "engine_type",
            "type": "enum",
            "description": "Engine type.",
            "values": ["petrol", "diesel", "hybrid", "ev"],
        },
    ],
}

CATALOG_TEXT_SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "description": "Vehicle service.",
            "catalog": {"Oil Change": "oil_1"},
        },
        {
            "name": "registration_number",
            "type": "text",
            "description": "Vehicle registration.",
        },
    ],
}


def _temporal_payload(**overrides):
    base = {
        "expression": "tomorrow",
        "start_date_expression": "tomorrow",
        "start_time_expression": None,
        "end_date_expression": None,
        "end_time_expression": None,
        "start_date": "2026-07-08",
        "start_time": None,
        "end_date": None,
        "end_time": None,
        "mode": "single_day",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


# ── Compiler / validation ─────────────────────────────────────────────────────


def test_compile_catalog_field():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    assert "service" in compiled.facts_schema["properties"]
    assert compiled.alias_map["premium haircut"] == "svc_1"
    assert compiled.alias_map["flexi haircut"] == "svc_2"
    assert "Entity: service" in compiled.prompt_rules
    assert "The requested service." in compiled.prompt_rules
    assert "facts.service" in compiled.prompt_rules
    assert compiled.bookable_item_field is not None
    assert resolved_id_key(compiled.bookable_item_field) == "service_id"


def test_compile_enum_and_text_fields():
    compiled = compile_business_entities(CATALOG_ENUM_SCHEMA)
    assert "engine_type" in compiled.facts_schema["properties"]
    assert "Allowed values" in compiled.prompt_rules
    text_compiled = compile_business_entities(CATALOG_TEXT_SCHEMA)
    assert "registration_number" in text_compiled.facts_schema["properties"]


def test_compile_rejects_unsupported_type():
    schema = {
        "version": 1,
        "fields": [
            {
                "name": "weird",
                "type": "vector",
                "description": "Nope",
            }
        ],
    }
    with pytest.raises(EntitySchemaValidationError, match="unsupported entity type"):
        compile_business_entities(schema)


def test_compile_rejects_bad_version():
    schema = {**SAMPLE_SCHEMA, "version": 99}
    with pytest.raises(EntitySchemaValidationError, match="unsupported entity_schema version"):
        compile_business_entities(schema)


def test_effective_tenant_context_does_not_mutate_original():
    original = {"aliases": {"massage": "m1"}, "booking_mode": "service"}
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    effective = effective_tenant_context(original, compiled)
    assert "premium haircut" not in original["aliases"]
    assert effective["aliases"]["premium haircut"] == "svc_1"
    assert effective["aliases"]["massage"] == "m1"
    assert original["aliases"] == {"massage": "m1"}


def test_service_term_from_facts_bridge():
    assert service_term_from_facts({"service": "premium"}, ["service"]) == "premium"
    assert service_term_from_facts({"service_term": "legacy"}, ["service"]) == "legacy"
    assert service_term_from_facts({}, ["service"]) is None


# ── Legacy path: prompt + tool identical ─────────────────────────────────────


def test_legacy_create_tool_matches_frozen_kwargs():
    legacy = build_create_tool(None)
    expected = build_tool(**_LEGACY_CREATE_TOOL_KWARGS)
    assert legacy == expected
    assert "service_term" in legacy["input_schema"]["properties"]["facts"]["properties"]
    assert set(legacy["input_schema"]["properties"]) == {
        "validated_intent",
        "confidence",
        "temporal",
        "facts",
        "operation",
    }


def test_legacy_system_prompt_uses_service_rules():
    aliases = {"premium haircut": "svc_1"}
    tenant = {"aliases": aliases, "booking_mode": "service"}
    prompt = _system_prompt(
        "2026-07-07T10:00:00 (local date 2026-07-07, weekday Tuesday, timezone UTC)",
        tenant,
        None,
        "CREATE_APPOINTMENT",
        compiled=None,
    )
    assert service_rules(aliases) in prompt
    assert "BUSINESS ENTITY RULES" not in prompt


def test_legacy_merge_keeps_service_term_path():
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service_term": "premium haircut"},
        "temporal": _temporal_payload(),
        "operation": None,
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=None)
    assert merged["service_term"] == "premium haircut"
    assert "service" not in merged["facts"]
    assert merged["facts"]["service_id"] is None


# ── Schema path: prompt + tool ───────────────────────────────────────────────


def test_schema_system_prompt_uses_compiled_rules():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    tenant = {"aliases": {}, "booking_mode": "service"}
    prompt = _system_prompt(
        "2026-07-07T10:00:00 (local date 2026-07-07, weekday Tuesday, timezone UTC)",
        tenant,
        None,
        "CREATE_APPOINTMENT",
        compiled=compiled,
    )
    assert compiled.prompt_rules in prompt
    assert "Entity: service" in prompt
    assert "Premium Haircut" in prompt
    assert "declined_entities" in prompt
    assert "stylist" not in prompt.lower()
    assert "mechanic" not in prompt.lower()
    # Legacy service_rules block must not appear.
    assert "SERVICE RULES" not in prompt


def test_schema_create_tool_uses_facts_schema():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    tool = build_create_tool(compiled)
    facts_props = tool["input_schema"]["properties"]["facts"]["properties"]
    assert "service" in facts_props
    assert "service_term" not in facts_props
    # Platform fields unchanged.
    assert "temporal" in tool["input_schema"]["properties"]
    assert "operation" in tool["input_schema"]["properties"]
    assert "validated_intent" in tool["input_schema"]["properties"]
    assert "declined_entities" in tool["input_schema"]["properties"]
    assert "declined_entities" not in facts_props


def test_merge_emits_declined_entities_and_keeps_fact_null():
    compiled = compile_business_entities(MULTI_CATALOG_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": "Premium Haircut", "technician": None},
        "temporal": _temporal_payload(),
        "operation": None,
        "declined_entities": ["technician"],
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["declined_entities"] == ["technician"]
    assert merged["facts"].get("technician") is None
    assert "declined_entities" not in merged["facts"]


def test_merge_filters_unknown_declined_entities():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": None},
        "temporal": _temporal_payload(),
        "declined_entities": ["staff", "service", "not_a_field"],
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["declined_entities"] == ["service"]


def test_merge_preserves_one_catalog_entity():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": "premium haircut"},
        "temporal": _temporal_payload(),
        "operation": None,
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["facts"]["service"] == "premium haircut"
    assert merged["service_term"] == "premium haircut"
    assert merged["facts"]["service_id"] is None  # resolve happens in pipeline


def test_merge_preserves_two_catalog_entities():
    compiled = compile_business_entities(MULTI_CATALOG_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": "premium haircut", "technician": "Alex Tech"},
        "temporal": _temporal_payload(),
        "operation": None,
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["facts"]["service"] == "premium haircut"
    assert merged["facts"]["technician"] == "Alex Tech"
    assert merged["service_term"] == "premium haircut"
    assert merged["facts"]["service_id"] is None


def test_merge_preserves_catalog_plus_enum():
    compiled = compile_business_entities(CATALOG_ENUM_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": "Oil Change", "engine_type": "Petrol"},
        "temporal": _temporal_payload(),
        "operation": None,
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["facts"]["service"] == "Oil Change"
    assert merged["facts"]["engine_type"] == "petrol"
    assert extract_declared_facts(raw["facts"], compiled)["engine_type"] == "petrol"


def test_merge_preserves_catalog_plus_text():
    compiled = compile_business_entities(CATALOG_TEXT_SCHEMA)
    raw = {
        "validated_intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {"service": "Oil Change", "registration_number": "AB12 CDE"},
        "temporal": _temporal_payload(),
        "operation": None,
    }
    merged = _merge(raw, "CREATE_APPOINTMENT", compiled=compiled)
    assert merged["facts"]["service"] == "Oil Change"
    assert merged["facts"]["registration_number"] == "AB12 CDE"


def test_schema_aliases_resolve_bookable_phrase_key():
    """resolve_service returns the matched alias key (Core service_id convention)."""
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    tenant = {"aliases": {}, "booking_mode": "service"}
    effective = effective_tenant_context(tenant, compiled)
    resolved = resolve_service(
        service_term="premium haircut",
        aliases=effective["aliases"],
    )
    assert resolved["service_id"] == "premium haircut"
    assert resolved["service_candidates"] == []


def test_pipeline_resolves_one_catalog_entity():
    compiled = compile_business_entities(SAMPLE_SCHEMA)
    pipe = NLUPipeline()
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {
            "service": "premium haircut",
            "service_id": None,
            "booking_id": None,
            "dates": [],
            "times": [],
            "date_time_pairs": [],
        },
        "service_term": "premium haircut",
        "service_candidates": [],
        "temporal": _temporal_payload(),
    }
    out = pipe._resolve_service_ambiguity(slm, {"aliases": {}}, None, compiled=compiled)
    assert out["facts"]["service"] == "premium haircut"
    assert out["facts"]["service_id"] == "premium haircut"
    assert out["service_candidates"] == []


def test_pipeline_resolves_two_catalog_entities_independently():
    compiled = compile_business_entities(MULTI_CATALOG_SCHEMA)
    pipe = NLUPipeline()
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {
            "service": "premium haircut",
            "technician": "Alex Tech",
            "service_id": None,
            "booking_id": None,
            "dates": [],
            "times": [],
            "date_time_pairs": [],
        },
        "service_term": "premium haircut",
        "service_candidates": [],
        "temporal": _temporal_payload(),
    }
    out = pipe._resolve_service_ambiguity(slm, {"aliases": {}}, None, compiled=compiled)
    assert out["facts"]["service"] == "premium haircut"
    assert out["facts"]["technician"] == "Alex Tech"
    assert out["facts"]["service_id"] == "premium haircut"
    assert out["facts"]["staff_id"] == "alex tech"
    assert out["service_candidates"] == []


def test_pipeline_catalog_ambiguity_does_not_affect_other_catalog():
    compiled = compile_business_entities(MULTI_CATALOG_SCHEMA)
    pipe = NLUPipeline()
    # "haircut" is ambiguous across Premium/Flexi; technician stays unique.
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {
            "service": "haircut",
            "technician": "Sam Tech",
            "service_id": None,
            "booking_id": None,
            "dates": [],
            "times": [],
            "date_time_pairs": [],
        },
        "service_term": "haircut",
        "service_candidates": [],
        "temporal": _temporal_payload(),
    }
    out = pipe._resolve_service_ambiguity(slm, {"aliases": {}}, None, compiled=compiled)
    assert out["facts"]["service_id"] is None
    assert set(out["service_candidates"]) == {"premium haircut", "flexi haircut"}
    assert out["facts"]["staff_id"] == "sam tech"
    assert out["facts"]["technician"] == "Sam Tech"


def test_pipeline_passes_through_enum_and_text_without_resolution():
    compiled = compile_business_entities(
        {
            "version": 1,
            "fields": [
                {
                    "name": "service",
                    "type": "catalog",
                    "role": "bookable_item",
                    "description": "Vehicle service.",
                    "catalog": {"Oil Change": "oil_1"},
                },
                {
                    "name": "engine_type",
                    "type": "enum",
                    "description": "Engine",
                    "values": ["petrol", "diesel"],
                },
                {
                    "name": "registration_number",
                    "type": "text",
                    "description": "Reg",
                },
            ],
        }
    )
    pipe = NLUPipeline()
    slm = {
        "intent": "CREATE_APPOINTMENT",
        "confidence": 0.9,
        "facts": {
            "service": "Oil Change",
            "engine_type": "diesel",
            "registration_number": "XY99 ZZZ",
            "service_id": None,
            "booking_id": None,
            "dates": [],
            "times": [],
            "date_time_pairs": [],
        },
        "service_term": "Oil Change",
        "service_candidates": [],
        "temporal": _temporal_payload(),
    }
    out = pipe._resolve_service_ambiguity(slm, {"aliases": {}}, None, compiled=compiled)
    assert out["facts"]["service_id"] == "oil change"
    assert out["facts"]["engine_type"] == "diesel"
    assert out["facts"]["registration_number"] == "XY99 ZZZ"


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


@patch("nlu.api._pipeline.run")
def test_api_passes_entity_schema(mock_run, client):
    mock_run.return_value = PipelineResult(
        intent={"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        facts={
            "dates": ["2026-07-08"],
            "times": [],
            "date_time_pairs": [],
            "service_id": "premium haircut",
            "booking_id": None,
            "service": "premium haircut",
        },
    )
    response = client.post(
        "/resolve",
        json={
            "text": "Book me a premium haircut tomorrow.",
            "tenant_context": {"booking_mode": "service"},
            "entity_schema": SAMPLE_SCHEMA,
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["intent"]["name"] == "CREATE_APPOINTMENT"
    assert body["facts"]["service_id"] == "premium haircut"
    # Response contract: no entity_schema echo / no new top-level keys required.
    assert "error" not in body
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    if "entity_schema" in call_kwargs:
        assert call_kwargs["entity_schema"] == SAMPLE_SCHEMA
    else:
        assert False, "entity_schema must be passed to pipeline.run"


@patch("nlu.api._pipeline.run")
def test_api_omits_entity_schema_when_absent(mock_run, client):
    mock_run.return_value = PipelineResult(
        intent={"name": "CREATE_APPOINTMENT", "confidence": 0.9},
        facts={
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
    )
    response = client.post(
        "/resolve",
        json={
            "text": "book haircut",
            "tenant_context": {"aliases": {}, "booking_mode": "service"},
        },
    )
    assert response.status_code == 200
    assert mock_run.call_args.kwargs.get("entity_schema") is None


def test_api_rejects_unsupported_entity_type(client):
    response = client.post(
        "/resolve",
        json={
            "text": "book something",
            "tenant_context": {"booking_mode": "service"},
            "entity_schema": {
                "version": 1,
                "fields": [
                    {
                        "name": "weird",
                        "type": "vector",
                        "description": "Nope",
                    }
                ],
            },
        },
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "entity_schema_invalid"
    assert "unsupported entity type" in body["message"]


def test_api_accepts_enum_entity_type(client):
    """Enum is valid at the API boundary; pipeline compile must succeed."""
    with patch("nlu.api._pipeline.run") as mock_run:
        mock_run.return_value = PipelineResult(
            intent={"name": "CREATE_APPOINTMENT", "confidence": 0.9},
            facts={
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": None,
                "booking_id": None,
                "engine_type": "petrol",
            },
        )
        response = client.post(
            "/resolve",
            json={
                "text": "diesel please",
                "tenant_context": {"booking_mode": "service"},
                "entity_schema": {
                    "version": 1,
                    "fields": [
                        {
                            "name": "engine_type",
                            "type": "enum",
                            "description": "Engine",
                            "values": ["petrol", "diesel"],
                        }
                    ],
                },
            },
        )
    assert response.status_code == 200


def test_build_tool_facts_schema_optional_preserves_facts_fields():
    legacy = build_tool(
        name="t",
        description="d",
        facts_fields=["service_term"],
        include_temporal=True,
    )
    custom = build_tool(
        name="t",
        description="d",
        facts_schema={
            "type": "object",
            "properties": {
                "service": {"type": ["string", "null"], "description": "x"},
            },
            "required": ["service"],
        },
        include_temporal=True,
    )
    assert "service_term" in legacy["input_schema"]["properties"]["facts"]["properties"]
    assert "service" in custom["input_schema"]["properties"]["facts"]["properties"]
