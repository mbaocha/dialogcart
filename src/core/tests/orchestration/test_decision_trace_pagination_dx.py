"""Decision trace validation for availability pagination developer experience."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from core.tests.orchestration.test_availability_pagination_flow import (
    _browse,
    _browse_luma_response,
    _luma_response,
    _page_index,
    _presented_starts,
    _run_turn,
    _setup_paginated_search,
    pagination_harness,
)
from core.tracing.binding import BIND_TIME_DECISION_ID
from core.tracing.browse import PAGINATION_HANDLE_ID
from core.tracing.decision_trace import finalize_turn_trace, reset_decision_trace_state, trace_to_dict
from core.tracing.planner import PLANNER_SELECT_ACTION_ID
from core.tracing.reason_codes import (
    BIND_EXACT_TIME_MATCH,
    BIND_TIME_MISMATCH,
    PAGINATION_HANDLED,
    PAGINATION_SHORT_CIRCUIT,
)
from core.tracing.schema_validation import validate_decision_trace
from core.tracing.spine import SPINE_EXECUTION_ID


@pytest.fixture(autouse=True)
def _reset_trace_state():
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


@pytest.fixture
def traced_pagination_harness(pagination_harness, monkeypatch):
    monkeypatch.setenv("DIALOGCART_TRACE_DECISIONS", "1")
    return pagination_harness


def _decision(trace: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    for record in trace.get("records") or []:
        if record.get("id") == node_id and record.get("kind") == "decision":
            return record
    return None


def _rejected_ids(trace: Dict[str, Any], node_id: str) -> List[str]:
    decision = _decision(trace, node_id)
    if not decision:
        return []
    return [
        str(candidate.get("id"))
        for candidate in decision.get("candidates") or []
        if isinstance(candidate, dict) and not candidate.get("matched")
    ]


def _trace_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    trace = result.get("decision_trace")
    if trace is None:
        trace = finalize_turn_trace()
    assert trace is not None, "expected decision trace for turn"
    payload = trace_to_dict(trace)
    validate_decision_trace(payload)
    return payload


def test_decision_trace_explains_show_more_pagination(traced_pagination_harness):
    user_id, session_store, availability_client, org_client = traced_pagination_harness
    session = _setup_paginated_search(user_id, availability_client, org_client, session_store)
    first_page = _presented_starts(session)
    searches_before = availability_client.get_service_availability.call_count

    result = _run_turn(
        text="show more",
        user_id=user_id,
        luma_response=_browse_luma_response("browse_next"),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    trace = _trace_from_result(result)

    pagination = _decision(trace, PAGINATION_HANDLE_ID)
    assert pagination is not None
    assert pagination.get("reason_code") == PAGINATION_HANDLED

    browse = _decision(trace, "decision.browse.resolve_direction")
    assert browse is not None
    assert browse.get("winner") == "next"
    assert browse.get("reason_code") == "BROWSE_OPERATION_DETECTED"

    execution = _decision(trace, SPINE_EXECUTION_ID)
    assert execution is not None
    assert execution.get("reason_code") == PAGINATION_SHORT_CIRCUIT
    assert execution.get("winner") == "skip"

    rejected = _rejected_ids(trace, PLANNER_SELECT_ACTION_ID)
    assert "SEARCH_AVAILABILITY" in rejected

    assert availability_client.get_service_availability.call_count == searches_before
    session = session_store.get_session(1, user_id)
    second_page = _presented_starts(session)
    assert second_page != first_page
    assert _page_index(session) == 1


def test_decision_trace_explains_exhausted_pagination(traced_pagination_harness):
    user_id, session_store, availability_client, org_client = traced_pagination_harness
    _setup_paginated_search(
        user_id,
        availability_client,
        org_client,
        session_store,
        slot_hours=tuple(range(9, 14)),
    )
    _browse(user_id, "show more", session_store, availability_client, org_client)
    searches_before = availability_client.get_service_availability.call_count

    result = _run_turn(
        text="show more",
        user_id=user_id,
        luma_response=_browse_luma_response("browse_next"),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    trace = _trace_from_result(result)

    handle = _decision(trace, PAGINATION_HANDLE_ID)
    assert handle is not None
    assert handle.get("reason_code") == PAGINATION_HANDLED

    outcome = result.get("outcome") or result.get("result") or {}
    pagination = (
        outcome.get("availability_pagination")
        or result.get("availability_pagination")
        or {}
    )
    assert pagination.get("exhausted") is True
    assert pagination.get("direction") == "next"

    execution = _decision(trace, SPINE_EXECUTION_ID)
    assert execution is not None
    assert execution.get("reason_code") == PAGINATION_SHORT_CIRCUIT

    assert availability_client.get_service_availability.call_count == searches_before


def test_decision_trace_explains_page_two_time_binding(traced_pagination_harness):
    user_id, session_store, availability_client, org_client = traced_pagination_harness
    _setup_paginated_search(user_id, availability_client, org_client, session_store)
    _browse(user_id, "show more", session_store, availability_client, org_client)

    result_9am = _run_turn(
        text="9am",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"service_id": "premium haircut"},
            slots={"service_id": "premium haircut", "time": "09:00"},
            missing_slots=[],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    trace_9am = _trace_from_result(result_9am)
    bind_9am = _decision(trace_9am, BIND_TIME_DECISION_ID)
    assert bind_9am is not None
    assert bind_9am.get("reason_code") == BIND_TIME_MISMATCH

    result_5pm = _run_turn(
        text="5pm",
        user_id=user_id,
        luma_response=_luma_response(
            facts={"service_id": "premium haircut"},
            slots={"service_id": "premium haircut", "time": "17:00"},
            missing_slots=[],
        ),
        session_store=session_store,
        availability_client=availability_client,
        org_client=org_client,
    )
    trace_5pm = _trace_from_result(result_5pm)
    bind_5pm = _decision(trace_5pm, BIND_TIME_DECISION_ID)
    assert bind_5pm is not None
    assert bind_5pm.get("reason_code") == BIND_EXACT_TIME_MATCH
    assert bind_5pm.get("winner") == "bound"
