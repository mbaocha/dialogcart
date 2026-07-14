"""Confirmation is consumed after successful CREATE_APPOINTMENT commit."""

from core.session.persist import build_session_state_from_outcome
from core.planning.facts.business_fact_registry import (
    PlanningFactContext,
    derive_business_facts,
)
from core.planning.orchestration.plan_builder import _maybe_enter_booking_confirmation_pending
from core.session.appointment_extensions import _maybe_persist_booking_confirmation_pending
from core.session.confirmation_gate import (
    consume_create_appointment_confirmation,
    get_confirmation_state,
    has_committed_create_appointment,
    is_confirmation_gate_open,
)


def test_has_committed_create_appointment():
    assert has_committed_create_appointment({"booking_id": "bk-1"}) is True
    assert has_committed_create_appointment({"booking_id": ""}) is False
    assert has_committed_create_appointment({}) is False


def test_persist_clears_confirmation_when_booking_id_present():
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "booking": {"confirmation_state": "pending"},
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-99",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    merged = {
        "booking": {"confirmation_state": "confirmed"},
        "confirmation_state": "confirmed",
    }
    _maybe_persist_booking_confirmation_pending(session_state, merged, {})
    assert get_confirmation_state(session_state) is None
    assert get_confirmation_state(merged) is None


def test_persist_clears_confirmation_from_successful_confirm_outcome():
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
        },
    }
    merged = {"booking": {"confirmation_state": "confirmed"}}
    outcome = {
        "status": "EXECUTED",
        "booking_id": "bk-new",
        "plan": {
            "action": "CONFIRM_APPOINTMENT",
            "slots": {
                "service_id": "premium",
                "date": "2026-07-10",
                "time": "10:00",
                "booking_id": "bk-new",
            },
        },
    }
    _maybe_persist_booking_confirmation_pending(session_state, merged, outcome)
    assert session_state["slots"]["booking_id"] == "bk-new"
    assert get_confirmation_state(session_state) is None
    assert get_confirmation_state(merged) is None


def test_persist_does_not_reenter_pending_when_booking_id_exists():
    session_state = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-1",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    _maybe_persist_booking_confirmation_pending(session_state, {}, {})
    assert get_confirmation_state(session_state) is None


def test_plan_builder_skips_pending_when_booking_id_exists():
    luma_response = {
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-1",
        },
        "_effective_collected_slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-1",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    result = _maybe_enter_booking_confirmation_pending(
        "CREATE_APPOINTMENT",
        luma_response,
        missing_slots=[],
        needs_clarification=False,
        availability_resolved=True,
        confirmation_state=None,
    )
    assert result is None
    assert get_confirmation_state(luma_response) is None


def test_business_facts_skip_confirmation_when_booking_id_exists():
    facts = derive_business_facts(
        PlanningFactContext(
            intent_name="CREATE_APPOINTMENT",
            slots={
                "service_id": "premium",
                "date": "2026-07-10",
                "time": "10:00",
                "booking_id": "bk-1",
            },
            session_state={
                "availability_fingerprint": "fp",
                "resolved_datetime_range": {
                    "start": "2026-07-10T10:00:00Z",
                    "end": "2026-07-10T10:30:00Z",
                },
            },
        )
    )
    assert facts.user_confirmation_required is False


def test_gate_closed_after_commit():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "booking": {"confirmation_state": "pending"},
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "bk-1",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    consume_create_appointment_confirmation(session)
    assert is_confirmation_gate_open(session) is False


def test_executed_confirm_appointment_rebuilds_session_and_consumes_confirmation():
    """EXECUTED outcomes must flow through the normal persistence pipeline."""
    previous_session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "booking": {"confirmation_state": "pending"},
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
            "organization_id": 1,
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
        "availability_fingerprint": "fp-abc",
        "presented_availability": {"search_date": "2026-07-10", "slots": []},
    }
    merged_luma = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "booking": {"confirmation_state": "confirmed"},
        "slots": previous_session["slots"],
        "_effective_collected_slots": previous_session["slots"],
        "resolved_datetime_range": previous_session["resolved_datetime_range"],
    }
    execution_outcome = {
        "status": "EXECUTED",
        "booking_id": "MOCK-BOOKING-001",
        "facts": {
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                **previous_session["slots"],
                "booking_id": "MOCK-BOOKING-001",
            },
        },
        "plan": {
            "status": "READY",
            "stage": "CONFIRM",
            "action": "CONFIRM_APPOINTMENT",
            "slots": {
                **previous_session["slots"],
                "booking_id": "MOCK-BOOKING-001",
            },
        },
    }

    session_state = build_session_state_from_outcome(
        execution_outcome,
        "EXECUTED",
        merged_luma,
        previous_session,
        user_id="e2e-user",
    )

    assert session_state is not None
    assert session_state["intent_name"] == "CREATE_APPOINTMENT"
    assert session_state["slots"]["booking_id"] == "MOCK-BOOKING-001"
    assert get_confirmation_state(session_state) is None
    assert session_state["slots"]["service_id"] == "premium haircut"
    assert session_state.get("resolved_datetime_range")
    assert session_state.get("availability_fingerprint") == "fp-abc"
