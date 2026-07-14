"""REST API tests for HANDLER_DELEGATED session persistence."""

from unittest.mock import patch

import pytest

from core.session.session_manager import clear_session, get_session

_FAQ_DATA = {
    "chunks": [
        {
            "id": 7,
            "source_type": "document",
            "source_id": 12,
            "content": "Haircuts start at $25 and include a wash.",
            "score": 0.84,
        }
    ],
    "structured_context": {
        "business_name": "Glamour Studio",
        "business_phone": "+1 555 000 1234",
        "services": [
            {"name": "Haircut", "type": "service", "config": {"price": 25, "duration": 30}}
        ],
        "hours": {"mon": "9am-6pm"},
        "cancellation_policy": {"notice_hours": 24, "fee": "50%"},
        "rescheduling_policy": None,
        "reservations": [],
    },
    "no_hit": False,
}


@pytest.fixture
def rag_api_user_id():
    uid = "test-rag-sess"
    clear_session(uid)
    yield uid
    clear_session(uid)


def test_session_messages_appended_after_handler_delegated(rag_api_user_id, api_client):
    """After HANDLER_DELEGATED turn via the API, session.messages has user+assistant."""
    delegated_result = {
        "success": True,
        "outcome": {
            "status": "HANDLER_DELEGATED",
            "active_handler": "rag",
            "search_query": "business hours",
            "intent_name": "GENERAL_INQUIRY",
            "slots": {},
        },
        "_merged_luma_response": None,
        "message": None,
        "error": None,
    }

    with patch(
        "core.api.message.handle_message", return_value=delegated_result
    ), patch(
        "extensions.handlers.adapters.rag.FaqClient"
    ) as MockFaqClient, patch(
        "core.api.message.render_llm", return_value="We are open Mon–Fri 9am–6pm."
    ):
        MockFaqClient.return_value.retrieve.return_value = _FAQ_DATA
        resp = api_client.post(
            "/api/message",
            json={
                "user_id": rag_api_user_id,
                "text": "what are your hours?",
                "organization_id": 1,
            },
        )

    assert resp.status_code == 200
    session = get_session(rag_api_user_id)
    assert session is not None, "Session must be saved after HANDLER_DELEGATED turn"
    messages = session.get("messages") or []
    assert any(m.get("role") == "user" for m in messages)
    assert any(m.get("role") == "assistant" for m in messages)
