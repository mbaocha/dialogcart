"""REJECT_ACTION while awaiting booking confirmation clears pending and stays open-ended."""

from unittest.mock import Mock

from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.rendering.booking_confirmation_renderer import (
    render_booking_confirmation_rejected,
)
from core.session.session_projector import SessionProjectorV2


class _StatefulSessionStore:
    def __init__(self, initial=None):
        self._sessions = {}
        if initial:
            self._sessions.update({(1, user_id): state for user_id, state in initial.items()})

    def get_session(self, organization_id, user_id):
        return self._sessions.get((organization_id, user_id))

    def save_session(self, organization_id, user_id, session_state):
        self._sessions[(organization_id, user_id)] = session_state


def test_render_booking_confirmation_rejected_is_open_ended():
    text = render_booking_confirmation_rejected()
    assert "won't book" in text.lower()
    assert "change" in text.lower()


def _reject_session(*, status: str, pending: bool):
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": status,
        "slots": {
            "service_id": "flexi haircut + pruning",
            "date": "2026-07-06",
            "time": "11:15",
        },
        "resolved_datetime_range": {
            "start": "2026-07-06T11:15:00Z",
            "end": "2026-07-06T11:45:00Z",
        },
        "presented_availability": {
            "search_date": "2026-07-06",
            "slots": [
                {
                    "starts_at": "2026-07-06T11:15:00Z",
                    "ends_at": "2026-07-06T11:45:00Z",
                }
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-06",
            "slots": [
                {
                    "starts_at": "2026-07-06T11:15:00Z",
                    "ends_at": "2026-07-06T11:45:00Z",
                }
            ],
        },
    }
    if pending:
        session["confirmation_state"] = "pending"
    return session


def _run_reject_and_persist(user_id: str, session: dict):
    session_store = _StatefulSessionStore({user_id: session})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "REJECT_ACTION"},
        "facts": {},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="no",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    assert "won't book" in (result.get("text") or "").lower()
    assert "change" in (result.get("text") or "").lower()
    assert "go ahead" not in (result.get("text") or "").lower()

    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "NEEDS_CLARIFICATION"
    assert outcome.get("intent_name") == "CREATE_APPOINTMENT"
    assert outcome.get("slots", {}).get("service_id") == "flexi haircut + pruning"
    assert outcome.get("slots", {}).get("date") == "2026-07-06"
    assert "time" not in (outcome.get("slots") or {})

    # Persist the way the HTTP layer does.
    previous_session = session_store.get_session(1, user_id)
    new_session = SessionProjectorV2().project(
        outcome=outcome,
        outcome_status=outcome.get("status"),
        organization_id=1,
        merged_luma_response=result.get("_merged_luma_response"),
        previous_session_state=previous_session,
        user_id=user_id,
        working_session_state=result.get("_working_session") or previous_session,
    )
    assert new_session is not None
    session_store.save_session(1, user_id, new_session)

    session = session_store.get_session(1, user_id)
    assert session.get("confirmation_state") is None
    assert session.get("booking") == {}
    assert session.get("slots", {}).get("service_id") == "flexi haircut + pruning"
    assert session.get("slots", {}).get("date") == "2026-07-06"
    assert "time" not in (session.get("slots") or {})
    assert session.get("presented_availability")
    assert session.get("status") == "NEEDS_CLARIFICATION"
    return result


def test_reject_action_clears_confirmation_and_asks_what_to_change():
    _run_reject_and_persist(
        "test_reject_confirmation",
        _reject_session(status="AWAITING_CONFIRMATION", pending=True),
    )


def test_reject_action_when_resolution_rewrites_to_create_appointment():
    """Production path: REJECT_ACTION is rewritten to CREATE_APPOINTMENT, session may be NEEDS_CLARIFICATION."""
    _run_reject_and_persist(
        "test_reject_rewritten",
        _reject_session(status="NEEDS_CLARIFICATION", pending=False),
    )


def test_reject_with_new_time_rebinds_and_reconfirms():
    """'no. switch to 11am' clears prior selection and confirms the new presented time."""
    user_id = "test_reject_with_revision"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["presented_availability"] = {
        "search_date": "2026-07-06",
        "slots": [
            {
                "starts_at": "2026-07-06T10:00:00Z",
                "ends_at": "2026-07-06T10:30:00Z",
            },
            {
                "starts_at": "2026-07-06T11:00:00Z",
                "ends_at": "2026-07-06T11:30:00Z",
            },
        ],
    }
    session["slots"]["time"] = "10:00"
    session["resolved_datetime_range"] = {
        "start": "2026-07-06T10:00:00Z",
        "end": "2026-07-06T10:30:00Z",
    }
    session_store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "REJECT_ACTION"},
        "facts": {"times": ["11:00 AM"]},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="no. switch to 11am",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    text = result.get("text") or ""
    text_lower = text.lower()
    assert "changed it to" in text_lower
    assert "11:00" in text or "11:00 am" in text_lower
    assert "go ahead" in text_lower
    assert text_lower.index("changed") < text_lower.index("go ahead")

    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "AWAITING_CONFIRMATION"
    assert outcome.get("slots", {}).get("time") == "11:00"
    assert outcome.get("slots", {}).get("date") == "2026-07-06"


def test_pending_time_revision_without_reject_rebinds():
    """While pending, a new exact time alone revises the selection."""
    user_id = "test_pending_time_revision"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["presented_availability"] = {
        "search_date": "2026-07-06",
        "slots": [
            {
                "starts_at": "2026-07-06T10:00:00Z",
                "ends_at": "2026-07-06T10:30:00Z",
            },
            {
                "starts_at": "2026-07-06T11:00:00Z",
                "ends_at": "2026-07-06T11:30:00Z",
            },
        ],
    }
    session["slots"]["time"] = "10:00"
    session_store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"times": ["11am"]},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="11am",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "AWAITING_CONFIRMATION"
    assert outcome.get("slots", {}).get("time") == "11:00"
    text = (result.get("text") or "").lower()
    assert "changed it to" in text
    assert "11:00" in text or "11:00 am" in text


def test_correction_time_revision_when_session_needs_clarification():
    """Production path: CORRECTION + times must not be treated as informational no-op."""
    user_id = "test_correction_needs_clarification"
    session = _reject_session(status="NEEDS_CLARIFICATION", pending=True)
    session["presented_availability"] = {
        "search_date": "2026-07-06",
        "slots": [
            {
                "starts_at": "2026-07-06T09:00:00Z",
                "ends_at": "2026-07-06T09:30:00Z",
            },
            {
                "starts_at": "2026-07-06T11:00:00Z",
                "ends_at": "2026-07-06T11:30:00Z",
            },
        ],
    }
    session["slots"]["time"] = "09:00"
    session["resolved_datetime_range"] = {
        "start": "2026-07-06T09:00:00Z",
        "end": "2026-07-06T09:30:00Z",
    }
    session_store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CORRECTION"},
        "facts": {
            "times": ["11:00"],
            # Same service as session — time-only revision must not look like service change.
            "service_id": "flexi haircut + pruning",
        },
        "time_constraint": {
            "mode": "exact",
            "start": "11:00",
            "end": "11:00",
            "label": None,
        },
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="no. switch to 11am",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    outcome = result.get("outcome") or {}
    assert outcome.get("status") == "AWAITING_CONFIRMATION"
    assert outcome.get("slots", {}).get("time") == "11:00"
    assert outcome.get("slots", {}).get("date") == "2026-07-06"
    text = (result.get("text") or "").lower()
    assert "11:00" in text or "11:00 am" in text
    assert "9:00" not in text and "09:00" not in text


def test_service_revision_invalidates_bound_slot_and_searches():
    """Changing service while pending must not re-confirm the old time."""
    from core.execution.clients.availability_client import (
        AvailabilityClient,
    )

    user_id = "test_service_revision"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["slots"] = {
        "service_id": "premium haircut",
        "date": "2026-07-06",
        "time": "11:00",
    }
    session["presented_availability"] = {
        "search_date": "2026-07-06",
        "slots": [
            {
                "starts_at": "2026-07-06T11:00:00Z",
                "ends_at": "2026-07-06T11:30:00Z",
            }
        ],
    }
    session["availability_fingerprint"] = "old-fp"
    session_store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {"service_id": "flexi haircut + pruning"},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": "2026-07-06T09:00:00Z",
                "end": "2026-07-06T09:45:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="rather book me for flexi haircut",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    plan = result.get("plan") or result.get("outcome") or {}
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    slots = plan.get("slots") or (result.get("outcome") or {}).get("slots") or {}
    assert slots.get("service_id") == "flexi haircut + pruning"
    assert "time" not in slots
    assert plan.get("status") != "AWAITING_CONFIRMATION"
    text = (result.get("text") or "").lower()
    assert "go ahead" not in text
    assert "switched it to" in text
    assert "flexi" in text


def test_date_revision_invalidates_bound_slot_and_searches():
    """Changing date while pending must search the new day, not re-confirm old slot."""
    from core.execution.clients.availability_client import (
        AvailabilityClient,
    )

    user_id = "test_date_revision"
    session = _reject_session(status="AWAITING_CONFIRMATION", pending=True)
    session["slots"] = {
        "service_id": "premium haircut",
        "date": "2026-07-06",
        "time": "11:00",
    }
    session["presented_availability"] = {
        "search_date": "2026-07-06",
        "slots": [
            {
                "starts_at": "2026-07-06T11:00:00Z",
                "ends_at": "2026-07-06T11:30:00Z",
            }
        ],
    }
    session_store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {
            "dates": ["2026-07-11"],
            "service_id": "premium haircut",
        },
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": "2026-07-11T10:00:00Z",
                "end": "2026-07-11T10:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="do you have free slots on July 11?",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=session_store,
        organization_id=1,
    )
    assert result.get("success") is True
    plan = result.get("plan") or result.get("outcome") or {}
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    slots = plan.get("slots") or (result.get("outcome") or {}).get("slots") or {}
    assert slots.get("service_id") == "premium haircut"
    assert "time" not in slots
    text = (result.get("text") or "").lower()
    assert "go ahead" not in text
    assert "july 11" in text
    assert "check" in text or "instead" in text
    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert "2026-07-11" in str(call_kwargs.get("date") or call_kwargs)


def test_no_acknowledgement_on_bare_reject():
    result = _run_reject_and_persist(
        "test_no_ack_reject",
        _reject_session(status="AWAITING_CONFIRMATION", pending=True),
    )
    text = (result.get("text") or "").lower()
    assert "won't book" in text
    assert "changed it to" not in text
    assert "switched it to" not in text
