from __future__ import annotations

from nlu.pipeline import NLUPipeline
from nlu.stages.shared.context import format_conversation_context
from nlu.stages.stage1.prompt import get_tool


def _proposal():
    return {
        "proposal_type": "ENTITY_RECOMMENDATION",
        "status": "PENDING",
        "entity_type": "service",
        "slot_key": "service_id",
        "canonical_id": "premium full service",
        "display_name": "Premium Full Service",
        "expected_responses": ["ACCEPT", "REJECT"],
    }


def test_pending_proposal_is_in_actual_formatted_luma_context() -> None:
    prompt = format_conversation_context({"pending_assistant_proposals": [_proposal()]})
    assert "Active assistant proposals:" in prompt
    assert "canonical_id=premium full service" in prompt
    assert "expected_responses=['ACCEPT', 'REJECT']" in prompt


def test_stage1_contract_has_additive_generic_response_act() -> None:
    schema = get_tool()["input_schema"]
    assert schema["properties"]["response_act"]["enum"] == [
        "CONFIRM_ACTION", "REJECT_ACTION", None
    ]
    assert "response_act" in schema["required"]


def test_availability_and_temporal_keep_additive_confirmation_evidence(monkeypatch) -> None:
    pipeline = object.__new__(NLUPipeline)
    pipeline._stage1 = type("Stage1", (), {"classify": lambda *_: {
        "intent": "AVAILABILITY", "confidence": 0.9,
        "response_act": "CONFIRM_ACTION",
    }})()
    monkeypatch.setattr("nlu.pipeline._stage2_extract", lambda **_: {
        "intent": "AVAILABILITY", "confidence": 0.9, "facts": {},
        "service_candidates": [], "service_term": None,
        "temporal": {"start_date_expression": "next week", "mode": "flexible"},
    })
    result = pipeline._slm_extract(
        "Yeah, maybe next week", {}, "2026-07-01T00:00:00Z", {}
    )
    assert result["intent"] == "AVAILABILITY"
    assert result["response_act"] == "CONFIRM_ACTION"
    assert result["temporal"]["start_date_expression"] == "next week"


def test_rejection_is_additive_evidence(monkeypatch) -> None:
    pipeline = object.__new__(NLUPipeline)
    pipeline._stage1 = type("Stage1", (), {"classify": lambda *_: {
        "intent": "AVAILABILITY", "confidence": 0.9,
        "response_act": "REJECT_ACTION",
    }})()
    monkeypatch.setattr("nlu.pipeline._stage2_extract", lambda **_: {
        "intent": "AVAILABILITY", "confidence": 0.9, "facts": {},
        "service_candidates": [], "service_term": None,
        "temporal": {},
    })
    result = pipeline._slm_extract("No, what else?", {}, "now", {})
    assert result["intent"] == "AVAILABILITY"
    assert result["response_act"] == "REJECT_ACTION"
