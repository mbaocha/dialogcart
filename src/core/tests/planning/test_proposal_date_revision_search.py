"""Proposal-based search date revisions must re-run SEARCH_AVAILABILITY.

After exploratory availability search, durable ``slots.date`` is intentionally
null while the active search date lives in ``date_proposal`` / fingerprint.
An explicit new date must revise and search — not reshow / browse the cache.
"""

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
JULY_28 = "2026-07-28"
JULY_30 = "2026-07-30"


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


def _exploratory_july28_session() -> dict:
    """Post-search session: proposal/fingerprint hold July 28; slots.date is null."""
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "confirmation_state": None,
        "slots": {
            "service_id": PREMIUM,
            # Intentionally absent — exploratory search does not durable-bind date.
        },
        "date_proposal": {"mode": "single_day", "start": JULY_28},
        "presented_availability": {
            "search_date": JULY_28,
            "slots": [
                {
                    "starts_at": f"{JULY_28}T09:00:00Z",
                    "ends_at": f"{JULY_28}T09:30:00Z",
                },
                {
                    "starts_at": f"{JULY_28}T10:00:00Z",
                    "ends_at": f"{JULY_28}T10:30:00Z",
                },
            ],
        },
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": JULY_28,
            "slots": [
                {
                    "starts_at": f"{JULY_28}T09:00:00Z",
                    "ends_at": f"{JULY_28}T09:30:00Z",
                },
                {
                    "starts_at": f"{JULY_28}T10:00:00Z",
                    "ends_at": f"{JULY_28}T10:30:00Z",
                },
            ],
        },
    }
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM},
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


def _plan_fields(result: dict) -> dict:
    outcome = result.get("outcome") or result.get("result") or {}
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    src = plan if plan else nested
    return {
        "action": src.get("action") if "action" in src else outcome.get("action"),
        "stage": src.get("stage") if src.get("stage") is not None else outcome.get("stage"),
        "availability_reshow": bool(
            src.get("availability_reshow") or outcome.get("availability_reshow")
        ),
        "date_proposal": src.get("date_proposal") or outcome.get("date_proposal"),
    }


def _july30_availability_script() -> dict:
    return {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {
            "service_id": PREMIUM,
            "slots": {"service_id": PREMIUM},
        },
        "slots": {"service_id": PREMIUM},
        "temporal": {
            "start_date": JULY_30,
            "mode": "single_day",
            "confidence": 1.0,
        },
        "missing_slots": ["time"],
        "needs_clarification": False,
    }


def test_explicit_new_date_after_proposal_search_selects_search_availability():
    """Case 1: July 28 proposal search → July 30 utterance → SEARCH_AVAILABILITY."""
    user_id = "proposal_date_revision_case1"
    session = _exploratory_july28_session()
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _july30_availability_script()

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_30}T11:00:00Z",
                "end": f"{JULY_30}T11:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="show availability for July 30",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["availability_reshow"] is False
    mock_availability.get_service_availability.assert_called()
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("date") == JULY_30


def test_explicit_new_date_updates_fingerprint_and_skips_browse_reshow():
    """Case 2: search executes for July 30; fingerprint updates; no cache reshow."""
    user_id = "proposal_date_revision_case2"
    session = _exploratory_july28_session()
    prior_fp = session["availability_fingerprint"]
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = _july30_availability_script()

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_30}T11:00:00Z",
                "end": f"{JULY_30}T11:30:00Z",
                "staff_id": 1,
            }
        ]
    }

    result = handle_message(
        text="show availability for July 30",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] == "SEARCH_AVAILABILITY"
    assert fields["availability_reshow"] is False
    assert mock_availability.get_service_availability.call_count == 1
    call_kwargs = mock_availability.get_service_availability.call_args.kwargs
    assert call_kwargs.get("date") == JULY_30

    saved = store.get_session(1, user_id) or {}
    new_fp = availability_fingerprint_from_session(saved)
    assert new_fp
    assert new_fp != prior_fp

    expected_fp = compute_availability_fingerprint(
        {
            "organization_id": 1,
            "service_id": PREMIUM,
            "date": JULY_30,
        },
        intent_name="CREATE_APPOINTMENT",
    )
    assert new_fp == expected_fp

    presented = presented_availability_from_session(saved) or {}
    assert presented.get("search_date") == JULY_30 or (
        (fields.get("date_proposal") or {}).get("start") == JULY_30
    )


def test_browse_next_after_proposal_search_does_not_search():
    """Case 3: show more / browse continues from cache when criteria unchanged."""
    user_id = "proposal_date_revision_case3"
    session = _exploratory_july28_session()
    prior_fp = session["availability_fingerprint"]
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "operation": "browse_next",
        "facts": {
            "operation": "browse_next",
            "service_id": PREMIUM,
            "slots": {"service_id": PREMIUM},
        },
        "slots": {"service_id": PREMIUM},
        "missing_slots": ["time"],
        "needs_clarification": False,
    }

    mock_availability = Mock(spec=AvailabilityClient)

    result = handle_message(
        text="show more",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    fields = _plan_fields(result)
    assert fields["action"] is None
    mock_availability.get_service_availability.assert_not_called()

    saved = store.get_session(1, user_id) or {}
    assert availability_fingerprint_from_session(saved) == prior_fp
