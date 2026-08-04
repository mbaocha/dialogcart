"""REJECT_ACTION dialog-act boundary — dismiss pending proposal vs cancel booking."""

import os
import sys
from pathlib import Path

import pytest

from nlu.stages.shared.reject_dialog_act import reject_action_dialog_act_section
from nlu.stages.stage1.prompt import build_system_prompt
from nlu.stages.stage2.base_prompt import intent_validation_section


_REJECT_MARKERS = (
    "REJECT_ACTION (dialog act — all workflows)",
    "refuses, dismisses, or withdraws authorization",
    "HARD CONSTRAINT: Under a pending confirmation ask",
    '"Cancel that"',
    '"Never mind"',
    '"Not anymore"',
    "CANCEL_BOOKING remains cancellation of an existing booking",
    'The word "cancel" alone must not determine intent',
)


def test_reject_dialog_act_section_distinguishes_proposal_vs_booking():
    section = reject_action_dialog_act_section()
    for marker in _REJECT_MARKERS:
        assert marker in section, marker
    assert "CANCEL_BOOKING" in section
    assert "REJECT_ACTION" in section


def test_stage1_and_stage2_share_identical_reject_dialog_act_rule():
    shared = reject_action_dialog_act_section()
    stage1 = build_system_prompt("2026-08-03T12:00:00", None)
    stage2 = intent_validation_section("REJECT_ACTION")
    assert shared in stage1
    assert shared in stage2


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
    sys.modules.pop("anthropic", None)
    import importlib

    import anthropic  # noqa: F401

    import nlu.stages.shared.confirm_dialog_act as cda
    import nlu.stages.shared.reject_dialog_act as rda
    import nlu.stages.stage1.extractor as s1
    import nlu.stages.stage1.prompt as s1p
    import nlu.stages.stage2.base_prompt as bp
    import nlu.stages.stage2.dispatcher as disp
    import nlu.stages.stage2.groups.cancel as cancel_g
    import nlu.stages.stage2.groups.create as create_g
    from nlu.pipeline import NLUPipeline

    for mod in (cda, rda, s1p, bp, s1, create_g, cancel_g, disp):
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
    [
        "No",
        "Cancel that",
        "Not anymore",
        "Never mind",
        "Forget it",
        "Don't proceed",
        "I don't want to go ahead",
    ],
)
def test_live_pending_dismissals_are_reject_action(text):
    result = _prepare_live_nlu().run(
        text,
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="Europe/London",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert _intent_name(result) == "REJECT_ACTION", result.intent


@pytest.mark.skipif(not _HAS_ANTHROPIC, reason="ANTHROPIC_API_KEY required")
@pytest.mark.parametrize(
    "text",
    [
        "Cancel my booking",
        "Cancel booking ABC123",
        "Cancel my reservation",
    ],
)
def test_live_existing_booking_cancel_remains_cancel_booking(text):
    result = _prepare_live_nlu().run(
        text,
        {"booking_mode": "service", "aliases": {}},
        now="2026-08-03T12:00:00",
        timezone="Europe/London",
        conversation_context=_PENDING_CONFIRM_CTX,
    )
    assert _intent_name(result) == "CANCEL_BOOKING", result.intent
