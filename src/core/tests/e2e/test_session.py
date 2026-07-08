"""READY outcomes must persist merged slots (not just chat messages)."""

from unittest.mock import patch

import pytest

from core.orchestration.session import clear_session, get_session, save_session


@pytest.fixture
def user_id():
    uid = "test-ready-persist-user"
    clear_session(uid)
    yield uid
    clear_session(uid)


def _turn1_session():
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": ["date", "service_id", "time"],
        "service_candidates": ["premium haircut", "flexi haircut + prunning"],
        "slots": {},
    }


def _ready_after_premium_outcome():
    return {
        "success": True,
        "text": "Here are available times for Premium Haircut.",
        "outcome": {
            "status": "READY",
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut"},
            "missing_slots": ["date", "time"],
            "facts": {
                "slots": {"service_id": "premium haircut"},
                "missing_slots": ["date", "time"],
            },
            "plan": {
                "status": "READY",
                "stage": "AVAILABILITY",
                "action": "SEARCH_AVAILABILITY",
                "intent_name": "CREATE_APPOINTMENT",
            },
        },
        "_merged_luma_response": {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut"},
            "_effective_collected_slots": {"service_id": "premium haircut"},
            "missing_slots": ["date", "time"],
        },
    }


def _executed_success_after_premium_outcome():
    return {
        "success": True,
        "text": "Here are available times for Premium Haircut.",
        "outcome": {
            "status": "success",
            "type": "availability",
            "slots": [
                {
                    "starts_at": "2026-07-03T09:00:00Z",
                    "ends_at": "2026-07-03T09:30:00Z",
                }
            ],
            "plan": {
                "status": "READY",
                "stage": "AVAILABILITY",
                "action": "SEARCH_AVAILABILITY",
            },
        },
        "plan": {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "READY",
            "stage": "AVAILABILITY",
            "action": "SEARCH_AVAILABILITY",
        },
        "_merged_luma_response": {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut"},
            "_effective_collected_slots": {"service_id": "premium haircut"},
            "missing_slots": ["date", "time"],
        },
    }


def test_ready_outcome_persists_service_id_slots(user_id, api_client):
    """Simulate turn 2 (premium → SEARCH_AVAILABILITY): slots must survive on disk."""
    save_session(user_id, _turn1_session())

    with patch(
        "core.orchestration.api.message.handle_message",
        return_value=_ready_after_premium_outcome(),
    ):
        resp = api_client.post(
            "/api/message",
            json={"user_id": user_id, "text": "premium", "organization_id": 1},
        )

    assert resp.status_code == 200
    session = get_session(user_id)
    assert session is not None
    assert session.get("slots", {}).get("service_id") == "premium haircut"
    assert session.get("status") == "READY"
    assert "service_id" not in (session.get("missing_slots") or [])
    assert "service_candidates" not in session
    messages = session.get("messages") or []
    assert any(m.get("role") == "user" and m.get("text") == "premium" for m in messages)


def test_executed_success_outcome_persists_service_id_slots(user_id, api_client):
    """Availability execution with status=success must still persist merged booking slots."""
    save_session(user_id, _turn1_session())

    with patch(
        "core.orchestration.api.message.handle_message",
        return_value=_executed_success_after_premium_outcome(),
    ):
        resp = api_client.post(
            "/api/message",
            json={"user_id": user_id, "text": "premium", "organization_id": 1},
        )

    assert resp.status_code == 200
    session = get_session(user_id)
    assert session is not None
    assert session.get("slots", {}).get("service_id") == "premium haircut"
    assert "service_id" not in (session.get("missing_slots") or [])
    assert session.get("status") == "READY"


def _session_after_availability_search():
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "missing_slots": ["time"],
        "slots": {"service_id": "premium haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-03"},
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [
                {
                    "starts_at": "2026-07-03T14:30:00Z",
                    "ends_at": "2026-07-03T15:00:00Z",
                }
            ],
        },
    }


def _awaiting_confirmation_after_time_bind_outcome():
    return {
        "success": True,
        "text": (
            "You're about to book a Premium Haircut on July 3 at 2:30 PM. "
            "Would you like me to go ahead?"
        ),
        "outcome": {
            "status": "AWAITING_CONFIRMATION",
            "intent_name": "CREATE_APPOINTMENT",
            "awaiting": "USER_CONFIRMATION",
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-03",
                "time": "14:30",
            },
            "missing_slots": [],
            "booking": {"confirmation_state": "pending"},
            "facts": {
                "slots": {
                    "service_id": "premium haircut",
                    "date": "2026-07-03",
                    "time": "14:30",
                },
                "missing_slots": [],
            },
            "plan": {
                "status": "AWAITING_CONFIRMATION",
                "stage": "CONFIRM",
                "action": None,
                "intent_name": "CREATE_APPOINTMENT",
                "awaiting": "USER_CONFIRMATION",
            },
        },
        "_merged_luma_response": {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "_effective_intent": "CREATE_APPOINTMENT",
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-03",
                "time": "14:30",
            },
            "_effective_collected_slots": {
                "service_id": "premium haircut",
                "date": "2026-07-03",
                "time": "14:30",
            },
            "missing_slots": [],
            "resolved_datetime_range": {
                "start": "2026-07-03T14:30:00Z",
                "end": "2026-07-03T15:00:00Z",
            },
            "booking": {"confirmation_state": "pending"},
        },
    }


def test_awaiting_confirmation_outcome_persists_bound_datetime_and_pending(user_id, api_client):
    """Time-bind turn (AWAITING_CONFIRMATION) must persist slots and confirmation_state."""
    save_session(user_id, _session_after_availability_search())

    with patch(
        "core.orchestration.api.message.handle_message",
        return_value=_awaiting_confirmation_after_time_bind_outcome(),
    ):
        resp = api_client.post(
            "/api/message",
            json={"user_id": user_id, "text": "2.30pm", "organization_id": 1},
        )

    assert resp.status_code == 200
    session = get_session(user_id)
    assert session is not None
    slots = session.get("slots") or {}
    assert slots.get("date") == "2026-07-03"
    assert slots.get("time") == "14:30"
    resolved = session.get("resolved_datetime_range") or {}
    assert resolved.get("start") == "2026-07-03T14:30:00Z"
    booking = session.get("booking") or {}
    assert booking.get("confirmation_state") == "pending"
    assert session.get("confirmation_state") == "pending"
    assert session.get("status") == "NEEDS_CLARIFICATION"
    assert session.get("missing_slots") == []
