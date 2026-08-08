"""Wiring contracts for correction precedence; these do not test model compliance."""

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.stage1.prompt import build_system_prompt, get_tool as get_stage1_tool
from nlu.stages.stage2.groups.create import (
    _system_prompt as create_system_prompt,
    build_create_tool,
)


NOW = "2026-07-01T10:00:00Z"
PENDING_CONTEXT = {
    "last_intent": "CREATE_APPOINTMENT",
    "confirmation_state": "pending",
    "messages": [
        {
            "role": "assistant",
            "text": "You're about to book at 10:00 AM. Would you like me to go ahead?",
        }
    ],
}


def test_active_stage1_prompt_contains_mixed_affirmative_correction_precedence():
    prompt = build_system_prompt(NOW, PENDING_CONTEXT)

    assert "CORRECTION OVERRIDES AN AFFIRMATIVE PREFIX" in prompt
    assert "not CONFIRM_ACTION and not CREATE_*" in prompt
    assert "does not authorize the stale proposal" in prompt
    assert '"Yes, but make it 11." → CORRECTION' in prompt
    stage1_tool = get_stage1_tool()
    assert stage1_tool["name"] == "classify_intent"
    intent_description = stage1_tool["input_schema"]["properties"]["intent"]["description"]
    assert "affirmative prefix followed by an explicit workflow-slot correction" in intent_description
    assert "does not authorize the stale proposal" in intent_description


def test_active_stage2_create_prompt_contains_self_correction_precedence():
    prompt = create_system_prompt(
        now=NOW,
        tenant_context={"booking_mode": "service", "aliases": {}},
        conversation_context=PENDING_CONTEXT,
        candidate_intent="CORRECTION",
    )

    for marker in (
        "CORRECTION OVERRIDES AN AFFIRMATIVE PREFIX",
        '"Yes, but make it 11." → CORRECTION',
        "Explicit self-correction precedence:",
        'Cues such as "sorry", "I mean", "actually", and "not X, Y"',
        "The final corrected mention is authoritative",
        "Exclude explicitly abandoned",
        "must all represent the same winning mention",
        '"Friday—sorry, Saturday." → expression="Saturday"',
        "start_date=<ISO for Saturday>",
        "not ordinary alternatives or ranges",
    ):
        assert marker in prompt, marker


def test_active_create_tool_schema_describes_consistent_correction_output():
    tool = build_create_tool(None)
    props = tool["input_schema"]["properties"]
    temporal_schema = props["temporal"]
    temporal = temporal_schema["properties"]

    intent_description = props["validated_intent"]["description"]
    assert "affirmative prefix followed by an explicit slot correction" in intent_description
    assert "does not authorize the stale proposal" in intent_description
    assert "corrected winning mention" in temporal["expression"]["description"]
    assert "same corrected winning mention" in temporal_schema["description"]
    assert "corrected winning mention" in temporal["start_date_expression"]["description"]
    assert "never the abandoned date" in temporal["start_date"]["description"]
    assert "corrected winning time" in temporal["start_time"]["description"]

    # Schema shape and enums remain unchanged.
    stage1_intents = get_stage1_tool()["input_schema"]["properties"]["intent"]["enum"]
    assert props["validated_intent"]["enum"] == stage1_intents + [None]
    assert temporal["mode"]["description"].startswith(
        "none|single_day|range|flexible"
    )
    assert set(temporal_schema["required"]) == set(temporal)
