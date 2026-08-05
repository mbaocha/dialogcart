"""CONFIRM_ACTION dialog-act boundary — authorization vs meta-questions."""

import os
import sys
from pathlib import Path

import pytest

from nlu.stages.shared.confirm_dialog_act import confirm_action_dialog_act_section
from nlu.stages.stage1.prompt import build_system_prompt
from nlu.stages.stage2.base_prompt import intent_validation_section


_CONFIRM_MARKERS = (
    "CONFIRM_ACTION (dialog act — all workflows)",
    "HARD CONSTRAINT: CONFIRM_ACTION is valid only when CONVERSATION CONTEXT contains an",
    "Pending-only override of booking verbs",
    'Empty context + "book it" + Stage 1 CREATE_*',
    "Time-selection prompts are NOT confirmation asks",
    "Clock replies",
    "Meta-questions are never CONFIRM_ACTION",
    "never promote to CONFIRM_ACTION",
    'Not CONFIRM_ACTION: "Did you update it?"',
    "Did you save my changes?",
    "Did you apply my correction?",
    "Are we still booking?",
    "What happens if I confirm?",
)


def test_confirm_dialog_act_section_is_authorization_not_inquiry():
    section = confirm_action_dialog_act_section()
    for marker in _CONFIRM_MARKERS:
        assert marker in section, marker
    assert "registration" not in section.lower()
    assert "engine" not in section.lower()
    assert "CREATE_APPOINTMENT" in section
    assert "CREATE_RESERVATION" in section


def test_stage1_and_stage2_share_identical_confirm_dialog_act_rule():
    shared = confirm_action_dialog_act_section()
    stage1 = build_system_prompt("2026-08-03T12:00:00", None)
    stage2 = intent_validation_section("CONFIRM_ACTION")
    assert shared in stage1
    assert shared in stage2


def test_stage1_booking_verb_rule_defers_to_pending_confirm():
    stage1 = build_system_prompt("2026-08-03T12:00:00", None)
    assert "Exception (pending proposal only)" in stage1
    assert "Cold start / no pending proposal is unchanged" in stage1


def test_stage2_hard_constraint_blocks_confirm_without_ask():
    section = intent_validation_section("CREATE_APPOINTMENT")
    assert "HARD CONSTRAINT: If CONVERSATION CONTEXT has no assistant confirmation ask" in section
    assert "validated_intent must not be CONFIRM_ACTION" in section


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


_PENDING_CONFIRM_CTX = {
    "last_intent": "CREATE_APPOINTMENT",
    "active_booking_intent": "CREATE_APPOINTMENT",
    "turns": [
        {
            "user": "aa123",
            "assistant": "You're about to book. Would you like me to go ahead?",
            "intent": "CREATE_APPOINTMENT",
        }
    ],
    "messages": [
        {
            "role": "assistant",
            "content": "You're about to book. Would you like me to go ahead?",
        }
    ],
}

_HAS_ANTHROPIC = _load_anthropic_key()


def _prepare_live_nlu():
    """Reload extractors against the real Anthropic SDK (avoid suite stubs)."""
    sys.modules.pop("anthropic", None)
    import importlib

    import anthropic  # noqa: F401

    import nlu.stages.shared.confirm_dialog_act as cda
    import nlu.stages.stage1.extractor as s1
    import nlu.stages.stage1.prompt as s1p
    import nlu.stages.stage2.base_prompt as bp
    import nlu.stages.stage2.dispatcher as disp
    import nlu.stages.stage2.groups.create as create_g
    import nlu.stages.stage2.groups.faq as faq_g
    from nlu.pipeline import NLUPipeline

    for mod in (cda, s1p, bp, s1, create_g, faq_g, disp):
        importlib.reload(mod)
    disp._instances.clear()
    return NLUPipeline()


def _intent_name(result) -> str:
    intent = result.intent
    if isinstance(intent, dict):
        return intent.get("name") or ""
    return str(intent or "")


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required")
@pytest.mark.parametrize(
    "text",
    ["Yes", "Go ahead", "Proceed", "Book it", "Reserve it", "Schedule it", "Confirm"],
)
def test_live_authorization_remains_confirm_action(text):
    result = _prepare_live_nlu().run(
        text,
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="Europe/London",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert _intent_name(result) == "CONFIRM_ACTION", result.intent


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required")
def test_live_cold_start_book_it_is_create_not_confirm():
    result = _prepare_live_nlu().run(
        "Book it",
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="Europe/London",
        conversation_context=None,
    )
    assert _intent_name(result) in {
        "CREATE_APPOINTMENT",
        "CREATE_RESERVATION",
    }, result.intent


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required")
@pytest.mark.parametrize(
    "text",
    [
        "Did you update it?",
        "Did you note the correction?",
        "Did you apply my correction?",
        "Is everything correct?",
        "Is everything correct now?",
        "Did you save my changes?",
        "Are we still booking?",
        "What happens if I confirm?",
        "Before confirming...",
        "Can you explain that first?",
    ],
)
def test_live_meta_question_is_not_confirm_action(text):
    pipeline = _prepare_live_nlu()
    from nlu.stages.stage2.groups.create import CreateGroupExtractor
    from nlu.stages.stage2.groups.faq import FAQGroupExtractor

    result = pipeline.run(
        text,
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="Europe/London",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert _intent_name(result) != "CONFIRM_ACTION", result.intent

    create_out = CreateGroupExtractor().extract(
        text=text,
        now="2026-08-03T12:00:00",
        tenant_context={"booking_mode": "service", "aliases": {}},
        candidate_intent="CONFIRM_ACTION",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert create_out.get("intent") != "CONFIRM_ACTION", create_out

    faq_out = FAQGroupExtractor().extract(
        text=text,
        now="2026-08-03T12:00:00",
        tenant_context={"booking_mode": "service", "aliases": {}},
        candidate_intent="GENERAL_INQUIRY",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert faq_out.get("intent") != "CONFIRM_ACTION", faq_out
