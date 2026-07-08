"""Unit tests for centralized invalidation registry (PR1)."""

from core.session.confirmation_gate import BookingRevision, get_confirmation_state
from core.session.invalidation import InvalidationTrigger, apply_invalidation


def _pending_session():
    return {
        "confirmation_state": "pending",
        "booking": {"confirmation_state": "pending"},
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
    apply_invalidation(
        session,
        InvalidationTrigger.REJECT_CONFIRMATION,
        reason="test_reject",
    )
    assert get_confirmation_state(session) is None
    assert "time" not in session["slots"]
    assert session["slots"]["date"] == "2026-07-06"


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
