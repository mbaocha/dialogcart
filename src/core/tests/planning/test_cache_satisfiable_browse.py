"""Browse suppresses SEARCH only for page movement — never for absolute dates.

Date / next-day requests must select SEARCH_AVAILABILITY.
"""

from __future__ import annotations

from unittest.mock import Mock

from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.execution.clients.availability_client import AvailabilityClient
from core.workflows.availability.browse import cache_satisfiable_browse_request
from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)
from core.workflows.availability.presentation import build_presented_availability


PREMIUM = "premium haircut"
JULY_23 = "2026-07-23"
JULY_24 = "2026-07-24"
JULY_25 = "2026-07-25"


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


def _slots_for_days(*days: str) -> list:
    slots = []
    for day in days:
        slots.extend(
            [
                {
                    "starts_at": f"{day}T09:00:00Z",
                    "ends_at": f"{day}T09:30:00Z",
                },
                {
                    "starts_at": f"{day}T10:00:00Z",
                    "ends_at": f"{day}T10:30:00Z",
                },
            ]
        )
    return slots


def _cache_session(*, days: tuple[str, ...], presented_day: str) -> dict:
    slots = _slots_for_days(*days)
    presented = build_presented_availability(
        slots, search_date=presented_day, max_times=6
    )
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "confirmation_state": None,
        "slots": {"service_id": PREMIUM, "date": presented_day},
        "date_proposal": {"mode": "single_day", "start": presented_day},
        "presented_availability": presented,
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": presented_day,
            "slots": slots,
        },
    }
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": presented_day},
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


def _plan_action(result: dict):
    outcome = result.get("outcome") or result.get("result") or {}
    nested = outcome.get("plan") if isinstance(outcome.get("plan"), dict) else {}
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    src = plan if plan else nested
    if "action" in src:
        return src.get("action")
    return outcome.get("action")


def test_cache_satisfiable_rejects_absolute_date_even_when_in_cache():
    """Absolute date in cache is SEARCH ownership — not cache-satisfiable browse."""
    session = _cache_session(
        days=(JULY_24, JULY_25), presented_day=JULY_24
    )
    merged = {
        "_source_text": "July 25",
        "_current_turn_has_date": True,
        "_current_turn_date": JULY_25,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "date_proposal": {"mode": "single_day", "start": JULY_25},
    }
    assert cache_satisfiable_browse_request(merged, session) is None


def test_cache_satisfiable_rejects_next_day_phrase():
    """\"next day\" is temporal SEARCH semantics — never browse."""
    session = _cache_session(days=(JULY_23, JULY_24), presented_day=JULY_23)
    merged = {
        "_source_text": "next day",
        "_current_turn_has_date": False,
        "intent": {"name": "CREATE_APPOINTMENT"},
    }
    assert cache_satisfiable_browse_request(merged, session) is None


def test_cache_satisfiable_allows_structured_browse_next():
    """Structured browse_next with trusted cache remains cache-satisfiable."""
    session = _cache_session(days=(JULY_24,), presented_day=JULY_24)
    merged = {
        "_source_text": "show more",
        "_current_turn_has_date": False,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "operation": "browse_next",
    }
    browse = cache_satisfiable_browse_request(merged, session)
    assert browse is not None
    assert browse.get("direction") == "next"


def test_absolute_date_selects_search_availability():
    """July 25 after a July 24 cache must SEARCH — never browse/project."""
    user_id = "absolute_date_searches"
    session = _cache_session(days=(JULY_24, JULY_25), presented_day=JULY_24)
    store = _StatefulSessionStore({user_id: session})
    prior_fp = session["availability_fingerprint"]

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "facts": {
            "service_id": PREMIUM,
            "slots": {"service_id": PREMIUM},
            "dates": [JULY_25],
        },
        "slots": {"service_id": PREMIUM},
        "temporal": {
            "start_date": JULY_25,
            "mode": "single_day",
            "confidence": 1.0,
        },
        "missing_slots": ["time"],
        "needs_clarification": False,
        "turn": {"understanding": "UNDERSTOOD"},
    }

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": f"{JULY_25}T09:00:00Z",
                "end": f"{JULY_25}T09:30:00Z",
                "available": True,
            }
        ]
    }

    result = handle_message(
        text="July 25",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    assert _plan_action(result) == "SEARCH_AVAILABILITY"
    mock_availability.get_service_availability.assert_called()
    saved = store.get_session(1, user_id) or {}
    assert saved.get("availability_fingerprint") != prior_fp


def test_structured_browse_next_does_not_select_search():
    """browse_next with trusted cache paginates — no commerce availability call."""
    user_id = "structured_browse_no_search"
    # Enough same-day slots to allow a second page.
    slots = [
        {
            "starts_at": f"{JULY_24}T{h:02d}:00:00Z",
            "ends_at": f"{JULY_24}T{h:02d}:30:00Z",
        }
        for h in range(9, 18)
    ]
    presented = build_presented_availability(
        slots, search_date=JULY_24, max_times=6
    )
    session = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "READY",
        "confirmation_state": None,
        "slots": {"service_id": PREMIUM, "date": JULY_24},
        "date_proposal": {"mode": "single_day", "start": JULY_24},
        "presented_availability": presented,
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": JULY_24,
            "slots": slots,
        },
    }
    fp_slots = build_availability_fingerprint_slots(
        {"service_id": PREMIUM, "date": JULY_24},
        intent_name="CREATE_APPOINTMENT",
        organization_id=1,
        luma_response={},
        session_state=session,
        date_proposal=session["date_proposal"],
    )
    session["availability_fingerprint"] = compute_availability_fingerprint(
        fp_slots, intent_name="CREATE_APPOINTMENT"
    )
    prior_fp = session["availability_fingerprint"]
    store = _StatefulSessionStore({user_id: session})

    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "AVAILABILITY"},
        "operation": "browse_next",
        "facts": {"service_id": PREMIUM, "slots": {"service_id": PREMIUM}},
        "slots": {"service_id": PREMIUM},
        "missing_slots": ["time"],
        "needs_clarification": False,
        "turn": {"understanding": "UNDERSTOOD"},
    }
    mock_availability = Mock(spec=AvailabilityClient)

    result = handle_message(
        text="next",
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=mock_availability,
        session_store=store,
        organization_id=1,
    )

    assert result.get("success") is True, result
    assert _plan_action(result) is None
    mock_availability.get_service_availability.assert_not_called()
    saved = store.get_session(1, user_id) or {}
    assert saved.get("availability_fingerprint") == prior_fp
    presented_after = saved.get("presented_availability") or {}
    assert presented_after.get("search_date") == JULY_24
    assert (presented_after.get("_cursor") or {}).get("page_index") == 1
