"""
Integration tests: RAG / HANDLER_DELEGATED path.

Covers:
- Turn 1: GENERAL_INQUIRY → HANDLER_DELEGATED with search_query; stub text via HandlerRunner
- Turn 2: session.conversation → luma.resolve called with conversation_context (mock assert)
- Booking intent: CREATE_APPOINTMENT → NOT HANDLER_DELEGATED (hits booking kernel)
"""

import os
from unittest.mock import Mock

import pytest

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
from core.orchestration.session import clear_session

os.environ.setdefault("CORE_EXECUTION_MODE", "test")


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
        for uid in ("test-rag-t1", "test-rag-t1b", "test-rag-t2", "test-rag-t3"):
            clear_session(uid)

    # ------------------------------------------------------------------
    # Turn 1 — HANDLER_DELEGATED outcome
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

    def test_turn1_stub_text_from_handler_runner(self):
        """HandlerRunner invoked with the search_query from the outcome → stub text."""
        result = handle_message(
            text="what are your return policies?",
            user_id="test-rag-t1b",
            luma_client=_rag_luma_mock(),
            organization_client=_org_mock(),
            organization_id=1,
        )

        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"

        from extensions.handlers.runner import HandlerRunner

        runner = HandlerRunner()
        handler_result = runner.handle(
            outcome.get("active_handler", ""),
            {"search_query": outcome.get("search_query")},
        )
        assert handler_result.text == "[RAG] return policies"

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
        # Booking intent enters the planning path (NEEDS_CLARIFICATION for missing slots)
        assert status in ("NEEDS_CLARIFICATION", "READY", "NON_DURABLE_INTENT"), (
            f"Unexpected booking outcome status: {status!r}"
        )
