"""
Tests for AVAILABILITY / CHECK_AVAILABILITY during active booking sessions.

Mid-booking date refinement must stay on the durable booking intent and reach
SEARCH_AVAILABILITY — not short-circuit as NON_DURABLE_INTENT.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

src_path = Path(__file__).parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from core.orchestration.clients.organization_client import OrganizationClient
from core.orchestration.execution.clients.availability_client import AvailabilityClient
from core.orchestration.execution.clients.booking_client import BookingClient
from core.orchestration.nlu import LumaClient
from core.orchestration.orchestrator import handle_message
from core.orchestration.api.session_merge import build_session_state_from_outcome
from core.planning.orchestration.intent_resolution import resolve_effective_intent
from core.session.appointment_extensions import resolve_availability_fingerprint


def test_availability_preserves_create_appointment_session():
    """AVAILABILITY over READY CREATE_APPOINTMENT keeps session intent, no reset."""
    luma_response = {
        "intent": {"name": "AVAILABILITY"},
        "facts": {"date": "2026-07-03"},
        "slots": {"date": "2026-07-03"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-02",
        },
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.return_value = True
        effective_intent, session_reset = resolve_effective_intent(
            luma_response, session_state, "test_user"
        )

    assert effective_intent == "CREATE_APPOINTMENT"
    assert session_reset is False


def test_check_availability_preserves_create_appointment_session():
    """CHECK_AVAILABILITY during booking keeps CREATE_APPOINTMENT intent."""
    luma_response = {
        "intent": {"name": "CHECK_AVAILABILITY"},
        "facts": {"date": "2026-07-03"},
        "slots": {"date": "2026-07-03"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "haircut"},
    }

    with patch("core.policy.intent_policy.get_intent_durable") as mock_durable:
        mock_durable.return_value = True
        effective_intent, session_reset = resolve_effective_intent(
            luma_response, session_state, "test_user"
        )

    assert effective_intent == "CREATE_APPOINTMENT"
    assert session_reset is False


def test_availability_without_session_stays_availability():
    """Cold AVAILABILITY with no session is not rerouted by intent_resolution."""
    luma_response = {
        "intent": {"name": "AVAILABILITY"},
        "facts": {"date": "2026-07-03"},
        "slots": {},
        "missing_slots": [],
    }

    effective_intent, session_reset = resolve_effective_intent(
        luma_response, None, "test_user"
    )

    assert effective_intent == "AVAILABILITY"
    assert session_reset is False


def test_availability_during_faq_still_switches_intent():
    """AVAILABILITY without a core booking session does not force CREATE_APPOINTMENT."""
    luma_response = {
        "intent": {"name": "AVAILABILITY"},
        "facts": {"date": "2026-07-03"},
        "slots": {"date": "2026-07-03"},
        "missing_slots": [],
    }
    session_state = {
        "intent_name": "FAQ",
        "status": "READY",
        "slots": {},
    }

    effective_intent, session_reset = resolve_effective_intent(
        luma_response, session_state, "test_user"
    )

    assert effective_intent == "AVAILABILITY"
    assert session_reset is False


def test_e2e_availability_date_refinement_after_service_resolved():
    """
    Turn 1: service resolved → READY CREATE_APPOINTMENT.
    Turn 2: AVAILABILITY intent with new date → SEARCH_AVAILABILITY on booking intent.
    """
    frozen_time = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_availability_refinement"

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    mock_availability_client = Mock(spec=AvailabilityClient)
    mock_availability_client.get_service_availability.return_value = {
        "slots": [
            {
                "start": "2026-07-03T10:00:00Z",
                "end": "2026-07-03T10:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    mock_luma_turn1 = Mock(spec=LumaClient)
    mock_luma_turn1.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"service_id": "premium haircut"},
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["date", "time"],
        "needs_clarification": False,
    }

    result_turn1 = handle_message(
        text="premium haircut",
        user_id=user_id,
        luma_client=mock_luma_turn1,
        organization_client=mock_org_client,
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result_turn1.get("success") is True
    plan_turn1 = result_turn1.get("result", {})
    assert plan_turn1.get("intent_name") == "CREATE_APPOINTMENT"
    assert plan_turn1.get("status") == "READY"

    session_state = {
        "intent_name": plan_turn1.get("intent_name", "CREATE_APPOINTMENT"),
        "slots": plan_turn1.get("slots", {}),
        "status": plan_turn1.get("status"),
        "stage": plan_turn1.get("stage"),
        "action": plan_turn1.get("action"),
    }

    mock_luma_turn2 = Mock(spec=LumaClient)
    mock_luma_turn2.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"dates": ["2026-07-03"], "service_id": "premium haircut"},
        "slots": {},
        "service_candidates": [],
        "missing_slots": [],
        "needs_clarification": False,
    }

    class MockSessionStore:
        def __init__(self, state):
            self._state = state

        def get_session(self, _user_id):
            return self._state

    result_turn2 = handle_message(
        text="do you have availability for 3rd july 2026",
        user_id=user_id,
        luma_client=mock_luma_turn2,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        session_store=MockSessionStore(session_state),
        frozen_time=frozen_time,
        organization_id=1,
    )

    assert result_turn2.get("success") is True, result_turn2.get("error")
    plan_turn2 = result_turn2.get("plan")
    assert plan_turn2 is not None
    assert plan_turn2.get("intent_name") == "CREATE_APPOINTMENT"
    assert plan_turn2.get("action") == "SEARCH_AVAILABILITY"
    assert plan_turn2.get("status") != "NON_DURABLE_INTENT"

    slots_turn2 = plan_turn2.get("slots", {})
    assert slots_turn2.get("service_id") == "premium haircut"
    assert (
        slots_turn2.get("date") == "2026-07-03"
        or (plan_turn2.get("date_proposal") or {}).get("start") == "2026-07-03"
    )

    mock_availability_client.get_service_availability.assert_called_once()
    call_kwargs = mock_availability_client.get_service_availability.call_args.kwargs
    assert call_kwargs["organization_id"] == 1
    assert call_kwargs["date"] == "2026-07-03"
    # SKU alias resolved to catalog id at execution boundary
    assert call_kwargs["service_id"] == 18


def test_resolve_availability_fingerprint_prefers_execution_over_stale_session():
    """Plan without fingerprint must not block top-level availability fingerprint."""
    outcome = {
        "type": "availability",
        "status": "success",
        "availability_fingerprint": "new-fingerprint",
        "plan": {"status": "READY", "stage": "AVAILABILITY", "action": "SEARCH_AVAILABILITY"},
    }
    previous = {"availability_fingerprint": "stale-fingerprint"}

    assert resolve_availability_fingerprint(outcome, previous, None, "user") == "new-fingerprint"


class _StatefulSessionStore:
    def __init__(self, state=None):
        self._state = state or {}

    def get_session(self, _user_id):
        return self._state

    def save_session(self, _user_id, state):
        self._state = state


def _persist_session_from_result(result, previous_session, user_id, session_store=None):
    outcome = dict(result.get("outcome") or result.get("result") or {})
    plan = result.get("plan") or {}
    if not outcome.get("intent_name") and not outcome.get("intent"):
        plan_intent = plan.get("intent_name") or plan.get("intent")
        if plan_intent:
            outcome["intent_name"] = plan_intent
    outcome_status = outcome.get("status") or "success"
    merged = result.get("_merged_luma_response")
    new_session = build_session_state_from_outcome(
        outcome,
        outcome_status,
        merged,
        previous_session,
        user_id,
        session_store,
    )
    if new_session and session_store is not None:
        session_store.save_session(user_id, new_session)
    return new_session


def test_e2e_july6_search_fingerprint_and_time_selection_confirm():
    """
    book haircut → premium → SEARCH (July 3)
    July 6 availability → SEARCH, stored fingerprint matches current
    9:00 AM → bind slots, AWAITING_CONFIRMATION prompt (no booking API)
    yes → CONFIRM_APPOINTMENT
    """
    frozen_time = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    user_id = "test_user_july6_time_bind"

    mock_org_client = Mock(spec=OrganizationClient)
    mock_org_client.get_details.return_value = {
        "organization": {"businessCategoryId": 1}
    }

    july3_slots = [
        {
            "start": "2026-07-03T10:00:00Z",
            "end": "2026-07-03T10:30:00Z",
            "staff_id": 1,
        }
    ]
    july6_slots = [
        {
            "start": "2026-07-06T09:00:00Z",
            "end": "2026-07-06T09:30:00Z",
            "staff_id": 1,
        },
        {
            "start": "2026-07-06T11:00:00Z",
            "end": "2026-07-06T11:30:00Z",
            "staff_id": 1,
        },
    ]

    mock_availability_client = Mock(spec=AvailabilityClient)

    def _availability_side_effect(**kwargs):
        if kwargs.get("date") == "2026-07-06":
            return {"slots": july6_slots}
        return {"slots": july3_slots}

    mock_availability_client.get_service_availability.side_effect = _availability_side_effect

    mock_booking_client = Mock(spec=BookingClient)
    mock_booking_client.create_booking.return_value = {
        "booking": {"id": 42, "booking_code": "42"},
    }

    session_store = _StatefulSessionStore()
    session_state = None

    # Turn 1: book haircut (service disambiguation path)
    mock_luma_turn1 = Mock(spec=LumaClient)
    mock_luma_turn1.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {},
        "slots": {},
        "service_candidates": [
            {"text": "premium haircut", "id": 18},
            {"text": "standard haircut", "id": 17},
        ],
        "missing_slots": ["service_id"],
        "needs_clarification": True,
    }
    result1 = handle_message(
        text="book me a haircut",
        user_id=user_id,
        luma_client=mock_luma_turn1,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )
    assert result1.get("success") is True
    session_state = _persist_session_from_result(
        result1, session_state, user_id, session_store
    )
    assert session_state is not None

    # Turn 2: premium → SEARCH July 3
    mock_luma_turn2 = Mock(spec=LumaClient)
    mock_luma_turn2.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"service_id": "premium haircut"},
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["date", "time"],
        "needs_clarification": False,
    }
    result2 = handle_message(
        text="premium",
        user_id=user_id,
        luma_client=mock_luma_turn2,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )
    assert result2.get("success") is True
    plan2 = result2.get("plan") or result2.get("result", {})
    assert plan2.get("action") == "SEARCH_AVAILABILITY"
    session_state = _persist_session_from_result(
        result2, session_state, user_id, session_store
    )
    assert session_state is not None
    assert session_state.get("last_execution_result", {}).get("slots")

    # Turn 3: July 6 availability search
    mock_luma_turn3 = Mock(spec=LumaClient)
    mock_luma_turn3.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"dates": ["2026-07-06"], "service_id": "premium haircut"},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    result3 = handle_message(
        text="do you have a slot for July 6",
        user_id=user_id,
        luma_client=mock_luma_turn3,
        availability_client=mock_availability_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )
    assert result3.get("success") is True, result3.get("error")
    plan3 = result3.get("plan") or {}
    assert plan3.get("intent_name") == "CREATE_APPOINTMENT"
    assert plan3.get("action") == "SEARCH_AVAILABILITY"

    exec_result3 = result3.get("result", {})
    stored_fp = session_store.get_session(user_id).get("availability_fingerprint")
    current_fp = exec_result3.get("availability_fingerprint")
    assert stored_fp == current_fp

    session_state = _persist_session_from_result(
        result3, session_state, user_id, session_store
    )
    assert session_state is not None
    assert session_state.get("availability_fingerprint") == current_fp

    # Turn 4: 9:00 AM → bind + await confirmation (no SEARCH, no booking)
    mock_luma_turn4 = Mock(spec=LumaClient)
    mock_luma_turn4.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"times": ["9:00 AM"]},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    result4 = handle_message(
        text="9:00 AM",
        user_id=user_id,
        luma_client=mock_luma_turn4,
        availability_client=mock_availability_client,
        booking_client=mock_booking_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )
    assert result4.get("success") is True, result4.get("error")
    plan4 = result4.get("plan") or result4.get("result", {})
    assert plan4.get("status") == "AWAITING_CONFIRMATION"
    assert plan4.get("action") == "CONFIRM_APPOINTMENT"
    assert plan4.get("action") != "SEARCH_AVAILABILITY"
    mock_booking_client.create_booking.assert_not_called()

    slots4 = plan4.get("slots", {})
    assert slots4.get("date") == "2026-07-06"
    assert slots4.get("time") == "09:00"

    merged = result4.get("_merged_luma_response") or {}
    if merged.get("resolved_datetime_range", {}).get("start"):
        assert merged["resolved_datetime_range"]["start"]

    session_state = _persist_session_from_result(
        result4, session_state, user_id, session_store
    )
    assert session_state is not None
    assert session_state.get("confirmation_state") == "pending"

    # Turn 5: yes → CONFIRM_APPOINTMENT
    mock_luma_turn5 = Mock(spec=LumaClient)
    mock_luma_turn5.resolve.return_value = {
        "success": True,
        "intent": {"name": "CONFIRM_ACTION"},
        "facts": {},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    result5 = handle_message(
        text="yes",
        user_id=user_id,
        luma_client=mock_luma_turn5,
        availability_client=mock_availability_client,
        booking_client=mock_booking_client,
        organization_client=mock_org_client,
        session_store=session_store,
        frozen_time=frozen_time,
        organization_id=1,
    )
    assert result5.get("success") is True, result5.get("error")
    plan5 = result5.get("plan") or result5.get("result", {})
    assert plan5.get("action") == "CONFIRM_APPOINTMENT"
    mock_booking_client.create_booking.assert_called_once()

    assert mock_availability_client.get_service_availability.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
