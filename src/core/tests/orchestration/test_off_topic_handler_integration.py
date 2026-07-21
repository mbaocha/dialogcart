"""
Integration tests: OFF_TOPIC / HANDLER_DELEGATED path.

Covers:
- Cold start OFF_TOPIC → HANDLER_DELEGATED via off_topic handler (no RAG)
- UNDERSTOOD turn understanding (not recovery)
- UNKNOWN remains distinct
- GENERAL_INQUIRY still routes to rag
- Mid-booking OFF_TOPIC preserves booking slots / confirmation / proposals
"""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.adapters.clients.organization_client import OrganizationClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.session.session_manager import clear_session, get_session
from core.tests.harness.clients import ScriptedLumaClient, stub_catalog_client
from extensions.handlers.adapters.off_topic import OffTopicAdapter
from extensions.handlers.runner import HandlerRunner

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

PREMIUM = "premium haircut"


def _catalog():
    return stub_catalog_client()


def _org_mock() -> Mock:
    mock = Mock(spec=OrganizationClient)
    mock.get_details.return_value = {
        "organization": {"businessCategoryId": 1, "payment_required": False}
    }
    return mock


def _off_topic_luma_payload() -> dict:
    return {
        "success": True,
        "intent": {"name": "OFF_TOPIC", "confidence": 0.95},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "search_query": None,
        "off_topic_query": "Who is the president of Nigeria?",
        "time_constraint": None,
        "turn": {"understanding": "UNDERSTOOD"},
        "context": {},
    }


def _unknown_luma_payload() -> dict:
    return {
        "success": True,
        "intent": {"name": "UNKNOWN", "confidence": 0.2},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "search_query": None,
        "time_constraint": None,
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "context": {},
    }


def _general_inquiry_luma_payload(search_query: str = "available services") -> dict:
    return {
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
        "turn": {"understanding": "UNDERSTOOD"},
        "context": {},
    }


class TestOffTopicHandlerIntegration:
    def setup_method(self):
        from core.planning.policy.handler_router import reload_handlers
        from extensions.handlers.bootstrap import register_default_handlers

        reload_handlers()
        register_default_handlers()
        for uid in (
            "test-ot-cold",
            "test-ot-unknown",
            "test-ot-gi",
            "test-ot-mid",
            "test-ot-adapter",
        ):
            clear_session(1, uid)

    def test_cold_start_off_topic_delegates_without_booking_planner(self):
        luma = Mock(spec=LumaClient)
        luma.resolve.return_value = _off_topic_luma_payload()

        with patch("core.api.message.render_llm", return_value="scope decline"):
            result = handle_message(
                text="Who is the president of Nigeria?",
                user_id="test-ot-cold",
                luma_client=luma,
                organization_client=_org_mock(),
                catalog_client=_catalog(),
                organization_id=1,
            )

        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"
        assert outcome.get("active_handler") == "off_topic"
        assert outcome.get("intent_name") == "OFF_TOPIC"
        assert outcome.get("search_query") in (None, "")
        assert (outcome.get("turn") or {}).get("understanding") == "UNDERSTOOD"

        # No booking execution path.
        assert result.get("execution") in (None, {}, False) or not result.get(
            "execution", {}
        ).get("executed")

    def test_unknown_aaa_is_not_off_topic(self):
        luma = Mock(spec=LumaClient)
        luma.resolve.return_value = _unknown_luma_payload()

        result = handle_message(
            text="aaa",
            user_id="test-ot-unknown",
            luma_client=luma,
            organization_client=_org_mock(),
            catalog_client=_catalog(),
            organization_id=1,
        )
        outcome = result.get("outcome", {})
        assert outcome.get("status") != "HANDLER_DELEGATED"
        assert outcome.get("active_handler") != "off_topic"
        intent = outcome.get("intent_name") or ""
        assert intent in ("UNKNOWN", "", "CREATE_APPOINTMENT") or intent != "OFF_TOPIC"

        merged = result.get("_merged_luma_response") or {}
        understanding = (merged.get("turn") or {}).get("understanding")
        if understanding is None:
            turn = outcome.get("turn") or {}
            understanding = turn.get("understanding")
        assert understanding == "UNRECOGNIZED_INPUT"

    def test_general_inquiry_still_routes_to_rag(self):
        luma = Mock(spec=LumaClient)
        luma.resolve.return_value = _general_inquiry_luma_payload()

        result = handle_message(
            text="What services do you offer?",
            user_id="test-ot-gi",
            luma_client=luma,
            organization_client=_org_mock(),
            catalog_client=_catalog(),
            organization_id=1,
        )
        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"
        assert outcome.get("active_handler") == "rag"
        assert outcome.get("intent_name") == "GENERAL_INQUIRY"

    def test_off_topic_adapter_uses_llm_answer_not_faq(self):
        from core.rendering.off_topic import OffTopicEvidence

        answer_fn = MagicMock(
            return_value=OffTopicEvidence(
                answer="Nigeria's current president is Bola Ahmed Tinubu.",
                answerable=True,
            )
        )
        adapter = OffTopicAdapter(answer_fn=answer_fn)
        cold = adapter.handle(
            {
                "user_id": "test-ot-adapter",
                "organization_id": 1,
                "user_text": "Who is the president of Nigeria?",
                "off_topic_query": "Who is the president of Nigeria?",
                "intent_name": "OFF_TOPIC",
                "session": {},
            }
        )
        assert cold.facts.get("scope") == "off_topic"
        assert cold.facts.get("booking_active") is False
        assert cold.facts.get("answerable") is True
        assert "Tinubu" in (cold.facts.get("answer") or "")
        assert "facts first" in cold.render_instruction.lower()
        assert "resume" in cold.render_instruction.lower()
        assert "evidence" not in cold.render_instruction.lower()
        assert "time works best" not in cold.render_instruction.lower()
        assert "misunderstood" in cold.render_instruction.lower()
        answer_fn.assert_called_once()

        booking = adapter.handle(
            {
                "user_id": "test-ot-adapter",
                "organization_id": 1,
                "user_text": "Who is the president of Nigeria?",
                "off_topic_query": "Who is the president of Nigeria?",
                "intent_name": "OFF_TOPIC",
                "session": {"intent_name": "CREATE_APPOINTMENT", "slots": {"service_id": PREMIUM}},
            }
        )
        assert booking.facts.get("booking_active") is True
        assert "time works best" not in booking.render_instruction.lower()
        assert booking.render_instruction == cold.render_instruction

        runner = HandlerRunner()
        from core.rendering.off_topic import OffTopicEvidence as OE
        from extensions.handlers.registry import register_handler

        answer_fn2 = MagicMock(return_value=OE(answer=None, answerable=False))
        register_handler(OffTopicAdapter(answer_fn=answer_fn2))
        result = runner.handle(
            "off_topic",
            {
                "session": {},
                "user_text": "tell me a joke",
                "off_topic_query": "Tell me a joke",
            },
        )
        assert result.render_instruction
        assert "misunderstood" in result.render_instruction.lower()
        assert "resume" in result.render_instruction.lower()
        assert "cannot answer" in result.render_instruction.lower()
        assert "time works best" not in result.render_instruction.lower()

    def test_mid_booking_off_topic_preserves_session(self):
        """Book → Premium → OFF_TOPIC must not mutate booking state."""
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
                "service_candidates": [{"text": PREMIUM}, {"text": "flexi haircut"}],
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "premium": {
                "success": True,
                "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
                "facts": {
                    "service_id": PREMIUM,
                    "slots": {"service_id": PREMIUM},
                    "dates": [],
                    "times": [],
                    "date_time_pairs": [],
                    "booking_id": None,
                },
                "slots": {"service_id": PREMIUM},
                "service_term": "premium",
                "missing_slots": ["date", "time"],
                "turn": {"understanding": "UNDERSTOOD"},
            },
            "who is the president of nigeria?": _off_topic_luma_payload(),
        }
        luma = ScriptedLumaClient(scripts)
        user_id = "test-ot-mid"

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
        before_confirmation = before.get("confirmation_state")
        before_proposals = {
            "date_proposal": before.get("date_proposal"),
            "time_proposal": before.get("time_proposal"),
        }
        planning = before.get("planning") if isinstance(before.get("planning"), dict) else {}
        before_planning_slots = dict(planning.get("slots") or {})

        with patch("core.api.message.render_llm", return_value="Let's continue your booking"):
            result = handle_message(
                text="Who is the president of Nigeria?",
                user_id=user_id,
                luma_client=luma,
                organization_client=_org_mock(),
                catalog_client=_catalog(),
                organization_id=1,
            )

        outcome = result.get("outcome", {})
        assert outcome.get("status") == "HANDLER_DELEGATED"
        assert outcome.get("active_handler") == "off_topic"
        assert outcome.get("intent_name") == "OFF_TOPIC"

        after = get_session(1, user_id) or {}
        assert after.get("intent_name") == before_intent
        assert dict(after.get("slots") or {}) == before_slots
        assert after.get("confirmation_state") == before_confirmation
        assert after.get("date_proposal") == before_proposals["date_proposal"]
        assert after.get("time_proposal") == before_proposals["time_proposal"]
        after_planning = after.get("planning") if isinstance(after.get("planning"), dict) else {}
        assert dict(after_planning.get("slots") or {}) == before_planning_slots
        # Service from Premium turn must still be present when it was captured.
        if before_slots.get("service_id") or before_planning_slots.get("service_id"):
            service = (after.get("slots") or {}).get("service_id") or (
                after_planning.get("slots") or {}
            ).get("service_id")
            assert service == PREMIUM
