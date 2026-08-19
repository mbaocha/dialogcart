from __future__ import annotations

import sys
from copy import deepcopy
from unittest.mock import MagicMock, patch

sys.modules.setdefault("anthropic", MagicMock())

from nlu.pipeline import NLUPipeline
from nlu.stages.stage2 import dispatcher
from nlu.stages.stage2.base_prompt import build_tool
from nlu.stages.stage2.prompt_cache import prefix_fingerprint
from nlu.stages.stage2.semantic_validation import validate_final_stage2_result


PENDING = {
    "confirmation_state": "pending",
    "last_intent": "CREATE_APPOINTMENT",
}


def _result(intent: str, proposal_response=None, *, facts=None) -> dict:
    return {
        "intent": intent,
        "proposal_response": proposal_response,
        "confidence": 0.9,
        "facts": facts or {
            "dates": [], "times": [], "date_time_pairs": [],
            "service_id": None, "booking_id": None,
        },
        "time_constraint": None,
        "search_query": None,
    }


def test_pending_legacy_confirm_routes_through_stage2_and_accepts() -> None:
    extractor = MagicMock()
    extractor.extract.return_value = _result("CREATE_APPOINTMENT", "ACCEPT")
    with patch.object(dispatcher, "_get_extractor", return_value=extractor):
        result = dispatcher.extract_slots(
            intent="CONFIRM_ACTION", text="yes", now="now",
            tenant_context={}, conversation_context=PENDING,
        )
    assert extractor.extract.call_args.kwargs["candidate_intent"] == "CREATE_APPOINTMENT"
    assert result["proposal_response"] == "ACCEPT"
    assert result["response_act"] == "CONFIRM_ACTION"


def test_pending_legacy_reject_routes_through_stage2_and_rejects() -> None:
    extractor = MagicMock()
    extractor.extract.return_value = _result("CREATE_APPOINTMENT", "REJECT")
    with patch.object(dispatcher, "_get_extractor", return_value=extractor):
        result = dispatcher.extract_slots(
            intent="REJECT_ACTION", text="no", now="now",
            tenant_context={}, conversation_context=PENDING,
        )
    assert result["proposal_response"] == "REJECT"
    assert result["response_act"] == "REJECT_ACTION"


def test_pending_correction_is_modify_and_never_accepts() -> None:
    changed = _result(
        "CORRECTION", "MODIFY",
        facts={"dates": [], "times": ["11:00"], "date_time_pairs": [],
               "service_id": None, "booking_id": None},
    )
    result = validate_final_stage2_result(changed, PENDING)
    assert result["intent"] == "CORRECTION"
    assert result["proposal_response"] == "MODIFY"
    assert result["response_act"] is None


def test_correction_with_stale_acceptance_is_suppressed() -> None:
    changed = _result(
        "CORRECTION", "ACCEPT",
        facts={"dates": [], "times": ["11:00"], "date_time_pairs": [],
               "service_id": None, "booking_id": None},
    )
    result = validate_final_stage2_result(changed, PENDING)
    assert result["proposal_response"] is None
    assert result["response_act"] is None


def test_redispatch_uses_only_destination_proposal_response() -> None:
    create = MagicMock()
    create.extract.return_value = _result("AVAILABILITY", "ACCEPT")
    availability = MagicMock()
    availability.extract.return_value = _result("AVAILABILITY", None)

    def get_extractor(group: str):
        return {"create": create, "availability": availability}[group]

    with patch.object(dispatcher, "_get_extractor", side_effect=get_extractor):
        result = dispatcher.extract_slots(
            intent="CONFIRM_ACTION", text="show availability", now="now",
            tenant_context={}, conversation_context=PENDING,
        )
    assert result["intent"] == "AVAILABILITY"
    assert result["proposal_response"] is None
    assert result["response_act"] is None


def test_pending_quote_and_off_topic_have_no_proposal_response() -> None:
    for intent in ("QUOTE", "OFF_TOPIC"):
        result = validate_final_stage2_result(_result(intent, "ACCEPT"), PENDING)
        assert result["proposal_response"] is None
        assert result["response_act"] is None


def test_pending_unknown_cannot_authorize() -> None:
    result = validate_final_stage2_result(_result("UNKNOWN", "ACCEPT"), PENDING)
    assert result["proposal_response"] is None
    assert result["response_act"] is None


def test_pending_requested_input_suppresses_authorization_for_any_schema_field() -> None:
    for requested in ("CUSTOMER_CONTACT_NAME", "ENGINE_TYPE"):
        context = {**PENDING, "pending_profile_request": requested}
        result = validate_final_stage2_result(
            _result("CREATE_APPOINTMENT", "ACCEPT", facts={requested.lower(): "value"}),
            context,
        )
        assert result["proposal_response"] is None
        assert result["response_act"] is None


def test_assistant_confirmation_wording_without_structured_pending_cannot_authorize() -> None:
    context = {
        "last_intent": "CREATE_APPOINTMENT",
        "messages": [{"role": "assistant", "text": "Should I confirm it?"}],
    }
    result = validate_final_stage2_result(
        _result("CREATE_APPOINTMENT", "ACCEPT"), context
    )
    assert result["response_act"] is None


def test_stage1_response_act_cannot_overwrite_final_stage2_null(monkeypatch) -> None:
    pipeline = object.__new__(NLUPipeline)
    pipeline._stage1 = type("Stage1", (), {"classify": lambda *_: {
        "intent": "CONFIRM_ACTION", "confidence": 0.9,
        "response_act": "CONFIRM_ACTION",
    }})()
    monkeypatch.setattr("nlu.pipeline._stage2_extract", lambda **_: {
        **_result("OFF_TOPIC", None), "response_act": None,
    })
    result = pipeline._slm_extract("question", {}, "now", PENDING)
    assert result["intent"] == "OFF_TOPIC"
    assert result["response_act"] is None


def test_legacy_primary_dialogue_intent_without_new_field_remains_consumable() -> None:
    legacy = _result("CONFIRM_ACTION")
    legacy.pop("proposal_response")
    result = validate_final_stage2_result(legacy, PENDING)
    assert result["proposal_response"] == "ACCEPT"
    assert result["response_act"] == "CONFIRM_ACTION"


def test_proposal_response_changes_cache_fingerprint_deterministically() -> None:
    tool = build_tool(name="t", description="d", facts_fields=[])
    same = deepcopy(tool)
    old_shape = deepcopy(tool)
    old_shape["input_schema"]["properties"].pop("proposal_response")
    old_shape["input_schema"]["required"].remove("proposal_response")
    assert prefix_fingerprint(tool, "stable") == prefix_fingerprint(same, "stable")
    assert prefix_fingerprint(tool, "stable") != prefix_fingerprint(old_shape, "stable")
