"""REST API tests for HANDLER_DELEGATED session persistence."""

from unittest.mock import patch

import pytest

from core.session.session_manager import clear_session, get_session
from core.adapters.nlu.conversation_memory import build_conversation_context
from core.rendering.llm_renderer import HandlerRenderResult
from core.tests.harness.recording_render_client import RecordingRenderClient

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
    clear_session(1, uid)
    yield uid
    clear_session(1, uid)


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
        "core.api.message._engine.process_turn", return_value=delegated_result
    ), patch(
        "extensions.handlers.adapters.rag.FaqClient"
    ) as MockFaqClient, patch(
        "core.api.message.render_handler_response",
        return_value=HandlerRenderResult(text="We are open Mon–Fri 9am–6pm."),
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
    session = get_session(1, rag_api_user_id)
    assert session is not None, "Session must be saved after HANDLER_DELEGATED turn"
    messages = session.get("messages") or []
    assert any(m.get("role") == "user" for m in messages)
    assert any(m.get("role") == "assistant" for m in messages)


def test_recorded_handler_render_result_follows_normal_persistence(
    rag_api_user_id, api_client
):
    delegated_result = {
        "success": True,
        "outcome": {
            "status": "HANDLER_DELEGATED",
            "active_handler": "rag",
            "search_query": "service recommendation",
            "intent_name": "GENERAL_INQUIRY",
            "slots": {},
        },
        "_merged_luma_response": None,
        "message": None,
        "error": None,
    }
    user_text = "what do you recommend?"
    assistant_text = "I recommend the Haircut. Would you like to book it?"
    renderer = RecordingRenderClient({user_text: {
        "text": assistant_text,
        "selected_entities": [{
            "entity_type": "service",
            "display_name": "Haircut",
        }],
    }})

    with patch(
        "core.api.message._engine.process_turn", return_value=delegated_result
    ), patch(
        "extensions.handlers.adapters.rag.FaqClient"
    ) as MockFaqClient, patch(
        "core.api.message.render_handler_response", renderer.render
    ):
        MockFaqClient.return_value.retrieve.return_value = _FAQ_DATA
        response = api_client.post(
            "/api/message",
            json={
                "user_id": rag_api_user_id,
                "text": user_text,
                "organization_id": 1,
            },
        )

    assert response.status_code == 200, response.json()
    assert response.json()["text"] == assistant_text
    assert renderer.last_request is not None
    assert renderer.last_request.user_request == user_text
    session = get_session(1, rag_api_user_id)
    assert session is not None
    messages = session.get("messages") or []
    assert {"role": "assistant", "text": assistant_text} in messages
    turns = ((session.get("conversation") or {}).get("memory") or {}).get("turns") or []
    assert turns[-1]["assistant"] == assistant_text
    proposals = (session.get("conversation") or {}).get("pending_proposals") or []
    assert proposals[0]["display_name"] == "Haircut"
    assert proposals[0]["canonical_id"] == "haircut"
    assert "service_id" not in (session.get("planning") or {}).get("slots", {})
    reloaded_context = build_conversation_context(session)
    assert reloaded_context is not None
    exposed = reloaded_context["pending_assistant_proposals"]
    assert exposed[0]["canonical_id"] == "haircut"
    assert "proposal_id" not in exposed[0]
    assert "source" not in exposed[0]
