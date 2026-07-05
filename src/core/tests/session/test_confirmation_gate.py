"""Unit tests for confirmation gate classification and clear-pending (PR1–PR2)."""

from core.session.confirmation_gate import (
    BookingRevision,
    ConfirmationGateTurn,
    apply_booking_revision,
    classify_confirmation_gate_turn,
    clear_pending_confirmation,
    detect_booking_revision,
    get_confirmation_state,
    has_actionable_booking_facts,
    has_revision_facts,
    is_confirmation_gate_open,
    normalize_confirmation_state,
    set_confirmation_state,
)


def _pending_session(**overrides):
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "booking": {"confirmation_state": "pending"},
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-06",
            "time": "09:00",
        },
        "resolved_datetime_range": {
            "start": "2026-07-06T09:00:00Z",
            "end": "2026-07-06T09:30:00Z",
        },
    }
    session.update(overrides)
    return session


def test_gate_open_when_pending():
    assert is_confirmation_gate_open(_pending_session()) is True


def test_gate_open_when_bound_datetime_even_if_status_needs_clarification():
    session = _pending_session(
        status="NEEDS_CLARIFICATION",
        confirmation_state=None,
        booking={},
    )
    assert is_confirmation_gate_open(session) is True


def test_gate_closed_without_booking_session():
    assert is_confirmation_gate_open(None) is False
    assert is_confirmation_gate_open({"intent_name": "CREATE_APPOINTMENT"}) is False


def test_accept_on_confirm_action():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "CONFIRM_ACTION"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.ACCEPT


def test_reject_on_reject_action_without_revision_facts():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "REJECT_ACTION"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.REJECT


def test_revise_wins_over_reject_when_time_present():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "REJECT_ACTION"},
            "facts": {"times": ["11:00"]},
            "time_constraint": {
                "mode": "exact",
                "start": "11:00",
                "end": "11:00",
            },
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.REVISE


def test_revise_on_correction_with_time():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CORRECTION"},
            "facts": {"times": ["11:00"], "service_id": "premium haircut"},
            "time_constraint": {
                "mode": "exact",
                "start": "11:00",
                "end": "11:00",
            },
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.REVISE


def test_revise_on_create_appointment_with_time_only():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"times": ["11am"]},
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.REVISE


def test_none_when_gate_closed():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "REJECT_ACTION"}, "facts": {}},
        {"intent_name": "CREATE_APPOINTMENT", "status": "NEEDS_CLARIFICATION", "slots": {}},
    )
    assert action == ConfirmationGateTurn.NONE


def test_none_for_unrelated_intent_while_pending():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "FAQ"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.NONE


def test_same_service_id_echo_is_not_revision_alone():
    assert (
        has_revision_facts(
            {"intent": {"name": "CORRECTION"}, "facts": {"service_id": "premium haircut"}}
        )
        is False
    )
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CORRECTION"},
            "facts": {"service_id": "premium haircut"},
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.NONE


def test_service_change_is_revise():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CORRECTION"},
            "facts": {"service_id": "flexi haircut + pruning"},
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.REVISE


def test_actionable_facts_include_exact_time_proposal():
    assert (
        has_actionable_booking_facts(
            {
                "intent": {"name": "CORRECTION"},
                "facts": {"times": ["11:00"]},
                "time_constraint": {"mode": "exact", "start": "11:00", "end": "11:00"},
            },
            _pending_session(),
        )
        is True
    )


def test_actionable_facts_false_for_empty_side_intent():
    assert (
        has_actionable_booking_facts(
            {"intent": {"name": "FAQ"}, "facts": {}},
            _pending_session(),
        )
        is False
    )


def test_set_confirmation_state_mirrors_booking_and_top_level():
    session = {"slots": {}}
    set_confirmation_state(session, "pending")
    assert session["booking"]["confirmation_state"] == "pending"
    assert session["confirmation_state"] == "pending"
    assert get_confirmation_state(session) == "pending"


def test_normalize_prefers_booking_over_top_level():
    session = {
        "confirmation_state": "confirmed",
        "booking": {"confirmation_state": "pending"},
    }
    normalize_confirmation_state(session)
    assert get_confirmation_state(session) == "pending"
    assert session["confirmation_state"] == "pending"
    assert session["booking"]["confirmation_state"] == "pending"


def test_normalize_promotes_top_level_into_booking():
    session = {"confirmation_state": "pending", "booking": {}}
    normalize_confirmation_state(session)
    assert session["booking"]["confirmation_state"] == "pending"
    assert get_confirmation_state(session) == "pending"


def test_detect_time_only_revision():
    revision = detect_booking_revision(
        {
            "facts": {"times": ["11:00"]},
            "time_constraint": {"mode": "exact", "start": "11:00", "end": "11:00"},
        },
        _pending_session(),
    )
    assert revision.time is True
    assert revision.service is False
    assert revision.date is False
    summary = revision.to_summary()
    assert summary == {
        "changes": [{"field": "time", "from": "09:00", "to": "11:00"}]
    }


def test_detect_date_revision():
    revision = detect_booking_revision(
        {"facts": {"dates": ["2026-07-11"]}},
        _pending_session(),
    )
    assert revision.date is True
    assert revision.time is False
    assert revision.to_summary()["changes"][0]["to"] == "2026-07-11"


def test_detect_service_revision():
    revision = detect_booking_revision(
        {"facts": {"service_id": "flexi haircut + pruning"}},
        _pending_session(),
    )
    assert revision.service is True
    assert revision.to_summary()["changes"][0]["from"] == "premium haircut"


def test_apply_time_revision_keeps_presented_availability():
    session = _pending_session(
        presented_availability={"search_date": "2026-07-06", "slots": []},
        availability_fingerprint="fp",
        last_execution_result={"slots": []},
    )
    apply_booking_revision(session, BookingRevision(time=True), reason="test")
    assert "time" not in session["slots"]
    assert session["slots"].get("date") == "2026-07-06"
    assert session["slots"].get("service_id") == "premium haircut"
    assert session.get("presented_availability") is not None
    assert get_confirmation_state(session) is None


def test_apply_date_revision_clears_availability_artifacts():
    session = _pending_session(
        presented_availability={"search_date": "2026-07-06", "slots": []},
        availability_fingerprint="fp",
        last_execution_result={"slots": []},
    )
    apply_booking_revision(session, BookingRevision(date=True), reason="test")
    assert "date" not in session["slots"]
    assert "time" not in session["slots"]
    assert session["slots"].get("service_id") == "premium haircut"
    assert "presented_availability" not in session
    assert "availability_fingerprint" not in session
    assert "last_execution_result" not in session


def test_apply_service_revision_clears_service_and_availability():
    session = _pending_session(
        presented_availability={"search_date": "2026-07-06", "slots": []},
        availability_fingerprint="fp",
    )
    apply_booking_revision(session, BookingRevision(service=True), reason="test")
    assert "service_id" not in session["slots"]
    assert "date" not in session["slots"]
    assert "time" not in session["slots"]
    assert "presented_availability" not in session


def test_clear_pending_with_time():
    session = _pending_session(
        facts={
            "slots": {"service_id": "premium haircut", "date": "2026-07-06", "time": "09:00"},
            "resolved_datetime_range": {
                "start": "2026-07-06T09:00:00Z",
                "end": "2026-07-06T09:30:00Z",
            },
        }
    )
    clear_pending_confirmation(session, clear_time=True, reason="test")
    assert "confirmation_state" not in session
    assert session.get("booking") == {}
    assert session["slots"].get("date") == "2026-07-06"
    assert "time" not in session["slots"]
    assert "resolved_datetime_range" not in session
    assert "resolved_datetime_range" not in session["facts"]
    assert "time" not in session["facts"]["slots"]


def test_clear_pending_confirmation_only_keeps_time():
    session = _pending_session()
    clear_pending_confirmation(session, clear_time=False, reason="rebind")
    assert "confirmation_state" not in session
    assert session.get("booking") == {}
    assert session["slots"].get("time") == "09:00"
    assert session.get("resolved_datetime_range")
