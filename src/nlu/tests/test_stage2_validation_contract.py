"""Stage 2 shared Intent Validation Contract — equal authority across groups."""

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.shared.confirm_dialog_act import confirm_action_dialog_act_section
from nlu.stages.shared.reject_dialog_act import reject_action_dialog_act_section
from nlu.stages.stage2 import dispatcher
from nlu.stages.stage2.base_prompt import build_tool, intent_validation_section
from nlu.stages.stage2.groups.availability import _system_prompt as availability_prompt
from nlu.stages.stage2.groups.cancel import _system_prompt as cancel_prompt
from nlu.stages.stage2.groups.create import _system_prompt as create_prompt
from nlu.stages.stage2.groups.faq import _system_prompt as faq_prompt
from nlu.stages.stage2.groups.modify import _system_prompt as modify_prompt
from nlu.stages.stage2.groups.view import _system_prompt as view_prompt


_CONTRACT_MARKERS = (
    "INTENT VALIDATION (Stage 2 contract",
    "prior only",
    "NOT the truth",
    "semantic authority",
    "Do NOT keep the proposal",
    "merely because Stage 1 suggested it",
)

_PRESERVE_BY_DEFAULT_MARKERS = (
    "If still unclear with no active booking context and not a conversational",
    "null to accept Stage 1's classification",
    "keep CORRECTION",
)


def test_shared_contract_rejects_preserve_by_default():
    section = intent_validation_section("CORRECTION")
    for marker in _CONTRACT_MARKERS:
        assert marker in section, marker
    for marker in _PRESERVE_BY_DEFAULT_MARKERS:
        assert marker not in section, marker
    # Candidate appears as proposal, not as sticky default keep-rule target alone.
    assert "Stage 1 proposal (prior only" in section
    assert "CORRECTION" in section


def test_shared_contract_includes_confirm_dialog_act_rule():
    section = intent_validation_section("CONFIRM_ACTION")
    assert "CONFIRM_ACTION (dialog act — all workflows)" in section
    assert "HARD CONSTRAINT: CONFIRM_ACTION is valid only when CONVERSATION CONTEXT contains an" in section
    assert "Meta-questions are never CONFIRM_ACTION" in section
    assert "HARD CONSTRAINT: If CONVERSATION CONTEXT has no assistant confirmation ask" in section
    assert confirm_action_dialog_act_section() in section


def test_shared_contract_includes_reject_dialog_act_rule():
    section = intent_validation_section("REJECT_ACTION")
    assert "REJECT_ACTION (dialog act — all workflows)" in section
    assert reject_action_dialog_act_section() in section
    assert "CANCEL_BOOKING remains cancellation of an existing booking" in section
    assert "Cancel that" in section


def test_validated_intent_tool_field_is_decision_not_null_accept():
    tool = build_tool(
        name="t",
        description="d",
        facts_fields=[],
        include_validated_intent=True,
        include_time_constraint=False,
    )
    desc = tool["input_schema"]["properties"]["validated_intent"]["description"]
    assert "Accept Stage 1's proposal only when" in desc
    assert "CONFIRM_ACTION only if context shows a pending" in desc
    assert "CORRECTION only when a workflow slot/selection is being changed" in desc
    assert "null to accept Stage 1" not in desc


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (
            create_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {"booking_mode": "service", "aliases": {}},
                "conversation_context": None,
                "candidate_intent": "CORRECTION",
            },
        ),
        (
            faq_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "conversation_context": None,
                "candidate_intent": "DISCOVERY",
            },
        ),
        (
            view_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {},
                "conversation_context": None,
                "candidate_intent": "BOOKING_INQUIRY",
            },
        ),
        (
            modify_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {},
                "conversation_context": None,
                "candidate_intent": "MODIFY_BOOKING",
            },
        ),
        (
            cancel_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {},
                "conversation_context": None,
                "candidate_intent": "CANCEL_BOOKING",
            },
        ),
        (
            availability_prompt,
            {
                "now": "2026-08-03T12:00:00",
                "tenant_context": {"aliases": {}},
                "conversation_context": None,
                "candidate_intent": "AVAILABILITY",
            },
        ),
    ],
)
def test_every_group_prompt_starts_with_shared_validation_contract(builder, kwargs):
    prompt = builder(**kwargs)
    # Validation contract is the first block (groups begin from shared authority).
    assert prompt.lstrip().startswith("════")
    assert "INTENT VALIDATION (Stage 2 contract" in prompt.split("── EXTRACTION", 1)[0]
    for marker in _CONTRACT_MARKERS:
        assert marker in prompt, f"{builder.__module__}: missing {marker}"
    # No FAQ-only anti-stickiness left as the sole authority source.
    assert "authoritative for this group" not in prompt


@pytest.fixture(autouse=True)
def _clear_extractor_cache():
    dispatcher._instances.clear()
    yield
    dispatcher._instances.clear()


@pytest.mark.parametrize(
    "stage1_intent,validated_intent,expected_first_group,expected_second_group",
    [
        ("CORRECTION", "GENERAL_INQUIRY", "create", "faq"),
        ("CREATE_APPOINTMENT", "DISCOVERY", "create", "faq"),
        ("DISCOVERY", "CREATE_APPOINTMENT", "faq", "create"),
        ("BOOKING_INQUIRY", "CANCEL_BOOKING", "view", "cancel"),
        ("CANCEL_BOOKING", "MODIFY_BOOKING", "cancel", "modify"),
        ("MODIFY_BOOKING", "AVAILABILITY", "modify", "availability"),
    ],
)
def test_stage2_rejection_triggers_stage3_regardless_of_initial_group(
    stage1_intent,
    validated_intent,
    expected_first_group,
    expected_second_group,
):
    """Wrong Stage 1 proposal → group validates to another family → Stage 3 re-extracts."""
    first_out = {
        "intent": validated_intent,
        "confidence": 0.85,
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "time_constraint": None,
        "search_query": "pricing" if expected_second_group == "faq" else None,
        "service_candidates": [],
    }
    second_out = {
        **first_out,
        "confidence": 0.9,
        "search_query": "pricing total" if expected_second_group == "faq" else None,
    }

    mock_first = MagicMock()
    mock_first.extract.return_value = first_out
    mock_second = MagicMock()
    mock_second.extract.return_value = second_out
    mocks = {
        expected_first_group: mock_first,
        expected_second_group: mock_second,
    }

    def _get_extractor(group: str):
        if group not in mocks:
            raise AssertionError(f"Unexpected group {group}; expected {list(mocks)}")
        return mocks[group]

    with patch.object(dispatcher, "_get_extractor", side_effect=_get_extractor):
        result = dispatcher.extract_slots(
            intent=stage1_intent,
            text="I thought it's 95 with a 10 reservation fee",
            now="2026-08-03T12:00:00",
            tenant_context={"booking_mode": "service", "aliases": {}},
            conversation_context={
                "last_intent": "GENERAL_INQUIRY",
                "active_booking_intent": "CREATE_APPOINTMENT",
            },
        )

    assert mock_first.extract.call_count == 1
    assert mock_first.extract.call_args.kwargs["candidate_intent"] == stage1_intent
    assert mock_second.extract.call_count == 1
    assert mock_second.extract.call_args.kwargs["candidate_intent"] == validated_intent
    assert result["intent"] == validated_intent
