"""Tests for availability render-request construction and wording."""

from core.rendering.availability_renderer import build_availability_render_request
from core.workflows.availability.presentation import build_presented_availability


def test_build_render_request_includes_availability_facts():
    decision = {
        "intent_name": "CREATE_APPOINTMENT",
        "facts": {"slots": {"service_id": "premium haircut"}},
    }
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-02T09:00:00.000Z",
                    "ends_at": "2026-07-02T09:30:00.000Z",
                },
            ],
            "time_resolution": None,
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(decision, execution, presented=presented)
    assert req is not None
    assert req.facts["availability"]["service_name"] == "Premium Haircut"
    assert req.facts["availability"]["times"]
    assert "bullet list" in req.render_instruction.lower()


def test_explicit_availability_without_current_time_defensively_lists_exact_match():
    decision = {
        "plan": {
            "turn_operation": "AVAILABILITY",
            "execution_proposal_context": {
                "current_turn_has_explicit_time": False,
                "confirmation_continuation": False,
            },
        },
    }
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "premium haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-21T09:00:00.000Z",
                    "ends_at": "2026-07-21T09:30:00.000Z",
                },
            ],
            "time_resolution": {
                "outcome": "TIME_MATCH_EXACT",
                "requested_time": "09:00",
                "matched_offer": "2026-07-21T09:00:00.000Z",
            },
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(decision, execution, presented=presented)

    assert req is not None
    assert "bullet list" in req.render_instruction.lower()
    assert "confirm" not in req.render_instruction.lower()
    assert req.facts["time_resolution"]["outcome"] == "TIME_MATCH_NOT_APPLICABLE"


def test_build_render_request_none_when_no_slots():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": None},
        "availability": {"slots": [], "time_resolution": None},
    }
    presented = build_presented_availability([])
    assert build_availability_render_request({}, execution, presented=presented) is None
