"""Unit tests for centralized invalidation registry (PR1)."""

from core.planning.booking_revision import BookingRevision
from core.session.confirmation_gate import get_confirmation_state
from core.session.invalidation import InvalidationTrigger, apply_invalidation


def _pending_session():
    return {
        "confirmation_state": "pending",
        "slots": {
            "service_id": "haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
        "presented_availability": {"slots": []},
        "availability_fingerprint": "fp",
    }


def test_reject_confirmation_trigger_clears_time():
    session = _pending_session()
    session["time_proposal"] = {"mode": "exact", "value": "09:00"}
    session["resolved_datetime_range"] = {
        "start": "2026-07-06T09:00:00Z",
        "end": "2026-07-06T09:30:00Z",
    }
    apply_invalidation(
        session,
        InvalidationTrigger.REJECT_CONFIRMATION,
        reason="test_reject",
    )
    assert get_confirmation_state(session) is None
    assert "time" not in session["slots"]
    assert session["slots"]["date"] == "2026-07-06"
    assert "time_proposal" not in session
    assert "resolved_datetime_range" not in session


def test_apply_bound_datetime_clear_syncs_slot_projections():
    from core.session.invalidation import (
        apply_bound_datetime_clear,
        sync_working_slot_projections,
    )

    state = {
        "confirmation_state": "pending",
        "slots": {
            "service_id": "haircut",
            "date": "2026-07-06",
            "time": "09:00",
            "has_datetime": True,
        },
        "_effective_collected_slots": {
            "service_id": "haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
        "resolved_datetime_range": {
            "start": "2026-07-06T09:00:00Z",
            "end": "2026-07-06T09:30:00Z",
        },
        "time_proposal": {"mode": "exact", "value": "09:00"},
        "time_match_outcome": "TIME_MATCH_EXACT",
        "time_resolution": {"status": "bound"},
    }
    slots = apply_bound_datetime_clear(state, preserve_current_turn_time=False)
    assert get_confirmation_state(state) == "pending"  # confirmation not consumed here
    assert "time" not in slots
    assert "has_datetime" not in slots
    assert state["slots"] is state["_effective_collected_slots"]
    assert state["slots"] is slots
    assert "resolved_datetime_range" not in state
    assert "time_proposal" not in state
    assert "time_match_outcome" not in state
    assert "time_resolution" not in state
    assert state.get("_bound_datetime_cleared") is True
    assert slots.get("service_id") == "haircut"
    assert slots.get("date") == "2026-07-06"

    # Sync helper is the single projection writer.
    synced = sync_working_slot_projections(state, {"service_id": "x", "date": "y"})
    assert state["slots"] is synced
    assert state["_effective_collected_slots"] is synced


def test_apply_bound_datetime_clear_preserves_current_turn_proposal():
    from core.session.invalidation import apply_bound_datetime_clear

    state = {
        "slots": {"service_id": "haircut", "date": "2026-07-06", "time": "09:00"},
        "resolved_datetime_range": {"start": "2026-07-06T09:00:00Z"},
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "time_match_outcome": "stale",
    }
    apply_bound_datetime_clear(state, preserve_current_turn_time=True)
    assert state.get("time_proposal") == {"mode": "exact", "value": "10:00"}
    assert "time" not in state["slots"]
    assert "resolved_datetime_range" not in state
    assert "time_match_outcome" not in state


def test_booking_revision_time_trigger_keeps_availability():
    session = _pending_session()
    apply_invalidation(
        session,
        InvalidationTrigger.BOOKING_REVISION,
        revision=BookingRevision(time=True),
        reason="test_time_revision",
    )
    assert "time" not in session["slots"]
    assert session.get("presented_availability") is not None


def test_ambiguous_service_trigger_drops_service_id():
    merged = {
        "facts": {"slots": {"service_id": "stale"}},
        "_raw_luma_slots": {"service_id": "stale", "date": "2026-07-06"},
    }
    merged_slots = {"service_id": "stale", "date": "2026-07-06"}
    apply_invalidation(
        merged,
        InvalidationTrigger.AMBIGUOUS_SERVICE,
        merged_slots=merged_slots,
        raw_service_id_from_session="stale",
        current_candidates=[{"text": "haircut"}],
    )
    assert "service_id" not in merged_slots
    assert "service_id" in merged.get("_intentionally_dropped_slots", set())


def test_new_booking_request_trigger_clears_booking_id():
    merged_slots = {"booking_id": "bk-1", "service_id": "haircut"}
    session_state = {"availability_fingerprint": "fp"}
    apply_invalidation(
        {},
        InvalidationTrigger.NEW_BOOKING_REQUEST,
        merged_slots=merged_slots,
        session_state=session_state,
        merged_intent_name="CREATE_APPOINTMENT",
        luma_slots={"date": "2026-07-10"},
    )
    assert "booking_id" not in merged_slots
    assert "availability_fingerprint" not in session_state
