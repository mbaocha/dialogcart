"""
Unit tests for _format_conversation_context.

Run: python -m pytest nlu/tests/test_format_conversation_context.py
"""
import sys
from unittest.mock import MagicMock

import pytest

# extractor.py imports anthropic at module load; mock it for pure formatter tests.
sys.modules.setdefault("anthropic", MagicMock())

from nlu.slm.extractor import _format_conversation_context  # noqa: E402


@pytest.mark.parametrize("ctx", [None, {}])
def test_format_conversation_context_empty_returns_empty_string(ctx):
    assert _format_conversation_context(ctx) == ""


def test_format_conversation_context_renders_thread_metadata_and_rules():
    ctx = {
        "last_intent": "DETAILS",
        "last_search_query": "deep tissue massage",
        "turns": [
            {
                "user": "tell me about deep tissue massage",
                "assistant": "Deep tissue massage targets deeper muscle layers.",
                "intent": "DETAILS",
                "search_query": "deep tissue massage",
            }
        ],
    }

    result = _format_conversation_context(ctx)

    assert "CONVERSATION CONTEXT" in result
    assert "Last intent: DETAILS" in result
    assert 'Last search query: "deep tissue massage"' in result
    assert "Prior turns (oldest first):" in result
    assert "User: tell me about deep tissue massage" in result
    assert "Assistant: Deep tissue massage targets deeper muscle layers." in result
    assert '→ intent=DETAILS, search_query="deep tissue massage"' in result
    assert "Context rules:" in result
    assert "merge/refine search_query" in result
