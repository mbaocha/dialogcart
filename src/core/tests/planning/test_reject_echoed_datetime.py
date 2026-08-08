"""REJECT_ACTION must not restore echoed datetime facts from NLU."""

from __future__ import annotations

from unittest.mock import Mock

from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage06_confirmation import resolve_confirmation
from core.planning.pipeline.types import (
    AvailabilityDecision,
    SlotTurnState,
    WorkingTurn,
)
from core.planning.planning_mutations import apply_confirmation_planning_mutations
from core.session.confirmation_gate import ConfirmationGateTurn, get_confirmation_state
from core.tests.planning.test_reject_booking_confirmation import (
    _StatefulSessionStore,
    _reject_session,
)


def _echo_reject_nlu(*, date: str = "2026-07-06", time: str = "11:15") -> dict:
    return {
        "success": True,
        "intent": {"name": "REJECT_ACTION"},
        "response_act": "REJECT_ACTION",
        "facts": {
            "dates": [date],
            "times": [time],
            "has_datetime": True,
            "service_id": "flexi haircut + pruning",
        },
        "temporal": {
            "mode": "none",
            "start_date": date,
            "start_time": time,
            "end_date": None,
            "end_time": None,
            "expression": time,
            "confidence": 0.9,
        },
        "slots": {"date": date, "time": time},
        "missing_slots": [],
        "needs_clarification": False,
    }


def test_reject_echoed_same_datetime_clears_time_and_proposal():
    user_id = "reject_echo_same_clock"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["time_proposal"] = {"mode": "exact", "value": "11:15"}
    store = _StatefulSessionStore({user_id: session})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _echo_reject_nlu()

    result = handle_message(
        text="no",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=store,
        organization_id=1,
    )
    assert result.get("success") is True
    persisted = store.get_session(1, user_id) or {}
    slots = persisted.get("slots") or {}
    assert get_confirmation_state(persisted) is None
    assert "time" not in slots or slots.get("time") in (None, "")
    assert persisted.get("time_proposal") in (None, {})
    assert persisted.get("resolved_datetime_range") in (None, {})
    temporal = persisted.get("temporal") or {}
    assert temporal.get("start_time") in (None, "")
    assert temporal.get("end_time") in (None, "")
    facts = persisted.get("facts") or {}
    assert facts.get("times") in (None, [], {})
    assert facts.get("time_proposal") in (None, {})
    outcome = result.get("outcome") or {}
    assert (outcome.get("slots") or {}).get("time") in (None, "")
    assert outcome.get("status") != "AWAITING_CONFIRMATION"
    assert outcome.get("action") != "CONFIRM_APPOINTMENT"


def test_stale_yes_after_echoed_reject_cannot_book():
    user_id = "reject_echo_then_stale_yes"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["time_proposal"] = {"mode": "exact", "value": "11:15"}
    store = _StatefulSessionStore({user_id: session})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _echo_reject_nlu()

    first = handle_message(
        text="no",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=store,
        organization_id=1,
    )
    assert first.get("success") is True

    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CONFIRM_ACTION"},
        "response_act": "CONFIRM_ACTION",
        "facts": {},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    second = handle_message(
        text="yes",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=store,
        organization_id=1,
        availability_client=Mock(),
    )
    assert second.get("success") is True
    outcome = second.get("outcome") or {}
    plan = second.get("plan") or {}
    assert outcome.get("action") != "CONFIRM_APPOINTMENT"
    assert plan.get("action") != "CONFIRM_APPOINTMENT"
    persisted = store.get_session(1, user_id) or {}
    slots = persisted.get("slots") or {}
    assert "time" not in slots or slots.get("time") in (None, "")
    yes_text = str(second.get("text") or outcome.get("text") or "").strip()
    assert yes_text, (
        f"stale yes after reject must ask the next step, got {second!r}"
    )


def test_reject_mutations_drop_restated_current_turn_time():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
        "presented_availability": {"search_date": "2026-07-10", "slots": []},
        "time_proposal": {"mode": "exact", "value": "10:00"},
    }
    payload = {
        "slots": dict(session["slots"]),
        "_effective_collected_slots": dict(session["slots"]),
        "resolved_datetime_range": dict(session["resolved_datetime_range"]),
        "presented_availability": session["presented_availability"],
        "confirmation_state": "pending",
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "_current_turn_has_time": True,
        "_current_turn_time": "10:00",
        "_source_text": "no",
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(session["slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="NONE",
            session_reset_occurred=False,
            gate_action=ConfirmationGateTurn.NO,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(session["slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="reject_restatement",
    )
    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )
    assert get_confirmation_state(working_turn.payload) is None
    assert (working_turn.payload.get("slots") or {}).get("time") in (None, "")
    assert working_turn.payload.get("time_proposal") in (None, {})
    assert working_turn.payload.get("_current_turn_has_time") is False
    assert working_turn.payload.get("_current_turn_time") in (None, "")
    assert session.get("time_proposal") in (None, {})
    assert session.get("slots", {}).get("time") in (None, "")


def test_reject_mutations_keep_distinct_current_turn_time():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "time_proposal": {"mode": "exact", "value": "10:00"},
    }
    payload = {
        "slots": dict(session["slots"]),
        "_effective_collected_slots": dict(session["slots"]),
        "confirmation_state": "pending",
        "time_proposal": {"mode": "exact", "value": "11:00"},
        "_current_turn_has_time": True,
        "_current_turn_time": "11:00",
        "_source_text": "no, make it 11",
    }
    working_turn = WorkingTurn(
        payload=payload,
        effective_collected_slots=dict(session["slots"]),
    )
    decision = resolve_confirmation(
        attached_request=AttachedRequest(
            planning_intent="CREATE_APPOINTMENT",
            turn_operation="NONE",
            session_reset_occurred=False,
            gate_action=ConfirmationGateTurn.NO,
        ),
        slot_state=SlotTurnState(
            intent_name="CREATE_APPOINTMENT",
            missing_slots=[],
            effective_collected_slots=dict(session["slots"]),
            base_status="AWAITING_CONFIRMATION",
        ),
        working_turn=working_turn,
        availability=AvailabilityDecision(availability_ready=True),
        session_state=session,
        gate_booking_intent="CREATE_APPOINTMENT",
        user_id="reject_new_clock",
    )
    apply_confirmation_planning_mutations(
        working_turn, decision, session_state=session
    )
    assert working_turn.payload.get("_current_turn_has_time") is True
    assert working_turn.payload.get("_current_turn_time") == "11:00"
    assert working_turn.payload.get("time_proposal") == {"mode": "exact", "value": "11:00"}
    assert working_turn.payload.get("_booking_confirmation_rejected") is True


def test_resolve_session_proposals_drops_echoed_reject_time():
    from core.planning.temporal_proposal import resolve_session_proposals

    previous = {
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-03"},
    }
    merged = {
        "_booking_confirmation_rejected": True,
        "_current_turn_has_time": False,
        "time_proposal": {"mode": "exact", "value": "10:00"},
    }
    proposals = resolve_session_proposals(
        merged_luma_response=merged,
        outcome={"facts": {"time_proposal": {"mode": "exact", "value": "10:00"}}},
        previous_session_state=previous,
    )
    assert proposals["time_proposal"] is None


def test_persist_echoed_reject_does_not_restore_time_proposal():
    from core.session.persist import assemble_session_projection_fields

    previous = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-03",
            "time": "10:00",
        },
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "facts": {"times": ["10:00"], "time_proposal": {"mode": "exact", "value": "10:00"}},
        "temporal": {
            "mode": "single_day",
            "start_date": "2026-07-03",
            "start_time": "10:00",
            "end_time": "10:00",
        },
    }
    merged = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": {"service_id": "premium haircut", "date": "2026-07-03"},
        "_effective_collected_slots": {
            "service_id": "premium haircut",
            "date": "2026-07-03",
        },
        "facts": {
            "times": ["10:00"],
            "has_datetime": True,
            "time_proposal": {"mode": "exact", "value": "10:00"},
        },
        "temporal": {
            "mode": "single_day",
            "start_date": "2026-07-03",
            "start_time": None,
            "end_time": None,
            "start_time_expression": None,
            "end_time_expression": None,
        },
        "_booking_confirmation_rejected": True,
        "_current_turn_has_time": False,
        "confirmation_state": None,
    }
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": merged["slots"],
        "facts": merged["facts"],
        "missing_slots": ["time"],
        "confirmation_state": None,
    }
    persisted = assemble_session_projection_fields(
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        organization_id=1,
        merged_luma_response=merged,
        previous_session_state=previous,
        user_id="reject-echo-persist",
    )
    assert persisted is not None
    assert persisted.get("time_proposal") in (None, {})
    assert (persisted.get("slots") or {}).get("time") in (None, "")
    assert (persisted.get("facts") or {}).get("times") in (None, [], {})
    assert (persisted.get("temporal") or {}).get("start_time") in (None, "")
