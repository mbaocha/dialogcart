"""API path: OFF_TOPIC answer + workflow resume; business FAQ never called."""

from unittest.mock import MagicMock, patch

import pytest

from core.rendering.off_topic import OffTopicEvidence
from core.session.session_manager import clear_session
from extensions.handlers.adapters.off_topic import OffTopicAdapter
from extensions.handlers.registry import register_handler


@pytest.fixture
def ot_api_user_id():
    uid = "test-ot-answer-api"
    clear_session(1, uid)
    yield uid
    clear_session(1, uid)


def test_off_topic_api_answers_then_resumes_without_faq(ot_api_user_id, api_client):
    delegated_result = {
        "success": True,
        "outcome": {
            "status": "HANDLER_DELEGATED",
            "active_handler": "off_topic",
            "search_query": None,
            "off_topic_query": "Who is the president of Nigeria?",
            "intent_name": "OFF_TOPIC",
            "slots": {},
            "turn": {"understanding": "UNDERSTOOD"},
        },
        "_merged_luma_response": None,
        "message": None,
        "error": None,
    }

    answer_fn = MagicMock(
        return_value=OffTopicEvidence(
            answer="Nigeria's current president is Bola Ahmed Tinubu.",
            answerable=True,
        )
    )
    register_handler(OffTopicAdapter(answer_fn=answer_fn))

    with patch(
        "core.api.message._engine.process_turn", return_value=delegated_result
    ), patch("extensions.handlers.adapters.rag.FaqClient") as faq_cls, patch(
        "core.api.message.render_llm"
    ) as render_mock:
        render_mock.return_value = (
            "Nigeria's current president is Bola Ahmed Tinubu.\n\n"
            "I'm here primarily to help with this business's services and appointments. "
            "How can I help you with a booking today?"
        )
        resp = api_client.post(
            "/api/message",
            json={
                "user_id": ot_api_user_id,
                "text": "Who is the president of Nigeria?",
                "organization_id": 1,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    faq_cls.assert_not_called()
    answer_fn.assert_called_once_with("Who is the president of Nigeria?")
    render_mock.assert_called_once()
    render_req = render_mock.call_args[0][0]
    assert render_req.facts.get("answer") == (
        "Nigeria's current president is Bola Ahmed Tinubu."
    )
    assert render_req.user_request == "Who is the president of Nigeria?"
    assert "facts first" in render_req.render_instruction.lower()
    assert "evidence" not in render_req.render_instruction.lower()
    assert "time works best" not in render_req.render_instruction.lower()
    assert isinstance(render_req.facts.get("resume_instruction"), str)
    assert render_req.facts["resume_instruction"].strip()
    assert "time works best" not in render_req.facts["resume_instruction"].lower()

    text = (body.get("text") or body.get("message") or "").lower()
    if not text:
        outcome = body.get("outcome") or {}
        text = (outcome.get("text") or "").lower()
    assert "tinubu" in text
    assert "appointment" in text or "booking" in text or "business" in text
