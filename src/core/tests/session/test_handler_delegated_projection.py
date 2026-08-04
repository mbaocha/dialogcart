"""HANDLER_DELEGATED projection must preserve booking authorization state."""

from __future__ import annotations

from core.session.confirmation_gate import get_confirmation_state
from core.session.session_projector import SessionProjectorV2


def _booking_session_with_authorization() -> dict:
    bound = {
        "start": "2026-07-21T14:00:00+00:00",
        "end": "2026-07-21T14:30:00+00:00",
    }
    return {
        "schema_version": 2,
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {"service_id": "premium haircut"},
        "planning": {
            "slots": {"service_id": "premium haircut"},
            "bound_datetime": bound,
        },
        "resolved_datetime_range": bound,
        "availability": {
            "fingerprint": "fp-abc",
            "cache": {"search_result": {"slots": [{"start": "14:00"}]}},
        },
        "availability_fingerprint": "fp-abc",
        "date_proposal": {"start": "2026-07-21", "end": "2026-07-21"},
        "time_proposal": {"start": "14:00", "end": "14:00"},
    }


def test_off_topic_digression_preserves_confirmation_and_bound_datetime():
    """Digression NLU without confirmation/bound must not clear prior booking auth."""
    previous = _booking_session_with_authorization()
    digression_nlu = {
        "intent": {"name": "OFF_TOPIC"},
        "facts": {},
        "search_query": None,
        "answerable": True,
        "answer": "A short joke.",
        # No confirmation_state, no resolved_datetime_range — the bug trigger.
    }
    projected = SessionProjectorV2().project(
        outcome={
            "status": "OFF_TOPIC",
            "intent_name": "OFF_TOPIC",
            "slots": {},
        },
        outcome_status="OFF_TOPIC",
        organization_id=1,
        merged_luma_response=digression_nlu,
        previous_session_state=previous,
        working_session_state=previous,
        user_id="test-hd-preserve",
        handler_conversation_update={
            "memory": {
                "last_intent": "OFF_TOPIC",
                "last_search_query": None,
                "turns": [{"user": "tell me a joke", "intent": "OFF_TOPIC"}],
            }
        },
        conversation_messages=[
            {"role": "user", "text": "tell me a joke"},
            {"role": "assistant", "text": "I help with appointments."},
        ],
    )

    assert projected is not None
    assert projected.get("intent_name") == "CREATE_APPOINTMENT"
    assert get_confirmation_state(projected) == "pending"
    assert projected.get("slots", {}).get("service_id") == "premium haircut"
    planning = projected.get("planning") or {}
    assert planning.get("bound_datetime", {}).get("start") == (
        "2026-07-21T14:00:00+00:00"
    )
    assert projected.get("resolved_datetime_range", {}).get("start") == (
        "2026-07-21T14:00:00+00:00"
    )
    assert projected.get("availability_fingerprint") == "fp-abc"
    assert projected.get("date_proposal") == previous["date_proposal"]
    assert projected.get("time_proposal") == previous["time_proposal"]
    # Conversation digression memory is still applied.
    memory = (projected.get("conversation") or {}).get("memory") or {}
    assert memory.get("last_intent") == "OFF_TOPIC"


def test_handler_delegated_preserves_for_general_inquiry_payload():
    previous = _booking_session_with_authorization()
    projected = SessionProjectorV2().project(
        outcome={
            "status": "HANDLER_DELEGATED",
            "intent_name": "GENERAL_INQUIRY",
            "active_handler": "rag",
            "search_query": "hours",
            "slots": {},
        },
        outcome_status="HANDLER_DELEGATED",
        organization_id=1,
        merged_luma_response={
            "intent": {"name": "GENERAL_INQUIRY"},
            "search_query": "hours",
        },
        previous_session_state=previous,
        working_session_state=previous,
        user_id="test-hd-gi",
    )
    assert projected is not None
    assert get_confirmation_state(projected) == "pending"
    assert (projected.get("planning") or {}).get("bound_datetime", {}).get(
        "start"
    ) == "2026-07-21T14:00:00+00:00"


def test_handler_delegated_preserves_for_payment_status_payload():
    previous = _booking_session_with_authorization()
    projected = SessionProjectorV2().project(
        outcome={
            "status": "HANDLER_DELEGATED",
            "intent_name": "PAYMENT_STATUS",
            "active_handler": "rag",
            "search_query": "why total 105",
            "slots": {},
        },
        outcome_status="HANDLER_DELEGATED",
        organization_id=1,
        merged_luma_response={
            "intent": {"name": "PAYMENT_STATUS"},
            "search_query": "why total 105",
        },
        previous_session_state=previous,
        working_session_state=previous,
        user_id="test-hd-pay",
    )
    assert projected is not None
    assert projected.get("intent_name") == "CREATE_APPOINTMENT"
    assert get_confirmation_state(projected) == "pending"
    assert (projected.get("planning") or {}).get("bound_datetime", {}).get(
        "start"
    ) == "2026-07-21T14:00:00+00:00"
