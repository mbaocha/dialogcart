"""
Integration tests: RAG / HANDLER_DELEGATED path.

Covers:
- Turn 1: GENERAL_INQUIRY → HANDLER_DELEGATED; handler returns render_instruction + facts
- Turn 2: session.conversation → luma.resolve called with conversation_context
- Session: messages appended after HANDLER_DELEGATED turn (via API endpoint)
- Booking intent: CREATE_APPOINTMENT → NOT HANDLER_DELEGATED
- Missing organization_id → no HTTP call to commerce
"""

import os
from unittest.mock import Mock, patch

import pytest

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session, get_session

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

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


def _rag_luma_mock(search_query: str = "return policies") -> Mock:
    mock = Mock(spec=LumaClient)
    mock.resolve.return_value = {
        "success": True,
        "intent": {"name": "GENERAL_INQUIRY", "confidence": 0.95},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "search_query": search_query,
        "time_constraint": None,
        "context": {},
    }
    return mock


def _org_mock() -> Mock:
    mock = Mock(spec=OrganizationClient)
    mock.get_details.return_value = {
        "organization": {"businessCategoryId": 1, "payment_required": False}
    }
    return mock


class TestRagHandlerIntegration:
    """End-to-end tests for the RAG / HANDLER_DELEGATED flow."""

    def setup_method(self):
        from extensions.handlers.bootstrap import register_default_handlers
        register_default_handlers()
        for uid in ("test-rag-t1", "test-rag-t1b", "test-rag-t2", "test-rag-t3",
                    "test-rag-noid", "test-rag-sess"):
            clear_session(uid)

    # ------------------------------------------------------------------
    # Turn 1 — HANDLER_DELEGATED outcome from orchestrator
    # ------------------------------------------------------------------

    def test_turn1_outcome_is_handler_delegated(self):
        """GENERAL_INQUIRY → outcome.status == HANDLER_DELEGATED with routing fields."""
        result = handle_message(
            text="what are your return policies?",
            user_id="test-rag-t1",
            luma_client=_rag_luma_mock(),
            organization_client=_org_mock(),
            organization_id=1,
        )
        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"
        assert outcome.get("active_handler") == "rag"
        assert outcome.get("search_query") == "return policies"
        assert outcome.get("intent_name") == "GENERAL_INQUIRY"

    def test_turn1_handler_returns_render_instruction_and_facts(self):
        """HandlerRunner returns render_instruction + raw facts (not rendered text)."""
        result = handle_message(
            text="tell me about haircuts",
            user_id="test-rag-t1b",
            luma_client=_rag_luma_mock(search_query="haircut"),
            organization_client=_org_mock(),
            organization_id=1,
        )
        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"

        from extensions.handlers.runner import HandlerRunner

        with patch("extensions.handlers.adapters.rag.FaqClient") as MockFaqClient:
            MockFaqClient.return_value.retrieve.return_value = _FAQ_DATA

            runner = HandlerRunner()
            handler_result = runner.handle(
                outcome.get("active_handler", ""),
                {
                    "user_id": "test-rag-t1b",
                    "organization_id": 1,
                    "user_text": "tell me about haircuts",
                    "intent_name": outcome.get("intent_name"),
                    "search_query": outcome.get("search_query"),
                    "slots": outcome.get("slots", {}),
                    "session_slots": {},
                    "session": {},
                },
            )

        assert handler_result.render_instruction, "render_instruction must be non-empty"
        assert "haircut" in handler_result.render_instruction.lower()
        assert handler_result.facts.get("chunks") == _FAQ_DATA["chunks"]
        assert handler_result.facts.get("structured_context") == _FAQ_DATA["structured_context"]
        assert handler_result.facts.get("no_hit") is False

    # ------------------------------------------------------------------
    # Turn 2 — conversation_context on follow-up
    # ------------------------------------------------------------------

    def test_turn2_luma_receives_conversation_context(self):
        """session.conversation on Turn 2 → luma.resolve called with conversation_context."""
        prior_conversation = {
            "last_intent": "GENERAL_INQUIRY",
            "last_search_query": "return policies",
            "turns": [
                {
                    "user": "what are your return policies?",
                    "intent": "GENERAL_INQUIRY",
                    "search_query": "return policies",
                }
            ],
        }
        session_with_conv = {"conversation": prior_conversation}

        luma = _rag_luma_mock(search_query="return policies group bookings")
        handle_message(
            text="and for group bookings?",
            user_id="test-rag-t2",
            luma_client=luma,
            organization_client=_org_mock(),
            organization_id=1,
            session_state=session_with_conv,
        )

        call_kwargs = luma.resolve.call_args.kwargs
        conv_ctx = call_kwargs.get("conversation_context")
        assert conv_ctx is not None, "luma.resolve must receive conversation_context on follow-up"
        assert conv_ctx.get("last_intent") == "GENERAL_INQUIRY"
        assert conv_ctx.get("last_search_query") == "return policies"
        assert len(conv_ctx.get("turns", [])) == 1

    # ------------------------------------------------------------------
    # Session — messages appended via API endpoint
    # ------------------------------------------------------------------

    def test_session_messages_appended_after_handler_delegated(self):
        """After HANDLER_DELEGATED turn via the API, session.messages has user+assistant."""
        from fastapi.testclient import TestClient
        from core.orchestration.api.main import app

        uid = "test-rag-sess"
        _delegated_result = {
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
            "core.orchestration.api.message.handle_message", return_value=_delegated_result
        ), patch(
            "extensions.handlers.adapters.rag.FaqClient"
        ) as MockFaqClient, patch(
            "core.orchestration.api.message.render_llm", return_value="We are open Mon–Fri 9am–6pm."
        ):
            MockFaqClient.return_value.retrieve.return_value = _FAQ_DATA
            client = TestClient(app)
            resp = client.post(
                "/api/message",
                json={"user_id": uid, "text": "what are your hours?", "organization_id": 1},
            )

        assert resp.status_code == 200
        session = get_session(uid)
        assert session is not None, "Session must be saved after HANDLER_DELEGATED turn"
        messages = session.get("messages") or []
        assert any(m.get("role") == "user" for m in messages)
        assert any(m.get("role") == "assistant" for m in messages)

    # ------------------------------------------------------------------
    # Missing organization_id — no HTTP call to commerce
    # ------------------------------------------------------------------

    def test_missing_organization_id_no_faq_call(self):
        """HANDLER_DELEGATED without organization_id → handler returns without calling commerce."""
        with patch("extensions.handlers.adapters.rag.FaqClient") as MockFaqClient:
            handle_message(
                text="what are your policies?",
                user_id="test-rag-noid",
                luma_client=_rag_luma_mock(),
                organization_client=_org_mock(),
                organization_id=None,
            )
            MockFaqClient.return_value.retrieve.assert_not_called()

    # ------------------------------------------------------------------
    # Booking intent — must NOT be HANDLER_DELEGATED
    # ------------------------------------------------------------------

    def test_booking_intent_not_delegated_to_handler(self):
        """CREATE_APPOINTMENT must not yield HANDLER_DELEGATED — hits booking kernel."""
        mock_luma = Mock(spec=LumaClient)
        mock_luma.resolve.return_value = {
            "success": True,
            "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
            "facts": {
                "dates": [],
                "times": [],
                "date_time_pairs": [],
                "service_id": "haircut",
                "booking_id": None,
            },
            "search_query": None,
            "time_constraint": None,
            "context": {},
        }

        result = handle_message(
            text="book a haircut",
            user_id="test-rag-t3",
            luma_client=mock_luma,
            organization_client=_org_mock(),
            organization_id=1,
        )

        outcome = result.get("outcome", {})
        status = outcome.get("status")
        assert status != "HANDLER_DELEGATED", (
            f"CREATE_APPOINTMENT must not be HANDLER_DELEGATED; got {status!r}"
        )
        assert status in ("NEEDS_CLARIFICATION", "READY", "NON_DURABLE_INTENT"), (
            f"Unexpected booking outcome status: {status!r}"
        )
