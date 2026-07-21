"""answer_off_topic — concise factual evidence for OFF_TOPIC (not RAG, not render)."""

from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

from core.rendering.off_topic import OffTopicEvidence, answer_off_topic


def _tool_response(answerable: bool, answer: Optional[str]):
    block = SimpleNamespace(
        type="tool_use",
        name="provide_factual_answer",
        input={"answerable": answerable, "answer": answer},
    )
    return SimpleNamespace(content=[block])


def test_answer_off_topic_returns_concise_factual_answer():
    client = MagicMock()
    client.messages.create.return_value = _tool_response(
        True, "Nigeria's current president is Bola Ahmed Tinubu."
    )
    result = answer_off_topic("Who is the president of Nigeria?", client=client)
    assert result.answerable is True
    assert result.answer == "Nigeria's current president is Bola Ahmed Tinubu."
    assert "redirect" not in (result.answer or "").lower()
    client.messages.create.assert_called_once()


def test_answer_off_topic_unanswerable():
    client = MagicMock()
    client.messages.create.return_value = _tool_response(False, None)
    result = answer_off_topic("Which phone should I buy?", client=client)
    assert result == OffTopicEvidence(answer=None, answerable=False)


def test_answer_off_topic_empty_question():
    result = answer_off_topic("  ", client=MagicMock())
    assert result.answerable is False
    assert result.answer is None
