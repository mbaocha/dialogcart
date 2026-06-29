"""Tests for resolve_faq_query — vague detection and session-context enrichment."""

import os

import pytest

os.environ.setdefault("CORE_EXECUTION_MODE", "test")

from extensions.handlers.query_resolution import resolve_faq_query


class TestPassthrough:
    """Specific queries pass through unchanged."""

    def test_specific_search_query_passthrough(self):
        result = resolve_faq_query(
            search_query="cancellation policy",
            user_text="what is your cancellation policy?",
            session={},
        )
        assert result == "cancellation policy"

    def test_haircut_price_passthrough(self):
        result = resolve_faq_query(
            search_query="haircut price",
            user_text="how much does a haircut cost?",
            session={},
        )
        assert result == "haircut price"

    def test_multi_word_query_passthrough(self):
        result = resolve_faq_query(
            search_query="what are your opening hours on weekends",
            user_text="",
            session=None,
        )
        assert result == "what are your opening hours on weekends"

    def test_no_search_query_falls_back_to_user_text(self):
        result = resolve_faq_query(
            search_query=None,
            user_text="do you offer group discounts?",
            session=None,
        )
        assert result == "do you offer group discounts?"


class TestServiceIdBoost:
    """Vague query enriched with service_id from session slots."""

    def test_how_much_with_slots_service_id(self):
        session = {"slots": {"service_id": "haircut"}}
        result = resolve_faq_query(
            search_query="how much",
            user_text="how much is it?",
            session=session,
        )
        assert result == "haircut price"

    def test_price_with_session_slots_service_id(self):
        session = {"session_slots": {"service_id": "manicure"}}
        result = resolve_faq_query(
            search_query="price",
            user_text="what's the price?",
            session=session,
        )
        assert result == "manicure price"

    def test_cost_with_service_from_slots(self):
        session = {"slots": {"service": "facial"}}
        result = resolve_faq_query(
            search_query="cost",
            user_text="cost?",
            session=session,
        )
        assert result == "facial price"


class TestPronounFollowUp:
    """Pronoun reference resolved via last search_query in conversation."""

    def test_how_much_is_it_with_prior_search_query(self):
        session = {
            "conversation": {
                "turns": [
                    {
                        "user": "tell me about haircuts",
                        "intent": "GENERAL_INQUIRY",
                        "search_query": "haircut",
                    }
                ]
            }
        }
        result = resolve_faq_query(
            search_query="how much is it",
            user_text="how much is it?",
            session=session,
        )
        assert result == "haircut price"

    def test_pronoun_that_enriched_from_conversation(self):
        session = {
            "conversation": {
                "turns": [
                    {
                        "user": "I want a massage",
                        "intent": "GENERAL_INQUIRY",
                        "search_query": "massage",
                    }
                ]
            }
        }
        result = resolve_faq_query(
            search_query="how much is that",
            user_text="how much is that?",
            session=session,
        )
        assert result == "massage price"

    def test_empty_search_query_uses_user_text_enriched(self):
        session = {"slots": {"service_id": "pedicure"}}
        result = resolve_faq_query(
            search_query="",
            user_text="how much",
            session=session,
        )
        assert result == "pedicure price"


class TestEdgeCases:
    def test_none_session_no_crash(self):
        result = resolve_faq_query(
            search_query="how much",
            user_text="how much?",
            session=None,
        )
        assert result == "how much"

    def test_empty_everything_falls_back_empty(self):
        result = resolve_faq_query(
            search_query="",
            user_text="",
            session=None,
        )
        assert result == ""

    def test_vague_query_no_service_returns_base(self):
        result = resolve_faq_query(
            search_query="price",
            user_text="price?",
            session={"conversation": {"turns": []}},
        )
        assert result == "price"
