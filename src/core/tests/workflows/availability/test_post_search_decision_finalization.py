"""Focused post-search Decision finalization for presented time selection."""

from copy import deepcopy

import pytest

from core.execution.dispatcher import _normalize_availability_response
from core.planning.pipeline.decision_finalization import (
    TimeResolutionEvidence,
    finalize_decision_after_time_resolution,
)
from core.planning.time_resolution import TIME_MATCH_NOT_APPLICABLE
from core.rendering.availability_renderer import (
    build_availability_render_request,
    render_successful_fresh_availability,
)
from core.workflows.availability.workflow import AvailabilityWorkflow


OFFER = {
    "starts_at": "2026-08-17T09:30:00+00:00",
    "ends_at": "2026-08-17T10:00:00+00:00",
}


@pytest.mark.parametrize(
    "response",
    [
        {"slots": "not-a-list"},
        {"slots": ["not-an-object"]},
        {"slots": [{"start": OFFER["starts_at"]}]},
    ],
)
def test_malformed_provider_availability_is_not_normalized_as_empty_success(response):
    with pytest.raises(ValueError):
        _normalize_availability_response(response)


def test_explicit_empty_provider_availability_remains_successful():
    assert _normalize_availability_response({"slots": []}) == {
        "type": "availability",
        "status": "success",
        "slots": [],
    }


def _plan(*, intent="CREATE_APPOINTMENT", date=None):
    slots = {"service_id": "oil-change", "engine_type": "ev"}
    missing = ["date", "time", "registration_number"]
    ask_next = "date"
    if date:
        slots["date"] = date
        missing = ["time", "registration_number"]
        ask_next = "time"
    return {
        "intent_name": intent,
        "status": "READY",
        "stage": "AVAILABILITY",
        "action": "SEARCH_AVAILABILITY",
        "ask_next": ask_next,
        "awaiting": None,
        "missing_slots": missing,
        "slots": dict(slots),
        "plan": {
            "status": "READY",
            "stage": "AVAILABILITY",
            "action": "SEARCH_AVAILABILITY",
            "ask_next": ask_next,
            "awaiting": None,
        },
        "_decision": {
            "ask_next": ask_next,
            "plan": {
                "status": "READY",
                "stage": "AVAILABILITY",
                "action": "SEARCH_AVAILABILITY",
                "ask_next": ask_next,
                "awaiting": None,
            },
            "facts": {"slots": dict(slots), "ask_next": ask_next},
        },
    }


def _n_a_evidence(options=None):
    return TimeResolutionEvidence(
        outcome=TIME_MATCH_NOT_APPLICABLE,
        time_resolution={"outcome": TIME_MATCH_NOT_APPLICABLE},
        presented_options=list(options if options is not None else [OFFER]),
        apply_confirmation_transition=False,
    )


def test_default_date_search_finalizes_all_decision_views_to_time_selection():
    plan = _plan()

    finalize_decision_after_time_resolution(plan, evidence=_n_a_evidence())

    assert plan["ask_next"] == "time"
    assert plan["awaiting"] == "time"
    assert plan["plan"]["ask_next"] == "time"
    assert plan["plan"]["awaiting"] == "time"
    assert plan["_decision"]["ask_next"] == "time"
    assert plan["_decision"]["plan"]["ask_next"] == "time"
    assert plan["_decision"]["plan"]["awaiting"] == "time"
    assert plan["_decision"]["facts"]["ask_next"] == "time"
    assert "date" not in plan["slots"]


def test_explicit_date_search_remains_time_selection_ready():
    plan = _plan(date="2026-08-19")

    finalize_decision_after_time_resolution(plan, evidence=_n_a_evidence())

    assert plan["ask_next"] == "time"
    assert plan["awaiting"] == "time"
    assert plan["slots"]["date"] == "2026-08-19"


def test_empty_or_malformed_presented_options_do_not_transition():
    for options in ([], [{}], [{"starts_at": "not-a-datetime"}]):
        plan = _plan()
        before = deepcopy(plan)

        finalize_decision_after_time_resolution(
            plan, evidence=_n_a_evidence(options)
        )

        assert plan == before


def test_selected_or_bound_time_is_not_reopened():
    for selected in (
        {"time": "09:30"},
        {"resolved_datetime_range": {"start": OFFER["starts_at"]}},
    ):
        plan = _plan()
        plan.update(selected)
        if "time" in selected:
            plan["slots"]["time"] = selected["time"]
        before = deepcopy(plan)

        finalize_decision_after_time_resolution(plan, evidence=_n_a_evidence())

        assert plan == before


def test_non_booking_and_browse_paths_do_not_transition():
    informational = _plan(intent="AVAILABILITY")
    browse = _plan()
    browse["availability_browse"] = {"direction": "next"}

    for plan in (informational, browse):
        before = deepcopy(plan)
        finalize_decision_after_time_resolution(plan, evidence=_n_a_evidence())
        assert plan == before


def test_successful_workflow_search_applies_n_a_finalization_after_presentation():
    plan = _plan()
    execution = {
        "status": "succeeded",
        "availability": {"slots": [dict(OFFER)]},
        "subject": {"service_name": "Executive Oil Change"},
    }

    slots, _, workflow_result = AvailabilityWorkflow().process_search_result(
        execution,
        plan,
        dict(plan["slots"]),
        session_state={},
        session_store=None,
        user_id="user-1",
        organization_id=2,
    )

    assert workflow_result["presented_availability"]["slots"] == [OFFER]
    assert workflow_result["kind"] == "availability_search"
    assert workflow_result["status"] == "succeeded"
    assert plan["ask_next"] == "time"
    assert plan["awaiting"] == "time"
    assert "date" not in slots


def test_empty_workflow_search_preserves_existing_next_step():
    plan = _plan()
    execution = {
        "status": "succeeded",
        "availability": {"slots": []},
        "subject": {"service_name": "Executive Oil Change"},
    }

    _, _, workflow_result = AvailabilityWorkflow().process_search_result(
        execution,
        plan,
        dict(plan["slots"]),
        session_state={},
        session_store=None,
        user_id="user-1",
        organization_id=2,
    )

    assert plan["ask_next"] == "date"
    assert plan["awaiting"] is None
    assert workflow_result["status"] == "succeeded"
    assert workflow_result["last_execution_result"]["slots"] == []


def test_failed_workflow_result_does_not_transition():
    plan = _plan()
    execution = {
        "status": "failed",
        "availability": {"slots": [dict(OFFER)]},
        "subject": {"service_name": "Executive Oil Change"},
    }

    _, _, workflow_result = AvailabilityWorkflow().process_search_result(
        execution,
        plan,
        dict(plan["slots"]),
        session_state={},
        session_store=None,
        user_id="user-1",
        organization_id=2,
    )

    assert plan["ask_next"] == "date"
    assert plan["awaiting"] is None
    assert workflow_result == {"kind": "availability_search", "status": "failed"}


@pytest.mark.parametrize(
    "availability",
    [None, {"slots": "not-a-list"}],
)
def test_malformed_workflow_result_is_typed_failed(availability):
    plan = _plan()

    _, _, workflow_result = AvailabilityWorkflow().process_search_result(
        {"status": "succeeded", "availability": availability},
        plan,
        dict(plan["slots"]),
        session_state={},
        session_store=None,
        user_id="user-1",
        organization_id=2,
    )

    assert workflow_result == {"kind": "availability_search", "status": "failed"}


def test_default_and_explicit_date_searches_render_the_selection_question():
    for explicit_date in (None, "2026-08-17"):
        plan = _plan(date=explicit_date)
        execution = {
            "status": "succeeded",
            "availability": {"slots": [dict(OFFER)]},
            "subject": {"service_name": "Executive Oil Change"},
        }
        _, _, workflow_result = AvailabilityWorkflow().process_search_result(
            execution,
            plan,
            dict(plan["slots"]),
            session_state={},
            session_store=None,
            user_id="user-1",
            organization_id=2,
        )
        decision = plan["_decision"]
        request = build_availability_render_request(
            decision,
            execution,
            presented=workflow_result["presented_availability"],
        )

        assert request is not None
        text = render_successful_fresh_availability(decision, request)
        assert text is not None
        assert "Which time works best for you?" in text
