"""Core path: OFF_TOPIC Stage-2 evidence + workflow resume; business FAQ never called.

HTTP path tests live under e2e (need ``api_client``). Pure render-request unit
coverage remains in ``test_off_topic_answer_flow.py``.
"""

from unittest.mock import patch

import pytest

from core.session.session_manager import clear_session


@pytest.fixture
def ot_api_user_id():
    uid = "test-ot-answer-api"
    clear_session(1, uid)
    yield uid
    clear_session(1, uid)


def test_off_topic_engine_answers_then_resumes_without_faq(ot_api_user_id, api_client):
    digression_result = {
        "success": True,
        "outcome": {
            "status": "OFF_TOPIC",
            "search_query": None,
            "off_topic_query": "Who is the president of Nigeria?",
            "answerable": True,
            "answer": "Nigeria's current president is Bola Ahmed Tinubu.",
            "intent_name": "OFF_TOPIC",
            "slots": {},
            "turn": {"understanding": "UNDERSTOOD"},
            "text": (
                "Nigeria's current president is Bola Ahmed Tinubu.\n\n"
                "I'm here primarily to help with this business's services and appointments. "
                "How can I help you with a booking today?"
            ),
        },
        "text": (
            "Nigeria's current president is Bola Ahmed Tinubu.\n\n"
            "I'm here primarily to help with this business's services and appointments. "
            "How can I help you with a booking today?"
        ),
        "_merged_luma_response": None,
        "message": None,
        "error": None,
    }

    with patch(
        "core.api.message._engine.process_turn", return_value=digression_result
    ), patch("extensions.handlers.adapters.rag.FaqClient") as faq_cls:
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

    text = (body.get("text") or body.get("message") or "").lower()
    if not text:
        outcome = body.get("outcome") or {}
        text = (outcome.get("text") or "").lower()
    assert "tinubu" in text
    assert "appointment" in text or "booking" in text or "business" in text
