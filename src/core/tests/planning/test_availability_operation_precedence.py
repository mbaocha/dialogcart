"""AVAILABILITY turn_operation must not enter CONFIRM or keep stale date/time."""

from __future__ import annotations

from unittest.mock import Mock

from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.execution.clients.availability_client import AvailabilityClient
from core.planning.temporal_proposal import extract_nlu_proposals


FLEXI = "flexi haircut + pruning"
PREMIUM = "premium haircut"
JULY_20 = "2026-07-20"
JULY_21 = "2026-07-21"


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


def _pending_july20_session(*, service_id: str = PREMIUM) -> dict:
    return {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": {
            "service_id": service_id,
            "date": JULY_20,
            "time": "09:00",
        },
        "date_proposal": {"mode": "single_day", "start": JULY_20},
        "time_proposal": {"mode": "exact", "value": "09:00"},
        "resolved_datetime_range": {
            "start": f"{JULY_20}T09:00:00Z",
            "end": f"{JULY_20}T09:30:00Z",
        },
        "presented_availability": {
            "search_date": JULY_20,
            "slots": [
                {
                    "starts_at": f"{JULY_20}T09:00:00Z",
                    "ends_at": f"{JULY_20}T09:30:00Z",
                },
                {
                    "starts_at": f"{JULY_20}T10:00:00Z",
                    "ends_at": f"{JULY_20}T10:30:00Z",
                },
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": JULY_20,
            "slots": [
                {
                    "starts_at": f"{JULY_20}T09:00:00Z",
                    "ends_at": f"{JULY_20}T09:30:00Z",
                },
                {
                    "starts_at": f"{JULY_20}T10:00:00Z",
                    "ends_at": f"{JULY_20}T10:30:00Z",
                },
            ],
        },
        "availability_fingerprint": "stale-july20-fingerprint",
    }


def _plan_fields(result: dict) -> dict:
    outcome = result.get("outcome") or result.get("result") or {}
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    # Prefer top-level plan when present; otherwise nested outcome.plan.
    src = plan if plan else nested
    merged = result.get("_merged_luma_response") or {}
    return {
        "intent_name": (
            src.get("intent_name")
            or src.get("intent")
            or outcome.get("intent_name")
        ),
        "stage": (
            src.get("stage")
            if src.get("stage") is not None
            else outcome.get("stage")
        ),
        "action": src.get("action") if "action" in src else outcome.get("action"),
        "status": src.get("status") or outcome.get("status"),
        "slots": src.get("slots") or outcome.get("slots") or {},
        "turn_operation": (
            src.get("turn_operation")
            or outcome.get("turn_operation")
            or merged.get("_turn_operation")
        ),
        "date_proposal": (
            src.get("date_proposal")
            or outcome.get("date_proposal")
            or (outcome.get("facts") or {}).get("date_proposal")
            or merged.get("date_proposal")
        ),
        "time_proposal": (
            src.get("time_proposal")
            or outcome.get("time_proposal")
            or (outcome.get("facts") or {}).get("time_proposal")
            or merged.get("time_proposal")
        ),
        "awaiting": src.get("awaiting") or outcome.get("awaiting"),
        "confirmation_state": (
            merged.get("confirmation_state") or outcome.get("confirmation_state")
        ),
        "availability_reshow": src.get("availability_reshow")
        or nested.get("availability_reshow"),
        "merged": merged,
        "outcome": outcome,
    }


def test_extract_nlu_proposals_honors_top_level_date_proposal():
    """Current-turn top-level date_proposal must beat empty facts.dates."""
    luma = {
        "intent": {"name": "AVAILABILITY"},
        "facts": {"service_id": FLEXI},
        "date_proposal": {"mode": "single_day", "start": JULY_21},
    }
    proposals = extract_nlu_proposals(luma)
    assert proposals["date_proposal"] is not None
    assert proposals["date_proposal"]["start"] == JULY_21


def test_extract_nlu_proposals_honors_slots_date_when_facts_empty():
    luma = {
        "intent": {"name": "AVAILABILITY"},
        "facts": {},
        "slots": {"date": JULY_21},
    }
    proposals = extract_nlu_proposals(luma)
    assert proposals["date_proposal"]["start"] == JULY_21


def test_availability_date_change_during_pending_searches_not_confirm():
    """Pending July 20 09:00 + AVAILABILITY July 21 → SEARCH July 21, not CONFIRM."""
    user_id = "avail_pending_date_change"
    session = _pending_july20_session(service_id=PREMIUM)
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"service_id": FLEXI},
        "slots": {"service_id": FLEXI},
        "date_proposal": {"mode": "single_day", "start": JULY_21},
        "missing_slots": [],
        "needs_clarification": False,
    }

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_21}T10:00:00Z",
                "end": f"{JULY_21}T10:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="show availability for flexi on 21st july",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["intent_name"] == "CREATE_APPOINTMENT"
    assert fields["turn_operation"] == "AVAILABILITY"
    assert fields["stage"] == "AVAILABILITY"
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["stage"] != "CONFIRM"
    assert fields["action"] is not None

    slots = fields["slots"]
    assert "time" not in slots or slots.get("time") in (None, "")
    date_ok = (
        slots.get("date") == JULY_21
        or (fields["date_proposal"] or {}).get("start") == JULY_21
        or (fields["merged"].get("date_proposal") or {}).get("start") == JULY_21
    )
    assert date_ok, fields

    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs["date"] == JULY_21


def test_availability_same_criteria_reshow_not_confirm():
    """Same-criteria AVAILABILITY reshows cache and never enters CONFIRM."""
    user_id = "avail_same_criteria_reshow"
    session = _pending_july20_session(service_id=PREMIUM)
    # Trusted fingerprint matching current Premium + July 20 criteria.
    from core.workflows.availability.fingerprint import (
        build_availability_fingerprint_slots,
        compute_availability_fingerprint,
    )

    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": JULY_20},
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response={},
        session_state=session,
    )
    session["availability_fingerprint"] = compute_availability_fingerprint(
        fp_slots, intent_name="CREATE_APPOINTMENT"
    )
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"service_id": PREMIUM},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }

    mock_availability = Mock(spec=AvailabilityClient)

    result = handle_message(
        text="show availability",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["intent_name"] == "CREATE_APPOINTMENT"
    assert fields["turn_operation"] == "AVAILABILITY"
    assert fields["stage"] == "AVAILABILITY"
    assert fields["action"] is None
    assert fields["availability_reshow"] is True
    mock_availability.get_service_availability.assert_not_called()
    assert not fields["slots"].get("time")


def test_availability_preserves_current_turn_time_clears_old_selection():
    """AVAILABILITY with current-turn 10:00 drops 09:00 but keeps 10:00 evidence."""
    user_id = "avail_current_turn_time"
    session = _pending_july20_session(service_id=PREMIUM)
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {"service_id": FLEXI},
        "slots": {"service_id": FLEXI},
        "date_proposal": {"mode": "single_day", "start": JULY_21},
        "temporal": {
            "start_date": JULY_21,
            "start_time": "10:00",
            "end_time": "10:00",
            "mode": "single_day",
            "confidence": 1.0,
        },
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "missing_slots": [],
        "needs_clarification": False,
    }

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_21}T10:00:00Z",
                "end": f"{JULY_21}T10:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="is 10am available on July 21 for flexi",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["turn_operation"] == "AVAILABILITY"
    # Planning/search must target the new date; must not keep the prior 09:00 selection.
    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs["date"] == JULY_21

    slots = fields["slots"]
    assert slots.get("time") != "09:00"
    assert fields["stage"] == "AVAILABILITY"
    assert fields["stage"] != "CONFIRM"

    merged = fields["merged"]
    time_proposal = fields["time_proposal"] or merged.get("time_proposal")
    temporal = merged.get("temporal") or fields.get("temporal")
    preserved = False
    if isinstance(time_proposal, dict) and "10" in str(time_proposal.get("value") or ""):
        preserved = True
    if isinstance(temporal, dict) and "10" in str(temporal.get("start_time") or ""):
        preserved = True
    if slots.get("time") and "10" in str(slots.get("time")):
        preserved = True
    assert preserved, fields


def test_correction_exact_time_still_reenters_confirmation():
    """Non-availability CORRECTION with exact time still reaches confirm presentation."""
    user_id = "correction_exact_time_unchanged"
    session = _pending_july20_session(service_id=PREMIUM)
    from core.workflows.availability.fingerprint import (
        build_availability_fingerprint_slots,
        compute_availability_fingerprint,
    )

    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": JULY_20},
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response={},
        session_state=session,
    )
    session["availability_fingerprint"] = compute_availability_fingerprint(
        fp_slots, intent_name="CREATE_APPOINTMENT"
    )
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CORRECTION"},
        "facts": {},
        "temporal": {
            "start_time": "10:00",
            "end_time": "10:00",
            "mode": "none",
            "confidence": 1.0,
        },
        "time_proposal": {"mode": "exact", "value": "10:00"},
        "missing_slots": [],
        "needs_clarification": False,
    }

    result = handle_message(
        text="switch to 10am",
        user_id=user_id,
        luma_client=mock_luma,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["intent_name"] == "CREATE_APPOINTMENT"
    assert fields["turn_operation"] == "CORRECTION"
    assert fields["stage"] == "CONFIRM"
    assert fields["action"] is None
    assert fields["status"] == "AWAITING_CONFIRMATION" or fields["awaiting"] == (
        "USER_CONFIRMATION"
    )
    slots = fields["slots"]
    assert slots.get("time") and "10" in str(slots.get("time"))
