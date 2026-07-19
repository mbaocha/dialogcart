"""Phase 2 planner decision trace tests."""

from __future__ import annotations

import copy

import pytest

from core.workflows.availability.fingerprint import (
    build_availability_fingerprint_slots,
    compute_availability_fingerprint,
)
from core.tests.harness.planning_compat import build_decision_plan
from core.tracing.decision_trace import (
    TRACE_ENV_VAR,
    TurnTrace,
    finalize_turn_trace,
    reset_decision_trace_state,
    trace_to_dict,
)
from core.tracing.planner import (
    PLANNER_CLARIFICATION_ID,
    PLANNER_CONFIRMATION_ID,
    PLANNER_EVIDENCE_BUSINESS_FACTS_ID,
    PLANNER_EVIDENCE_MISSING_SLOTS_ID,
    PLANNER_EXECUTION_ROUTE_ID,
    PLANNER_SELECT_ACTION_ID,
    PLANNER_SELECT_STAGE_ID,
    PLANNER_STATUS_ID,
    ROUTING_CATEGORY,
)
from core.tracing.reason_codes import (
    CLARIFICATION_REQUIRED,
    CONFIRMATION_REQUIRED,
    EXECUTION_ROUTE_BROWSE,
    EXECUTION_ROUTE_PLAN,
    STEP_SELECTED,
)
from core.tracing.schema_validation import validate_decision_trace


@pytest.fixture(autouse=True)
def _reset_trace(monkeypatch):
    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    reset_decision_trace_state()
    yield
    reset_decision_trace_state()


def _build_plan_with_trace(
    *,
    intent_name: str,
    luma_response: dict,
    session_state: dict | None = None,
    availability_resolved: bool = False,
) -> tuple[dict, dict]:
    TurnTrace.begin(user_id="planner-trace-test", text="test")
    plan = build_decision_plan(
        intent_name=intent_name,
        luma_response=luma_response,
        domain="service",
        availability_resolved=availability_resolved,
        session_state=session_state,
    )
    trace = trace_to_dict(finalize_turn_trace())
    validate_decision_trace(trace)
    return plan, trace


def _record(trace: dict, node_id: str) -> dict:
    return next(record for record in trace["records"] if record["id"] == node_id)


def _availability_cache_session(slots, *, intent_name="CREATE_APPOINTMENT", **extra):
    fp_slots = build_availability_fingerprint_slots(
        slots, intent_name=intent_name, organization_id=slots.get("organization_id")
    )
    search_date = fp_slots.get("date")
    return {
        **extra,
        "availability_fingerprint": compute_availability_fingerprint(
            fp_slots, intent_name=intent_name
        ),
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "slots": [
                {
                    "starts_at": f"{search_date or '2026-07-10'}T14:00:00Z",
                    "ends_at": f"{search_date or '2026-07-10'}T14:30:00Z",
                }
            ],
            "search_date": search_date,
        },
    }


def _build_create_appointment_luma(
    slots: dict,
    *,
    missing_slots: list[str] | None = None,
    needs_clarification: bool = False,
    confirmation_state: str | None = None,
    operation: str | None = None,
) -> dict:
    response = {
        "intent": {"name": "CREATE_APPOINTMENT"},
        "slots": slots,
        "_effective_collected_slots": slots,
        "missing_slots": missing_slots if missing_slots is not None else [],
        "needs_clarification": needs_clarification,
        "facts": {"service_id": slots.get("service_id")},
        "context": {},
    }
    if confirmation_state is not None:
        response["confirmation_state"] = confirmation_state
    if operation is not None:
        response["operation"] = operation
        response["facts"]["operation"] = operation
    return response


def test_search_availability_chosen_in_planner_trace():
    slots = {"service_id": "svc-haircut", "organization_id": "org-1"}
    luma_response = _build_create_appointment_luma(
        slots, missing_slots=["time", "date"]
    )

    plan, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        availability_resolved=False,
    )

    assert plan["action"] == "SEARCH_AVAILABILITY"
    assert plan["stage"] == "AVAILABILITY"

    action = _record(trace, PLANNER_SELECT_ACTION_ID)
    assert action["category"] == ROUTING_CATEGORY
    assert action["winner"] == "SEARCH_AVAILABILITY"
    assert action["reason_code"] == STEP_SELECTED
    assert len(action["candidates"]) >= 1
    matched = [c for c in action["candidates"] if c["matched"]]
    assert matched[0]["id"] == "SEARCH_AVAILABILITY"

    stage = _record(trace, PLANNER_SELECT_STAGE_ID)
    assert stage["winner"] == "AVAILABILITY"
    assert stage["category"] == ROUTING_CATEGORY

    route = _record(trace, PLANNER_EXECUTION_ROUTE_ID)
    assert route["winner"] == "plan_execution"
    assert route["reason_code"] == EXECUTION_ROUTE_PLAN


def test_bind_time_chosen_in_planner_trace():
    slots = {
        "service_id": "svc-haircut",
        "date": "2026-07-10",
        "time": "14:00",
        "organization_id": "org-1",
    }
    session = _availability_cache_session(
        slots,
        confirmation_state="confirmed",
    )
    luma_response = _build_create_appointment_luma(
        slots,
        missing_slots=[],
        confirmation_state="confirmed",
    )

    plan, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        session_state=session,
        availability_resolved=True,
    )

    assert plan["action"] == "CONFIRM_APPOINTMENT"
    assert plan["stage"] == "CONFIRM"

    action = _record(trace, PLANNER_SELECT_ACTION_ID)
    assert action["winner"] == "CONFIRM_APPOINTMENT"
    facts = _record(trace, PLANNER_EVIDENCE_BUSINESS_FACTS_ID)
    assert facts["facts"].get("time_selection_ready") is True

    route = _record(trace, PLANNER_EXECUTION_ROUTE_ID)
    assert route["inputs_evaluated"]["time_selection_ready"] is True


def test_clarification_chosen_in_planner_trace():
    slots = {"organization_id": "org-1"}
    luma_response = _build_create_appointment_luma(
        slots, missing_slots=["service_id", "time", "date"]
    )

    plan, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        availability_resolved=False,
    )

    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["action"] is None

    status = _record(trace, PLANNER_STATUS_ID)
    assert status["winner"] == "NEEDS_CLARIFICATION"
    assert status["reason_code"] == CLARIFICATION_REQUIRED
    assert status["category"] == ROUTING_CATEGORY
    assert status["candidates"]
    assert status["inputs_evaluated"]["missing_slots"]

    clarification = _record(trace, PLANNER_CLARIFICATION_ID)
    assert clarification["skipped"] is False
    assert clarification["winner"] == "clarify"
    assert clarification["reason_code"] == CLARIFICATION_REQUIRED

    missing_evidence = _record(trace, PLANNER_EVIDENCE_MISSING_SLOTS_ID)
    assert "service_id" in missing_evidence["facts"]["missing_slots"]


def test_browse_chosen_in_planner_trace():
    slots = {
        "service_id": "svc-haircut",
        "date": "2026-07-10",
        "time": "14:00",
        "organization_id": "org-1",
    }
    session = _availability_cache_session(
        slots,
        confirmation_state="pending",
    )
    luma_response = _build_create_appointment_luma(
        slots,
        missing_slots=[],
        confirmation_state="pending",
        operation="browse_next",
    )

    plan, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        session_state=session,
        availability_resolved=True,
    )

    route = _record(trace, PLANNER_EXECUTION_ROUTE_ID)
    assert route["winner"] == "browse_pagination"
    assert route["reason_code"] == EXECUTION_ROUTE_BROWSE
    assert route["category"] == ROUTING_CATEGORY
    assert route["inputs_evaluated"]["browse_operation"] == "browse_next"
    assert plan["status"] == "AWAITING_CONFIRMATION"


def test_confirmation_chosen_in_planner_trace():
    slots = {
        "service_id": "svc-haircut",
        "date": "2026-07-10",
        "time": "14:00",
        "organization_id": "org-1",
    }
    session = _availability_cache_session(
        slots,
        confirmation_state="pending",
    )
    luma_response = _build_create_appointment_luma(
        slots,
        missing_slots=[],
        confirmation_state="pending",
    )

    plan, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        session_state=session,
        availability_resolved=True,
    )

    assert plan["status"] == "AWAITING_CONFIRMATION"
    assert plan["awaiting"] == "USER_CONFIRMATION"
    assert plan["action"] is None

    confirmation = _record(trace, PLANNER_CONFIRMATION_ID)
    assert confirmation["skipped"] is False
    assert confirmation["winner"] == "await_confirmation"
    assert confirmation["reason_code"] == CONFIRMATION_REQUIRED
    assert confirmation["category"] == ROUTING_CATEGORY

    status = _record(trace, PLANNER_STATUS_ID)
    assert status["winner"] == "AWAITING_CONFIRMATION"


def test_planner_trace_disabled_does_not_change_plan(monkeypatch):
    slots = {"service_id": "svc-haircut", "organization_id": "org-1"}
    luma_response = _build_create_appointment_luma(
        slots, missing_slots=["time", "date"]
    )

    monkeypatch.delenv(TRACE_ENV_VAR, raising=False)
    reset_decision_trace_state()

    plan_without_trace = build_decision_plan(
        intent_name="CREATE_APPOINTMENT",
        luma_response=copy.deepcopy(luma_response),
        domain="service",
        availability_resolved=False,
    )

    monkeypatch.setenv(TRACE_ENV_VAR, "1")
    TurnTrace.begin(user_id="planner-trace-test", text="test")
    plan_with_trace = build_decision_plan(
        intent_name="CREATE_APPOINTMENT",
        luma_response=copy.deepcopy(luma_response),
        domain="service",
        availability_resolved=False,
    )
    trace = trace_to_dict(finalize_turn_trace())

    assert plan_with_trace == plan_without_trace
    assert len(trace["records"]) > 0

    monkeypatch.delenv(TRACE_ENV_VAR, raising=False)
    reset_decision_trace_state()
    plan_after_reset = build_decision_plan(
        intent_name="CREATE_APPOINTMENT",
        luma_response=copy.deepcopy(luma_response),
        domain="service",
        availability_resolved=False,
    )
    assert plan_after_reset == plan_without_trace


def test_planner_graph_links_evidence_and_mutations():
    slots = {"service_id": "svc-haircut", "organization_id": "org-1"}
    luma_response = _build_create_appointment_luma(
        slots, missing_slots=["time", "date"]
    )

    _, trace = _build_plan_with_trace(
        intent_name="CREATE_APPOINTMENT",
        luma_response=luma_response,
        availability_resolved=False,
    )

    depends = [edge for edge in trace["edges"] if edge["kind"] == "depends_on"]
    causes = [edge for edge in trace["edges"] if edge["kind"] == "causes"]

    assert any(
        edge["from"] == PLANNER_EVIDENCE_MISSING_SLOTS_ID
        and edge["to"] == PLANNER_STATUS_ID
        for edge in depends
    )
    assert any(
        edge["from"] == PLANNER_SELECT_ACTION_ID and edge["kind"] == "causes"
        for edge in causes
    )
    mutations = [record for record in trace["records"] if record["kind"] == "mutation"]
    assert any(mutation["field"] == "plan.action" for mutation in mutations)
