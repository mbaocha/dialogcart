"""Service revision during confirmation: Stage 08 invalidation propagation.

Regression: after Premium@09:00 pending confirmation, switching to Flexi must
invalidate availability trust, disable session time-proposal reuse, SEARCH for
Flexi, and must not rematch the old 09:00 into confirmation — even when Flexi
also has a 09:00 offer.
"""

from __future__ import annotations

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
DAY = "2026-07-24"


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


def _pending_premium_session() -> dict:
    slots = {
        "service_id": PREMIUM,
        "date": DAY,
        "time": "09:00",
    }
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "AWAITING_CONFIRMATION",
        "confirmation_state": "pending",
        "slots": slots,
        "date_proposal": {"mode": "single_day", "start": DAY},
        "time_proposal": {"mode": "exact", "value": "09:00"},
        "temporal": {
            "start_date": DAY,
            "mode": "single_day",
            "confidence": 1.0,
        },
        "resolved_datetime_range": {
            "start": f"{DAY}T09:00:00Z",
            "end": f"{DAY}T09:30:00Z",
        },
        "presented_availability": {
            "search_date": DAY,
            "slots": [
                {
                    "starts_at": f"{DAY}T09:00:00Z",
                    "ends_at": f"{DAY}T09:30:00Z",
                },
                {
                    "starts_at": f"{DAY}T10:00:00Z",
                    "ends_at": f"{DAY}T10:30:00Z",
                },
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": DAY,
            "slots": [
                {
                    "starts_at": f"{DAY}T09:00:00Z",
                    "ends_at": f"{DAY}T09:30:00Z",
                },
            ],
        },
    }
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": DAY},
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


def _flexi_correction_nlu(*, with_time: str | None = None) -> dict:
    facts: dict = {"service_id": FLEXI, "slots": {"service_id": FLEXI}}
    temporal = {
        "confidence": 0.95,
        "mode": "none",
        "start_date": None,
        "start_time": None,
        "end_date": None,
        "end_time": None,
        "start_date_expression": None,
        "end_date_expression": None,
        "start_time_expression": None,
        "end_time_expression": None,
        "expression": None,
    }
    payload = {
        "success": True,
        "intent": {"name": "CORRECTION", "confidence": 0.95},
        "facts": facts,
        "slots": {"service_id": FLEXI},
        "temporal": temporal,
        "missing_slots": [],
        "needs_clarification": False,
        "turn": {"understanding": "UNDERSTOOD"},
    }
    if with_time:
        facts["times"] = [with_time]
        temporal["mode"] = "single_day"
        temporal["start_time"] = with_time
        temporal["start_time_expression"] = with_time
        payload["time_proposal"] = {"mode": "exact", "value": with_time}
    return payload


def _plan_fields(result: dict) -> dict:
    outcome = result.get("outcome") or {}
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    src = plan if plan else nested
    ctx = (
        src.get("execution_proposal_context")
        or result.get("execution_proposal_context")
        or nested.get("execution_proposal_context")
        or {}
    )
    slots = src.get("slots") or outcome.get("slots") or {}
    return {
        "action": src.get("action") if "action" in src else outcome.get("action"),
        "status": src.get("status") if "status" in src else outcome.get("status"),
        "stage": src.get("stage") if "stage" in src else outcome.get("stage"),
        "confirmation_state": (
            src.get("confirmation_state")
            if "confirmation_state" in src
            else outcome.get("confirmation_state")
        ),
        "slots": slots if isinstance(slots, dict) else {},
        "execution_proposal_context": ctx if isinstance(ctx, dict) else {},
        "time_match_outcome": (
            src.get("time_match_outcome")
            or outcome.get("time_match_outcome")
            or (result.get("_merged_luma_response") or {}).get("time_match_outcome")
        ),
    }


def test_stage08_marks_availability_invalidated_and_disables_time_reuse():
    """Planner: service-only CORRECTION sets Stage 08 execution invalidation."""
    user_id = "svc_rev_stage08_ctx"
    store = _StatefulSessionStore({user_id: _pending_premium_session()})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_correction_nlu()
    mock_availability = Mock(spec=AvailabilityClient)
    # Include 09:00 so a rematch bug would surface.
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{DAY}T09:00:00Z",
                "end": f"{DAY}T09:30:00Z",
                "staff_id": 1,
            },
            {
                "start": f"{DAY}T11:00:00Z",
                "end": f"{DAY}T11:30:00Z",
                "staff_id": 1,
            },
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
    ctx = fields["execution_proposal_context"]

    assert ctx.get("availability_invalidated") is True
    assert ctx.get("session_time_proposal_reuse_allowed") is False
    assert ctx.get("current_turn_has_explicit_time") is False
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["status"] != "AWAITING_CONFIRMATION"
    assert not fields["slots"].get("time")
    assert fields.get("confirmation_state") not in ("pending", "confirmed")
    assert fields.get("time_match_outcome") != "TIME_MATCH_EXACT"

    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("service_id") == FLEXI


def test_post_search_does_not_rematch_stale_09_00_into_confirmation():
    """Post-SEARCH: Flexi also offering 09:00 must not resume confirmation."""
    user_id = "svc_rev_post_search_no_rematch"
    store = _StatefulSessionStore({user_id: _pending_premium_session()})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_correction_nlu()
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{DAY}T09:00:00Z",
                "end": f"{DAY}T09:30:00Z",
                "staff_id": 1,
            },
            {
                "start": f"{DAY}T10:00:00Z",
                "end": f"{DAY}T10:30:00Z",
                "staff_id": 1,
            },
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
    outcome = result.get("outcome") or {}

    assert fields["action"] in (None, "SEARCH_AVAILABILITY") or fields[
        "status"
    ] != "AWAITING_CONFIRMATION"
    assert fields["status"] != "AWAITING_CONFIRMATION"
    assert outcome.get("status") != "AWAITING_CONFIRMATION"
    assert outcome.get("awaiting") != "USER_CONFIRMATION"
    assert not (fields["slots"].get("time") or outcome.get("slots", {}).get("time"))
    assert fields.get("time_match_outcome") != "TIME_MATCH_EXACT"

    text = (result.get("text") or outcome.get("text") or "").lower()
    assert "go ahead" not in text
    assert "would you like" not in text or "confirm" not in text


def test_same_turn_explicit_time_may_exact_match_after_service_revision():
    """Same-turn 'flexi at 11am' may rematch and re-enter confirmation."""
    user_id = "svc_rev_same_turn_11am"
    store = _StatefulSessionStore({user_id: _pending_premium_session()})
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _flexi_correction_nlu(with_time="11:00")
    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{DAY}T09:00:00Z",
                "end": f"{DAY}T09:30:00Z",
                "staff_id": 1,
            },
            {
                "start": f"{DAY}T11:00:00Z",
                "end": f"{DAY}T11:30:00Z",
                "staff_id": 1,
            },
        ]
    }

    result = handle_message(
        text="switch to flexi haircut at 11am",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )
    assert result.get("success") is True, result
    fields = _plan_fields(result)
    ctx = fields["execution_proposal_context"]
    outcome = result.get("outcome") or {}

    assert ctx.get("availability_invalidated") is True
    assert ctx.get("current_turn_has_explicit_time") is True
    # May SEARCH then finalize to confirmation, or bind pre-search.
    status = fields["status"] or outcome.get("status")
    slots = fields["slots"] or outcome.get("slots") or {}
    if status == "AWAITING_CONFIRMATION":
        assert slots.get("service_id") == FLEXI
        assert str(slots.get("time", "")).startswith("11")
        assert fields.get("confirmation_state") == "pending" or outcome.get(
            "confirmation_state"
        ) == "pending"
    else:
        # Still searching / clarifying is acceptable if bind deferred.
        assert fields["action"] == "SEARCH_AVAILABILITY" or status in (
            "READY",
            "NEEDS_CLARIFICATION",
            "AWAITING_CONFIRMATION",
        )
        if slots.get("time"):
            assert str(slots.get("time")).startswith("11")
            assert not str(slots.get("time")).startswith("09")
