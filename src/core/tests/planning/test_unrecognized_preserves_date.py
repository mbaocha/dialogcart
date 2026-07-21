"""Unrecognized input must not drop previously resolved booking date state."""

from copy import deepcopy

from core.planning.booking_revision import has_actionable_booking_facts, has_revision_facts
from core.planning.pipeline.requests import AttachedRequest
from core.planning.pipeline.stage02_working_turn import build_working_turn
from core.planning.pipeline.stage04_slots import resolve_slot_turn_state


def _oooo_luma() -> dict:
    return {
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.4},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "service_term": None,
        "temporal": {
            "mode": "none",
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "end_time": None,
            "start_date_expression": None,
            "start_time_expression": None,
            "end_date_expression": None,
            "end_time_expression": None,
            "expression": None,
            "confidence": 0.0,
        },
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
        "understanding": "UNRECOGNIZED_INPUT",
    }


def _run_oooo(session: dict):
    attached = AttachedRequest(
        planning_intent="CREATE_APPOINTMENT",
        turn_operation="NONE",
        session_reset_occurred=False,
    )
    luma = _oooo_luma()
    working = build_working_turn(
        luma_response=deepcopy(luma),
        raw_luma_response_deep_copy=deepcopy(luma),
        attached_request=attached,
        session_state=deepcopy(session),
        original_session_state=deepcopy(session),
        source_text="oooo",
        tenant_context={"aliases": {}, "booking_mode": "service"},
        apply_domain_filter=True,
    )
    slot_state = resolve_slot_turn_state(
        intent_name="CREATE_APPOINTMENT",
        working_turn=working,
        attached_request=attached,
        session_state=session,
    )
    return working, slot_state


def test_unrecognized_input_keeps_date_proposal():
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "premium haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-21"},
        "missing_slots": ["time"],
        "presented_availability": {
            "search_date": "2026-07-21",
            "times": ["09:00", "10:00"],
        },
    }
    working, slot_state = _run_oooo(session)
    assert working.payload.get("date_proposal", {}).get("start") == "2026-07-21"
    assert slot_state.missing_slots == ["time"]
    assert working.payload.get("slots", {}).get("service_id") == "premium haircut"


def test_unrecognized_input_recovers_date_from_presented_availability():
    """When proposal mirrors are absent, active search date still satisfies planning."""
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["time"],
        "presented_availability": {
            "search_date": "2026-07-21",
            "times": ["09:00", "10:00"],
        },
        "last_execution_result": {
            "search_date": "2026-07-21",
            "slots": [{"starts_at": "2026-07-21T09:00:00"}],
        },
    }
    working, slot_state = _run_oooo(session)
    assert working.payload.get("date_proposal", {}).get("start") == "2026-07-21"
    assert slot_state.missing_slots == ["time"]


def test_session_carried_date_proposal_is_not_actionable_revision():
    merged = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "dates": [],
            "times": [],
            "date_time_pairs": [],
            "service_id": None,
            "booking_id": None,
        },
        "temporal": {
            "mode": "single_day",
            "start_date": "2026-07-21",
            "confidence": 1.0,
        },
        "date_proposal": {"mode": "single_day", "start": "2026-07-21"},
        "_current_turn_has_date": False,
        "_current_turn_has_time": False,
        "turn": {"understanding": "UNRECOGNIZED_INPUT"},
    }
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "slots": {"service_id": "premium haircut"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-21"},
    }
    assert has_revision_facts(merged) is False
    assert has_actionable_booking_facts(merged, session) is False
