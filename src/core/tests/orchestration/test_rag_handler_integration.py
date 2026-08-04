"""
Integration tests: RAG / HANDLER_DELEGATED path.

Covers:
- Turn 1: GENERAL_INQUIRY → HANDLER_DELEGATED; handler returns render_instruction + facts
- Turn 2: session.conversation → luma.resolve called with conversation_context
- Session: messages appended after HANDLER_DELEGATED turn (see core/tests/e2e/)
- Booking intent: CREATE_APPOINTMENT → NOT HANDLER_DELEGATED
- Missing organization_id → no HTTP call to commerce
"""

import os
from unittest.mock import Mock, patch

import pytest

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.session.session_manager import clear_session
from core.tests.harness.clients import stub_catalog_client

os.environ.setdefault("CORE_EXECUTION_MODE", "test")


def _catalog():
    return stub_catalog_client()


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


def _rag_luma_mock(
    search_query: str = "return policies",
    intent_name: str = "GENERAL_INQUIRY",
) -> Mock:
    mock = Mock(spec=LumaClient)
    mock.resolve.return_value = {
        "success": True,
        "intent": {"name": intent_name, "confidence": 0.95},
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
        from core.planning.policy.handler_router import reload_handlers
        from extensions.handlers.bootstrap import register_default_handlers

        reload_handlers()
        register_default_handlers()
        for uid in (
            "test-rag-t1",
            "test-rag-t1b",
            "test-rag-t2",
            "test-rag-t3",
            "test-rag-noid",
            "test-rag-pay",
            "test-rag-mid",
        ):
            clear_session(1, uid)

    # ------------------------------------------------------------------
    # Turn 1 — HANDLER_DELEGATED outcome from orchestrator
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "intent_name,text,search_query",
        [
            ("GENERAL_INQUIRY", "what are your return policies?", "return policies"),
            ("PAYMENT", "I want to pay", "payment"),
            ("PAYMENT_STATUS", "why is it 105?", "pricing total 105"),
        ],
    )
    def test_turn1_outcome_is_handler_delegated(self, intent_name, text, search_query):
        """Informational + payment intents → HANDLER_DELEGATED via rag."""
        result = handle_message(
            text=text,
            user_id="test-rag-t1",
            luma_client=_rag_luma_mock(
                search_query=search_query, intent_name=intent_name
            ),
            organization_client=_org_mock(),
            catalog_client=_catalog(),
            organization_id=1,
        )
        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"
        assert outcome.get("active_handler") == "rag"
        assert outcome.get("search_query") == search_query
        assert outcome.get("intent_name") == intent_name

    def test_turn1_handler_returns_render_instruction_and_facts(self):
        """HandlerRunner returns render_instruction + raw facts (not rendered text)."""
        result = handle_message(
            text="tell me about haircuts",
            user_id="test-rag-t1b",
            luma_client=_rag_luma_mock(search_query="haircut"),
            organization_client=_org_mock(),
            catalog_client=_catalog(),
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
            catalog_client=_catalog(),
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
                catalog_client=_catalog(),
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
            catalog_client=_catalog(),
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

    def test_mid_booking_payment_status_delegates_and_preserves_session(self):
        """Book → service → PAYMENT_STATUS digression preserves booking, resumes via rag."""
        from core.session.session_manager import get_session
        from core.tests.harness.clients import ScriptedLumaClient

        premium = "premium haircut"
        scripts = {
            "book haircut": {
                "success": True,
                "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
                "facts": {
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "service_id": None,
                    "booking_id": None,
                },
                "needs_clarification": True,
                "missing_slots": ["service_id", "date", "time"],
                "service_candidates": [{"text": premium}, {"text": "flexi haircut"}],
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "premium": {
                "success": True,
                "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
                "facts": {
                    "service_id": premium,
                    "slots": {"service_id": premium},
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "booking_id": None,
                },
                "slots": {"service_id": premium},
                "service_term": "premium",
                "missing_slots": ["date", "time"],
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "how much does it cost?": {
                "success": True,
                "intent": {"name": "QUOTE", "confidence": 0.95},
                "facts": {
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "service_id": None,
                    "booking_id": None,
                },
                "search_query": "haircut cost",
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "explain reservation fee": {
                "success": True,
                "intent": {"name": "GENERAL_INQUIRY", "confidence": 0.95},
                "facts": {
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "service_id": None,
                    "booking_id": None,
                },
                "search_query": "reservation fee",
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "why is it 105?": {
                "success": True,
                "intent": {"name": "PAYMENT_STATUS", "confidence": 0.95},
                "facts": {
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "service_id": None,
                    "booking_id": None,
                },
                "search_query": "why total 105",
                "turn": {"understanding": "UNDERSTOOD"},
            },
        }
        luma = ScriptedLumaClient(scripts)
        user_id = "test-rag-mid"

        handle_message(
            text="Book haircut",
            user_id=user_id,
            luma_client=luma,
            organization_client=_org_mock(),
            catalog_client=_catalog(),
            organization_id=1,
        )
        handle_message(
            text="Premium",
            user_id=user_id,
            luma_client=luma,
            organization_client=_org_mock(),
            catalog_client=_catalog(),
            organization_id=1,
        )

        before = get_session(1, user_id) or {}
        before_slots = dict(before.get("slots") or {})
        before_intent = before.get("intent_name")
        planning = before.get("planning") if isinstance(before.get("planning"), dict) else {}
        before_planning_slots = dict(planning.get("slots") or {})

        with patch("extensions.handlers.adapters.rag.FaqClient") as MockFaqClient:
            MockFaqClient.return_value.retrieve.return_value = _FAQ_DATA
            for text, intent_name, search_query in (
                ("How much does it cost?", "QUOTE", "haircut cost"),
                ("Explain reservation fee", "GENERAL_INQUIRY", "reservation fee"),
                ("Why is it 105?", "PAYMENT_STATUS", "why total 105"),
            ):
                result = handle_message(
                    text=text,
                    user_id=user_id,
                    luma_client=luma,
                    organization_client=_org_mock(),
                    catalog_client=_catalog(),
                    organization_id=1,
                )
                outcome = result.get("outcome", {})
                assert outcome.get("status") == "HANDLER_DELEGATED", text
                assert outcome.get("active_handler") == "rag", text
                assert outcome.get("intent_name") == intent_name, text
                assert outcome.get("search_query") == search_query, text

                after = get_session(1, user_id) or {}
                assert after.get("intent_name") == before_intent, text
                assert dict(after.get("slots") or {}) == before_slots, text
                after_planning = (
                    after.get("planning")
                    if isinstance(after.get("planning"), dict)
                    else {}
                )
                assert dict(after_planning.get("slots") or {}) == before_planning_slots, text
                service = (after.get("slots") or {}).get("service_id") or (
                    after_planning.get("slots") or {}
                ).get("service_id")
                if before_slots.get("service_id") or before_planning_slots.get(
                    "service_id"
                ):
                    assert service == premium, text
