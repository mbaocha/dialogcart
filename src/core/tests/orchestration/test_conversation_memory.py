"""
Tests for conversation_memory — build_conversation_context and update_conversation.
"""

import pytest

from core.orchestration.nlu.conversation_memory import (
    build_conversation_context,
    update_conversation,
)


# ---------------------------------------------------------------------------
# build_conversation_context
# ---------------------------------------------------------------------------


class TestBuildConversationContext:
    def test_returns_none_for_none_session(self):
        assert build_conversation_context(None) is None

    def test_returns_none_for_empty_session(self):
        assert build_conversation_context({}) is None

    def test_returns_none_when_no_conversation_key(self):
        assert build_conversation_context({"intent_name": "CREATE_APPOINTMENT"}) is None

    def test_returns_none_for_empty_conversation(self):
        session = {"conversation": {}}
        assert build_conversation_context(session) is None

    def test_returns_none_for_content_free_conversation(self):
        # has the key but no useful data
        session = {"conversation": {"last_intent": None, "last_search_query": None, "turns": []}}
        assert build_conversation_context(session) is None

    def test_returns_context_when_last_intent_present(self):
        conv = {"last_intent": "GENERAL_INQUIRY", "last_search_query": None, "turns": []}
        session = {"conversation": conv}
        result = build_conversation_context(session)
        assert result is conv

    def test_returns_context_when_last_search_query_present(self):
        conv = {"last_intent": None, "last_search_query": "cancellation policy", "turns": []}
        session = {"conversation": conv}
        result = build_conversation_context(session)
        assert result is conv

    def test_returns_context_when_turns_non_empty(self):
        conv = {
            "last_intent": "DETAILS",
            "last_search_query": "deep tissue massage",
            "turns": [{"user": "tell me about deep tissue", "intent": "DETAILS", "search_query": "deep tissue massage"}],
        }
        session = {"conversation": conv}
        result = build_conversation_context(session)
        assert result is conv

    def test_ignores_other_session_keys(self):
        conv = {"last_intent": "GENERAL_INQUIRY", "last_search_query": "hours", "turns": []}
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "haircut"},
            "conversation": conv,
        }
        result = build_conversation_context(session)
        assert result is conv


# ---------------------------------------------------------------------------
# update_conversation
# ---------------------------------------------------------------------------


class TestUpdateConversation:
    def test_first_turn_creates_conversation_key(self):
        session: dict = {}
        updated = update_conversation(session, user_text="what are your hours?", intent="GENERAL_INQUIRY", search_query="hours")
        conv = updated["conversation"]
        assert conv["last_intent"] == "GENERAL_INQUIRY"
        assert conv["last_search_query"] == "hours"
        assert len(conv["turns"]) == 1
        assert conv["turns"][0]["user"] == "what are your hours?"
        assert conv["turns"][0]["intent"] == "GENERAL_INQUIRY"
        assert conv["turns"][0]["search_query"] == "hours"

    def test_does_not_mutate_input_session(self):
        session = {"slots": {"service_id": "haircut"}}
        update_conversation(session, user_text="hello", intent="GENERAL_INQUIRY", search_query=None)
        assert "conversation" not in session

    def test_preserves_existing_session_keys(self):
        session = {"intent_name": "CREATE_APPOINTMENT", "slots": {"service_id": "haircut"}}
        updated = update_conversation(session, user_text="book", intent="CREATE_APPOINTMENT", search_query=None)
        assert updated["intent_name"] == "CREATE_APPOINTMENT"
        assert updated["slots"] == {"service_id": "haircut"}

    def test_appends_turns(self):
        session: dict = {}
        s1 = update_conversation(session, user_text="turn 1", intent="GENERAL_INQUIRY", search_query="q1")
        s2 = update_conversation(s1, user_text="turn 2", intent="DETAILS", search_query="q2")
        assert len(s2["conversation"]["turns"]) == 2

    def test_three_turn_cap_fifo(self):
        session: dict = {}
        s = session
        for i in range(4):
            s = update_conversation(s, user_text=f"turn {i}", intent="GENERAL_INQUIRY", search_query=f"q{i}")
        turns = s["conversation"]["turns"]
        assert len(turns) == 3
        # Oldest turn (0) evicted; turns 1-3 remain
        assert turns[0]["user"] == "turn 1"
        assert turns[1]["user"] == "turn 2"
        assert turns[2]["user"] == "turn 3"

    def test_last_intent_and_search_query_updated_each_turn(self):
        session: dict = {}
        s1 = update_conversation(session, user_text="t1", intent="GENERAL_INQUIRY", search_query="policy")
        s2 = update_conversation(s1, user_text="t2", intent="DETAILS", search_query="massage details")
        assert s2["conversation"]["last_intent"] == "DETAILS"
        assert s2["conversation"]["last_search_query"] == "massage details"

    def test_null_search_query_stored(self):
        session: dict = {}
        updated = update_conversation(session, user_text="book me", intent="CREATE_APPOINTMENT", search_query=None)
        assert updated["conversation"]["last_search_query"] is None
        assert updated["conversation"]["turns"][0]["search_query"] is None

    def test_assistant_text_optional(self):
        session: dict = {}
        updated = update_conversation(
            session,
            user_text="hi",
            intent="GENERAL_INQUIRY",
            search_query=None,
            assistant_text="Hello! How can I help?",
        )
        turn = updated["conversation"]["turns"][0]
        assert turn["assistant"] == "Hello! How can I help?"

    def test_assistant_text_absent_when_not_provided(self):
        session: dict = {}
        updated = update_conversation(session, user_text="hi", intent="GENERAL_INQUIRY", search_query=None)
        turn = updated["conversation"]["turns"][0]
        assert "assistant" not in turn
