"""
Multi-turn integration tests for availability pagination (PR4).

Uses mocked NLU and availability API — no live Luma required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest

from core.session.turn_persistence import project_and_persist_turn_result
from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient
from core.execution.clients.availability_client import AvailabilityClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message
from core.planning.temporal_proposal import try_bind_offered_time_selection
from core.workflows.availability.presentation import (
    build_availability_presentation,
    build_presented_availability_page,
)

FROZEN_TIME = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
SEARCH_DATE = "2026-07-03"
SERVICE = "premium haircut"


class _StatefulSessionStore:
    def __init__(self, state: Optional[Dict[str, Any]] = None):
        self._sessions = {}
        if state:
            self._sessions[(1, "u1")] = state

    def get_session(self, organization_id: int, user_id: str) -> Dict[str, Any]:
        return self._sessions.get((organization_id, user_id), {})

    def save_session(
        self, organization_id: int, user_id: str, state: Dict[str, Any]
    ) -> None:
        self._sessions[(organization_id, user_id)] = state


def _paginated_availability_client(
    slot_hours: tuple[int, ...] = tuple(range(9, 18)),
) -> Mock:
    mock_client = Mock(spec=AvailabilityClient)

    def get_service_availability(**kwargs):
        date = kwargs.get("date") or SEARCH_DATE
        if isinstance(date, str):
            date = date.split("T")[0]
        return {
            "slots": [
                {
                    "start": f"{date}T{hour:02d}:00:00Z",
                    "end": f"{date}T{hour:02d}:30:00Z",
                    "available": True,
                }
                for hour in slot_hours
            ]
        }

    mock_client.get_service_availability.side_effect = get_service_availability
    return mock_client


def _persist_session_from_result(
    result: Dict[str, Any],
    previous_session: Optional[Dict[str, Any]],
    user_id: str,
    session_store: _StatefulSessionStore,
) -> Dict[str, Any]:
    projected = result.get("_projected_session_state")
    if isinstance(projected, dict):
        return projected
    new_session = project_and_persist_turn_result(
        result=result,
        organization_id=1,
        user_id=user_id,
        previous_session_state=previous_session,
        working_session_state=result.get("_working_session") or previous_session,
        session_store=session_store,
    )
    return new_session or {}


def _mock_org_client() -> Mock:
    mock_org = Mock(spec=OrganizationClient)
    mock_org.get_details.return_value = {"organization": {"businessCategoryId": 1}}
    return mock_org


def _mock_catalog_client() -> Mock:
    mock_catalog = Mock(spec=CatalogClient)
    mock_catalog.get_services.return_value = {
        "catalog_last_updated_at": "2026-01-01T00:00:00Z",
        "services": [{"id": 18, "name": SERVICE, "is_active": True}],
    }
    mock_catalog.get_reservation.return_value = {"room_types": [], "extras": []}
    return mock_catalog


def _luma_response(**overrides: Any) -> Dict[str, Any]:
    base = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT"},
        "facts": {},
        "slots": {},
        "missing_slots": [],
        "needs_clarification": False,
    }
    base.update(overrides)
    return base


def _run_turn(
    *,
    text: str,
    user_id: str,
    luma_response: Dict[str, Any],
    session_store: _StatefulSessionStore,
    availability_client: Mock,
    org_client: Mock,
    catalog_client: Optional[Mock] = None,
) -> Dict[str, Any]:
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = luma_response
    return handle_message(
        text=text,
        user_id=user_id,
        luma_client=mock_luma,
        availability_client=availability_client,
        organization_client=org_client,
        catalog_client=catalog_client or _mock_catalog_client(),
        session_store=session_store,
        frozen_time=FROZEN_TIME,
        organization_id=1,
    )


def _presented_starts(session: Dict[str, Any]) -> List[str]:
    presented = session.get("presented_availability") or {}
    slots = presented.get("slots") or []
    starts = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        start = slot.get("starts_at") or slot.get("start")
        if start:
            starts.append(str(start))
    return starts


def _page_index(session: Dict[str, Any]) -> int:
    presentation = session.get("availability_presentation") or {}
    return int(presentation.get("page_index") or 0)


def _setup_paginated_search(
    user_id: str,
    availability_client: Mock,
    org_client: Mock,
    session_store: _StatefulSessionStore,
    *,
    slot_hours: tuple[int, ...] = tuple(range(9, 18)),
) -> Dict[str, Any]:
    """Resolve service and run initial SEARCH_AVAILABILITY."""
    if slot_hours != tuple(range(9, 18)):
        availability_client.get_service_availability.side_effect = (
            _paginated_availability_client(slot_hours).get_service_availability.side_effect
        )

    result1 = _run_turn(
        text="book haircut",
        user_id=user_id,
        luma_response=_luma_response(
            service_candidates=[{"text": SERVICE, "id": 18}],
            missing_slots=["service_id"],
            needs_clarification=True,
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result1.get("success") is True
    session = _persist_session_from_result(result1, None, user_id, session_store)

    result2 = _run_turn(
        text="premium",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"service_id": SERVICE},
            slots={"service_id": SERVICE},
            missing_slots=["date", "time"],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result2.get("success") is True
    plan2 = result2.get("plan") or result2.get("result", {})
    assert plan2.get("action") == "SEARCH_AVAILABILITY"
    session = _persist_session_from_result(result2, session, user_id, session_store)
    assert _presented_starts(session), "expected first availability page"
    assert _page_index(session) == 0
    return session


def _browse_luma_response(operation: str, **overrides: Any) -> Dict[str, Any]:
    return _luma_response(
        intent={"name": "AVAILABILITY"},
        operation=operation,
        facts={"service_id": SERVICE},
        slots={"service_id": SERVICE},
        missing_slots=["time"],
        **overrides,
    )


def _browse(
    user_id: str,
    text: str,
    session_store: _StatefulSessionStore,
    availability_client: Mock,
    org_client: Mock,
    *,
    operation: str = "browse_next",
) -> Dict[str, Any]:
    result = _run_turn(
        text=text,
        user_id=user_id,
        luma_response=_browse_luma_response(operation),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    pagination = result.get("availability_pagination") or (
        (result.get("outcome") or {}).get("availability_pagination")
    )
    assert pagination is not None, (
        f"expected browse turn to paginate, got plan action="
        f"{(result.get('plan') or {}).get('action')!r}"
    )
    return session_store.get_session(1, user_id)


@pytest.fixture
def pagination_harness():
    user_id = "pagination-flow-user"
    session_store = _StatefulSessionStore()
    availability_client = _paginated_availability_client()
    org_client = _mock_org_client()
    yield user_id, session_store, availability_client, org_client


def test_show_more_displays_different_page(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    first_page = _presented_starts(session)

    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    second_page = _presented_starts(session)

    assert second_page != first_page
    assert not set(first_page) & set(second_page)
    assert _page_index(session) == 1


def test_show_more_with_create_appointment_intent_no_operation(pagination_harness):
    """Live-Luma shape: CREATE_APPOINTMENT NLU, no operation, text-only browse."""
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    first_page = _presented_starts(session)

    result = _run_turn(
        text="show more",
        user_id=user_id,
        luma_response=_luma_response(
            intent={"name": "CREATE_APPOINTMENT"},
            facts={"service_id": SERVICE},
            slots={"service_id": SERVICE},
            missing_slots=["time"],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    plan = result.get("plan") or result.get("outcome", {}).get("plan") or {}
    assert plan.get("action") is None
    pagination = result.get("availability_pagination") or (
        (result.get("outcome") or {}).get("availability_pagination")
    )
    assert pagination is not None
    assert pagination.get("page_index") == 1

    session = session_store.get_session(1, user_id)
    second_page = _presented_starts(session)
    assert second_page != first_page
    assert _page_index(session) == 1


def test_show_more_never_searches_again(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    searches_after_setup = availability_client.get_service_availability.call_count
    assert searches_after_setup >= 1

    result = _run_turn(
        text="show more",
        user_id=user_id,
        luma_response=_browse_luma_response("browse_next"),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    plan = result.get("plan") or result.get("outcome", {}).get("plan") or {}
    outcome = result.get("outcome") or result.get("result") or {}
    assert plan.get("action") != "SEARCH_AVAILABILITY"
    assert outcome.get("action") != "SEARCH_AVAILABILITY"
    assert plan.get("action") is None or outcome.get("action") is None
    assert availability_client.get_service_availability.call_count == searches_after_setup


def test_browse_persists_page_index_through_message_session_build(pagination_harness):
    """API-path: handle_message browse + build_session_state_from_outcome (like message.py)."""
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    full_cached_count = len(session["last_execution_result"]["slots"])
    first_page_starts = set(_presented_starts(session))
    searches_after_setup = availability_client.get_service_availability.call_count

    previous_before_browse = dict(session_store.get_session(1, user_id))
    result = _run_turn(
        text="show me additional times",
        user_id=user_id,
        luma_response=_browse_luma_response("browse_next"),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    plan = result.get("plan") or result.get("outcome", {}).get("plan") or {}
    outcome = result.get("outcome") or result.get("result") or {}
    assert plan.get("action") != "SEARCH_AVAILABILITY"
    assert outcome.get("action") != "SEARCH_AVAILABILITY"
    assert availability_client.get_service_availability.call_count == searches_after_setup

    session = _persist_session_from_result(
        result,
        previous_before_browse,
        user_id,
        session_store,
    )
    assert _page_index(session) == 1
    page1_starts = set(_presented_starts(session))
    assert page1_starts != first_page_starts
    assert not page1_starts & first_page_starts
    assert len(session["last_execution_result"]["slots"]) == full_cached_count
    assert len(session["presented_availability"]["slots"]) == 3
    assert session["presented_availability"]["slots"][0]["starts_at"].endswith(
        "T15:00:00Z"
    )


def test_no_more_pages_explicit_response_not_repeat():
    user_id = "pagination-flow-no-more"
    session_store = _StatefulSessionStore()
    seven_hours = tuple(range(9, 16))
    availability_client = _paginated_availability_client(seven_hours)
    org_client = _mock_org_client()
    session = _setup_paginated_search(
        user_id,
        availability_client,
        org_client,
        session_store,
        slot_hours=seven_hours,
    )
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    last_page = list(_presented_starts(session))
    assert _page_index(session) == 1

    result = _run_turn(
        text="show more",
        user_id=user_id,
        luma_response=_browse_luma_response("browse_next"),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    pagination = result.get("availability_pagination") or (
        (result.get("outcome") or {}).get("availability_pagination")
    )
    assert pagination is not None
    assert pagination.get("exhausted") is True
    session_after = session_store.get_session(1, user_id)
    assert _presented_starts(session_after) == last_page
    assert _page_index(session_after) == 1


def test_previous_page_returns_earlier_page(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    first_page = _presented_starts(session)

    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1

    session = _browse(
        user_id,
        "earlier times",
        session_store,
        availability_client,
        org_client,
        operation="browse_previous",
    )
    assert _page_index(session) == 0
    assert _presented_starts(session) == first_page


def test_service_change_resets_pagination(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1

    result = _run_turn(
        text="flexi haircut instead",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"service_id": "flexi haircut + prunning"},
            slots={"service_id": "flexi haircut + prunning"},
            missing_slots=["time"],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    plan = result.get("plan") or {}
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    session_after = _persist_session_from_result(
        result, session, user_id, session_store
    )
    assert _page_index(session_after) == 0


def test_date_change_resets_pagination(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1

    result = _run_turn(
        text="do you have a slot for July 6",
        user_id=user_id,
        luma_response=_luma_response(
            intent={"name": "AVAILABILITY"},
            facts={"service_id": SERVICE},
            slots={},
            temporal={
                "start_date": "2026-07-06",
                "mode": "single_day",
                "confidence": 1.0,
            },
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    plan = result.get("plan") or {}
    assert plan.get("action") == "SEARCH_AVAILABILITY"
    session_after = _persist_session_from_result(
        result, session, user_id, session_store
    )
    assert _page_index(session_after) == 0


def test_time_selection_binds_only_from_current_page(pagination_harness):
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1
    page_two_starts = set(_presented_starts(session))
    assert any("T09:00" in s for s in _presented_starts(session)) is False

    assert (
        try_bind_offered_time_selection(
            {"service_id": SERVICE},
            session,
            time_proposal={"mode": "exact", "value": "9am"},
        )
        is None
    ), "9am is on page 0 and must not bind while page 1 is presented"

    bind_result = try_bind_offered_time_selection(
        {"service_id": SERVICE},
        session,
        time_proposal={"mode": "exact", "value": "5pm"},
    )
    assert bind_result is not None
    assert bind_result["slots"]["time"] == "17:00"
    bound_start = bind_result["resolved_datetime_range"]["start"]
    assert bound_start in page_two_starts or any(
        bound_start.startswith(s.split("T")[0]) for s in page_two_starts
    )


def test_time_on_page_two_full_turn_no_search(pagination_harness):
    """Selecting a page-2 time binds and confirms without a new availability search."""
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1
    full_cache_count = len(session["last_execution_result"]["slots"])
    searches_before = availability_client.get_service_availability.call_count

    result = _run_turn(
        text="5pm",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"time": "17:00", "service_id": SERVICE},
            slots={"service_id": SERVICE, "time": "17:00"},
            time_proposal={"mode": "exact", "value": "5pm"},
            missing_slots=[],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    assert availability_client.get_service_availability.call_count == searches_before
    outcome = result.get("outcome") or result.get("result") or {}
    assert outcome.get("status") == "AWAITING_CONFIRMATION"
    plan = outcome.get("plan") or {}
    assert plan.get("action") is None
    session_after = _persist_session_from_result(result, session, user_id, session_store)
    assert len(session_after["last_execution_result"]["slots"]) == full_cache_count
    bound = session_after.get("resolved_datetime_range") or {}
    assert bound.get("start") == "2026-07-03T17:00:00Z"


def test_time_on_page_two_with_time_proposal_rejects_page_one_slot(pagination_harness):
    """Post-bind fallback must not match page-1 times while page 2 is presented."""
    user_id, session_store, availability_client, org_client = pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    session = _browse(
        user_id,
        "show more",
        session_store,
        availability_client,
        org_client,
    )
    assert _page_index(session) == 1
    searches_before = availability_client.get_service_availability.call_count

    result = _run_turn(
        text="9am",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"time": "09:00", "service_id": SERVICE},
            slots={"service_id": SERVICE, "time": "09:00"},
            time_proposal={"mode": "exact", "value": "9am"},
            missing_slots=[],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    assert result.get("success") is True
    assert availability_client.get_service_availability.call_count == searches_before
    outcome = result.get("outcome") or result.get("result") or {}
    assert outcome.get("status") != "AWAITING_CONFIRMATION"
    assert outcome.get("status") == "NEEDS_CLARIFICATION"
    session_after = _persist_session_from_result(result, session, user_id, session_store)
    assert session_after.get("resolved_datetime_range") is None
    assert "time" not in (session_after.get("slots") or {})


def test_bind_after_pagination_page_two_rejects_page_one_slot():
    """Unit-level bind guard: page-2 presentation must reject page-1-only times."""
    raw = [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00Z",
        }
        for h in range(9, 18)
    ]
    session = {
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-09",
            "slots": raw,
        },
        "presented_availability": build_presented_availability_page(
            raw, page_index=1, page_size=6, search_date="2026-07-09"
        ),
        "availability_presentation": build_availability_presentation(
            raw, page_index=1, page_size=6
        ),
    }
    assert try_bind_offered_time_selection(
        {"service_id": SERVICE},
        session,
        time_proposal={"mode": "exact", "value": "10am"},
    ) is None
    result = try_bind_offered_time_selection(
        {"service_id": SERVICE},
        session,
        time_proposal={"mode": "exact", "value": "3pm"},
    )
    assert result is not None
    assert result["slots"]["time"] == "15:00"
