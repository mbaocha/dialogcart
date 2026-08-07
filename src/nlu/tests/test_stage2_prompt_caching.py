"""Focused request-shape coverage for Stage 2 prompt caching (not live LLM)."""
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.stage2.entity_schema import compile_business_entities
from nlu.stages.stage2.groups.availability import (
    _TOOL as AVAILABILITY_TOOL,
    _prompt_blocks as availability_blocks,
)
from nlu.stages.stage2.groups.create import (
    _LEGACY_CREATE_TOOL_KWARGS,
    _prompt_blocks as create_blocks,
    build_create_tool,
)
from nlu.stages.stage2.prompt_cache import (
    HAIKU_45_MIN_CACHE_TOKENS,
    cache_eligibility,
    log_usage,
    prefix_fingerprint,
    system_blocks,
)


SCHEMA = {
    "version": 1,
    "fields": [
        {
            "name": "service",
            "type": "catalog",
            "role": "bookable_item",
            "required": True,
            "availability_criteria": True,
            "description": "Vehicle service.",
            "catalog": {"Oil Change": 26, "Full Service": 27},
        },
        {
            "name": "engine_type",
            "type": "enum",
            "required": True,
            "availability_criteria": True,
            "description": "Engine type.",
            "values": ["petrol", "diesel"],
        },
    ],
}


def _context():
    return {
        "last_intent": "CREATE_APPOINTMENT",
        "active_booking_intent": "CREATE_APPOINTMENT",
        "pending_assistant_proposals": [
            {
                "proposal_type": "catalog_entity",
                "entity_type": "service",
                "slot_key": "service_id",
                "canonical_id": "full service",
                "display_name": "Full Service",
                "expected_responses": ["confirm", "reject"],
            }
        ],
        "service_candidates": ["Oil Change", "Full Service"],
        "missing_slots": ["service_id"],
    }


def test_create_stable_prefix_ignores_request_specific_values():
    compiled = compile_business_entities(SCHEMA)
    tenant = {"booking_mode": "service", "aliases": {"oil change": 26}}
    first_stable, first_dynamic = create_blocks(
        "2026-08-01T10:00:00", tenant, None, "CREATE_APPOINTMENT", compiled
    )
    second_stable, second_dynamic = create_blocks(
        "2026-09-02T12:30:00", tenant, _context(), "CORRECTION", compiled
    )
    assert first_stable == second_stable
    assert first_dynamic != second_dynamic
    assert "Stage 1 proposal (prior only — NOT the truth): CORRECTION" not in first_stable
    assert "2026-09-02" not in first_stable
    assert "Active assistant proposals" not in first_stable
    assert "Stage 1 proposal (prior only — NOT the truth): CORRECTION" in second_dynamic
    assert "2026-09-02" in second_dynamic
    assert "Active assistant proposals" in second_dynamic
    assert "Oil Change" in second_dynamic


def test_availability_stable_prefix_ignores_request_specific_values():
    tenant = {"aliases": {"oil change": 26, "full service": 27}}
    first_stable, first_dynamic = availability_blocks(
        "2026-08-01T10:00:00", tenant, None, "AVAILABILITY"
    )
    second_stable, second_dynamic = availability_blocks(
        "2026-09-02T12:30:00", tenant, _context(), "CORRECTION"
    )
    assert first_stable == second_stable
    assert first_dynamic != second_dynamic
    assert "Stage 1 proposal (prior only — NOT the truth): CORRECTION" not in first_stable
    assert "2026-09-02" in second_dynamic
    assert "Active assistant proposals" in second_dynamic


def test_meaningful_schema_change_changes_create_prefix_and_fingerprint():
    first = compile_business_entities(SCHEMA)
    changed_schema = {
        **SCHEMA,
        "fields": [
            *SCHEMA["fields"],
            {
                "name": "registration_number",
                "type": "text",
                "required": True,
                "description": "Registration number.",
            },
        ],
    }
    second = compile_business_entities(changed_schema)
    tenant = {"booking_mode": "service", "aliases": {}}
    first_prefix, _ = create_blocks("now", tenant, None, "CREATE_APPOINTMENT", first)
    second_prefix, _ = create_blocks("now", tenant, None, "CREATE_APPOINTMENT", second)
    assert first_prefix != second_prefix
    assert prefix_fingerprint(build_create_tool(first), first_prefix) != prefix_fingerprint(
        build_create_tool(second), second_prefix
    )


def test_semantically_unordered_field_mapping_keys_compile_identically():
    reordered = {
        "version": 1,
        "fields": [
            {
                "catalog": {"Oil Change": 26, "Full Service": 27},
                "description": "Vehicle service.",
                "availability_criteria": True,
                "required": True,
                "role": "bookable_item",
                "type": "catalog",
                "name": "service",
            },
            {
                "values": ["petrol", "diesel"],
                "description": "Engine type.",
                "availability_criteria": True,
                "required": True,
                "type": "enum",
                "name": "engine_type",
            },
        ],
    }
    assert compile_business_entities(SCHEMA) == compile_business_entities(reordered)


def test_catalog_order_is_preserved_as_meaningful_prompt_order():
    reversed_schema = {
        **SCHEMA,
        "fields": [
            {
                **SCHEMA["fields"][0],
                "catalog": {"Full Service": 27, "Oil Change": 26},
            },
            SCHEMA["fields"][1],
        ],
    }
    first = compile_business_entities(SCHEMA)
    second = compile_business_entities(reversed_schema)
    assert '"Oil Change", "Full Service"' in first.prompt_rules
    assert '"Full Service", "Oil Change"' in second.prompt_rules
    assert first.prompt_rules != second.prompt_rules


def test_cache_control_attaches_only_when_provider_count_meets_minimum():
    tool = AVAILABILITY_TOOL
    client = MagicMock()
    client.messages.count_tokens.return_value = SimpleNamespace(
        input_tokens=HAIKU_45_MIN_CACHE_TOKENS
    )
    eligible, count, _ = cache_eligibility(
        client, model="eligible-model", tool=tool, stable_text="eligible-prefix"
    )
    blocks = system_blocks("eligible-prefix", "dynamic", eligible=eligible)
    assert count == HAIKU_45_MIN_CACHE_TOKENS
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]

    client.messages.count_tokens.return_value = SimpleNamespace(
        input_tokens=HAIKU_45_MIN_CACHE_TOKENS - 1
    )
    eligible, _, _ = cache_eligibility(
        client, model="ineligible-model", tool=tool, stable_text="short-prefix"
    )
    assert not eligible
    assert "cache_control" not in system_blocks(
        "short-prefix", "dynamic", eligible=eligible
    )[0]


def test_usage_fields_are_captured_and_absence_defaults_safely(caplog):
    caplog.set_level(logging.INFO)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=5000,
            cache_creation_input_tokens=4200,
            cache_read_input_tokens=0,
            output_tokens=120,
        )
    )
    log_usage(
        response,
        model="claude-haiku-4-5-20251001",
        group="create",
        prefix="opaque",
        prefix_tokens=4200,
        cache_eligible=True,
        cache_control_applied=True,
    )
    log_usage(
        SimpleNamespace(),
        model="claude-haiku-4-5-20251001",
        group="availability",
        prefix="opaque2",
        prefix_tokens=None,
        cache_eligible=False,
        cache_control_applied=False,
    )
    assert "cache_creation_input_tokens=4200" in caplog.text
    assert "cache_read_input_tokens=0" in caplog.text
    assert "prefix_below_minimum_or_count_unavailable" in caplog.text


def test_existing_stage2_tool_contracts_remain_present():
    create_tool = build_create_tool(None)
    assert create_tool["name"] == _LEGACY_CREATE_TOOL_KWARGS["name"]
    assert set(create_tool["input_schema"]["properties"]) == {
        "validated_intent",
        "confidence",
        "temporal",
        "facts",
        "operation",
    }
    assert AVAILABILITY_TOOL["name"] == "extract_availability_slots"
