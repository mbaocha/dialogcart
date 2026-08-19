"""
Unit tests for shared conversation context formatting (compact assistant move).

Run: python -m pytest nlu/tests/test_format_conversation_context.py
"""
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("anthropic", MagicMock())

from nlu.stages.shared.context import (  # noqa: E402
    compact_assistant_move,
    format_conversation_context,
)
from nlu.slm.extractor import _format_conversation_context  # noqa: E402
from nlu.stages.stage1.prompt import build_system_prompt  # noqa: E402
from nlu.stages.stage2.groups.faq import _system_prompt as faq_system_prompt  # noqa: E402


@pytest.mark.parametrize("ctx", [None, {}])
def test_format_conversation_context_empty_returns_empty_string(ctx):
    assert format_conversation_context(ctx or {}) == ""
    assert _format_conversation_context(ctx) == ""


def test_format_conversation_context_renders_compact_assistant_not_full_transcript():
    long_assistant = (
        "We're CarOne, a car servicing center, and we offer three main services:\n\n"
        "**Executive Oil Change** — £95\n"
        "We handle your oil change using the best oil type for your vehicle.\n\n"
        "**Premium Full Service** — £85\n"
        "A comprehensive full service.\n\n"
        "**Brake Pad Change** — £25\n"
        "We replace your brake pads.\n\n"
        "Which of these services are you interested in?"
    )
    ctx = {
        "last_intent": "DISCOVERY",
        "last_search_query": "available services",
        "turns": [
            {
                "user": "What services do you offer?",
                "assistant": long_assistant,
                "intent": "DISCOVERY",
                "search_query": "available services",
            }
        ],
    }

    result = format_conversation_context(ctx)

    assert "CONVERSATION CONTEXT" in result
    assert "Last intent: DISCOVERY" in result
    assert 'Last search query: "available services"' in result
    assert "Immediately preceding assistant:" in result
    assert "Asked: Which of these services are you interested in?" in result
    assert "Offered:" in result
    assert "Executive Oil Change" in result
    assert "Premium Full Service" in result
    assert "Brake Pad Change" in result
    # Must not dump the full assistant marketing prose into the prompt.
    assert "We handle your oil change using the best oil type" not in result
    assert "Prior turns (oldest first):" in result
    assert "User: What services do you offer?" in result
    assert "Conversational answer:" in result
    # Shared + legacy wrapper stay aligned.
    assert _format_conversation_context(ctx) == result


def test_format_conversation_context_falls_back_to_messages_for_preceding_ask():
    result = format_conversation_context(
        {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time", "engine_type", "registration_number"],
            "messages": [
                {"role": "user", "text": "Book me an Executive Oil Change"},
                {"role": "assistant", "text": "What engine type does your vehicle use?"},
            ],
            "turns": [
                {
                    "user": "Book me an Executive Oil Change",
                    "intent": "CREATE_APPOINTMENT",
                }
            ],
        }
    )
    assert "Asked: What engine type does your vehicle use?" in result
    assert "Missing slots: date, time, engine_type, registration_number" in result


def test_format_conversation_context_includes_missing_slots_and_resolved_service():
    result = format_conversation_context(
        {
            "last_intent": "CREATE_APPOINTMENT",
            "missing_slots": ["date", "time", "engine_type", "registration_number"],
            "resolved_service_id": 26,
            "turns": [{"user": "Book me an Executive Oil Change", "intent": "CREATE_APPOINTMENT"}],
        }
    )
    assert "Missing slots: date, time, engine_type, registration_number" in result
    assert "Resolved service id: 26" in result


def test_format_conversation_context_includes_authoritative_pending_profile_request():
    result = format_conversation_context(
        {
            "last_intent": "CREATE_APPOINTMENT",
            "pending_profile_request": "CUSTOMER_CONTACT_NAME",
            "messages": [
                {
                    "role": "assistant",
                    "text": "Before we confirm, may I have your name?",
                }
            ],
            "turns": [{"user": "AS123WQ", "intent": "CREATE_APPOINTMENT"}],
        }
    )
    assert "Pending profile request: CUSTOMER_CONTACT_NAME" in result
    assert "Asked: Before we confirm, may I have your name?" in result


def test_pending_profile_request_alone_is_useful_context():
    result = format_conversation_context(
        {"pending_profile_request": {"kind": "CUSTOMER_CONTACT_NAME"}}
    )
    assert result
    assert "Pending profile request: CUSTOMER_CONTACT_NAME" in result


def test_compact_assistant_move_extracts_which_question_and_bullets():
    ask, options = compact_assistant_move(
        "Please choose:\n- Petrol\n- Diesel\n\nWhich engine type?"
    )
    assert ask == "Which engine type?"
    assert options == ["Petrol", "Diesel"]


def test_stage1_prompt_includes_conversational_answer_rule_and_examples():
    prompt = build_system_prompt(
        "2026-08-03T12:00:00",
        {
            "last_intent": "DISCOVERY",
            "turns": [
                {
                    "user": "What services do you offer?",
                    "assistant": (
                        "**Executive Oil Change**\n**Premium Full Service**\n"
                        "Which service would you like?"
                    ),
                    "intent": "DISCOVERY",
                }
            ],
        },
    )
    assert "CONVERSATIONAL ANSWER" in prompt
    assert "Which service would you like?" in prompt
    assert "Executive Oil Change" in prompt
    assert "Which engine type?" in prompt
    assert "Which room?" in prompt
    assert "Which stylist?" in prompt
    assert "Which membership?" in prompt
    assert "the first one" in prompt
    assert "SLOT-FILL CONTINUATION" in prompt
    assert "OFF_TOPIC" in prompt


def test_faq_prompt_distinguishes_answers_from_faq_questions():
    prompt = faq_system_prompt(
        "2026-08-03T12:00:00",
        {
            "last_intent": "DISCOVERY",
            "turns": [
                {
                    "user": "What services do you offer?",
                    "assistant": (
                        "**Executive Oil Change**\n**Premium**\n"
                        "Which service would you like?"
                    ),
                    "intent": "DISCOVERY",
                }
            ],
        },
        "DISCOVERY",
    )
    assert "INTENT VALIDATION (Stage 2 contract" in prompt
    assert "prior only" in prompt
    assert "CONVERSATIONAL ANSWER vs FAQ EXTRACTION" in prompt
    assert "Set search_query to null" in prompt or "search_query to null" in prompt
    assert "Do not keep DISCOVERY/DETAILS/GENERAL_INQUIRY merely because Stage 1" not in prompt
    assert "The user is asking a question, not making a booking." not in prompt
