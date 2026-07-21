"""OFF_TOPIC handler — answer_off_topic orchestration; no business FAQ."""

from unittest.mock import MagicMock, patch

from core.rendering.off_topic import OffTopicEvidence
from extensions.handlers.adapters.off_topic import OffTopicAdapter
from extensions.handlers.runner import HandlerRunner


def test_handler_invokes_answer_fn_and_puts_answer_in_facts():
    answer_fn = MagicMock(
        return_value=OffTopicEvidence(
            answer="Nigeria's current president is Bola Ahmed Tinubu.",
            answerable=True,
        )
    )
    adapter = OffTopicAdapter(answer_fn=answer_fn)
    response = adapter.handle(
        {
            "user_text": "who is president of nigeria",
            "off_topic_query": "Who is the president of Nigeria?",
            "intent_name": "OFF_TOPIC",
            "session": {},
        }
    )
    answer_fn.assert_called_once_with("Who is the president of Nigeria?")
    assert response.facts["answer"] == "Nigeria's current president is Bola Ahmed Tinubu."
    assert response.facts["answerable"] is True
    assert response.facts["scope"] == "off_topic"
    assert response.facts["booking_active"] is False
    instruction = response.render_instruction.lower()
    assert "facts are supplied" in instruction
    assert "facts first" in instruction
    assert "evidence" not in instruction
    assert "time works best" not in instruction
    assert "misunderstood" in instruction


def test_handler_never_calls_faq_client():
    answer_fn = MagicMock(
        return_value=OffTopicEvidence(
            answer="Nigeria's current president is Bola Ahmed Tinubu.",
            answerable=True,
        )
    )
    with patch("extensions.handlers.adapters.rag.FaqClient") as faq_cls:
        adapter = OffTopicAdapter(answer_fn=answer_fn)
        adapter.handle(
            {
                "off_topic_query": "Who is the president of Nigeria?",
                "session": {},
            }
        )
        faq_cls.assert_not_called()


def test_handler_unanswerable_instructs_inability_then_resume_section():
    answer_fn = MagicMock(return_value=OffTopicEvidence(answer=None, answerable=False))
    adapter = OffTopicAdapter(answer_fn=answer_fn)
    response = adapter.handle(
        {
            "off_topic_query": "Tell me a joke",
            "session": {},
        }
    )
    assert response.facts["answerable"] is False
    assert response.facts["answer"] is None
    instruction = response.render_instruction.lower()
    assert "cannot answer" in instruction
    assert "resume" in instruction
    assert "time works best" not in instruction


def test_handler_booking_active_does_not_choose_next_slot():
    answer_fn = MagicMock(
        return_value=OffTopicEvidence(
            answer="Nigeria's current president is Bola Ahmed Tinubu.",
            answerable=True,
        )
    )
    adapter = OffTopicAdapter(answer_fn=answer_fn)
    response = adapter.handle(
        {
            "off_topic_query": "Who is the president of Nigeria?",
            "session": {
                "intent_name": "CREATE_APPOINTMENT",
                "slots": {"service_id": "premium haircut"},
            },
        }
    )
    assert response.facts["booking_active"] is True
    instruction = response.render_instruction.lower()
    assert "time works best" not in instruction
    assert "which service" not in instruction
    assert "ask which" not in instruction


def test_runner_off_topic_uses_llm_answer_path():
    answer_fn = MagicMock(
        return_value=OffTopicEvidence(
            answer="Nigeria's current president is Bola Ahmed Tinubu.",
            answerable=True,
        )
    )
    from extensions.handlers.registry import register_handler

    register_handler(OffTopicAdapter(answer_fn=answer_fn))
    result = HandlerRunner().handle(
        "off_topic",
        {"off_topic_query": "Who is the president of Nigeria?", "session": {}},
    )
    assert result.facts.get("answer")
    assert "facts first" in result.render_instruction.lower()
