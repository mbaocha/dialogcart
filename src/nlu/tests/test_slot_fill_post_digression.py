"""Prompt-contract tests for post-digression booking slot-fill + clock forms."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.slm.extractor import _system_prompt as combined_extractor_prompt
from nlu.stages.shared.confirm_dialog_act import confirm_action_dialog_act_section
from nlu.stages.shared.slot_fill_continuation import slot_fill_continuation_section
from nlu.stages.stage1.prompt import build_system_prompt
from nlu.stages.stage2.base_prompt import temporal_rules
from nlu.stages.stage2.groups.create import _system_prompt as create_system_prompt
from nlu.stages.stage2.in_flow_validation import in_flow_act_validation_rules


_FORBIDDEN_FAQ_EXCLUSION = (
    "Do NOT apply when Last intent is QUOTE, GENERAL_INQUIRY, DISCOVERY, DETAILS"
)

_SLOT_MARKERS = (
    "SLOT-FILL CONTINUATION (active booking context only)",
    "Prefer active_booking_intent when it is set",
    "Do NOT invent booking slots from the FAQ / OFF_TOPIC",
    "Do NOT refuse slot-fill solely because last_intent is QUOTE",
    "CLOCK FORMS",
    '"1.30"',
    "NEVER CONFIRM_ACTION merely because the previous assistant asked which time",
    "time-selection prompt",
    "is NOT a confirmation ask",
    "price is 1.30",
)


def test_shared_slot_fill_section_has_unified_contract():
    section = slot_fill_continuation_section()
    for marker in _SLOT_MARKERS:
        assert marker in section, marker
    assert _FORBIDDEN_FAQ_EXCLUSION not in section


def test_stage1_and_combined_extractor_share_slot_fill_contract():
    shared = slot_fill_continuation_section()
    stage1 = build_system_prompt("2026-08-03T12:00:00", None)
    combined = combined_extractor_prompt(
        "2026-08-03T12:00:00",
        {"haircut": "haircut"},
        booking_mode="service",
        conversation_context=None,
    )
    assert shared in stage1
    assert shared in combined
    assert _FORBIDDEN_FAQ_EXCLUSION not in stage1
    assert _FORBIDDEN_FAQ_EXCLUSION not in combined


def test_create_stage2_includes_slot_fill_and_clock_rules():
    prompt = create_system_prompt(
        "2026-08-03T12:00:00",
        {"booking_mode": "service", "aliases": {"haircut": "haircut"}},
        {
            "last_intent": "OFF_TOPIC",
            "active_booking_intent": "CREATE_APPOINTMENT",
        },
        "CREATE_APPOINTMENT",
    )
    assert slot_fill_continuation_section() in prompt
    assert "1.30" in temporal_rules("2026-08-03T12:00:00")
    assert _FORBIDDEN_FAQ_EXCLUSION not in prompt


def test_confirm_guard_rejects_clock_after_time_selection_ask():
    section = confirm_action_dialog_act_section()
    assert "Time-selection prompts are NOT confirmation asks" in section
    assert "Clock replies" in section
    assert "1.30" in section
    stage1 = build_system_prompt(
        "2026-08-03T12:00:00",
        {
            "last_intent": "OFF_TOPIC",
            "active_booking_intent": "CREATE_APPOINTMENT",
            "turns": [
                {
                    "user": "Does a lion lay eggs?",
                    "intent": "OFF_TOPIC",
                    "assistant": "No.\n\nWhich time works best for your appointment?",
                }
            ],
        },
    )
    assert "Time-selection prompts are NOT confirmation asks" in stage1


def test_in_flow_unknown_validation_allows_clock_after_digression():
    rules = in_flow_act_validation_rules("UNKNOWN")
    assert "1.30" in rules
    assert "Do NOT refuse slot-fill solely because last_intent is QUOTE/FAQ" in rules


def _load_anthropic_key() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    root = Path(__file__).resolve().parents[3]
    for env_path in (root / "src" / "nlu" / ".env", root / "src" / ".env", root / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "ANTHROPIC_API_KEY" and v:
                os.environ["ANTHROPIC_API_KEY"] = v
                return True
    return False


_HAS_ANTHROPIC = _load_anthropic_key()

_TENANT = {
    "booking_mode": "service",
    "aliases": {"haircut": "haircut", "premium haircut": "haircut"},
}


def _prepare_live_nlu():
    sys.modules.pop("anthropic", None)
    import importlib

    import anthropic  # noqa: F401

    import nlu.stages.shared.confirm_dialog_act as cda
    import nlu.stages.shared.slot_fill_continuation as sfc
    import nlu.stages.stage1.extractor as s1
    import nlu.stages.stage1.prompt as s1p
    import nlu.stages.stage2.base_prompt as bp
    import nlu.stages.stage2.dispatcher as disp
    import nlu.stages.stage2.groups.create as create_g
    import nlu.stages.stage2.in_flow_validation as ifv
    from nlu.pipeline import NLUPipeline

    for mod in (sfc, cda, s1p, bp, ifv, s1, create_g, disp):
        importlib.reload(mod)
    disp._instances.clear()
    return NLUPipeline()


def _intent_name(result) -> str:
    intent = result.intent if hasattr(result, "intent") else result.get("intent")
    if isinstance(intent, dict):
        return str(intent.get("name") or "")
    return str(intent or "")


def _times(result):
    facts = result.facts if hasattr(result, "facts") else result.get("facts") or {}
    return list(facts.get("times") or [])


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY not set")
@pytest.mark.parametrize(
    "label,ctx",
    [
        (
            "direct",
            {
                "last_intent": "CREATE_APPOINTMENT",
                "missing_slots": ["date", "time"],
                "turns": [
                    {
                        "user": "book me a premium haircut",
                        "intent": "CREATE_APPOINTMENT",
                        "search_query": None,
                    }
                ],
            },
        ),
        (
            "off_topic",
            {
                "last_intent": "OFF_TOPIC",
                "active_booking_intent": "CREATE_APPOINTMENT",
                "missing_slots": ["time"],
                "turns": [
                    {
                        "user": "book me a premium haircut",
                        "intent": "CREATE_APPOINTMENT",
                        "search_query": None,
                    },
                    {
                        "user": "Does a lion lay eggs?",
                        "intent": "OFF_TOPIC",
                        "assistant": "No.\n\nWhich time works best for your appointment?",
                    },
                ],
            },
        ),
        (
            "quote",
            {
                "last_intent": "QUOTE",
                "last_search_query": "haircut price",
                "active_booking_intent": "CREATE_APPOINTMENT",
                "missing_slots": ["time"],
                "turns": [
                    {
                        "user": "book me a premium haircut",
                        "intent": "CREATE_APPOINTMENT",
                        "search_query": None,
                    },
                    {
                        "user": "how much does a haircut cost?",
                        "intent": "QUOTE",
                        "search_query": "haircut price",
                        "assistant": "A premium haircut is 34 pounds.\n\nWhich time works best?",
                    },
                ],
            },
        ),
        (
            "invalid",
            {
                "last_intent": "CREATE_APPOINTMENT",
                "missing_slots": ["time"],
                "turns": [
                    {
                        "user": "book me a premium haircut",
                        "intent": "CREATE_APPOINTMENT",
                        "search_query": None,
                    },
                    {"user": "xxxxx", "intent": "CREATE_APPOINTMENT"},
                ],
            },
        ),
    ],
)
def test_live_clock_1_30_resumes_booking_after_digression(label, ctx):
    pipeline = _prepare_live_nlu()
    result = pipeline.run(
        "1.30",
        now="2026-07-03T10:00:00",
        tenant_context=_TENANT,
        conversation_context=ctx,
    )
    assert _intent_name(result) == "CREATE_APPOINTMENT", (label, result)
    assert _intent_name(result) != "CONFIRM_ACTION", label
    assert "01:30" in _times(result), (label, _times(result))


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY not set")
def test_live_price_is_1_30_is_not_a_booking_time():
    pipeline = _prepare_live_nlu()
    result = pipeline.run(
        "price is 1.30",
        now="2026-07-03T10:00:00",
        tenant_context=_TENANT,
        conversation_context={
            "last_intent": "CREATE_APPOINTMENT",
            "active_booking_intent": "CREATE_APPOINTMENT",
            "turns": [
                {"user": "book a haircut", "intent": "CREATE_APPOINTMENT"},
            ],
        },
    )
    assert "01:30" not in _times(result)
    assert "13:30" not in _times(result)
