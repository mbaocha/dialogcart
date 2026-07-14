"""E2E tests for decision trace spine, causal graph, and forensic availability nodes."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.api import message as message_api
from core.adapters.clients.catalog_client import CatalogClient
from core.adapters.clients.organization_client import OrganizationClient
from core.execution.clients.availability_client import AvailabilityClient
from core.adapters.nlu import LumaClient
from core.api.compat import handle_message as real_handle_message
from core.session.session_manager import clear_session
from core.planning.time_resolution import TIME_MATCH_EXACT
from core.tests.e2e.framework.fixtures import TARGET_DATE
from core.tests.e2e.framework.trace_helpers import (
    maybe_print_decision_trace,
    stash_decision_trace_from_body,
)
from core.tracing.availability import AVAILABILITY_REQUEST_ID, AVAILABILITY_RESPONSE_ID
from core.tracing.facts import FACTS_DERIVE_ALL_ID
from core.tracing.fingerprint import FINGERPRINT_TRUST_ID
from core.tracing.planner import PLANNER_SELECT_ACTION_ID, PLANNER_STATUS_ID
from core.tracing.schema_validation import validate_decision_trace
from core.tracing.spine import (
    SPINE_EXECUTION_ID,
    SPINE_PERSIST_SAVE_ID,
    SPINE_RELOAD_VERIFY_ID,
    SPINE_TURN_OUTCOME_ID,
)


def _trace_record(trace: dict, record_id: str) -> dict:
    for record in trace.get("records") or []:
        if record.get("id") == record_id:
            return record
    raise AssertionError(f"trace record {record_id!r} not found")


@pytest.fixture
def trace_user_id():
    user_id = "test-decision-trace"
    clear_session(user_id)
    yield user_id
    clear_session(user_id)


@pytest.fixture
def booking_message_mocks(monkeypatch):
    mock_luma = Mock(spec=LumaClient)
    mock_luma.resolve.return_value = {
        "success": True,
        "intent": {"name": "CREATE_APPOINTMENT", "confidence": 0.95},
        "needs_clarification": False,
        "facts": {"service_id": 1, "dates": ["2026-07-10"]},
        "slots": {},
        "missing_slots": ["time"],
        "issues": {"time": "missing"},
        "context": {},
    }

    mock_org = Mock(spec=OrganizationClient)
    mock_org.get_details.return_value = {
        "organization": {
            "id": 1,
            "businessCategoryId": 1,
            "domain": "service",
        }
    }

    mock_catalog = Mock(spec=CatalogClient)
    mock_catalog.get_services.return_value = {
        "catalog_last_updated_at": "2026-01-01T00:00:00Z",
        "business_category_id": 1,
        "services": [
            {
                "id": 1,
                "name": "Haircut",
                "canonical": "haircut",
                "is_active": True,
                "duration": 60,
            }
        ],
    }
    mock_catalog.get_reservation.return_value = {"room_types": [], "extras": []}

    mock_availability = Mock(spec=AvailabilityClient)
    mock_availability.get_service_availability.return_value = {
        "slots": [
            {
                "start": "2026-07-10T10:00:00Z",
                "end": "2026-07-10T10:30:00Z",
                "available": True,
            }
        ]
    }
    monkeypatch.setattr(message_api, "_availability_client", mock_availability)

    return {
        "luma_client": mock_luma,
        "organization_client": mock_org,
        "catalog_client": mock_catalog,
    }


@pytest.fixture
def handle_message_with_booking_mocks(booking_message_mocks, monkeypatch):
    def _handle_message(**kwargs):
        merged = {**booking_message_mocks, **kwargs}
        return real_handle_message(**merged)

    monkeypatch.setattr(message_api, "handle_message", _handle_message)
    return _handle_message


def test_decision_trace_spine_records(
    trace_user_id,
    api_client,
    handle_message_with_booking_mocks,
    monkeypatch,
):
    monkeypatch.setenv("DIALOGCART_TRACE_DECISIONS", "1")

    response = api_client.post(
        "/api/message",
        params={"trace": "forensic"},
        json={
            "user_id": trace_user_id,
            "text": "book haircut",
            "organization_id": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    stash_decision_trace_from_body(body)
    maybe_print_decision_trace(body)
    assert body.get("decision_trace") is not None

    trace = body["decision_trace"]
    validate_decision_trace(trace)

    record_ids = {record["id"] for record in trace["records"]}
    assert SPINE_EXECUTION_ID in record_ids
    assert SPINE_PERSIST_SAVE_ID in record_ids
    assert SPINE_RELOAD_VERIFY_ID in record_ids
    assert SPINE_TURN_OUTCOME_ID in record_ids
    assert trace["root_id"] == SPINE_TURN_OUTCOME_ID

    execution = next(r for r in trace["records"] if r["id"] == SPINE_EXECUTION_ID)
    assert execution["subsystem"] == "execution"
    assert execution["decision_type"] == "EXECUTE_PLAN_ACTION"

    turn_outcome = next(r for r in trace["records"] if r["id"] == SPINE_TURN_OUTCOME_ID)
    assert turn_outcome["subsystem"] == "api"
    assert turn_outcome["decision_type"] == "TURN_OUTCOME"
    assert turn_outcome["winner"]["intent"] == "CREATE_APPOINTMENT"


def test_decision_trace_absent_when_disabled(
    trace_user_id,
    api_client,
    handle_message_with_booking_mocks,
    monkeypatch,
):
    monkeypatch.delenv("DIALOGCART_TRACE_DECISIONS", raising=False)

    response = api_client.post(
        "/api/message",
        json={
            "user_id": trace_user_id,
            "text": "book haircut",
            "organization_id": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body.get("decision_trace") is None
    assert body.get("outcome") is not None


def test_decision_trace_causal_graph_from_session_to_outcome(
    trace_user_id,
    api_client,
    handle_message_with_booking_mocks,
    monkeypatch,
):
    monkeypatch.setenv("DIALOGCART_TRACE_DECISIONS", "1")

    response = api_client.post(
        "/api/message",
        params={"trace": "forensic"},
        json={
            "user_id": trace_user_id,
            "text": "book haircut",
            "organization_id": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    trace = body.get("decision_trace")
    assert trace is not None
    validate_decision_trace(trace)

    record_ids = {record["id"] for record in trace["records"]}
    expected_nodes = {
        FACTS_DERIVE_ALL_ID,
        FINGERPRINT_TRUST_ID,
        PLANNER_STATUS_ID,
        PLANNER_SELECT_ACTION_ID,
        SPINE_EXECUTION_ID,
        SPINE_PERSIST_SAVE_ID,
        SPINE_TURN_OUTCOME_ID,
    }
    missing = expected_nodes - record_ids
    assert not missing, f"Missing causal graph nodes: {sorted(missing)}"

    assert trace["root_id"] == SPINE_TURN_OUTCOME_ID
    assert trace["summary"]["why_chain"]

    depends_edges = [e for e in trace["edges"] if e["kind"] == "depends_on"]
    assert any(
        e["from"] == FINGERPRINT_TRUST_ID and e["to"] == PLANNER_SELECT_ACTION_ID
        for e in depends_edges
    ) or any(e["to"] == PLANNER_SELECT_ACTION_ID for e in depends_edges)

    mutations = [r for r in trace["records"] if r["kind"] == "mutation"]
    assert mutations, "Expected at least one mutation in causal graph"

    categories = {
        r.get("category")
        for r in trace["records"]
        if r.get("kind") == "decision" and r.get("category")
    }
    assert "routing" in categories or "inference" in categories


def test_forensic_trace_records_availability_and_time_resolution(
    traced_scripted_conversation,
    monkeypatch,
):
    conv, _, _ = traced_scripted_conversation
    monkeypatch.setenv("DIALOGCART_TRACE_DECISIONS", "1")

    conv.send("book haircut tomorrow by 9am")
    conv.send("premium", trace="forensic")

    assert conv.last_http.status_code == 200
    trace = conv.last_body.get("decision_trace")
    assert trace is not None
    validate_decision_trace(trace)

    avail_req = _trace_record(trace, AVAILABILITY_REQUEST_ID)
    assert avail_req["facts"]["time_constraint"] is None
    assert avail_req["facts"].get("service_id") is not None
    assert avail_req["facts"].get("date") == TARGET_DATE

    avail_resp = _trace_record(trace, AVAILABILITY_RESPONSE_ID)
    assert avail_resp["facts"].get("available_slot_count") is not None

    time_res = _trace_record(trace, "evidence.time_resolution")
    assert (time_res.get("facts") or {}).get("time_resolution", {}).get("outcome") == (
        TIME_MATCH_EXACT
    )
