"""Unit tests for confirmation gate classification and clear-pending (PR1–PR2)."""

from core.planning.booking_revision import (
    BookingRevision,
    detect_booking_revision,
    has_actionable_booking_facts,
    has_revision_facts,
)
from core.session.confirmation_gate import (
    ConfirmationGateTurn,
    classify_confirmation_gate_turn,
    get_confirmation_state,
    is_confirmation_gate_open,
    normalize_confirmation_state,
    set_confirmation_state,
)
from core.session.invalidation import (
    InvalidationTrigger,
    apply_invalidation,
    clear_booking_state,
)


def _pending_session(**overrides):
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
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


def test_gate_closed_without_pending_authorization_even_if_datetime_bound():
    session = _pending_session(
        status="NEEDS_CLARIFICATION",
        confirmation_state=None,
        booking={},
    )
    assert is_confirmation_gate_open(session) is False


def test_gate_closed_without_booking_session():
    assert is_confirmation_gate_open(None) is False
    assert is_confirmation_gate_open(
        {"intent_name": "CREATE_APPOINTMENT"}) is False


def test_accept_on_confirm_action():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "CONFIRM_ACTION"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.YES


def test_reject_on_reject_action_without_revision_facts():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "REJECT_ACTION"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.NO


def test_reject_action_is_no_even_when_time_present():
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
    assert action == ConfirmationGateTurn.NO


def test_correction_with_time_is_another_request():
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
    assert action == ConfirmationGateTurn.ANOTHER_REQUEST


def test_create_appointment_with_time_only_is_another_request():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CREATE_APPOINTMENT"},
            "facts": {"times": ["11am"]},
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.ANOTHER_REQUEST


def test_no_classification_when_gate_closed():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "REJECT_ACTION"}, "facts": {}},
        {"intent_name": "CREATE_APPOINTMENT",
            "status": "NEEDS_CLARIFICATION", "slots": {}},
    )
    assert action is None


def test_unrelated_intent_while_pending_is_another_request():
    action = classify_confirmation_gate_turn(
        {"intent": {"name": "FAQ"}, "facts": {}},
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.ANOTHER_REQUEST


def test_same_service_id_echo_is_not_revision_alone():
    assert (
        has_revision_facts(
            {"intent": {"name": "CORRECTION"}, "facts": {
                "service_id": "premium haircut"}}
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
    assert action == ConfirmationGateTurn.ANOTHER_REQUEST


def test_service_change_is_another_request():
    action = classify_confirmation_gate_turn(
        {
            "intent": {"name": "CORRECTION"},
            "facts": {"service_id": "flexi haircut + pruning"},
        },
        _pending_session(),
    )
    assert action == ConfirmationGateTurn.ANOTHER_REQUEST


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


def test_set_confirmation_state_writes_top_level_only():
    session = {"slots": {}}
    set_confirmation_state(session, "pending")
    assert session["confirmation_state"] == "pending"
    assert "booking" not in session
    assert get_confirmation_state(session) == "pending"


def test_normalize_prefers_top_level_and_removes_nested_value():
    session = {
        "confirmation_state": "confirmed",
        "booking": {"confirmation_state": "pending"},
    }
    normalize_confirmation_state(session)
    assert get_confirmation_state(session) == "confirmed"
    assert session["confirmation_state"] == "confirmed"
    assert "confirmation_state" not in session["booking"]


def test_normalize_migrates_nested_value_to_top_level():
    session = {"booking": {"confirmation_state": "pending"}}
    normalize_confirmation_state(session)
    assert session["confirmation_state"] == "pending"
    assert "confirmation_state" not in session["booking"]
    assert get_confirmation_state(session) == "pending"


def test_explicit_top_level_clear_wins_over_legacy_nested_value():
    session = {
        "confirmation_state": None,
        "booking": {"confirmation_state": "pending"},
    }
    normalize_confirmation_state(session)
    assert "confirmation_state" not in session
    assert "confirmation_state" not in session["booking"]
    assert get_confirmation_state(session) is None


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
    assert len(revision.changes) == 1
    assert revision.changes[0].field == "time"
    assert revision.changes[0].from_value == "09:00"
    assert revision.changes[0].to_value == "11:00"


def test_detect_date_revision():
    revision = detect_booking_revision(
        {"facts": {"dates": ["2026-07-11"]}},
        _pending_session(),
    )
    assert revision.date is True
    assert revision.time is False
    assert revision.changes[0].field == "date"
    assert revision.changes[0].to_value == "2026-07-11"


def test_detect_service_revision():
    revision = detect_booking_revision(
        {"facts": {"service_id": "flexi haircut + pruning"}},
        _pending_session(),
    )
    assert revision.service is True
    assert revision.changes[0].field == "service"
    assert revision.changes[0].from_value == "premium haircut"


def test_apply_time_revision_keeps_presented_availability():
    session = _pending_session(
        presented_availability={"search_date": "2026-07-06", "slots": []},
        availability_fingerprint="fp",
        last_execution_result={"slots": []},
    )
    apply_invalidation(
        session,
        InvalidationTrigger.BOOKING_REVISION,
        revision=BookingRevision(time=True),
        reason="test",
    )
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
    apply_invalidation(
        session,
        InvalidationTrigger.BOOKING_REVISION,
        revision=BookingRevision(date=True),
        reason="test",
    )
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
    apply_invalidation(
        session,
        InvalidationTrigger.BOOKING_REVISION,
        revision=BookingRevision(service=True),
        reason="test",
    )
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
    clear_booking_state(session, clear_time=True, reason="test")
    assert get_confirmation_state(session) is None
    booking = session.get("booking")
    assert booking is None or booking.get("booking_id") is None
    assert session["slots"].get("date") == "2026-07-06"
    assert "time" not in session["slots"]
    assert "resolved_datetime_range" not in session
    assert "resolved_datetime_range" not in session["facts"]
    assert "time" not in session["facts"]["slots"]


def test_clear_pending_confirmation_only_keeps_time():
    session = _pending_session()
    clear_booking_state(session, clear_time=False, reason="rebind")
    assert get_confirmation_state(session) is None
    booking = session.get("booking")
    assert booking is None or booking.get("booking_id") is None
    assert session["slots"].get("time") == "09:00"
    assert session.get("resolved_datetime_range")
