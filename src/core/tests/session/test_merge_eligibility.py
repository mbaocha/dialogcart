"""Tests for durable-flow merge eligibility (PR5)."""

from core.session.merge import should_merge_session_context


def test_merge_durable_flow_regardless_of_status():
    for status in (
        "NEEDS_CLARIFICATION",
        "READY",
        "AWAITING_CONFIRMATION",
        "EXECUTED",
        None,
    ):
        session = {
            "intent_name": "CREATE_APPOINTMENT",
            "status": status,
            "slots": {"service_id": "premium haircut"},
        }
        assert should_merge_session_context(session) is True


def test_no_merge_on_session_reset():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": {"service_id": "premium haircut"},
    }
    assert (
        should_merge_session_context(session, session_reset_occurred=True) is False
    )


def test_no_merge_without_session():
    assert should_merge_session_context(None) is False


def test_no_merge_for_non_durable_intent_without_pre_intent_slots():
    session = {
        "intent_name": "FAQ",
        "status": "NEEDS_CLARIFICATION",
        "slots": {},
    }
    assert should_merge_session_context(session) is False


def test_merge_pre_intent_slots_for_materialization():
    session = {
        "intent_name": None,
        "status": "NEEDS_CLARIFICATION",
        "slots": {"date": "2026-07-06"},
    }
    assert should_merge_session_context(session) is True


def test_merge_unknown_intent_with_slots():
    session = {
        "intent_name": "UNKNOWN",
        "slots": {"date": "2026-07-06"},
    }
    assert should_merge_session_context(session) is True
