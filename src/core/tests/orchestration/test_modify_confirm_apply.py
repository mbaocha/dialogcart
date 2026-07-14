"""Confirm continuation selects APPLY_MODIFICATION without service_id fingerprint."""

from core.adapters.nlu.luma_response_processor import process_luma_response

_SLOTS = {
    "booking_id": "ABC12345",
    "organization_id": 1,
    "date": "2026-01-14",
    "time": "15:00",
}


def test_confirm_continuation_uses_resolved_datetime_range_without_fingerprint():
    session_state = {
        "intent_name": "MODIFY_BOOKING",
        "status": "READY",
        "stage": "AVAILABILITY",
        "action": "SEARCH_AVAILABILITY",
        "resolved_datetime_range": {
            "start": "2026-01-14T15:00:00",
            "end": "2026-01-14T16:00:00",
        },
        "slots": _SLOTS,
    }
    luma_response = {
        "intent": {"name": "MODIFY_BOOKING"},
        "_effective_intent": "MODIFY_BOOKING",
        "_confirm_booking_continuation": True,
        "booking": {"confirmation_state": "confirmed"},
        "slots": dict(_SLOTS),
        "_effective_collected_slots": dict(_SLOTS),
        "missing_slots": [],
        "facts": {"org": {"businessCategoryId": 1}},
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", session_state=session_state
    )

    assert decision["plan"]["action"] == "APPLY_MODIFICATION"
    assert decision["plan"]["status"] == "READY"


def test_confirm_continuation_modify_ready_without_range_or_fingerprint():
    """MODIFY confirm after search: READY session without fingerprint or resolved range."""
    session_state = {
        "intent_name": "MODIFY_BOOKING",
        "status": "READY",
        "stage": "AVAILABILITY",
        "action": "SEARCH_AVAILABILITY",
        "slots": {
            "booking_id": "ABC12345",
            "organization_id": 1,
            "date": "tomorrow",
            "time": "15:00",
        },
    }
    luma_response = {
        "intent": {"name": "MODIFY_BOOKING"},
        "_effective_intent": "MODIFY_BOOKING",
        "_confirm_booking_continuation": True,
        "booking": {"confirmation_state": "confirmed"},
        "slots": dict(session_state["slots"]),
        "_effective_collected_slots": dict(session_state["slots"]),
        "missing_slots": [],
        "facts": {"org": {"businessCategoryId": 1}},
    }

    decision = process_luma_response(
        luma_response, "service", "test_user", session_state=session_state
    )

    assert decision["plan"]["action"] == "APPLY_MODIFICATION"
    assert decision["plan"]["status"] == "READY"
