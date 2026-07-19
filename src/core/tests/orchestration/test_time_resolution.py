"""Tests for deterministic time resolution after availability search."""

from core.planning.pipeline.decision_finalization import (
    TimeResolutionEvidence,
    finalize_decision_after_time_resolution,
)
from core.planning.time_resolution import (
    TIME_MATCH_EXACT,
    TIME_MATCH_MISMATCH,
    TIME_MATCH_NOT_APPLICABLE,
    apply_post_bind_time_resolution,
    resolve_time_after_availability,
)
from core.tests.harness.planning_compat import build_decision_plan
from core.rendering.availability_renderer import build_availability_render_request
from core.workflows.availability.presentation import (
    build_availability_presentation,
    build_presented_availability,
    build_presented_availability_page,
)


def test_exact_match_binds_requested_time():
    offers = [
        {
            "starts_at": "2026-07-09T09:00:00.000Z",
            "ends_at": "2026-07-09T09:30:00.000Z",
        },
        {
            "starts_at": "2026-07-09T10:00:00.000Z",
            "ends_at": "2026-07-09T10:30:00.000Z",
        },
    ]
    result = resolve_time_after_availability(
        offers=offers,
        time_proposal={"mode": "exact", "value": "09:00"},
        date_proposal={"mode": "single_day", "start": "2026-07-09"},
        search_date="2026-07-09",
        slots={"service_id": "premium haircut"},
    )
    resolution = result["time_resolution"]
    assert resolution["outcome"] == TIME_MATCH_EXACT
    assert resolution["requested_time"] == "09:00"
    assert resolution["matched_offer"] == "2026-07-09T09:00:00.000Z"
    bind = result["bind_result"]
    assert bind["slots"]["date"] == "2026-07-09"
    assert bind["slots"]["time"] == "09:00"
    assert bind["resolved_datetime_range"]["start"] == "2026-07-09T09:00:00.000Z"


def test_no_match_returns_alternatives():
    offers = [
        {
            "starts_at": "2026-07-09T09:30:00.000Z",
            "ends_at": "2026-07-09T10:00:00.000Z",
        },
        {
            "starts_at": "2026-07-09T10:00:00.000Z",
            "ends_at": "2026-07-09T10:30:00.000Z",
        },
    ]
    result = resolve_time_after_availability(
        offers=offers,
        time_proposal={"mode": "exact", "value": "09:00"},
        search_date="2026-07-09",
        slots={"service_id": "haircut"},
    )
    resolution = result["time_resolution"]
    assert resolution["outcome"] == TIME_MATCH_MISMATCH
    assert resolution["requested_time"] == "09:00"
    assert resolution["alternatives"] == [
        "2026-07-09T09:30:00.000Z",
        "2026-07-09T10:00:00.000Z",
    ]
    assert result["bind_result"] is None


def test_not_applicable_without_exact_time_proposal():
    result = resolve_time_after_availability(
        offers=[
            {
                "starts_at": "2026-07-09T09:00:00.000Z",
                "ends_at": "2026-07-09T09:30:00.000Z",
            }
        ],
        time_proposal={"mode": "fuzzy", "label": "morning", "start": "09:00", "end": "12:00"},
        slots={},
    )
    assert result["time_resolution"]["outcome"] == TIME_MATCH_NOT_APPLICABLE
    assert result["bind_result"] is None


def test_render_request_exact_match_includes_resolution():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-09T09:00:00.000Z",
                    "ends_at": "2026-07-09T09:30:00.000Z",
                }
            ],
            "time_resolution": {
                "outcome": TIME_MATCH_EXACT,
                "requested_time": "09:00",
                "matched_offer": "2026-07-09T09:00:00.000Z",
            },
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(
        {"facts": {"slots": {"service_id": "haircut"}}},
        execution,
        presented=presented,
    )
    assert req is not None
    assert req.facts["time_resolution"]["outcome"] == TIME_MATCH_EXACT
    assert "confirm" in req.render_instruction.lower()
    assert "do not list other times" in req.render_instruction.lower()


def test_render_request_no_match_uses_alternatives():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": "haircut"},
        "availability": {
            "slots": [
                {
                    "starts_at": "2026-07-09T09:30:00.000Z",
                    "ends_at": "2026-07-09T10:00:00.000Z",
                }
            ],
            "time_resolution": {
                "outcome": TIME_MATCH_MISMATCH,
                "requested_time": "09:00",
                "alternatives": ["2026-07-09T09:30:00.000Z"],
            },
        },
    }
    presented = build_presented_availability(execution["availability"]["slots"])
    req = build_availability_render_request(
        {"facts": {"slots": {"service_id": "haircut"}}},
        execution,
        presented=presented,
    )
    assert req is not None
    assert req.facts["time_resolution"]["outcome"] == TIME_MATCH_MISMATCH
    assert "not available" in req.render_instruction.lower()
    assert req.facts["availability"]["times"]


def test_apply_time_match_exact_updates_plan_status():
    plan = {
        "status": "NEEDS_CLARIFICATION",
        "plan": {"status": "NEEDS_CLARIFICATION", "action": "SEARCH_AVAILABILITY"},
        "_decision": {
            "status": "NEEDS_CLARIFICATION",
            "plan": {"status": "NEEDS_CLARIFICATION", "action": "SEARCH_AVAILABILITY"},
            "facts": {"slots": {"service_id": "haircut"}},
        },
    }
    bind_result = {
        "slots": {"service_id": "haircut", "date": "2026-07-09", "time": "09:00"},
        "resolved_datetime_range": {
            "start": "2026-07-09T09:00:00.000Z",
            "end": "2026-07-09T09:30:00.000Z",
        },
    }
    finalize_decision_after_time_resolution(
        plan,
        evidence=TimeResolutionEvidence(
            outcome=TIME_MATCH_EXACT,
            time_resolution={"outcome": TIME_MATCH_EXACT, "requested_time": "09:00"},
            bind_result=bind_result,
            apply_confirmation_transition=False,
        ),
    )
    assert plan["status"] == "AWAITING_CONFIRMATION"
    assert plan["time_match_outcome"] == TIME_MATCH_EXACT
    assert plan["slots"]["time"] == "09:00"
    assert plan["action"] is None
    assert plan["_decision"]["status"] == "AWAITING_CONFIRMATION"


def test_apply_time_match_mismatch_requires_clarification():
    plan = {
        "status": "READY",
        "plan": {"status": "READY", "action": None},
        "_decision": {
            "status": "READY",
            "plan": {"status": "READY", "action": None},
            "facts": {"slots": {"service_id": "haircut"}},
        },
    }
    resolution = {
        "outcome": TIME_MATCH_MISMATCH,
        "requested_time": "09:15",
        "alternatives": ["2026-07-09T09:00:00.000Z", "2026-07-09T09:30:00.000Z"],
    }
    finalize_decision_after_time_resolution(
        plan,
        evidence=TimeResolutionEvidence(
            outcome=TIME_MATCH_MISMATCH,
            time_resolution=resolution,
            time_proposal={"mode": "exact", "value": "09:15"},
            apply_confirmation_transition=False,
        ),
    )
    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["time_match_outcome"] == TIME_MATCH_MISMATCH
    assert plan["action"] is None
    assert plan["awaiting"] == "TIME_SELECTION"
    assert plan["time_proposal"]["value"] == "09:15"


def test_plan_builder_forces_mismatch_clarification_not_ready():
    luma_response = {
        "missing_slots": [],
        "needs_clarification": False,
        "slots": {"service_id": "haircut", "date": "2026-07-09"},
        "time_proposal": {"mode": "exact", "value": "09:15"},
        "time_match_outcome": TIME_MATCH_MISMATCH,
        "time_resolution": {
            "outcome": TIME_MATCH_MISMATCH,
            "requested_time": "09:15",
            "alternatives": [
                "2026-07-09T09:00:00.000Z",
                "2026-07-09T09:30:00.000Z",
            ],
        },
    }
    plan = build_decision_plan(
        "CREATE_APPOINTMENT",
        luma_response,
        "service",
        organization_id=1,
        availability_resolved=True,
    )
    assert plan["status"] == "NEEDS_CLARIFICATION"
    assert plan["action"] is None
    assert plan["time_match_outcome"] == TIME_MATCH_MISMATCH
    assert plan["awaiting"] == "TIME_SELECTION"


def test_render_request_no_match_without_alternatives():
    execution = {
        "schema_version": 1,
        "status": "succeeded",
        "subject": {"service_name": None},
        "availability": {
            "slots": [],
            "time_resolution": {
                "outcome": TIME_MATCH_MISMATCH,
                "requested_time": "09:00",
                "alternatives": [],
            },
        },
    }
    presented = {
        "search_date": None,
        "slots": [],
        "times": [],
        "more_count": 0,
        "total_unique": 0,
    }
    req = build_availability_render_request({}, execution, presented=presented)
    assert req is not None
    assert "no alternative" in req.render_instruction.lower()


def _paginated_session(*, page_index: int) -> dict:
    """Nine-slot cache with page_index presentation (page_size=6)."""
    raw = [
        {
            "starts_at": f"2026-07-09T{h:02d}:00:00Z",
            "ends_at": f"2026-07-09T{h:02d}:30:00Z",
        }
        for h in range(9, 18)
    ]
    return {
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-09",
            "slots": raw,
        },
        "presented_availability": build_presented_availability_page(
            raw, page_index=page_index, page_size=6, search_date="2026-07-09"
        ),
        "availability_presentation": build_availability_presentation(
            raw, page_index=page_index, page_size=6
        ),
    }


def test_apply_post_bind_time_resolution_page_two_rejects_page_one_time():
    session = _paginated_session(page_index=1)
    merged = {
        "slots": {"service_id": "premium haircut"},
        "time_proposal": {"mode": "exact", "value": "9am"},
    }
    payload = apply_post_bind_time_resolution(merged, session)
    assert payload is not None
    assert merged["time_match_outcome"] == TIME_MATCH_MISMATCH
    assert merged.get("resolved_datetime_range") is None
    assert "time" not in (merged.get("slots") or {})
    assert payload["bind_result"] is None


def test_apply_post_bind_time_resolution_page_two_binds_presented_time():
    session = _paginated_session(page_index=1)
    merged = {
        "slots": {"service_id": "premium haircut"},
        "time_proposal": {"mode": "exact", "value": "5pm"},
    }
    payload = apply_post_bind_time_resolution(merged, session)
    assert payload is not None
    assert merged["time_match_outcome"] == TIME_MATCH_EXACT
    assert merged["slots"]["time"] == "17:00"
    assert merged["slots"]["date"] == "2026-07-09"
    assert merged["resolved_datetime_range"]["start"] == "2026-07-09T17:00:00Z"


def test_apply_post_bind_time_resolution_first_page_unchanged():
    """Without pagination, post-bind resolves against the first presented page."""
    raw = [
        {
            "starts_at": "2026-07-09T09:00:00.000Z",
            "ends_at": "2026-07-09T09:30:00.000Z",
        },
        {
            "starts_at": "2026-07-09T10:00:00.000Z",
            "ends_at": "2026-07-09T10:30:00.000Z",
        },
    ]
    session = {
        "last_execution_result": {
            "type": "availability",
            "status": "success",
            "search_date": "2026-07-09",
            "slots": raw,
        },
        "presented_availability": build_presented_availability(
            raw, search_date="2026-07-09"
        ),
    }
    merged = {
        "slots": {"service_id": "premium haircut"},
        "time_proposal": {"mode": "exact", "value": "09:00"},
        "date_proposal": {"mode": "single_day", "start": "2026-07-09"},
    }
    payload = apply_post_bind_time_resolution(merged, session)
    assert payload is not None
    assert merged["time_match_outcome"] == TIME_MATCH_EXACT
    assert merged["slots"]["time"] == "09:00"
    assert merged["resolved_datetime_range"]["start"] == "2026-07-09T09:00:00.000Z"


def test_apply_post_bind_time_resolution_legacy_session_caps_to_first_page():
    """Sessions without presented_availability still bind only against page-0 offers."""
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
    }
    merged = {
        "slots": {"service_id": "premium haircut"},
        "time_proposal": {"mode": "exact", "value": "5pm"},
    }
    payload = apply_post_bind_time_resolution(merged, session)
    assert payload is not None
    assert merged["time_match_outcome"] == TIME_MATCH_MISMATCH
    assert merged.get("resolved_datetime_range") is None
