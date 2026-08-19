from core.session.booking_lifecycle import BookingLifecycle, derive_booking_lifecycle
from core.session.session_schema_v2 import empty_session_v2, prepare_session_for_load


def test_booking_lifecycle_idle():
    assert derive_booking_lifecycle(empty_session_v2()) == BookingLifecycle.IDLE


def test_booking_lifecycle_active():
    session = empty_session_v2()
    session["planning"]["intent_name"] = "CREATE_APPOINTMENT"
    session["planning"]["slots"] = {"service_id": "oil-change"}
    assert derive_booking_lifecycle(session) == BookingLifecycle.ACTIVE


def test_booking_lifecycle_awaiting_confirmation():
    session = empty_session_v2()
    session["planning"]["intent_name"] = "CREATE_APPOINTMENT"
    session["planning"]["slots"] = {"service_id": "oil-change"}
    session["confirmation_state"] = "pending"
    assert (
        derive_booking_lifecycle(session)
        == BookingLifecycle.AWAITING_CONFIRMATION
    )


def test_booking_lifecycle_committed_wins_over_active_and_pending():
    session = empty_session_v2()
    session["booking"]["booking_id"] = "bk-1"
    session["planning"]["intent_name"] = "CREATE_APPOINTMENT"
    session["planning"]["slots"] = {"service_id": "oil-change"}
    session["confirmation_state"] = "pending"
    assert derive_booking_lifecycle(session) == BookingLifecycle.COMMITTED


def test_load_sanitizes_contradictory_committed_authorization():
    session = empty_session_v2()
    session["booking"] = {"booking_id": "bk-1", "booking_code": "ORG-1"}
    session["planning"].update(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "AWAITING_CONFIRMATION",
            "slots": {"service_id": "oil-change", "date": "2026-08-17"},
            "bound_datetime": {"start": "2026-08-17T09:00:00Z"},
            "missing_slots": ["registration_number"],
            "ask_next": "registration_number",
            "pending_profile_request": "CUSTOMER_CONTACT_NAME",
        }
    )
    session["confirmation_state"] = "pending"
    session["availability"]["fingerprint"] = "old-fingerprint"
    session["availability"]["cache"]["search_result"] = {"slots": [{}]}

    loaded = prepare_session_for_load(session)

    assert derive_booking_lifecycle(loaded) == BookingLifecycle.COMMITTED
    assert loaded["confirmation_state"] is None
    assert loaded["planning"]["intent_name"] is None
    assert loaded["planning"]["slots"] == {}
    assert loaded["planning"]["bound_datetime"] is None
    assert loaded["planning"]["missing_slots"] == []
    assert loaded["planning"]["pending_profile_request"] is None
    assert loaded["availability"]["fingerprint"] is None
    assert loaded["availability"]["cache"]["search_result"] is None


def test_legacy_committed_session_is_normalized_before_lifecycle_derivation():
    loaded = prepare_session_for_load(
        {
            "intent_name": "CREATE_APPOINTMENT",
            "status": "AWAITING_CONFIRMATION",
            "slots": {"booking_id": "bk-legacy", "service_id": "oil-change"},
            "confirmation_state": "pending",
        }
    )
    assert derive_booking_lifecycle(loaded) == BookingLifecycle.COMMITTED
    assert loaded["confirmation_state"] is None


def test_load_does_not_close_distinct_modify_workflow_for_committed_booking():
    session = empty_session_v2()
    session["booking"] = {"booking_id": "bk-1", "booking_code": "ORG-1"}
    session["planning"]["intent_name"] = "MODIFY_BOOKING"
    session["planning"]["status"] = "NEEDS_CLARIFICATION"
    session["planning"]["slots"] = {"booking_id": "bk-1"}

    loaded = prepare_session_for_load(session)

    assert loaded["planning"]["intent_name"] == "MODIFY_BOOKING"
    assert loaded["planning"]["status"] == "NEEDS_CLARIFICATION"
