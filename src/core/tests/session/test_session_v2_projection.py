"""Direct Session V2 projection behavioural coverage."""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import patch

from core.session.session_projector import SessionProjectorV2
from core.session.session_schema_v2 import (
    empty_session_v2,
    prepare_session_for_persist,
)
from core.session.session_v2_projection import (
    assert_pure_v2_without_mirrors,
    project_session_v2,
)


def _persisted_v2(
    *,
    outcome: Dict[str, Any],
    outcome_status: str,
    merged: Optional[Dict[str, Any]] = None,
    previous: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    v2 = project_session_v2(
        previous_session_state=previous,
        outcome=outcome,
        outcome_status=outcome_status,
        merged_luma_response=merged,
        organization_id=1,
        user_id="u1",
    )
    if v2 is None:
        return None
    return prepare_session_for_persist(v2)


# --- Direct projection ---


def test_direct_projection_is_pure_v2_without_mirrors():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        "missing_slots": ["time"],
        "ask_next": "time",
        "facts": {"slots": {"service_id": "premium haircut", "date": "2026-07-10"}},
    }
    merged = {
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        "_effective_collected_slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
        },
    }
    v2 = project_session_v2(
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        merged_luma_response=merged,
        organization_id=1,
        user_id="u1",
    )
    assert v2 is not None
    assert_pure_v2_without_mirrors(v2)
    assert v2["planning"]["slots"]["service_id"] == "premium haircut"
    assert "booking_id" not in v2["planning"]["slots"]
    assert v2["planning"]["ask_next"] == "time"
    assert v2["planning"]["missing_slots"] == ["time"]


def test_direct_projection_moves_committed_ids_to_booking():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
            "booking_id": "b-1",
            "booking_code": "CODE1",
        },
        "missing_slots": [],
        "facts": {},
        "refs": {"booking_id": "b-1", "booking_code": "CODE1"},
    }
    merged = {
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
        }
    }
    v2 = project_session_v2(
        outcome=outcome,
        outcome_status="EXECUTED",
        merged_luma_response=merged,
        organization_id=1,
        user_id="u1",
    )
    assert v2 is not None
    assert v2["booking"]["booking_id"] == "b-1"
    assert v2["booking"]["booking_code"] == "CODE1"
    assert "booking_id" not in v2["planning"]["slots"]
    assert "booking_code" not in v2["planning"]["slots"]


def test_direct_projection_preserves_customer_id():
    previous = empty_session_v2()
    previous["customer_id"] = 42
    previous["planning"]["intent_name"] = "CREATE_APPOINTMENT"
    previous["planning"]["slots"] = {"service_id": "premium haircut"}
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        "missing_slots": ["time"],
        "facts": {},
    }
    merged = {
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
    }
    v2 = project_session_v2(
        previous_session_state=previous,
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        merged_luma_response=merged,
        organization_id=1,
        user_id="u1",
    )
    assert v2 is not None
    assert v2["customer_id"] == 42


def test_session_clear_lifecycle_returns_none():
    outcome = {"intent_name": "UNKNOWN", "slots": {}, "facts": {}}
    assert (
        project_session_v2(
            outcome=outcome,
            outcome_status="READY",
            merged_luma_response={"slots": {}},
            organization_id=1,
            user_id="u1",
        )
        is None
    )


# --- Projector integration ---


def test_projector_uses_project_session_v2_only():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["date", "time"],
        "ask_next": "date",
        "facts": {},
    }
    merged = {"slots": {"service_id": "premium haircut"}}
    with patch(
        "core.session.session_projector.project_session_v2",
        wraps=__import__(
            "core.session.session_v2_projection", fromlist=["project_session_v2"]
        ).project_session_v2,
    ) as projected_fn:
        projected = SessionProjectorV2().project(
            outcome=outcome,
            outcome_status="NEEDS_CLARIFICATION",
            organization_id=1,
            merged_luma_response=merged,
            user_id="u1",
        )
        projected_fn.assert_called()
    assert projected is not None
    assert projected.get("schema_version") == 2
    assert "slots" in projected
    assert projected["slots"]["service_id"] == "premium haircut"
    pure = prepare_session_for_persist(projected)
    assert "slots" not in pure
    assert pure["planning"]["slots"]["service_id"] == "premium haircut"


def test_projector_persists_confirmation_from_merged():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "missing_slots": [],
        "facts": {},
    }
    merged = {
        "slots": {
            "service_id": "premium haircut",
            "date": "2026-07-10",
            "time": "10:00",
        },
        "confirmation_state": "pending",
        "resolved_datetime_range": {
            "start": "2026-07-10T10:00:00Z",
            "end": "2026-07-10T10:30:00Z",
        },
    }
    projected = SessionProjectorV2().project(
        outcome=outcome,
        outcome_status="AWAITING_CONFIRMATION",
        organization_id=1,
        merged_luma_response=merged,
        user_id="u1",
    )
    assert projected is not None
    assert projected["confirmation_state"] == "pending"
    pure = prepare_session_for_persist(projected)
    assert pure["confirmation_state"] == "pending"
    assert pure["planning"]["bound_datetime"]["start"] == "2026-07-10T10:00:00Z"


def test_projector_availability_from_workflow_result():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        "missing_slots": ["time"],
        "facts": {},
    }
    merged = {"slots": {"service_id": "premium haircut", "date": "2026-07-10"}}
    workflow = {
        "availability_fingerprint": "fp-1",
        "last_execution_result": {"slots": [{"time": "10:00"}]},
        "presented_availability": {"slots": [{"time": "10:00"}]},
        "availability_presentation": {"page_index": 1, "page_size": 3},
    }
    projected = SessionProjectorV2().project(
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        organization_id=1,
        merged_luma_response=merged,
        workflow_result=workflow,
        user_id="u1",
    )
    assert projected is not None
    pure = prepare_session_for_persist(projected)
    assert pure["availability"]["fingerprint"] == "fp-1"
    assert pure["availability"]["cache"]["search_result"]["slots"][0]["time"] == "10:00"
    assert pure["availability"]["presentation"]["page_index"] == 1


def test_projector_appends_conversation_history():
    outcome = {
        "intent_name": "CREATE_APPOINTMENT",
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["date"],
        "facts": {},
    }
    projected = SessionProjectorV2().project(
        outcome=outcome,
        outcome_status="NEEDS_CLARIFICATION",
        organization_id=1,
        merged_luma_response={"slots": {"service_id": "premium haircut"}},
        conversation_messages=[
            {"role": "user", "text": "book premium"},
            {"role": "assistant", "text": "What date?"},
        ],
        user_id="u1",
    )
    assert projected is not None
    pure = prepare_session_for_persist(projected)
    assert len(pure["conversation"]["history"]) == 2


# --- Canonical projection scenarios ---


def test_persisted_empty_session_clear():
    assert (
        _persisted_v2(
            outcome={"intent_name": "UNKNOWN", "slots": {}, "facts": {}},
            outcome_status="READY",
            merged={"slots": {}},
        )
        is None
    )


def test_persisted_in_progress_create_appointment():
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut"},
            "missing_slots": ["date", "time"],
            "ask_next": "date",
            "facts": {},
        },
        outcome_status="NEEDS_CLARIFICATION",
        merged={"slots": {"service_id": "premium haircut"}},
    )
    assert pure is not None
    assert_pure_v2_without_mirrors(pure)
    assert pure["planning"]["slots"]["service_id"] == "premium haircut"
    assert pure["planning"]["ask_next"] == "date"


def test_persisted_missing_required_slot():
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
            "missing_slots": ["time"],
            "ask_next": "time",
            "facts": {},
        },
        outcome_status="NEEDS_CLARIFICATION",
        merged={
            "slots": {"service_id": "premium haircut", "date": "2026-07-10"},
        },
    )
    assert pure is not None
    assert pure["planning"]["missing_slots"] == ["time"]


def test_persisted_bound_datetime():
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-10",
                "time": "10:00",
            },
            "missing_slots": [],
            "facts": {},
        },
        outcome_status="AWAITING_CONFIRMATION",
        merged={
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-10",
                "time": "10:00",
            },
            "resolved_datetime_range": {
                "start": "2026-07-10T10:00:00Z",
                "end": "2026-07-10T10:30:00Z",
            },
        },
    )
    assert pure is not None
    assert pure["planning"]["bound_datetime"]["start"] == "2026-07-10T10:00:00Z"


def test_persisted_successful_booking_commit():
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-10",
                "time": "10:00",
            },
            "missing_slots": [],
            "refs": {"booking_id": "b-99", "booking_code": "ZZ"},
            "facts": {},
        },
        outcome_status="EXECUTED",
        merged={
            "slots": {
                "service_id": "premium haircut",
                "date": "2026-07-10",
                "time": "10:00",
            }
        },
    )
    assert pure is not None
    assert pure["booking"]["booking_id"] == "b-99"
    assert pure["booking"]["booking_code"] == "ZZ"


def test_persisted_existing_persisted_v2_input():
    previous = empty_session_v2()
    previous["customer_id"] = 7
    previous["planning"]["intent_name"] = "CREATE_APPOINTMENT"
    previous["planning"]["slots"] = {"service_id": "premium haircut"}
    previous["planning"]["status"] = "NEEDS_CLARIFICATION"
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut", "date": "2026-07-11"},
            "missing_slots": ["time"],
            "ask_next": "time",
            "facts": {},
        },
        outcome_status="NEEDS_CLARIFICATION",
        merged={
            "slots": {"service_id": "premium haircut", "date": "2026-07-11"},
        },
        previous=previous,
    )
    assert pure is not None
    assert pure["customer_id"] == 7
    assert pure["planning"]["slots"]["service_id"] == "premium haircut"
    assert "time" in (pure["planning"].get("missing_slots") or [])


def test_persisted_existing_persisted_v1_input():
    previous = {
        "intent_name": "CREATE_APPOINTMENT",
        "status": "NEEDS_CLARIFICATION",
        "slots": {"service_id": "premium haircut"},
        "missing_slots": ["date", "time"],
        "customer_id": 9,
        "facts": {},
    }
    pure = _persisted_v2(
        outcome={
            "intent_name": "CREATE_APPOINTMENT",
            "slots": {"service_id": "premium haircut", "date": "2026-07-12"},
            "missing_slots": ["time"],
            "ask_next": "time",
            "facts": {},
        },
        outcome_status="NEEDS_CLARIFICATION",
        merged={
            "slots": {"service_id": "premium haircut", "date": "2026-07-12"},
        },
        previous=previous,
    )
    assert pure is not None
    assert pure["customer_id"] == 9
    assert pure["planning"]["slots"]["service_id"] == "premium haircut"
