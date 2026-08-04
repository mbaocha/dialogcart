"""Service-only revision must preserve the active search date_proposal."""

from __future__ import annotations

from core.workflows.availability.presentation import (
    availability_cache_from_session,
    availability_fingerprint_from_session,
    presented_availability_from_session,
)

from unittest.mock import Mock

from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.execution.clients.availability_client import AvailabilityClient
from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)


PREMIUM = "premium haircut"
FLEXI = "flexi haircut + pruning"
JULY_22 = "2026-07-22"
JULY_23 = "2026-07-23"


class _StatefulSessionStore:
    def __init__(self, initial=None):
        self._sessions = {}
        if initial:
            self._sessions.update(
                {(1, user_id): state for user_id, state in initial.items()}
            )

    def get_session(self, organization_id, user_id):
        return self._sessions.get((organization_id, user_id))

    def save_session(self, organization_id, user_id, session_state):
        self._sessions[(organization_id, user_id)] = session_state


def _july22_premium_session(*, pending: bool = False, with_time: bool = False) -> dict:
    slots = {"service_id": PREMIUM}
    if with_time:
        slots["date"] = JULY_22
        slots["time"] = "09:00"
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION" if pending else "READY",
        "confirmation_state": "pending" if pending else None,
        "slots": slots,
        "date_proposal": {"mode": "single_day", "start": JULY_22},
        "temporal": {
            "start_date": JULY_22,
            "mode": "single_day",
            "confidence": 1.0,
        },
        "presented_availability": {
            "search_date": JULY_22,
            "slots": [
                {
                    "starts_at": f"{JULY_22}T09:00:00Z",
                    "ends_at": f"{JULY_22}T09:30:00Z",
                },
                {
                    "starts_at": f"{JULY_22}T10:00:00Z",
                    "ends_at": f"{JULY_22}T10:30:00Z",
                },
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": JULY_22,
            "slots": [
                {
                    "starts_at": f"{JULY_22}T09:00:00Z",
                    "ends_at": f"{JULY_22}T09:30:00Z",
                },
            ],
        },
    }
    if with_time:
        session["resolved_datetime_range"] = {
            "start": f"{JULY_22}T09:00:00Z",
            "end": f"{JULY_22}T09:30:00Z",
        }
        session["time_proposal"] = {"mode": "exact", "value": "09:00"}
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": JULY_22},
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response={},
        session_state=session,
        date_proposal=session["date_proposal"],
    )
    session["availability_fingerprint"] = compute_availability_fingerprint(
        fp_slots, intent_name="CREATE_APPOINTMENT"
    )
    return session


def _flexi_switch_script() -> dict:
    return {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {
            "service_id": FLEXI,
            "slots": {"service_id": FLEXI},
        },
        "slots": {"service_id": FLEXI},
        "missing_slots": [],
        "needs_clarification": False,
    }


def _plan_fields(result: dict) -> dict:
    outcome = result.get("outcome") or {}
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    src = plan if plan else nested
    proposal = (
        src.get("date_proposal")
        or outcome.get("date_proposal")
        or (src.get("facts") or {}).get("date_proposal")
        or (outcome.get("facts") or {}).get("date_proposal")
        or (result.get("_merged_luma_response") or {}).get("date_proposal")
    )
    slots = src.get("slots") or outcome.get("slots") or {}
    return {
        "action": src.get("action") if "action" in src else outcome.get("action"),
        "confirmation_state": (
            src.get("confirmation_state")
            if "confirmation_state" in src
            else outcome.get("confirmation_state")
        ),
        "slots": slots if isinstance(slots, dict) else {},
        "date_proposal": proposal,
    }


def test_service_switch_preserves_july22_date_proposal_and_searches():
    """Case 1: Premium + July 22 search → Flexi → SEARCH Flexi on July 22."""
    user_id = "service_date_preserve_case1"
    store = _StatefulSessionStore({user_id: _july22_premium_session()})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_switch_script()
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_22}T11:00:00Z",
                "end": f"{JULY_22}T11:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="switch to flexi haircut",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )
    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["slots"].get("service_id") == FLEXI
    assert "time" not in fields["slots"] or not fields["slots"].get("time")
    assert isinstance(fields["date_proposal"], dict)
    assert fields["date_proposal"].get("start") == JULY_22
    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("date") == JULY_22


def test_service_switch_from_pending_clears_time_keeps_july22():
    """Case 2: pending Premium 9am on July 22 → Flexi clears confirm/time, keeps date."""
    user_id = "service_date_preserve_case2"
    store = _StatefulSessionStore(
        {user_id: _july22_premium_session(pending=True, with_time=True)}
    )
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_switch_script()
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_22}T10:00:00Z",
                "end": f"{JULY_22}T10:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="switch to flexi haircut",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )
    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["slots"].get("service_id") == FLEXI
    assert not fields["slots"].get("time")
    assert fields.get("confirmation_state") not in ("pending", "confirmed")
    assert (fields["date_proposal"] or {}).get("start") == JULY_22
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("date") == JULY_22

    saved = store.get_session(1, user_id) or {}
    assert saved.get("confirmation_state") not in ("pending", "confirmed")
    saved_proposal = saved.get("date_proposal") or {}
    assert saved_proposal.get("start") == JULY_22 or (
        (presented_availability_from_session(saved) or {}).get("search_date") == JULY_22
    )


def test_service_switch_after_date_change_uses_latest_date():
    """Case 3: July 22 → July 23 search → Flexi uses July 23."""
    user_id = "service_date_preserve_case3"
    session = _july22_premium_session()
    session["date_proposal"] = {"mode": "single_day", "start": JULY_23}
    session["temporal"] = {
        "start_date": JULY_23,
        "mode": "single_day",
        "confidence": 1.0,
    }
    session["presented_availability"] = {
        "search_date": JULY_23,
        "slots": [
            {
                "starts_at": f"{JULY_23}T09:00:00Z",
                "ends_at": f"{JULY_23}T09:30:00Z",
            }
        ],
    }
    session["last_execution_result"] = {
        "type": "availability",
        "status": "success",
        "search_date": JULY_23,
        "slots": session["presented_availability"]["slots"],
    }
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": JULY_23},
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response={},
        session_state=session,
        date_proposal=session["date_proposal"],
    )
    session["availability_fingerprint"] = compute_availability_fingerprint(
        fp_slots, intent_name="CREATE_APPOINTMENT"
    )
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_switch_script()
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_23}T12:00:00Z",
                "end": f"{JULY_23}T12:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="switch to flexi haircut",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )
    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["slots"].get("service_id") == FLEXI
    assert (fields["date_proposal"] or {}).get("start") == JULY_23
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("date") == JULY_23
